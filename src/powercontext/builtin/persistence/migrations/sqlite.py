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

"""Explicit offline SQLite migrations with atomic steps and durable recovery.

This executor is for an explicitly supplied bundle. It does not establish that
the rest of the PowerContext service or its projections are ready.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import threading
from collections.abc import Iterator, Mapping
from contextlib import closing, contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from alembic import command
from sqlalchemy import Connection, create_engine, text
from sqlalchemy.exc import OperationalError

from .bundle import MigrationBundle, sqlite_inventory
from .locking import local_migration_lock
from .models import MigrationError, MigrationPlan, MigrationResult, digest

_CONTROL_DDL = {
    "pc_schema_revision": (
        "CREATE TABLE pc_schema_revision (version_num VARCHAR(32) NOT NULL, "
        "CONSTRAINT pc_schema_revision_pkc PRIMARY KEY (version_num))"
    ),
    "pc_migration_runs": (
        "CREATE TABLE pc_migration_runs (run_id TEXT PRIMARY KEY NOT NULL, plan_json TEXT NOT NULL, "
        "backup_path TEXT NOT NULL, backup_checksum TEXT NOT NULL, "
        "state TEXT NOT NULL CHECK (state IN ('running', 'complete')), started_at TEXT NOT NULL)"
    ),
    "pc_migration_steps": (
        "CREATE TABLE pc_migration_steps (run_id TEXT NOT NULL, revision TEXT NOT NULL, checksum TEXT NOT NULL, "
        "PRIMARY KEY (run_id, revision), FOREIGN KEY (run_id) REFERENCES pc_migration_runs(run_id))"
    ),
}
_CONTROL_NAMES = frozenset(_CONTROL_DDL)
# Alembic's environment/op proxies are process-global, even for different DBs.
_ALEMBIC_CONTEXT_LOCK = threading.RLock()


class SQLiteMigrationRunner:
    """A synchronous maintenance executor. Call outside the business event loop.

    File locks serialize cooperating migrators; BEGIN IMMEDIATE detects a current
    SQLite writer. Neither proves that an idle old process has stopped, so the
    operator's maintenance confirmation is required for every changing run.
    """

    def __init__(
        self, database: Path, bundle: MigrationBundle, *, configuration: Mapping[str, Any] | None = None
    ) -> None:
        self.database = database.expanduser().resolve()
        self.bundle = bundle
        self.configuration_digest = digest(configuration or {})

    def plan(self) -> MigrationPlan:
        """Inspect without creating the database, its parent, locks or version rows."""
        if MigrationBundle(self.bundle.directory).checksum != self.bundle.checksum:
            raise MigrationError("plan_changed", "Migration resources changed after the bundle was loaded.")
        if not self.database.exists():
            return self._plan(None, digest([]), initialized=False)
        self._check_file()
        with self._connect(read_only=True) as connection:
            return self._inspect(connection)

    def verify(self) -> MigrationResult:
        plan = self.plan()
        if plan.state != "ready":
            raise MigrationError(plan.state, "The bundle is not fully migrated; inspect its plan before applying.")
        return MigrationResult(revision=self.bundle.head, changed=False)

    def apply(
        self,
        *,
        plan_id: str | None = None,
        maintenance_confirmed: bool = False,
        accepted: bool = False,
        resume: str | None = None,
    ) -> MigrationResult:
        """Require an accepted plan; resume keeps its original plan and backup."""
        initial = self.plan()
        if initial.state == "ready" and resume is None:
            return MigrationResult(revision=self.bundle.head, changed=False)
        if not accepted or plan_id is None:
            raise MigrationError("confirmation_required", "Accept the displayed plan ID explicitly.")
        if not maintenance_confirmed:
            raise MigrationError("maintenance_required", "Stop all application, SDK and background writers first.")
        if initial.state == "recovery_required" and resume != initial.resume_run_id:
            raise MigrationError("recovery_required", "Resume the recorded run; do not start a replacement run.")
        if resume is not None and resume != initial.resume_run_id:
            raise MigrationError("recovery_required", "The requested run is not the pending run.")
        if initial.plan_id != plan_id:
            raise MigrationError("plan_changed", "The reviewed plan no longer matches the database or bundle.")
        with local_migration_lock(self.database):
            # Revalidate before opening in write mode, including a missing target.
            if self.plan() != initial:
                raise MigrationError("plan_changed", "State changed while acquiring the migration lock.")
            with self._connect(read_only=False) as connection:
                connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
                connection.commit()
                self._begin(connection)
                locked = self._inspect(connection)
                if locked != initial:
                    raise MigrationError("plan_changed", "State changed before acquiring the SQLite write transaction.")
                if resume:
                    run_id, backup = self._resume(connection, resume, initial)
                    connection.commit()
                else:
                    # A separate read connection can back up the committed WAL
                    # while this connection prevents writers with BEGIN IMMEDIATE.
                    backup, checksum = self._backup()
                    run_id = uuid4().hex
                    self._bootstrap(connection, initial, run_id, backup, checksum)
                    connection.commit()
                self._upgrade(connection, run_id)
                self._begin(connection)
                self._verify_schema(connection, self.bundle.head)
                connection.execute(
                    text("UPDATE pc_migration_runs SET state='complete' WHERE run_id=:run"), {"run": run_id}
                )
                connection.commit()
        return MigrationResult(revision=self.bundle.head, changed=True, run_id=run_id, backup_ref=str(backup))

    def _check_file(self) -> None:
        if not self.database.is_file() or self.database.stat().st_nlink != 1:
            raise MigrationError("unsupported_target", "Use a regular database file without hard-link aliases.")

    @contextmanager
    def _connect(self, *, read_only: bool) -> Iterator[Connection]:
        mode = "ro" if read_only else "rwc"
        uri = self.database.as_uri() + "?mode=" + mode
        engine = create_engine(
            "sqlite://", creator=lambda: sqlite3.connect(uri, uri=True, isolation_level=None, timeout=0)
        )
        try:
            with engine.connect() as connection:
                if read_only:
                    connection.exec_driver_sql("PRAGMA query_only=ON")
                    connection.exec_driver_sql("BEGIN")
                yield connection
        finally:
            engine.dispose()

    def _inspect(self, connection: Connection) -> MigrationPlan:
        inventory = sqlite_inventory(connection)
        controls = [row for row in inventory if row[1] in _CONTROL_NAMES or row[2] in _CONTROL_NAMES]
        initialized = bool(controls)
        if initialized:
            expected = sorted([["table", name, name, ddl] for name, ddl in _CONTROL_DDL.items()])
            if sorted(controls) != expected:
                raise MigrationError(
                    "recovery_required", "Migration bookkeeping is incomplete or has an unknown shape."
                )
        fingerprint = digest(sqlite_inventory(connection, exclude=_CONTROL_NAMES))
        if not initialized:
            if not inventory:
                return self._plan(None, fingerprint, initialized=False)
            revision = self.bundle.baselines.get(fingerprint)
            if revision is None:
                raise MigrationError("unknown_baseline", "Schema is not a recognized baseline; no changes were made.")
            self._verify_integrity(connection)
            return self._plan(revision, fingerprint, initialized=False)
        revisions = list(connection.exec_driver_sql("SELECT version_num FROM pc_schema_revision").scalars())
        if len(revisions) > 1:
            raise MigrationError("incompatible_schema", "Multiple database revision heads are unsupported.")
        revision = revisions[0] if revisions else None
        self._verify_schema(connection, revision)
        self._verify_history(connection, revision)
        recovery = self._pending_run(connection, revision)
        if recovery is not None:
            return recovery
        return self._plan(revision, fingerprint, initialized=True)

    def _verify_history(self, connection: Connection, revision: str | None) -> None:
        receipts = list(connection.exec_driver_sql("SELECT revision, checksum FROM pc_migration_steps"))
        expected = self.bundle.revisions[: self.bundle.revisions.index(revision) + 1] if revision else ()
        if sorted(item[0] for item in receipts) != sorted(expected):
            raise MigrationError("recovery_required", "Revision receipts do not match the database version.")
        for recorded_revision, checksum in receipts:
            if self.bundle.checksums.get(recorded_revision) != checksum:
                raise MigrationError("checksum_conflict", "An applied revision is absent or its source has changed.")

    def _pending_run(self, connection: Connection, revision: str | None) -> MigrationPlan | None:
        runs = list(connection.exec_driver_sql("SELECT run_id, plan_json FROM pc_migration_runs WHERE state='running'"))
        if len(runs) > 1:
            raise MigrationError("recovery_required", "Multiple unfinished runs require manual inspection.")
        if runs:
            run_id, plan_json = runs[0]
            original = MigrationPlan.model_validate_json(plan_json)
            if (
                original.bundle_checksum != self.bundle.checksum
                or original.configuration_digest != self.configuration_digest
            ):
                raise MigrationError("plan_changed", "Resume requires the original migration bundle and configuration.")
            if original.database_id != self._database_id():
                raise MigrationError("plan_changed", "The recovery record belongs to a different database target.")
            completed_count = self.bundle.revisions.index(revision) + 1 if revision is not None else 0
            source_count = self.bundle.revisions.index(original.source_revision) + 1 if original.source_revision else 0
            if completed_count < source_count:
                raise MigrationError("recovery_required", "Recorded progress precedes the original source revision.")
            return original.model_copy(update={"state": "recovery_required", "resume_run_id": run_id})
        return None

    def _database_id(self) -> str:
        # The deployment identity is the canonical path, never a DSN or payload.
        return digest({"kind": "sqlite", "path": str(self.database)})

    def _plan(self, revision: str | None, fingerprint: str, *, initialized: bool) -> MigrationPlan:
        pending = self.bundle.pending(revision)
        adopt = not initialized and revision is not None
        state = "migration_required" if pending or adopt else "ready"
        if not initialized and revision is None:
            state = "uninitialized"
        fields = {
            "database_id": self._database_id(),
            "source_revision": revision,
            "target_revision": self.bundle.head,
            "schema_fingerprint": fingerprint,
            "bundle_checksum": self.bundle.checksum,
            "configuration_digest": self.configuration_digest,
            "state": state,
            "revisions": pending,
            "adopt_baseline": adopt,
        }
        return MigrationPlan.model_validate({"plan_id": digest(fields), **fields})

    @staticmethod
    def _begin(connection: Connection) -> None:
        try:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
        except OperationalError as error:
            if isinstance(error.orig, sqlite3.OperationalError) and error.orig.sqlite_errorcode in {
                sqlite3.SQLITE_BUSY,
                sqlite3.SQLITE_LOCKED,
            }:
                raise MigrationError("active_writers", "SQLite is busy; stop writers before retrying.") from error
            raise

    def _backup(self) -> tuple[Path, str]:
        directory = self.database.with_name(self.database.name + ".pc-migration-backups")
        backup = directory / f"{uuid4().hex}.sqlite3"
        try:
            directory.mkdir(mode=0o700, exist_ok=True)
            os.close(os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
            with (
                closing(sqlite3.connect(self.database.as_uri() + "?mode=ro", uri=True)) as source,
                closing(sqlite3.connect(backup)) as destination,
            ):
                source.backup(destination)
                if destination.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                    raise MigrationError("backup_required", "The consistent SQLite backup failed integrity checks.")
            with backup.open("rb") as persisted:
                os.fsync(persisted.fileno())
            if os.name != "nt":
                directory_fd = os.open(directory, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            return backup, _file_checksum(backup)
        except (OSError, sqlite3.Error) as error:
            raise MigrationError(
                "backup_required", "Could not create and verify a consistent SQLite backup."
            ) from error

    def _bootstrap(self, connection: Connection, plan: MigrationPlan, run_id: str, backup: Path, checksum: str) -> None:
        if plan.adopt_baseline or plan.state == "uninitialized":
            for ddl in _CONTROL_DDL.values():
                connection.exec_driver_sql(ddl)
        if plan.adopt_baseline and plan.source_revision is not None:
            # Adoption follows exact baseline recognition and a verified backup.
            with _ALEMBIC_CONTEXT_LOCK:
                command.stamp(self.bundle.config(connection), plan.source_revision)
        connection.execute(
            text("INSERT INTO pc_migration_runs VALUES (:run, :plan, :backup, :checksum, 'running', :started)"),
            {
                "run": run_id,
                "plan": plan.model_dump_json(),
                "backup": str(backup),
                "checksum": checksum,
                "started": datetime.now(UTC).isoformat(),
            },
        )
        if plan.adopt_baseline and plan.source_revision:
            for revision in self.bundle.revisions[: self.bundle.revisions.index(plan.source_revision) + 1]:
                connection.execute(
                    text("INSERT INTO pc_migration_steps VALUES (:run, :revision, :checksum)"),
                    {"run": run_id, "revision": revision, "checksum": self.bundle.checksums[revision]},
                )

    def _resume(self, connection: Connection, run_id: str, plan: MigrationPlan) -> tuple[str, Path]:
        path, checksum, plan_json = connection.execute(
            text("SELECT backup_path, backup_checksum, plan_json FROM pc_migration_runs WHERE run_id=:run"),
            {"run": run_id},
        ).one()
        original = MigrationPlan.model_validate_json(plan_json)
        if original.plan_id != plan.plan_id:
            raise MigrationError("plan_changed", "The run does not match the accepted plan.")
        backup = Path(path)
        try:
            valid = backup.is_file() and _file_checksum(backup) == checksum
        except OSError as error:
            raise MigrationError("backup_required", "The original recovery point cannot be read.") from error
        if not valid:
            raise MigrationError("backup_required", "The original recovery point is missing or has changed.")
        return run_id, backup

    def _upgrade(self, connection: Connection, run_id: str) -> None:
        current = connection.exec_driver_sql("SELECT version_num FROM pc_schema_revision").scalar_one_or_none()
        connection.commit()
        for revision in self.bundle.pending(current):
            self._begin(connection)
            self._verify_schema(connection, current)
            with _ALEMBIC_CONTEXT_LOCK:
                command.upgrade(self.bundle.config(connection), revision)
            self._verify_schema(connection, revision)
            connection.execute(
                text("INSERT INTO pc_migration_steps VALUES (:run, :revision, :checksum)"),
                {"run": run_id, "revision": revision, "checksum": self.bundle.checksums[revision]},
            )
            connection.commit()
            current = revision

    def _verify_schema(self, connection: Connection, revision: str | None) -> None:
        self.bundle.verify(revision, digest(sqlite_inventory(connection, exclude=_CONTROL_NAMES)))
        self._verify_integrity(connection)

    @staticmethod
    def _verify_integrity(connection: Connection) -> None:
        if connection.exec_driver_sql("PRAGMA foreign_key_check").first() is not None:
            raise MigrationError("verification_failed", "Foreign-key violations block migration readiness.")
        if connection.exec_driver_sql("PRAGMA quick_check").scalars().all() != ["ok"]:
            raise MigrationError("verification_failed", "SQLite integrity verification failed.")


def _file_checksum(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()
