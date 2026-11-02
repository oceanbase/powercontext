# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Generate Candidate recovery evidence by observing the frozen migration on real databases.

Development prototype only. This module does not register or replace production resources.
The command uses the repository's isolated real-backend fixtures, never an application database.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from importlib.metadata import version
from pathlib import Path
from typing import Any
from unittest.mock import patch
from xml.etree import ElementTree

from sqlalchemy import Connection, Engine, event

from powercontext.builtin.persistence.migrations.bundle import MigrationBundle, sqlite_inventory
from powercontext.builtin.persistence.migrations.models import digest
from powercontext.builtin.persistence.migrations.mysql_schema import MySQLSchema, mysql_inventory

_CONTROL = frozenset({"pc_schema_revision"})


class ReplayMismatch(AssertionError):
    def __init__(self, operation: str):
        super().__init__(f"Generated inventory differs from the frozen checkpoint: {operation}")


class ReplayInterrupted(BaseException):
    """Stop after a real database operation has returned, before the runner can continue."""


def inventory(connection: Connection) -> Any:
    if connection.dialect.name == "sqlite":
        return sqlite_inventory(connection, exclude=_CONTROL)
    return mysql_inventory(connection, exclude=_CONTROL)


def objects(state: Any) -> dict[str, Any]:
    if isinstance(state, list):
        return {f"{row[0]}:{row[1]}": row for row in state}
    return {
        **{f"table:{name}": value for name, value in state["tables"].items()},
        "views": state["views"],
        "triggers": state["triggers"],
    }


class CandidateReplay:
    """Observe actual full-database inventories; frozen checkpoints are comparison inputs only."""

    def __init__(self, bundle: MigrationBundle, backend: str, output: Path, *, interrupt: str | None = None):
        self.bundle = bundle
        self.backend = backend
        self.output = output
        output.mkdir(parents=True, exist_ok=False)
        self.interrupt = interrupt
        self.records: list[dict[str, Any]] = []
        self.previous: Any = None
        self.recording = False
        self.manifest: dict[str, Any] = {
            "format": "powercontext.candidate-replay.v1",
            "backend": backend,
            "source_release": "1.3.0",
            "source_kind": "frozen captured full Server baseline, populated synthetic Candidate history",
            "source_revision": "s1300",
            "conversion_revision": "s1400",
            "bundle_sha256": bundle.checksum,
            "revision_checksums": bundle.checksums,
            "resources": bundle.resources,
            "status": "incomplete",
            "records": self.records,
        }
        if backend == "seekdb":
            self.manifest["binding_version"] = version("pylibseekdb")
        self.save()

    def save(self) -> None:
        (self.output / "manifest.json").write_text(
            json.dumps(self.manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    def capture(
        self,
        connection: Connection,
        statement: str,
        *,
        position: int | None = None,
        expected: Any = None,
        schema: MySQLSchema | None = None,
    ) -> Any:
        state = inventory(connection)
        before = objects(self.previous) if self.previous is not None else {}
        after = objects(state)
        changed = {
            name: after.get(name)
            for name in sorted(before.keys() | after.keys())
            if before.get(name) != after.get(name)
        }
        record: dict[str, Any] = {
            "sequence": len(self.records),
            "operation": statement,
            "registered_position": position,
            "schema_sha256": digest(state),
            "changes": changed,
        }
        if expected is not None:
            record["registered_schema_sha256"] = digest(expected)
            record["matches_registered_checkpoint"] = schema._matches(state, expected) if schema else state == expected
        filename = f"{len(self.records):04d}.json"
        record["inventory"] = filename
        (self.output / filename).write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        self.records.append(record)
        self.previous = copy.deepcopy(state)
        self.save()
        if record.get("matches_registered_checkpoint") is False:
            raise ReplayMismatch(statement)
        return state

    def begin(self, connection: Connection) -> Any:
        sql = "SELECT sqlite_version()" if self.backend == "sqlite" else "SELECT VERSION()"
        self.manifest["engine_version"] = connection.exec_driver_sql(sql).scalar_one()
        return self.capture(connection, "source")

    def interrupt_after(self, statement: str) -> None:
        if self.interrupt and statement.startswith(self.interrupt):
            self.interrupt = None
            raise ReplayInterrupted

    @contextmanager
    def native(self, schema: MySQLSchema) -> Iterator[None]:
        entry = json.loads((self.bundle.snapshot_directory / "schemas/mysql-s1400.json").read_text())["revisions"][
            "s1400"
        ]
        operations = []
        for statement in entry["statements"]:
            if "candidate" not in statement:
                break
            operations.append(statement)
        self.manifest["registered_operations"] = operations
        execute = schema.execute

        def observed(connection, statement, configuration):
            if statement not in operations:
                execute(connection, statement, configuration)
                return
            executed = []

            def capture_sql(_connection, _cursor, sql, _parameters, _context, _executemany):
                if sql.lstrip().upper().startswith(("CREATE ", "ALTER ", "DROP ", "INSERT ", "UPDATE ", "DELETE ")):
                    executed.append(sql)

            event.listen(connection, "after_cursor_execute", capture_sql)
            try:
                execute(connection, statement, configuration)
            finally:
                event.remove(connection, "after_cursor_execute", capture_sql)
            position = operations.index(statement) + 1
            self.capture(
                connection, statement, position=position, expected=schema._prefixes["s1400"][position], schema=schema
            )
            self.records[-1]["executed_sql"] = executed
            self.save()
            self.interrupt_after(statement)

        # Observation only: every original operation executes against the real connection.
        with patch.object(schema, "execute", observed):
            yield

    @contextmanager
    def sqlite(self) -> Iterator[None]:
        fingerprints = self.bundle.fingerprints["s1400"]

        def observed(connection, _cursor, statement, _parameters, _context, _executemany):
            if self.recording or connection.dialect.name != "sqlite":
                return
            operation = statement.strip()
            candidate = "candidate" in operation and operation.upper().startswith((
                "CREATE ",
                "ALTER ",
                "DROP ",
                "INSERT ",
            ))
            revision = operation.upper().startswith(("UPDATE PC_SCHEMA_REVISION", "INSERT INTO PC_SCHEMA_REVISION"))
            if not candidate and not revision:
                return
            self.recording = True
            try:
                if candidate:
                    self.capture(connection, operation)
                    self.interrupt_after(operation)
                elif connection.exec_driver_sql("SELECT version_num FROM pc_schema_revision").scalar_one() == "s1400":
                    state = self.capture(connection, "s1400 transaction checkpoint")
                    business = [
                        row
                        for row in state
                        if row[1] not in self.bundle.retained_objects and row[2] not in self.bundle.retained_objects
                    ]
                    self.records[-1]["matches_registered_checkpoint"] = digest(business) in fingerprints
                    self.records[-1]["registered_schema_sha256"] = fingerprints
                    self.save()
                    if digest(business) not in fingerprints:
                        raise ReplayMismatch("s1400")
            finally:
                self.recording = False

        event.listen(Engine, "after_cursor_execute", observed)
        try:
            yield
        finally:
            event.remove(Engine, "after_cursor_execute", observed)

    def finish(self, **evidence: Any) -> None:
        self.bundle.assert_unchanged()
        self.manifest.update(evidence, status="passed")
        self.save()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("backend", choices=("sqlite", "seekdb", "oceanbase"))
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    output = arguments.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    os.environ["POWERCONTEXT_CANDIDATE_REPLAY_OUTPUT"] = str(output)
    import pytest

    root = Path(__file__).resolve().parents[1]
    report = output / "junit.xml"
    result = pytest.main([
        str(root / "tests/builtin/persistence/test_candidate_schema_replay.py"),
        "-k",
        arguments.backend,
        "-vv",
        "-o",
        "faulthandler_timeout=180",
        f"--junitxml={report}",
    ])
    if result:
        return int(result)
    cases = list(ElementTree.parse(report).iter("testcase"))  # noqa: S314 -- locally generated pytest report
    if len(cases) != 3 or any(case.find(tag) is not None for case in cases for tag in ("skipped", "failure", "error")):
        print("All three real-backend Candidate replay/recovery cases must pass without skips", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
