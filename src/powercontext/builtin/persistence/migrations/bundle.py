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

"""A checked, linear Alembic history with explicitly recognized schema shapes."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import Connection

from .models import MigrationError, digest


class MigrationBundle:
    """Load a trusted local bundle. No revision imports current application metadata.

    The manifest records exact SQLite schema fingerprints, including alternate
    shapes produced by a supported historical initializer. Unlisted objects or
    partially applied DDL cannot be mistaken for a supported baseline.
    """

    def __init__(self, directory: Path) -> None:
        self.directory = directory.resolve()
        # Execute exactly the reviewed bytes, even if the installed resources
        # change during a long backup. Alembic reloads scripts on every command.
        resources = {
            path.relative_to(self.directory).as_posix(): path.read_bytes()
            for path in sorted(self.directory.rglob("*"))
            if path.is_file() and path.suffix in {".py", ".sql", ".json"}
        }
        self._snapshot = TemporaryDirectory(prefix="pc-migration-bundle-")
        self.snapshot_directory = Path(self._snapshot.name).resolve()
        for name, content in resources.items():
            target = self.snapshot_directory / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        self.script = ScriptDirectory(str(self.snapshot_directory))
        revisions = list(reversed(list(self.script.walk_revisions())))
        previous = None
        for item in revisions:
            if item.down_revision != previous or item.dependencies or len(item.revision) > 32:
                raise MigrationError("invalid_bundle", "Migration history must be a single linear chain.")
            previous = item.revision
        if not revisions:
            raise MigrationError("invalid_bundle", "Migration history is empty.")
        self.revisions = tuple(item.revision for item in revisions)
        self.head = self.revisions[-1]
        manifest = json.loads(resources["manifest.json"])
        self.affected_objects = tuple(manifest.get("affected_objects", ()))
        self.retained_objects = tuple(manifest.get("retained_objects", ()))
        self.retained_fingerprints = manifest.get("retained_fingerprints", {})
        self.retained_revisions = manifest.get("retained_revisions", {})
        if set(self.retained_objects) != set(self.retained_revisions) or not set(
            self.retained_revisions.values()
        ) <= set(self.revisions):
            raise MigrationError("invalid_bundle", "Retained objects must declare their introducing revision.")
        self.fingerprints: dict[str, list[str]] = manifest["sqlite_fingerprints"]
        self.baselines: dict[str, str] = manifest["sqlite_baselines"]
        if set(self.fingerprints) != set(self.revisions) or not set(self.baselines.values()) <= set(self.revisions):
            raise MigrationError("invalid_bundle", "Schema inventory does not cover the revision chain.")
        if any(fingerprint not in self.fingerprints[revision] for fingerprint, revision in self.baselines.items()):
            raise MigrationError("invalid_bundle", "A baseline does not match its declared revision.")
        self.resources = {name: hashlib.sha256(content).hexdigest() for name, content in resources.items()}
        self.checksums = {}
        for item in revisions:
            dependencies = manifest["revision_resources"][item.revision]
            inputs = {name: self.resources[name] for name in ["env.py", *dependencies]}
            inputs[Path(item.path).relative_to(self.snapshot_directory).as_posix()] = hashlib.sha256(
                Path(item.path).read_bytes()
            ).hexdigest()
            self.checksums[item.revision] = digest(inputs)
        self.checksum = digest(self.resources)

    def assert_unchanged(self) -> None:
        resources = {
            path.relative_to(self.directory).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(self.directory.rglob("*"))
            if path.is_file() and path.suffix in {".py", ".sql", ".json"}
        }
        if resources != self.resources:
            raise MigrationError("plan_changed", "Migration resources changed after the bundle was loaded.")

    def config(self, connection: Connection) -> Config:
        config = Config()
        config.set_main_option("script_location", str(self.snapshot_directory).replace("%", "%%"))
        config.attributes["connection"] = connection
        return config

    def pending(self, revision: str | None) -> tuple[str, ...]:
        if revision is None:
            return self.revisions
        if revision not in self.revisions:
            raise MigrationError("incompatible_schema", "Database revision is unknown to this binary.")
        return self.revisions[self.revisions.index(revision) + 1 :]

    def verify(self, revision: str | None, fingerprint: str) -> None:
        if revision is None:
            allowed = [digest([])]
        else:
            self.pending(revision)
            allowed = self.fingerprints[revision]
        if fingerprint not in allowed:
            raise MigrationError("incompatible_schema", "Actual schema does not match the recorded revision.")


def sqlite_inventory(connection: Connection, *, exclude: frozenset[str] = frozenset()) -> list[list[Any]]:
    """Include tables, indexes, triggers, views and constraint-bearing SQL.

    This deliberately uses exact SQLite definitions. A semantically equivalent
    but unregistered schema requires review, never an optimistic stamp.
    """
    rows = connection.exec_driver_sql(
        "SELECT type, name, tbl_name, sql FROM sqlite_schema WHERE substr(name, 1, 7) != 'sqlite_' ORDER BY type, name"
    )
    return [list(row) for row in rows if row[1] not in exclude and row[2] not in exclude]
