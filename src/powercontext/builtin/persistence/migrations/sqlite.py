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

"""Explicit SQLite maintenance with Alembic's single standard version table.

External evidence preserves the original recovery point. Retry inspects the
actual revision and schema; there is no generic run ID or task checkpoint.
Readiness applies only to the registered bundle, not the entire Server runtime.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Literal, Self
from uuid import uuid4

from alembic import command
from pydantic import BaseModel, ConfigDict, model_validator
from sqlalchemy import Connection, create_engine
from sqlalchemy.exc import DatabaseError, OperationalError

from .backup import BackupContext, BackupProvider, BackupRef, SQLiteBackupProvider
from .bundle import MigrationBundle, sqlite_inventory
from .locking import local_migration_lock
from .models import MigrationError, MigrationPlan, MigrationResult, digest

_VERSION_DDL = (
    "CREATE TABLE pc_schema_revision (version_num VARCHAR(32) NOT NULL, "
    "CONSTRAINT pc_schema_revision_pkc PRIMARY KEY (version_num))"
)
_CONTROL_NAMES = frozenset({"pc_schema_revision"})
_ALEMBIC_CONTEXT_LOCK = threading.RLock()
BackupPolicy = Literal["auto", "manual", "skip"]


class _MaintenanceEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: Literal["preparing", "active", "complete"]
    database_id: str
    source_revision: str | None
    source_fingerprint: str
    bundle_checksum: str
    configuration_digest: str
    backup_policy: BackupPolicy
    maintenance_window_id: str
    backup_state: Literal["pending", "completed", "user_confirmed", "skipped", "not_required"]
    backup: BackupRef | None
    manual_ref: str | None = None
    release_checksums: dict[str, str]
    retained_objects: tuple[str, ...] = ()
    backup_history: list[dict[str, Any]] = []

    @model_validator(mode="after")
    def consistent_recovery_point(self) -> Self:
        if self.backup_state == "not_required":
            if self.source_revision is not None or self.backup is not None:
                raise ValueError("Only an originally empty database needs no recovery point")  # noqa: TRY003
            return self
        expected = {"auto": "completed", "manual": "user_confirmed", "skip": "skipped"}[self.backup_policy]
        if self.backup_state != expected and not (self.state == "preparing" and self.backup_state == "pending"):
            raise ValueError("The backup policy and recorded maintenance state disagree")  # noqa: TRY003
        if self.backup_state == "completed":
            ref = self.backup
            if ref is None or (
                ref.database_id != self.database_id
                or ref.source_revision != self.source_revision
                or ref.bundle_checksum != self.bundle_checksum
                or ref.maintenance_window_id != self.maintenance_window_id
            ):
                raise ValueError("The original backup reference belongs to another maintenance context")  # noqa: TRY003
        elif self.backup is not None:
            raise ValueError("An automatic recovery point is inconsistent with the declared backup state")  # noqa: TRY003
        return self


class SQLiteMigrationRunner:
    """Keep business writers stopped throughout this synchronous executor.

    The OS lock serializes cooperating migrators. BEGIN IMMEDIATE detects a
    present writer, but cannot prove that idle old processes will remain stopped.
    """

    def __init__(
        self,
        database: Path,
        bundle: MigrationBundle,
        *,
        configuration: Mapping[str, Any] | None = None,
        backup_provider: BackupProvider | None = None,
    ) -> None:
        self.database = database.expanduser().resolve()
        self.bundle = bundle
        self.configuration_digest = digest(configuration or {})
        self.backup_provider = backup_provider or SQLiteBackupProvider(self.database)
        self.evidence_path = self.database.with_name(self.database.name + ".pc-migration-state") / "maintenance.json"

    def plan(
        self,
        *,
        backup_policy: BackupPolicy = "auto",
        service: dict[str, Any] | None = None,
        shared_database: bool = False,
    ) -> MigrationPlan:
        """Inspect without creating a database, parent, lock or external evidence."""
        self.bundle.assert_unchanged()
        if backup_policy not in {"auto", "manual", "skip"}:
            raise MigrationError("invalid_backup_policy", "Choose auto, manual or skip.")
        if not self.database.exists():
            return self._plan(
                None,
                digest([]),
                initialized=False,
                backup_policy=backup_policy,
                service=service,
                shared_database=shared_database,
            )
        self._check_file()
        with self._connect(read_only=True) as connection:
            return self._inspect(
                connection, backup_policy=backup_policy, service=service, shared_database=shared_database
            )

    def verify(self) -> MigrationResult:
        evidence = self._evidence()
        policy = evidence["backup_policy"] if evidence and evidence["state"] != "complete" else "auto"
        plan = self.plan(backup_policy=policy)
        if plan.state != "ready":
            raise MigrationError(plan.state, "Inspect and explicitly apply the registered bundle before verification.")
        return MigrationResult(revision=self.bundle.head, changed=False)

    def apply(
        self,
        *,
        plan_id: str | None = None,
        maintenance_confirmed: bool = False,
        accepted: bool = False,
        backup_policy: BackupPolicy = "auto",
        backup_confirmed: bool = False,
        accept_no_backup: bool = False,
        backup_ref: str | None = None,
        service: dict[str, Any] | None = None,
        shared_database: bool = False,
    ) -> MigrationResult:
        options: dict[str, Any] = {
            "backup_policy": backup_policy,
            "service": service,
            "shared_database": shared_database,
        }
        initial = self.plan(**options)
        if initial.state == "ready":
            return MigrationResult(revision=self.bundle.head, changed=False)
        self._require_acceptance(initial, plan_id, accepted, maintenance_confirmed, backup_confirmed, accept_no_backup)
        with local_migration_lock(self.database):
            if self.plan(**options) != initial:
                raise MigrationError("plan_changed", "State changed while acquiring the migration lock.")
            with self._connect(read_only=False) as connection:
                connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
                connection.commit()
                self._begin(connection)
                if self._inspect(connection, **options) != initial:
                    raise MigrationError("plan_changed", "State changed before acquiring the write transaction.")
                evidence = self._prepare_evidence(initial, backup_ref)
                self.bundle.assert_unchanged()
                version_exists = (
                    connection.exec_driver_sql("SELECT name FROM sqlite_schema WHERE name='pc_schema_revision'").first()
                    is not None
                )
                if not version_exists:
                    connection.exec_driver_sql(_VERSION_DDL)
                    if initial.adopt_baseline and initial.source_revision is not None:
                        with _ALEMBIC_CONTEXT_LOCK:
                            command.stamp(self.bundle.config(connection), initial.source_revision)
                connection.commit()
                self._upgrade(connection)
                self._begin(connection)
                self._verify_schema(connection, self.bundle.head)
                connection.commit()
                evidence["state"] = "complete"
                evidence["release_checksums"] = self.bundle.checksums
                inventory = sqlite_inventory(connection)
                evidence["retained_objects"] = [
                    name for name in self.bundle.retained_objects if any(row[1] == name for row in inventory)
                ]
                self._write_evidence(evidence)
        ref = evidence.get("backup")
        return MigrationResult(
            revision=self.bundle.head,
            changed=True,
            backup_state=evidence["backup_state"],
            backup_ref=ref["location"] if ref else evidence.get("manual_ref"),
        )

    @staticmethod
    def _require_acceptance(
        initial: MigrationPlan,
        plan_id: str | None,
        accepted: bool,
        maintenance_confirmed: bool,
        backup_confirmed: bool,
        accept_no_backup: bool,
    ) -> None:
        if not accepted or plan_id is None:
            raise MigrationError("confirmation_required", "Accept the displayed plan ID explicitly.")
        if not maintenance_confirmed:
            raise MigrationError(
                "maintenance_required", "Stop every writer and coordinate all affected shared-db nodes."
            )
        if initial.plan_id != plan_id:
            raise MigrationError("plan_changed", "The reviewed target, schema, bundle or maintenance choices changed.")
        if initial.state != "uninitialized":
            if initial.backup_policy == "manual" and not backup_confirmed:
                raise MigrationError(
                    "backup_confirmation_required", "Declare your manual backup; PC does not verify it."
                )
            if initial.backup_policy == "skip" and not accept_no_backup:
                raise MigrationError(
                    "backup_confirmation_required", "Accept that the original data may be unrecoverable."
                )
        if initial.backup_policy == "auto" and not initial.backup_available:
            raise MigrationError("backup_unsupported", "; ".join(initial.backup_reason))

    def _check_file(self) -> None:
        if not self.database.is_file() or self.database.stat().st_nlink != 1:
            raise MigrationError("unsupported_target", "Use a regular SQLite file without hard-link aliases.")

    @contextmanager
    def _connect(self, *, read_only: bool) -> Iterator[Connection]:
        uri = self.database.as_uri() + ("?mode=ro" if read_only else "?mode=rwc")
        engine = create_engine(
            "sqlite://", creator=lambda: sqlite3.connect(uri, uri=True, isolation_level=None, timeout=0)
        )
        try:
            with engine.connect() as connection:
                if read_only:
                    connection.exec_driver_sql("PRAGMA query_only=ON")
                    connection.exec_driver_sql("BEGIN")
                yield connection
        except DatabaseError as error:
            if isinstance(error.orig, sqlite3.Error):
                raise MigrationError(
                    "invalid_database", "SQLite could not read or execute the registered schema."
                ) from error
            raise
        finally:
            engine.dispose()

    def _inspect(self, connection: Connection, **options: Any) -> MigrationPlan:
        inventory = sqlite_inventory(connection)
        controls = [row for row in inventory if row[1] in _CONTROL_NAMES or row[2] in _CONTROL_NAMES]
        initialized = bool(controls)
        if initialized and controls != [["table", "pc_schema_revision", "pc_schema_revision", _VERSION_DDL]]:
            raise MigrationError("recovery_required", "The Alembic version table has an unknown shape.")
        fingerprint = digest(
            sqlite_inventory(connection, exclude=_CONTROL_NAMES | frozenset(self.bundle.retained_objects))
        )
        if initialized:
            revisions = list(connection.exec_driver_sql("SELECT version_num FROM pc_schema_revision").scalars())
            if len(revisions) > 1:
                raise MigrationError("incompatible_schema", "Multiple revision heads are unsupported.")
            revision = revisions[0] if revisions else None
            self._verify_schema(connection, revision)
            if self.bundle.pending(revision) and self._evidence() is None:
                raise MigrationError(
                    "recovery_required",
                    "The original external maintenance evidence is missing; do not back up partial state.",
                )
        elif inventory:
            revision = self.bundle.baselines.get(fingerprint)
            if revision is None:
                raise MigrationError("unknown_baseline", "Schema is not registered; no changes were made.")
            self._verify_retained(inventory, revision)
            self._verify_integrity(connection)
        else:
            revision = None
        return self._plan(revision, fingerprint, initialized=initialized, **options)

    def _plan(
        self,
        revision: str | None,
        fingerprint: str,
        *,
        initialized: bool,
        backup_policy: BackupPolicy,
        service: dict[str, Any] | None,
        shared_database: bool,
    ) -> MigrationPlan:
        pending = self.bundle.pending(revision)
        adopt = not initialized and revision is not None
        state = "migration_required" if pending or adopt else "ready"
        if not initialized and revision is None:
            state = "uninitialized"
        evidence = self._evidence()
        if evidence:
            for recorded, checksum in evidence.get("release_checksums", {}).items():
                if self.bundle.checksums.get(recorded) != checksum:
                    raise MigrationError("checksum_conflict", "A recorded released migration resource changed.")
            if evidence["state"] != "complete":
                if (
                    evidence["bundle_checksum"] != self.bundle.checksum
                    or evidence["configuration_digest"] != self.configuration_digest
                ):
                    raise MigrationError("recovery_required", "Keep the original bundle and configuration.")
                pristine = self._pristine_preparation(evidence, revision, fingerprint)
                if evidence["backup_policy"] != backup_policy and backup_policy == "auto" and not pristine:
                    raise MigrationError(
                        "recovery_required", "Do not create an automatic backup of partially migrated state."
                    )
                if not pristine:
                    state = "recovery_required"
        context = self._backup_context(revision, "preview")
        available = True
        reasons: tuple[str, ...] = ()
        if backup_policy == "auto" and revision is not None and state != "ready":
            capability = self.backup_provider.capabilities(context)
            available, reasons = capability.available, capability.reasons
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
            "backup_policy": backup_policy,
            "backup_available": available,
            "backup_reason": reasons,
            "affected_objects": self.bundle.affected_objects,
            "retained_objects": self.bundle.retained_objects,
            "service": service,
            "shared_database": shared_database,
        }
        return MigrationPlan.model_validate({"plan_id": digest(fields), **fields})

    def _database_id(self) -> str:
        return digest({"kind": "sqlite", "path": str(self.database)})

    def _backup_context(self, revision: str | None, window: str) -> BackupContext:
        return BackupContext(
            database_id=self._database_id(),
            source_revision=revision,
            bundle_checksum=self.bundle.checksum,
            maintenance_window_id=window,
            objects=self.bundle.affected_objects,
            writers_stopped=True,
        )

    def _prepare_evidence(self, plan: MigrationPlan, manual_ref: str | None) -> dict[str, Any]:
        previous = self._evidence()
        if previous and previous["state"] != "complete":
            if previous["backup_policy"] != plan.backup_policy:
                # apply has already required the new plan ID and explicit backup consent.
                previous.setdefault("backup_history", []).append({
                    key: previous.get(key) for key in ("backup_policy", "backup_state", "backup", "manual_ref")
                })
                previous.update(
                    state="preparing",
                    backup_policy=plan.backup_policy,
                    backup_state="pending",
                    backup=None,
                    manual_ref=None,
                )
                if plan.backup_policy in {"manual", "skip"}:
                    previous.update(
                        state="active",
                        backup_state="user_confirmed" if plan.backup_policy == "manual" else "skipped",
                        manual_ref=manual_ref if plan.backup_policy == "manual" else None,
                    )
                    self._write_evidence(previous)
                    return previous
            recovered = self._reuse_evidence(previous, plan)
            if recovered is not None:
                return recovered
        evidence: dict[str, Any] = (
            previous
            if previous and previous["state"] != "complete"
            else {
                "state": "preparing",
                "database_id": plan.database_id,
                "source_revision": plan.source_revision,
                "source_fingerprint": plan.schema_fingerprint,
                "bundle_checksum": plan.bundle_checksum,
                "configuration_digest": plan.configuration_digest,
                "backup_policy": plan.backup_policy,
                "maintenance_window_id": uuid4().hex,
                "backup_state": "pending",
                "backup": None,
                "release_checksums": previous.get("release_checksums", {}) if previous else {},
                "retained_objects": previous.get("retained_objects", []) if previous else [],
            }
        )
        self._write_evidence(evidence)
        if plan.source_revision is None:
            evidence["backup_state"] = "not_required"
        elif plan.backup_policy == "auto":
            ref = self.backup_provider.create_backup(
                self._backup_context(plan.source_revision, evidence["maintenance_window_id"])
            )
            if self.backup_provider.inspect_backup(ref).state != "completed":
                raise MigrationError("backup_failed", "The automatic recovery point did not pass provider inspection.")
            evidence.update(backup=ref.model_dump(mode="json"), backup_state="completed")
        elif plan.backup_policy == "manual":
            evidence.update(backup_state="user_confirmed", manual_ref=manual_ref)
        else:
            evidence["backup_state"] = "skipped"
        evidence["state"] = "active"
        self._write_evidence(evidence)
        return evidence

    def _reuse_evidence(self, previous: dict[str, Any], plan: MigrationPlan) -> dict[str, Any] | None:
        if self._pristine_preparation(previous, plan.source_revision, plan.schema_fingerprint):
            previous["backup_policy"] = plan.backup_policy
        if previous.get("backup"):
            ref = BackupRef.model_validate(previous["backup"])
            if self.backup_provider.inspect_backup(ref).state != "completed":
                raise MigrationError(
                    "backup_required", "The original automatic recovery point or manifest is unavailable."
                )
            return previous
        if previous["backup_state"] in {"user_confirmed", "skipped", "not_required"}:
            return previous
        if previous["source_fingerprint"] != plan.schema_fingerprint:
            raise MigrationError(
                "backup_required", "Do not replace a missing original recovery point after schema changes."
            )
        return None

    @staticmethod
    def _pristine_preparation(evidence: dict[str, Any], revision: str | None, fingerprint: str) -> bool:
        return (
            evidence["state"] == "preparing"
            and evidence["backup_state"] == "pending"
            and evidence["backup"] is None
            and evidence["source_revision"] == revision
            and evidence["source_fingerprint"] == fingerprint
        )

    def _evidence(self) -> dict[str, Any] | None:
        if not self.evidence_path.exists():
            return None
        try:
            value = _MaintenanceEvidence.model_validate_json(self.evidence_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise MigrationError(
                "recovery_required", "The external maintenance evidence cannot be read safely."
            ) from error
        if value.database_id != self._database_id():
            raise MigrationError("recovery_required", "The external maintenance evidence belongs to another target.")
        return value.model_dump(mode="json")

    def _write_evidence(self, evidence: dict[str, Any]) -> None:
        directory = self.evidence_path.parent
        temporary = directory / f".{uuid4().hex}.json"
        try:
            directory.mkdir(parents=True, mode=0o700, exist_ok=True)
            with temporary.open("x", encoding="utf-8") as output:
                os.chmod(temporary, 0o600)
                json.dump(evidence, output, ensure_ascii=False, sort_keys=True)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.evidence_path)
            if os.name != "nt":
                descriptor = os.open(directory, os.O_RDONLY)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
        except OSError as error:
            raise MigrationError(
                "recovery_required", "Cannot persist external recovery evidence; migration is blocked."
            ) from error

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

    def _upgrade(self, connection: Connection) -> None:
        current = connection.exec_driver_sql("SELECT version_num FROM pc_schema_revision").scalar_one_or_none()
        connection.commit()
        for revision in self.bundle.pending(current):
            self._begin(connection)
            self._verify_schema(connection, current)
            with _ALEMBIC_CONTEXT_LOCK:
                command.upgrade(self.bundle.config(connection), revision)
            self._verify_schema(connection, revision)
            connection.commit()
            current = revision

    def _verify_schema(self, connection: Connection, revision: str | None) -> None:
        excluded = _CONTROL_NAMES | frozenset(self.bundle.retained_objects)
        inventory = sqlite_inventory(connection)
        self._verify_retained(inventory, revision)
        self.bundle.verify(revision, digest(sqlite_inventory(connection, exclude=excluded)))
        self._verify_integrity(connection)

    def _verify_retained(self, inventory: list[list[Any]], revision: str | None) -> None:
        for name in self.bundle.retained_objects:
            objects = [row for row in inventory if row[1] == name or row[2] == name]
            if objects and self.bundle.retained_revisions[name] in self.bundle.pending(revision):
                raise MigrationError("recovery_required", "A retained object exists before its introducing revision.")
            if objects and digest(objects) not in self.bundle.retained_fingerprints.get(name, []):
                raise MigrationError("incompatible_schema", "A retained historical object has an unregistered shape.")
        evidence = self._evidence()
        required = set(evidence["retained_objects"]) if evidence else set()
        if evidence:
            # A revision can commit before its retained inventory is recorded.
            # Adoption at the current revision did not execute that revision.
            committed = set(self.bundle.pending(evidence["source_revision"])) - set(self.bundle.pending(revision))
            required.update(
                name for name, introduced in self.bundle.retained_revisions.items() if introduced in committed
            )
        if any(not any(row[1] == name for row in inventory) for name in required):
            raise MigrationError(
                "recovery_required", "A required retained table was removed without a cleanup revision."
            )

    def _verify_integrity(self, connection: Connection) -> None:
        if any(
            row[0] not in self.bundle.retained_objects for row in connection.exec_driver_sql("PRAGMA foreign_key_check")
        ):
            raise MigrationError("verification_failed", "Foreign-key violations block migration readiness.")
        if connection.exec_driver_sql("PRAGMA quick_check").scalars().all() != ["ok"]:
            raise MigrationError("verification_failed", "SQLite integrity verification failed.")
