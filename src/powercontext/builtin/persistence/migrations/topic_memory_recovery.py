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

"""Versioned offline conversion of Topic budgets and retained retry evidence.

The frozen resource owns this task's DDL. Other domain tables are untouched and
are covered by the database recovery point, not claimed as verified by this task.
"""

# Identifiers come only from frozen schema resources/constants; values are bound.
# ruff: noqa: S608

from __future__ import annotations

import copy
import hashlib
import json
import sqlite3
from collections.abc import Callable, Iterator, Mapping
from contextlib import nullcontext
from datetime import datetime
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy import Connection, create_engine, inspect, text
from sqlalchemy.engine import make_url

from powercontext.builtin.persistence.oceanbase import OceanBaseConfig
from powercontext.builtin.persistence.seekdb import SeekDBConfig
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.persistence.topic_memory_recovery_schema import (
    ATTEMPTS,
    BLOCKS,
    BUDGET,
    LEASES,
    OBJECTS,
    RESOURCE,
    recovery_initialization_prefixes,
    recovery_inventory,
    recovery_layout,
    recovery_matches,
    recovery_resource,
    recovery_shape,
    recovery_statements,
)

from .backup import BackupContext, BackupProvider, BackupRef, ForkBackupProvider, SQLiteBackupProvider
from .connections import BackendIdentity, MaintenanceConnections
from .locking import local_migration_lock
from .models import MigrationError, MigrationPlan, MigrationResult, digest
from .oceanbase import _read_record, _write_record

_BUDGET_CORE = (
    "scope_id",
    "binding_name",
    "source_after",
    "source_through",
    "attempt_id",
    "attempts",
    "requests",
    "tokens",
    "failure_code",
)
_CURRENT = ("started_at", "finished_at", "supervisor_group", "supervisor_generation", "holder_id", "last_error_code")
_SAME_ATTEMPT = " AND ".join(
    f"b.{name}=a.{name}" for name in ("scope_id", "binding_name", "source_after", "attempt_id")
)
_BLOCK_FIELDS = (
    "block_id",
    "attempt_id",
    "scope_id",
    "binding_name",
    "source_after",
    "target_source_through",
    "attempts",
    "requests",
    "tokens",
    "last_error_code",
    "blocked_at",
    "retried_at",
    "retry_request_generation",
)
_BACKUP_OBJECTS = (
    *OBJECTS,
    "pc_sources",
    "pc_source_journal_heads",
    "pc_source_cursors",
    "pc_topic_memory_processing_targets",
    "pc_artifact_processing_pending",
    "pc_artifact_processing_intents",
    "pc_artifact_processing_leases",
)


def _resource_checksum() -> str:
    return hashlib.sha256(RESOURCE.read_bytes()).hexdigest()


def _canonical(row: Mapping[Any, Any]) -> dict[str, Any]:
    return {
        name: datetime.fromisoformat(str(value)).isoformat(timespec="microseconds")
        if name.endswith("_at") and value is not None
        else value
        for name, value in row.items()
    }


def _row_digest(rows) -> str:
    result = hashlib.sha256()
    for row in rows:
        result.update(json.dumps(_canonical(row), sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode())
        result.update(b"\n")
    return result.hexdigest()


def _budgets(connection: Connection) -> str:
    if BUDGET not in inspect(connection).get_table_names():
        return _row_digest(())
    return _row_digest(
        connection.execute(
            text(f"SELECT {','.join(_BUDGET_CORE)} FROM {BUDGET} ORDER BY scope_id,binding_name,source_after")
        ).mappings()
    )


def _imported_blocks(connection: Connection) -> Iterator[dict[str, Any]]:
    if ATTEMPTS not in inspect(connection).get_table_names():
        return
    rows = connection.execute(
        text(
            f"SELECT a.*, b.source_through AS cumulative FROM {ATTEMPTS} a LEFT JOIN {BUDGET} b "
            f"ON {_SAME_ATTEMPT} WHERE a.blocked_at IS NOT NULL ORDER BY a.block_id"
        )
    ).mappings()
    for row in rows:
        yield {
            **{name: row[name] for name in _BLOCK_FIELDS},
            "source_through": None,
            "budget_source_through": row["cumulative"],
            "legacy_source_through": row["source_through"],
            "error_code": row["blocked_code"],
        }


def _blocks(connection: Connection) -> str:
    return _row_digest(connection.execute(text(f"SELECT * FROM {BLOCKS} ORDER BY block_id")).mappings())


def _validate_source(connection: Connection) -> None:
    inventory = recovery_inventory(connection, include_leases=True)
    if LEASES in inventory and not recovery_matches(
        {LEASES: inventory[LEASES]}, recovery_shape("leases_v0", connection.dialect.name), connection.dialect.name
    ):
        raise MigrationError(
            "incompatible_schema", "The processing lease format is not registered for Topic maintenance."
        )
    if ATTEMPTS not in inspect(connection).get_table_names():
        return
    invalid = connection.execute(
        text(
            f"SELECT 1 FROM {ATTEMPTS} WHERE "
            "(blocked_at IS NOT NULL AND (blocked_code IS NULL OR blocked_code = '')) OR "
            "(blocked_at IS NULL AND (blocked_code IS NOT NULL OR retried_at IS NOT NULL OR retry_request_generation IS NOT NULL)) OR "
            "(retried_at IS NULL AND retry_request_generation IS NOT NULL) OR "
            "(retried_at IS NOT NULL AND (retry_request_generation IS NULL OR retry_request_generation <= 0)) LIMIT 1"
        )
    ).first()
    if invalid:
        raise MigrationError("incompatible_evidence", "An old block has an invalid terminal or acceptance state.")


def _mysql_steps() -> list[str]:
    target = recovery_statements("budget_v1", "mysql")[0]
    fields = ("attempt_source_through", *_CURRENT)
    additions = [
        f"ALTER TABLE {BUDGET} ADD COLUMN {line.strip().rstrip(',')}"
        for field in fields
        for line in target.splitlines()
        if line.strip().startswith(field + " ")
    ]
    constraint = next(
        line.strip().rstrip(",")
        for line in target.splitlines()
        if "CONSTRAINT ck_pc_topic_budget_attempt_range " in line
    )
    return [*recovery_statements("blocks_v1", "mysql"), *additions, f"ALTER TABLE {BUDGET} ADD {constraint}"]


def _prefix(connection: Connection, source: str) -> int:
    """Only exact, ordered MySQL DDL prefixes are eligible for journaled recovery."""
    actual = recovery_inventory(connection)
    if source == "empty":
        for number, expected in enumerate(recovery_initialization_prefixes("mysql")):
            if recovery_matches(actual, expected, "mysql"):
                return number
        raise MigrationError("recovery_required", "Empty initialization is not a registered DDL prefix.")
    old = copy.deepcopy(recovery_shape("budget_v0", "mysql"))
    if source == "attempts-v0":
        old.update(recovery_shape("attempts_v0", "mysql"))
    if recovery_matches(actual, old, "mysql"):
        return 0
    old.update(recovery_shape("blocks_v1", "mysql"))
    if recovery_matches(actual, old, "mysql"):
        return 1
    target = recovery_shape("budget_v1", "mysql")[BUDGET]
    for number, name in enumerate(("attempt_source_through", *_CURRENT), 2):
        old[BUDGET]["columns"][name] = target["columns"][name]
        if recovery_matches(actual, old, "mysql"):
            return number
    old[BUDGET]["checks"] = target["checks"]
    complete = {name: shape for name, shape in old.items() if name != ATTEMPTS}
    if any(recovery_matches(actual, expected, "mysql") for expected in (old, complete)):
        return len(_mysql_steps())
    raise MigrationError("recovery_required", "The partial Topic schema is not a registered DDL prefix.")


def _copy(connection: Connection) -> None:
    if ATTEMPTS not in inspect(connection).get_table_names():
        return
    if connection.execute(text(f"SELECT 1 FROM {BLOCKS} LIMIT 1")).first():
        if _blocks(connection) != _row_digest(_imported_blocks(connection)):
            raise MigrationError("evidence_conflict", "Converted blocks differ from their retained source.")
    else:
        connection.execute(text(recovery_resource()["conversion"]["copy_blocks"]))
    # Only live budgets need execution metadata; historical successful attempts
    # are never scanned into a sequence of no-op budget updates.
    connection.execute(text(recovery_resource()["conversion"]["copy_current"]))


class TopicMemoryRecoveryMigrationRunner:
    """A domain bundle using shared maintenance locks, backups and CLI declarations."""

    def __init__(
        self,
        config: SQLiteConfig | SeekDBConfig | OceanBaseConfig,
        *,
        evidence_directory: Path | None = None,
        lock_coordination: Literal["single-host"] | None = None,
        acknowledge_previous_run: str | None = None,
    ) -> None:
        self.config = config
        self.revision = recovery_resource()["revision"]
        self.checksum = _resource_checksum()
        self.configuration = digest(config.model_dump(mode="json", exclude={"url", "echo"}))
        self.database: Path | None = None
        self.connections: MaintenanceConnections | None = None
        if isinstance(config, SQLiteConfig):
            url = make_url(config.url)
            if not config.is_persistent or url.query or url.host or url.username or url.password or not url.database:
                raise MigrationError("unsupported_target", "Topic migration requires a regular persistent SQLite file.")
            self.database = Path(url.database).absolute().resolve()
            default = self.database.with_name(self.database.name + ".pc-migration-state")
        else:
            default = (
                config.path.expanduser().resolve().with_suffix(".pc-migration-state")
                if isinstance(config, SeekDBConfig)
                else None
            )
            if isinstance(config, OceanBaseConfig) and (
                evidence_directory is None or lock_coordination != "single-host"
            ):
                raise MigrationError(
                    "coordination_required", "OceanBase requires one fixed evidence host and single-host coordination."
                )
        directory = evidence_directory or default
        if directory is None:
            raise MigrationError("evidence_required", "Choose persistent migration evidence storage.")
        self.directory = directory.expanduser().resolve()
        if not isinstance(config, SQLiteConfig):
            self.connections = MaintenanceConnections(
                config,
                evidence_directory=self.directory,
                lock_coordination=lock_coordination,
                acknowledge_previous_run=acknowledge_previous_run,
            )

    def _run(self, operation, *, writable: bool = False):
        if self.connections is not None:
            return self.connections.run(operation, writable=writable)
        database = self.database
        if database is None:
            raise MigrationError("target_unavailable", "The SQLite maintenance target is unavailable.")
        identity = BackendIdentity("sqlite", digest({"kind": "sqlite", "path": str(database)}), database.name)
        if not database.exists() and not writable:
            return operation(None, identity, lambda: None)
        with local_migration_lock(database) if writable else nullcontext():
            uri = database.as_uri() + ("?mode=rwc" if writable else "?mode=ro")
            engine = create_engine("sqlite://", creator=lambda: sqlite3.connect(uri, uri=True))
            try:
                with engine.connect() as connection:
                    return operation(connection, identity, lambda: None)
            finally:
                engine.dispose()

    def _journal(self, identity: BackendIdentity) -> Path:
        return self.directory / identity.database_id / "topic-memory-recovery.json"

    def _evidence(self, identity: BackendIdentity) -> dict[str, Any] | None:
        evidence = _read_record(self._journal(identity))
        if evidence is None:
            return None
        required = {
            "revision",
            "checksum",
            "configuration",
            "database_id",
            "source",
            "state",
            "backup_policy",
            "window",
            "budgets",
            "blocks",
            "source_schema",
            "target_schema",
        }
        if (
            not required <= evidence.keys()
            or any(not isinstance(evidence[name], str) for name in required)
            or evidence["database_id"] != identity.database_id
            or evidence["source"] not in {"empty", "atomic-maintenance-sqlite-v1", "budget-v0", "attempts-v0"}
            or evidence["state"] not in {"preparing", "active", "complete"}
            or evidence["backup_policy"] not in {"auto", "manual", "skip"}
            or (
                evidence["state"] != "preparing"
                and (
                    not isinstance(evidence.get("backup_state"), str)
                    or evidence["backup_state"] not in {"not_required", "completed", "user_confirmed", "skipped"}
                )
            )
        ):
            raise MigrationError("recovery_required", "The Topic recovery journal has an invalid identity or state.")
        return evidence

    def _provider(self, connection: Connection, identity: BackendIdentity) -> BackupProvider:
        if self.database is not None:
            return SQLiteBackupProvider(self.database)
        return ForkBackupProvider(
            connection,
            database_name=identity.database_name,
            expected_product="seekdb" if identity.product == "seekdb" else "oceanbase_ai",
            directory=self.directory,
        )

    def _inspect(self, connection: Connection | None, identity: BackendIdentity, **options) -> MigrationPlan:
        if _resource_checksum() != self.checksum:
            raise MigrationError("plan_changed", "Topic recovery migration resources changed after planning.")
        if connection is not None and inspect(connection).has_table("pc_schema_revision"):
            raise MigrationError(
                "unsupported_target",
                "Managed Server storage requires the full Server bundle or registered maintenance.",
            )
        evidence = self._evidence(identity)
        active = evidence is not None and evidence.get("state") != "complete"
        if evidence and (
            evidence.get("revision") != self.revision
            or evidence.get("checksum") != self.checksum
            or evidence.get("configuration") != self.configuration
        ):
            raise MigrationError(
                "recovery_required", "Keep the original Topic migration resources, configuration and evidence."
            )
        try:
            layout = recovery_layout(connection) if connection is not None else "empty"
        except MigrationError:
            if (
                not active
                or connection is None
                or connection.dialect.name != "mysql"
                or evidence["state"] != "active"
                or "ddl_step" not in evidence
            ):
                raise
            _prefix(connection, evidence["source"])
            layout = "partial"
        if connection is not None:
            _validate_source(connection)
        schema = recovery_inventory(connection, include_leases=True) if connection is not None else {}
        costs = _budgets(connection) if connection is not None else _row_digest(())
        source_blocks = _row_digest(_imported_blocks(connection)) if connection is not None else _row_digest(())
        state = (
            "recovery_required"
            if active
            else "ready"
            if layout == "ready"
            else "uninitialized"
            if layout in {"empty", "atomic-maintenance-sqlite-v1"}
            else "migration_required"
        )
        policy = options.get("backup_policy", "auto")
        if policy not in {"auto", "manual", "skip"}:
            raise MigrationError("invalid_backup_policy", "Choose auto, manual or skip.")
        available = self.database is not None
        reasons = (
            ()
            if available
            else ("Select a manual backup or explicitly skip it; native fork acceptance is unavailable.",)
        )
        if connection is not None and policy == "auto" and layout not in {"empty", "ready", "partial"} and not active:
            capability = self._provider(connection, identity).capabilities(self._context(identity, layout, "preview"))
            available, reasons = capability.available, capability.reasons
        fields = {
            "database_id": identity.database_id,
            "source_revision": self.revision
            if layout == "ready"
            else None
            if layout == "empty"
            else evidence["source"]
            if layout == "partial" and evidence is not None
            else layout,
            "target_revision": self.revision,
            "schema_fingerprint": digest(schema),
            "bundle_checksum": self.checksum,
            "configuration_digest": self.configuration,
            "state": state,
            "revisions": () if state == "ready" else (self.revision,),
            "backup_policy": policy,
            "backup_available": available,
            "backup_reason": reasons,
            "affected_objects": (*OBJECTS, LEASES),
            "retained_objects": (),
            "service": options.get("service"),
            "shared_database": options.get("shared_database", False),
            "coordination": self.connections.coordination(identity) if self.connections is not None else None,
        }
        return MigrationPlan.model_validate({
            "plan_id": digest({**fields, "costs": costs, "blocks": source_blocks}),
            **fields,
        })

    def plan(self, **options) -> MigrationPlan:
        return self._run(lambda connection, identity, _verify: self._inspect(connection, identity, **options))

    def verify(self) -> MigrationResult:
        plan = self.plan()
        if plan.state != "ready":
            raise MigrationError(
                plan.state, "Apply the selected Topic recovery bundle before reopening business access."
            )
        return MigrationResult(revision=self.revision, changed=False)

    def _context(self, identity: BackendIdentity, source: str, window: str) -> BackupContext:
        return BackupContext(
            database_id=identity.database_id,
            source_revision=None if source == "empty" else source,
            bundle_checksum=self.checksum,
            maintenance_window_id=window,
            objects=_BACKUP_OBJECTS,
            writers_stopped=True,
        )

    def apply(
        self,
        *,
        plan_id: str | None = None,
        accepted: bool = False,
        maintenance_confirmed: bool = False,
        backup_policy: str = "auto",
        backup_confirmed: bool = False,
        accept_no_backup: bool = False,
        backup_ref: str | None = None,
        **options,
    ) -> MigrationResult:
        initial = self.plan(backup_policy=backup_policy, **options)
        if initial.state == "ready":
            return self.verify()
        self._require_consent(
            initial,
            plan_id,
            accepted,
            maintenance_confirmed,
            backup_policy,
            backup_confirmed,
            accept_no_backup,
            options,
        )

        def execute(
            connection: Connection | None, identity: BackendIdentity, verify: Callable[[], None]
        ) -> MigrationResult:
            if connection is None:
                raise MigrationError("target_unavailable", "A writable maintenance connection is required.")
            current = self._inspect(connection, identity, backup_policy=backup_policy, **options)
            if current.plan_id != initial.plan_id:
                raise MigrationError("plan_changed", "Schema or evidence changed while acquiring the migration lock.")
            path = self._journal(identity)
            evidence = self._prepare_evidence(connection, identity, backup_policy, backup_ref)
            verify()
            if connection.dialect.name == "sqlite":
                connection.exec_driver_sql("BEGIN IMMEDIATE")
            if evidence["source"] == "atomic-maintenance-sqlite-v1" and recovery_layout(connection) not in {
                "atomic-maintenance-sqlite-v1",
                "ready",
            }:
                raise MigrationError("plan_changed", "The Atomic maintenance initialization baseline changed.")
            if _budgets(connection) != evidence["budgets"]:
                raise MigrationError("evidence_conflict", "Consumed Topic allowance changed during maintenance.")
            if (
                ATTEMPTS in inspect(connection).get_table_names()
                and _row_digest(_imported_blocks(connection)) != evidence["blocks"]
            ):
                raise MigrationError("evidence_conflict", "Retained retry evidence changed during maintenance.")
            self._expand(connection, evidence, path, verify)
            if connection.dialect.name == "mysql":
                connection.commit()
                connection.exec_driver_sql("START TRANSACTION")
            return self._finish(connection, evidence, path, verify)

        return self._run(execute, writable=True)

    def _prepare_evidence(self, connection, identity, backup_policy, backup_ref):
        path = self._journal(identity)
        evidence = self._evidence(identity)
        if evidence is None or evidence.get("state") == "complete":
            source = recovery_layout(connection)
            evidence = {
                "revision": self.revision,
                "checksum": self.checksum,
                "configuration": self.configuration,
                "database_id": identity.database_id,
                "source": source,
                "state": "preparing",
                "backup_policy": backup_policy,
                "window": uuid4().hex,
                "budgets": _budgets(connection),
                "blocks": _row_digest(_imported_blocks(connection)),
                "source_schema": digest(recovery_inventory(connection, include_leases=True)),
                "target_schema": digest({
                    **recovery_shape("budget_v1", connection.dialect.name),
                    **recovery_shape("blocks_v1", connection.dialect.name),
                }),
            }
            path.parent.mkdir(parents=True, exist_ok=True)
            _write_record(path, evidence)
        elif evidence["backup_policy"] != backup_policy:
            raise MigrationError("recovery_required", "Resume with the original backup policy and recovery point.")
        provider = self._provider(connection, identity)
        connection.commit()
        if evidence["state"] == "preparing":
            if evidence["source"] == "empty":
                evidence.update(backup_state="not_required", backup=None)
            elif backup_policy == "auto":
                ref = provider.create_backup(self._context(identity, evidence["source"], evidence["window"]))
                if provider.inspect_backup(ref).state != "completed":
                    raise MigrationError("backup_failed", "The recovery point did not pass provider inspection.")
                evidence.update(backup_state="completed", backup=ref.model_dump(mode="json"))
            else:
                evidence.update(
                    backup_state="user_confirmed" if backup_policy == "manual" else "skipped",
                    backup=None,
                    manual_ref=backup_ref,
                )
            evidence["state"] = "active"
            _write_record(path, evidence)
        elif evidence.get("backup_state") == "completed":
            try:
                ref = BackupRef.model_validate(evidence.get("backup"))
            except ValidationError as error:
                raise MigrationError("backup_required", "The retained recovery point has an invalid format.") from error
            expected = self._context(identity, evidence["source"], evidence["window"])
            if (
                any(
                    getattr(ref, name) != getattr(expected, name)
                    for name in ("database_id", "source_revision", "bundle_checksum", "maintenance_window_id")
                )
                or provider.inspect_backup(ref).state != "completed"
            ):
                raise MigrationError("backup_required", "Keep the original verified recovery point.")
        return evidence

    def _expand(self, connection, evidence, path, verify):
        if evidence["source"] in {"empty", "atomic-maintenance-sqlite-v1"}:
            actual = recovery_inventory(connection)
            for shape in recovery_resource()["initialization"]:
                name = next(iter(recovery_shape(shape, connection.dialect.name)))
                if name not in actual:
                    verify()
                    evidence["ddl_step"] = shape
                    _write_record(path, evidence)
                    for statement in recovery_statements(shape, connection.dialect.name):
                        connection.exec_driver_sql(statement)
                    if connection.dialect.name == "mysql":
                        connection.commit()
        elif connection.dialect.name == "sqlite" and recovery_layout(connection) != "ready":
            ddl = recovery_statements("budget_v1", "sqlite")[0].replace(BUDGET, BUDGET + "_upgrade", 1)
            connection.exec_driver_sql(ddl)
            columns = ",".join(_BUDGET_CORE)
            connection.exec_driver_sql(f"INSERT INTO {BUDGET}_upgrade ({columns}) SELECT {columns} FROM {BUDGET}")
            connection.exec_driver_sql(f"DROP TABLE {BUDGET}")
            connection.exec_driver_sql(f"ALTER TABLE {BUDGET}_upgrade RENAME TO {BUDGET}")
            for statement in recovery_statements("blocks_v1", "sqlite"):
                connection.exec_driver_sql(statement)
        elif connection.dialect.name == "mysql":
            start = _prefix(connection, evidence["source"])
            for number, statement in enumerate(_mysql_steps()[start:], start):
                verify()
                evidence["ddl_step"] = number
                _write_record(path, evidence)
                connection.exec_driver_sql(statement)
                connection.commit()

    def _finish(self, connection, evidence, path, verify) -> MigrationResult:
        if ATTEMPTS in inspect(connection).get_table_names():
            if _row_digest(_imported_blocks(connection)) != evidence["blocks"]:
                raise MigrationError("evidence_conflict", "Retained retry evidence changed during maintenance.")
            _copy(connection)
        if LEASES in inspect(connection).get_table_names():
            connection.execute(
                text(recovery_resource()["conversion"]["revoke_old_fences_" + connection.dialect.name]),
                {"migration_holder": evidence["window"]},
            )
        if _budgets(connection) != evidence["budgets"] or _blocks(connection) != evidence["blocks"]:
            raise MigrationError("verification_failed", "Converted Topic evidence or consumed allowance differs.")
        if connection.dialect.name == "mysql":
            connection.commit()
            verify()
            evidence["ddl_step"] = "remove-attempts"
            _write_record(path, evidence)
        if ATTEMPTS in inspect(connection).get_table_names():
            connection.exec_driver_sql(f"DROP TABLE {ATTEMPTS}")
        if recovery_layout(connection) != "ready":
            raise MigrationError("verification_failed", "The frozen Topic recovery target was not reached.")
        verify()
        connection.commit()
        evidence["state"] = "complete"
        _write_record(path, evidence)
        ref = evidence.get("backup")
        return MigrationResult(
            revision=self.revision,
            changed=True,
            backup_state=evidence["backup_state"],
            backup_ref=ref["location"] if ref else evidence.get("manual_ref"),
        )

    @staticmethod
    def _require_consent(
        plan, plan_id, accepted, maintenance_confirmed, backup_policy, backup_confirmed, accept_no_backup, options
    ):
        if not accepted:
            raise MigrationError("confirmation_required", "Accept the exact Topic migration plan.")
        if plan_id != plan.plan_id:
            raise MigrationError("plan_changed", "The reviewed Topic evidence or maintenance options changed.")
        if not maintenance_confirmed:
            raise MigrationError("maintenance_required", "Stop every writer, including Workers, before migration.")
        if plan.state != "uninitialized" and (
            (backup_policy == "manual" and not backup_confirmed) or (backup_policy == "skip" and not accept_no_backup)
        ):
            raise MigrationError("confirmation_required", "Declare the selected backup policy explicitly.")
        if options.get("service") is not None:
            raise MigrationError("service_unsupported", "Use deployment tooling to stop and restart all writers.")
