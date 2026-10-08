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

"""Dream's frozen transformation for an RFC 1771 Alembic revision.

The framework must recognize the complete baseline, stop writers, hold its lock,
apply the selected backup policy and own the supplied connection/transaction.
This module never opens connections, commits, stamps, locks, or resumes DDL.
It is not a migration runner or a full-database readiness check.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from sqlalchemy import Connection, inspect, text

RESOURCE_DIRECTORY = Path(__file__).parent


class DreamUpgradeError(RuntimeError):
    """A domain precondition or postcondition failed; framework recovery applies."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


def _schema() -> dict[str, Any]:
    return json.loads((RESOURCE_DIRECTORY / "schema.json").read_text(encoding="utf-8"))


def validate_dream_tasks(connection: Connection) -> None:
    """Legacy operations keep their original payloads; unknown formats block DDL.

    Other queues and lease protocols belong to the full framework's task inventory.
    This recognizer intentionally accepts only the released pre-expansion operations.
    """
    names = set(inspect(connection).get_table_names())
    if "pc_dream_runs" not in names:
        raise DreamUpgradeError("unknown_baseline")
    validator = Draft202012Validator(_schema()["task_payload_schema"], format_checker=FormatChecker())
    versions = "pc_candidate_versions" if "pc_candidate_versions" in names else "pc_artifact_candidate_versions"
    rows = connection.exec_driver_sql(
        "SELECT scope_id, run_id, principal_key, idempotency_key, operation, status, generation, "
        "request_generation, payload FROM pc_dream_runs"
    ).mappings()
    for row in rows:
        try:
            payload = json.loads(row["payload"])
            run, request = payload["run"], payload["request"]
            valid = (
                validator.is_valid(payload)
                and row["operation"] in {"refine_experience", "derive_skill"}
                and row["status"] in {"queued", "running", "succeeded", "failed"}
                and run["operation"] == request["operation"] == row["operation"]
                and run["status"] == row["status"]
                and run["scope_id"] == row["scope_id"]
                and run["run_id"] == row["run_id"]
                and payload.get("generation", 0) == row["generation"]
                and payload.get("request_generation", 0) == row["request_generation"]
                and isinstance(payload["principal_id"], str)
                and bool(payload["principal_id"])
                and hashlib.sha256(payload["principal_id"].encode()).hexdigest() == row["principal_key"]
                and request["idempotency_key"] == row["idempotency_key"]
                and _valid_selection(request)
            )
        except (ValueError, TypeError, KeyError):
            raise DreamUpgradeError("unsupported_task_format") from None
        if not valid:
            raise DreamUpgradeError("unsupported_task_format")
        candidate = run.get("candidate")
        if (
            candidate is not None
            and connection.execute(
                text(
                    f"SELECT 1 FROM {versions} WHERE scope_id=:scope AND candidate_id=:candidate AND version=:version"  # noqa: S608
                ),
                {"scope": row["scope_id"], "candidate": candidate["candidate_id"], "version": candidate["version"]},
            ).first()
            is None
        ):
            raise DreamUpgradeError("dream_candidate_reference_invalid")


def _valid_selection(request: dict[str, Any]) -> bool:
    """Frozen semantic rules not expressible by the historical model's JSON schema."""
    artifacts = request.get("artifacts", [])
    citations = request.get("memory_citations", [])
    sources = request.get("sources", [])
    target = request.get("target")
    selected = len(artifacts) + len(citations)
    key = request["idempotency_key"]
    return (
        key == key.strip()
        and 1 <= selected <= 20
        and selected + len(sources) <= 32
        and all(ref["family"] == "experience" for ref in artifacts)
        and all(
            ref["memory_ref"]["family"] == "memory"
            and 1 <= len(ref["entry_id"]) <= 128
            and 1 <= len(ref["entry_version_id"]) <= 128
            for ref in citations
        )
        and (target is None or target in artifacts)
        and (request["operation"] != "derive_skill" or (not citations and target is None and bool(artifacts)))
    )


def upgrade_dream_storage(connection: Connection) -> None:
    """Transform the recognized legacy shape on the framework's pinned connection.

    SQLite needs foreign_keys disabled *before* the framework starts its transaction,
    then validates all live foreign keys before revision advancement. Remote DDL is
    not atomic; a failure is left to framework recovery, never retried here.
    """
    schema = _schema()
    dialect = connection.dialect.name
    if dialect not in {"sqlite", "mysql"}:
        raise DreamUpgradeError("unsupported_backend")
    if not connection.in_transaction():
        raise DreamUpgradeError("maintenance_connection_required")
    if dialect == "sqlite" and connection.exec_driver_sql("PRAGMA foreign_keys").scalar():
        raise DreamUpgradeError("maintenance_connection_required")
    _validate_source(connection, schema)
    validate_dream_tasks(connection)

    for entry in schema["tables"].values():
        # Retained copies have no live FKs: deleting a Scope or Artifact must
        # not cascade into the recovery snapshot. The frozen PK/checks remain.
        connection.exec_driver_sql(entry["retained"][dialect])
        fields = ", ".join(entry["source_columns"])
        connection.exec_driver_sql(
            f"INSERT INTO {entry['retained_name']} ({fields}) SELECT {fields} FROM {entry['source_name']}"  # noqa: S608
        )
    for entry in schema["tables"].values():
        connection.exec_driver_sql(f"ALTER TABLE {entry['source_name']} RENAME TO {entry['target_name']}")
    if dialect == "sqlite":
        _sqlite_candidates(connection, schema)
    else:
        _mysql_candidates(connection, schema)
    for table, addition in schema["add_columns"].items():
        connection.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {addition['ddl'][dialect]}")
    connection.exec_driver_sql(schema["indexes"][dialect])
    verify_dream_storage(connection)


def _validate_source(connection: Connection, schema: dict[str, Any]) -> None:
    names = set(inspect(connection).get_table_names())
    for entry in schema["tables"].values():
        if entry["source_name"] not in names or {entry["target_name"], entry["retained_name"]} & names:
            raise DreamUpgradeError("unknown_baseline")
        actual = [column["name"] for column in inspect(connection).get_columns(entry["source_name"])]
        if actual != entry["source_columns"]:
            raise DreamUpgradeError("unknown_baseline")
    if names & {"pc_catalog_change_candidate_heads", "pc_catalog_change_candidate_versions"}:
        raise DreamUpgradeError("unknown_baseline")
    for table, addition in schema["add_columns"].items():
        if table not in names or addition["column"] in {c["name"] for c in inspect(connection).get_columns(table)}:
            raise DreamUpgradeError("unknown_baseline")
    if "pc_profile_policies" not in names:
        raise DreamUpgradeError("unknown_baseline")


def _sqlite_candidates(connection: Connection, schema: dict[str, Any]) -> None:
    for entry in schema["tables"].values():
        name = entry["target_name"]
        temporary = "pc_dream_upgrade_" + name.removeprefix("pc_")
        connection.exec_driver_sql(
            entry["target"]["sqlite"].replace("CREATE TABLE " + name, "CREATE TABLE " + temporary, 1)
        )
        fields = ", ".join(entry["source_columns"])
        connection.exec_driver_sql(f"INSERT INTO {temporary} ({fields}) SELECT {fields} FROM {name}")  # noqa: S608
    # The earlier rename rewrote Profile's FK. Rebuilding without another live
    # table rename preserves that reference to the final head table.
    for entry in reversed(list(schema["tables"].values())):
        connection.exec_driver_sql(f"DROP TABLE {entry['target_name']}")
    for entry in schema["tables"].values():
        name = entry["target_name"]
        temporary = "pc_dream_upgrade_" + name.removeprefix("pc_")
        connection.exec_driver_sql(f"ALTER TABLE {temporary} RENAME TO {name}")


def _mysql_candidates(connection: Connection, schema: dict[str, Any]) -> None:
    heads = schema["tables"]["pc_candidate_heads"]
    for field in ("candidate_kind", "result_payload"):
        declaration = next(
            line.strip().rstrip(",")
            for line in heads["target"]["mysql"].splitlines()
            if line.strip().startswith(field + " ")
        )
        connection.exec_driver_sql(f"ALTER TABLE pc_candidate_heads ADD COLUMN {declaration}")
    quote = connection.dialect.identifier_preparer.quote
    for entry in schema["tables"].values():
        name = entry["target_name"]
        for check in inspect(connection).get_check_constraints(name):
            constraint_name = check["name"]
            if constraint_name is None:
                raise DreamUpgradeError("unknown_baseline")
            connection.exec_driver_sql(f"ALTER TABLE {name} DROP CHECK {quote(constraint_name)}")
        for line in entry["target"]["mysql"].splitlines():
            if line.strip().startswith("CONSTRAINT "):
                connection.exec_driver_sql(f"ALTER TABLE {name} ADD {line.strip().rstrip(',')}")
        connection.exec_driver_sql(
            f"ALTER TABLE {name} ADD CONSTRAINT fk_{name}_scope FOREIGN KEY (scope_id) "
            "REFERENCES pc_scopes (scope_id) ON DELETE CASCADE"
        )
    for key in inspect(connection).get_foreign_keys("pc_candidate_heads"):
        if key["constrained_columns"] == ["scope_id", "candidate_id", "version"]:
            constraint_name = key["name"]
            if constraint_name is None:
                raise DreamUpgradeError("unknown_baseline")
            connection.exec_driver_sql(f"ALTER TABLE pc_candidate_heads DROP FOREIGN KEY {quote(constraint_name)}")
    connection.exec_driver_sql(
        "ALTER TABLE pc_candidate_heads ADD CONSTRAINT fk_pc_candidate_head_version "
        "FOREIGN KEY (scope_id, candidate_id, version) REFERENCES pc_candidate_versions "
        "(scope_id, candidate_id, version) ON DELETE CASCADE"
    )


def verify_dream_storage(connection: Connection) -> None:
    """Check copied values and domain links; not a declaration of Server readiness."""
    schema = _schema()
    for entry in schema["tables"].values():
        fields = ", ".join(entry["source_columns"])
        order = "scope_id, candidate_id" + (", version" if entry["target_name"].endswith("versions") else "")
        old = connection.exec_driver_sql(f"SELECT {fields} FROM {entry['retained_name']} ORDER BY {order}")  # noqa: S608
        new = connection.exec_driver_sql(f"SELECT {fields} FROM {entry['target_name']} ORDER BY {order}")  # noqa: S608
        while True:
            left, right = old.fetchmany(256), new.fetchmany(256)
            if left != right:
                raise DreamUpgradeError("candidate_copy_mismatch")
            if not left:
                break
    invalid = connection.exec_driver_sql(
        "SELECT 1 FROM pc_candidate_heads h LEFT JOIN pc_candidate_versions v ON "
        "h.scope_id=v.scope_id AND h.candidate_id=v.candidate_id AND h.version=v.version "
        "WHERE v.candidate_id IS NULL OR h.candidate_kind <> 'artifact' OR h.result_payload IS NOT NULL LIMIT 1"
    ).first()
    if invalid:
        raise DreamUpgradeError("candidate_verification_failed")
    invalid = connection.exec_driver_sql(
        "SELECT 1 FROM pc_profile_policies p LEFT JOIN pc_candidate_heads h ON "
        "p.scope_id=h.scope_id AND p.pending_candidate_id=h.candidate_id "
        "WHERE p.pending_candidate_id IS NOT NULL AND h.candidate_id IS NULL LIMIT 1"
    ).first()
    if invalid:
        raise DreamUpgradeError("profile_candidate_reference_invalid")
    if connection.exec_driver_sql(
        "SELECT 1 FROM pc_artifact_processing_intents WHERE consecutive_dream_attempts <> 0 LIMIT 1"
    ).first():
        raise DreamUpgradeError("processing_state_invalid")
    if connection.dialect.name == "sqlite" and connection.exec_driver_sql("PRAGMA foreign_key_check").first():
        raise DreamUpgradeError("foreign_key_verification_failed")
    validate_dream_tasks(connection)
