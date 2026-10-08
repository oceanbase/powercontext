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

"""Offline seekdb/OceanBase migrations with explicit nontransactional DDL recovery."""

from __future__ import annotations

import hashlib
import os
import socket
from collections.abc import Callable, Mapping
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Literal
from uuid import uuid4

from alembic import command
from alembic.config import Config
from pydantic import BaseModel, ConfigDict, model_validator
from sqlalchemy import Connection

from .backup import BackupContext, BackupProvider, BackupRef, ForkBackupProvider
from .bundle import ALEMBIC_CONTEXT_LOCK, MigrationBundle
from .connections import BackendIdentity, MaintenanceConnections
from .models import MigrationError, MigrationPlan, MigrationResult, digest
from .mysql_schema import MySQLSchema, mysql_inventory, read_revision

BackupPolicy = Literal["auto", "manual", "skip"]
BackupFactory = Callable[[Connection, BackendIdentity, Path], BackupProvider]


class _Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: Literal["preparing", "active", "complete"]
    database_id: str
    source_revision: str | None
    source_fingerprint: str
    bundle_checksum: str
    configuration_digest: str
    last_revision: str | None
    pending_revision: str | None = None
    adopting: bool = False
    maintenance_window_id: str
    backup_policy: BackupPolicy
    backup_state: Literal["pending", "completed", "user_confirmed", "skipped", "not_required"]
    backup: BackupRef | None = None
    manual_ref: str | None = None
    backup_history: list[dict[str, Any]] = []
    release_checksums: dict[str, str] = {}

    @model_validator(mode="after")
    def validate_backup(self) -> _Evidence:
        if self.backup_state == "not_required":
            if self.source_revision is not None or self.backup is not None:
                raise ValueError("Only an empty source needs no recovery point")  # noqa: TRY003
        elif self.backup_state == "pending":
            if self.state != "preparing" or self.backup is not None:
                raise ValueError("Pending backup is only valid before migration")  # noqa: TRY003
        elif (
            self.backup_state
            != {"auto": "completed", "manual": "user_confirmed", "skip": "skipped"}[self.backup_policy]
        ):
            raise ValueError("Backup declaration and policy disagree")  # noqa: TRY003
        if self.backup_state == "completed":
            ref = self.backup
            if ref is None or (
                ref.database_id != self.database_id
                or ref.source_revision != self.source_revision
                or ref.bundle_checksum != self.bundle_checksum
                or ref.maintenance_window_id != self.maintenance_window_id
            ):
                raise ValueError("Recovery point belongs to another maintenance context")  # noqa: TRY003
        elif self.backup is not None:
            raise ValueError("Unexpected automatic recovery point")  # noqa: TRY003
        return self


class _Journal:
    def __init__(self, directory: Path, identity: BackendIdentity) -> None:
        self.database_id = identity.database_id
        self.path = directory / identity.database_id / "maintenance.json"

    def read(self) -> _Evidence | None:
        if not self.path.exists():
            return None
        try:
            evidence = _Evidence.model_validate_json(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise MigrationError(
                "recovery_required", "The external maintenance evidence cannot be read safely."
            ) from error
        if evidence.database_id != self.database_id:
            raise MigrationError("recovery_required", "The maintenance evidence belongs to another database.")
        return evidence

    def write(self, evidence: _Evidence) -> None:
        # Revalidate mutations before replacing the durable recovery record.
        value = _Evidence.model_validate(evidence.model_dump())
        directory = self.path.parent
        temporary = directory / f".{uuid4().hex}.json"
        try:
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            with temporary.open("x", encoding="utf-8") as output:
                os.chmod(temporary, 0o600)
                output.write(value.model_dump_json())
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.path)
            if os.name != "nt":
                descriptor = os.open(directory, os.O_RDONLY)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
        except OSError as error:
            raise MigrationError(
                "recovery_required", "Cannot persist maintenance evidence; migration is blocked."
            ) from error


class MySQLMigrationRunner:
    """Use one version table, a pinned backend lock, and persistent external intent.

    The supported historical layouts are intentionally finite. A partial DDL
    state is resumable only when both the original intent and its exact declared
    schema prefix agree. Each Alembic upgrade callback verifies the full target
    before Alembic advances its standard version_num row.
    """

    def __init__(
        self,
        connections: MaintenanceConnections,
        bundle: MigrationBundle,
        *,
        evidence_directory: Path,
        configuration: Mapping[str, Any] | None = None,
        backup_factory: BackupFactory | None = None,
    ) -> None:
        self.connections = connections
        self.bundle = bundle
        self.schema = MySQLSchema(bundle)
        self.evidence_directory = evidence_directory.expanduser().resolve()
        self.configuration_digest = digest(configuration or {})
        if connections.config.kind == "oceanbase":
            if (
                connections.lock_coordination != "single-host"
                or connections.evidence_directory != self.evidence_directory
            ):
                raise MigrationError(
                    "coordination_required",
                    "Use single-host coordination and the same evidence directory for this runner.",
                )
            self.configuration_digest = digest({
                "configuration": configuration or {},
                "coordination": "single-host",
                "host": socket.gethostname(),
                "evidence_directory": str(self.evidence_directory),
            })
        self.backup_factory = backup_factory or _fork_provider
        self._history_directory = Path(__file__).parent / "mysql_resources"
        resources = {
            path.relative_to(self._history_directory).as_posix(): path.read_bytes()
            for path in sorted(self._history_directory.rglob("*.py"))
        }
        self._history_bytes = resources
        self._snapshot = TemporaryDirectory(prefix="pc-mysql-migrations-")
        for name, content in resources.items():
            target = Path(self._snapshot.name) / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        hashes = {name: hashlib.sha256(content).hexdigest() for name, content in resources.items()}
        self.checksum = digest({"bundle": bundle.checksum, "mysql_history": hashes})
        self.checksums = {
            revision: digest({
                "revision": checksum,
                "env": hashes["env.py"],
                "wrapper": {name: value for name, value in hashes.items() if name.startswith(f"versions/{revision}_")},
            })
            for revision, checksum in bundle.checksums.items()
        }

    def _assert_unchanged(self) -> None:
        self.bundle.assert_unchanged()
        resources = {
            path.relative_to(self._history_directory).as_posix(): path.read_bytes()
            for path in sorted(self._history_directory.rglob("*.py"))
        }
        if resources != self._history_bytes:
            raise MigrationError("plan_changed", "MySQL migration resources changed after planning.")

    def plan(
        self,
        *,
        backup_policy: BackupPolicy = "auto",
        service: dict[str, Any] | None = None,
        shared_database: bool = False,
    ) -> MigrationPlan:
        self._assert_unchanged()
        return self.connections.run(
            lambda connection, identity, _guard: self._inspect(
                connection, identity, backup_policy=backup_policy, service=service, shared_database=shared_database
            )
        )

    def verify(self) -> MigrationResult:
        def verify(
            connection: Connection | None, identity: BackendIdentity, _guard: Callable[[], None]
        ) -> MigrationResult:
            evidence = _Journal(self.evidence_directory, identity).read()
            policy = evidence.backup_policy if evidence and evidence.state != "complete" else "auto"
            plan = self._inspect(connection, identity, backup_policy=policy)
            if plan.state != "ready":
                raise MigrationError(
                    plan.state, "Inspect and explicitly apply the registered bundle before verification."
                )
            return MigrationResult(revision=self.bundle.head, changed=False)

        self._assert_unchanged()
        return self.connections.run(verify)

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
        previous_run = (initial.coordination or {}).get("pending_run_id")
        acknowledgement = self.connections.acknowledge_previous_run
        if previous_run is not None and acknowledgement != previous_run:
            raise MigrationError(
                "recovery_required",
                "Confirm that the previous remote operation has ended, then acknowledge its exact pending run ID.",
            )
        if previous_run is None and acknowledgement is not None:
            raise MigrationError("stale_ack", "There is no pending run matching this acknowledgement.")
        if initial.state == "ready":
            return MigrationResult(revision=self.bundle.head, changed=False)
        self._check_consent(
            initial,
            plan_id=plan_id,
            accepted=accepted,
            maintenance_confirmed=maintenance_confirmed,
            backup_confirmed=backup_confirmed,
            accept_no_backup=accept_no_backup,
        )
        return self.connections.run(
            lambda connection, identity, guard: self._apply_locked(
                connection, identity, guard, initial=initial, manual_ref=backup_ref
            ),
            writable=True,
        )

    @staticmethod
    def _check_consent(
        initial: MigrationPlan,
        *,
        plan_id: str | None,
        accepted: bool,
        maintenance_confirmed: bool,
        backup_confirmed: bool,
        accept_no_backup: bool,
    ) -> None:
        if not accepted or plan_id is None:
            raise MigrationError("confirmation_required", "Accept the displayed plan ID explicitly.")
        if initial.plan_id != plan_id:
            raise MigrationError(
                "plan_changed", "The reviewed target, schema, resources or maintenance choices changed."
            )
        if not maintenance_confirmed:
            raise MigrationError("maintenance_required", "Stop every writer and coordinate all shared-database nodes.")
        if initial.state != "uninitialized":
            if initial.backup_policy == "manual" and not backup_confirmed:
                raise MigrationError(
                    "backup_confirmation_required", "Declare your manual backup; PC does not verify it."
                )
            if initial.backup_policy == "skip" and not accept_no_backup:
                raise MigrationError("backup_confirmation_required", "Accept that original data may be unrecoverable.")
        if initial.backup_policy == "auto" and not initial.backup_available:
            raise MigrationError("backup_unsupported", "; ".join(initial.backup_reason))

    def _apply_locked(
        self,
        connection: Connection | None,
        identity: BackendIdentity,
        guard: Callable[[], None],
        *,
        initial: MigrationPlan,
        manual_ref: str | None,
    ) -> MigrationResult:
        if connection is None:
            raise MigrationError("target_unavailable", "The maintenance connection is unavailable.")
        guard()
        self._assert_unchanged()
        if (
            self._inspect(
                connection,
                identity,
                backup_policy=initial.backup_policy,
                service=initial.service,
                shared_database=initial.shared_database,
            )
            != initial
        ):
            raise MigrationError("plan_changed", "The database changed before the maintenance lock was acquired.")
        journal = _Journal(self.evidence_directory, identity)
        previous = journal.read()
        if not initial.revisions and not initial.adopt_baseline and (previous is None or previous.state == "complete"):
            # A completed migration can retain its run receipt after an
            # interrupted connection close. Acknowledgement still requires
            # the lock and full schema/data reinspection, but no new backup.
            guard()
            return MigrationResult(revision=self.bundle.head, changed=False)
        provider = self.backup_factory(connection, identity, journal.path.parent / "backups")
        evidence = self._prepare_evidence(initial, journal, provider, manual_ref=manual_ref)
        guard()
        self._assert_unchanged()
        if self.schema.fingerprint(mysql_inventory(connection)) != initial.schema_fingerprint:
            raise MigrationError("plan_changed", "The source schema changed during backup.")
        config = self._config(connection)
        if initial.adopt_baseline:
            self._adopt(connection, config, journal, evidence, initial.source_revision, guard)
        current = initial.source_revision
        for target in self.bundle.pending(current):
            # An interrupted prior run can have committed Alembic's version row
            # before saving the journal. Normalize that observed head first.
            evidence.last_revision = current
            evidence.pending_revision = target
            journal.write(evidence)
            self._upgrade(connection, config, current, target, guard)
            evidence.last_revision = target
            evidence.pending_revision = None
            evidence.release_checksums[target] = self.checksums[target]
            journal.write(evidence)
            current = target
        self.schema.verify(mysql_inventory(connection), self.bundle.head)
        self.schema.validate_data(connection, self.bundle.head)
        guard()
        evidence.state = "complete"
        evidence.last_revision = self.bundle.head
        evidence.pending_revision = None
        evidence.adopting = False
        evidence.release_checksums = self.checksums.copy()
        journal.write(evidence)
        if evidence.backup_state == "pending":
            raise MigrationError("backup_required", "The backup decision has not completed.")
        return MigrationResult(
            revision=self.bundle.head,
            changed=True,
            backup_state=evidence.backup_state,
            backup_ref=evidence.backup.location if evidence.backup else evidence.manual_ref,
        )

    def _adopt(
        self,
        connection: Connection,
        config: Config,
        journal: _Journal,
        evidence: _Evidence,
        revision: str | None,
        guard: Callable[[], None],
    ) -> None:
        if revision is None:
            raise MigrationError("unknown_baseline", "Cannot stamp an unidentified baseline.")
        self.schema.verify(mysql_inventory(connection), revision)
        evidence.adopting = True
        journal.write(evidence)
        connection.commit()
        guard()
        with ALEMBIC_CONTEXT_LOCK:
            command.stamp(config, revision)
        connection.commit()
        guard()
        evidence.adopting = False
        journal.write(evidence)

    def _upgrade(
        self,
        connection: Connection,
        config: Config,
        source: str | None,
        target: str,
        guard: Callable[[], None],
    ) -> None:
        def upgrade(revision: str) -> None:
            if revision != target:
                raise MigrationError("invalid_bundle", "Alembic selected an unexpected migration revision.")
            guard()
            recovery = self.schema.recovery(mysql_inventory(connection), source)
            if recovery.revision != revision:
                raise MigrationError("recovery_required", "The stored intent does not match the schema transition.")
            for statement in recovery.pending_statements:
                guard()
                connection.exec_driver_sql(statement)
                # DDL may already have committed; rollback cannot undo it.
                guard()
                self.schema.recovery(mysql_inventory(connection), source)
            self.schema.verify(mysql_inventory(connection), revision)
            self.schema.validate_data(connection, revision)
            guard()

        config.attributes["execute_revision"] = upgrade
        connection.commit()
        guard()
        with ALEMBIC_CONTEXT_LOCK:
            command.upgrade(config, target)
        connection.commit()
        guard()
        self.schema.verify(mysql_inventory(connection), target)

    def _config(self, connection: Connection) -> Config:
        config = Config()
        config.set_main_option("script_location", self._snapshot.name.replace("%", "%%"))
        config.attributes["connection"] = connection
        return config

    def _inspect(
        self,
        connection: Connection | None,
        identity: BackendIdentity,
        *,
        backup_policy: BackupPolicy,
        service: dict[str, Any] | None = None,
        shared_database: bool = False,
    ) -> MigrationPlan:
        if backup_policy not in {"auto", "manual", "skip"}:
            raise MigrationError("invalid_backup_policy", "Choose auto, manual or skip.")
        journal = _Journal(self.evidence_directory, identity)
        evidence = journal.read()
        inventory = (
            mysql_inventory(connection) if connection is not None else {"tables": {}, "views": [], "triggers": []}
        )
        initialized, current = read_revision(connection, inventory) if connection is not None else (False, None)
        self._validate_evidence(evidence)
        current, adopt = self._recognize_source(inventory, initialized, current, evidence)
        if initialized and self.bundle.pending(current) and evidence is None:
            raise MigrationError("recovery_required", "The original external maintenance evidence is missing.")
        self._validate_position(evidence, current)
        if connection is not None and current is not None:
            self.schema.validate_data(connection, current)
        fingerprint = self.schema.fingerprint(inventory)
        state = "migration_required" if self.bundle.pending(current) or adopt else "ready"
        if not initialized and current is None:
            state = "uninitialized"
        if evidence and evidence.state != "complete":
            state = "recovery_required"
            if (
                evidence.backup_policy != backup_policy
                and backup_policy == "auto"
                and not (
                    evidence.state == "preparing"
                    and evidence.backup_state == "pending"
                    and evidence.source_fingerprint == fingerprint
                )
            ):
                raise MigrationError(
                    "recovery_required", "Do not create an automatic backup of partially migrated state."
                )
        available, reasons = self._backup_availability(
            connection, identity, journal, evidence, current, backup_policy=backup_policy, ready=state == "ready"
        )
        coordination = self.connections.coordination(identity)
        if coordination.get("pending_run_id"):
            state = "recovery_required"
        fields = {
            "database_id": identity.database_id,
            "source_revision": current,
            "target_revision": self.bundle.head,
            "schema_fingerprint": fingerprint,
            "bundle_checksum": self.checksum,
            "configuration_digest": self.configuration_digest,
            "state": state,
            "revisions": self.bundle.pending(current),
            "adopt_baseline": adopt,
            "backup_policy": backup_policy,
            "backup_available": available,
            "backup_reason": reasons,
            "affected_objects": self.bundle.affected_objects,
            "retained_objects": (),
            "service": service,
            "shared_database": shared_database,
            "coordination": coordination,
        }
        return MigrationPlan.model_validate({"plan_id": digest(fields), **fields})

    def _validate_evidence(self, evidence: _Evidence | None) -> None:
        if evidence is None:
            return
        for revision, checksum in evidence.release_checksums.items():
            if self.checksums.get(revision) != checksum:
                raise MigrationError("checksum_conflict", "A recorded released migration resource changed.")
        if evidence.state != "complete" and (
            evidence.bundle_checksum != self.checksum or evidence.configuration_digest != self.configuration_digest
        ):
            raise MigrationError("recovery_required", "Keep the original migration resources and configuration.")
        for revision in (evidence.source_revision, evidence.last_revision, evidence.pending_revision):
            if revision is not None and revision not in self.checksums:
                raise MigrationError("recovery_required", "The maintenance evidence contains an unknown revision.")
        if evidence.pending_revision is not None and (
            self.bundle.pending(evidence.last_revision)[:1] != (evidence.pending_revision,)
        ):
            raise MigrationError("recovery_required", "The pending revision is not the next registered migration.")
        if evidence.state == "complete" and (evidence.pending_revision is not None or evidence.adopting):
            raise MigrationError("recovery_required", "Completed maintenance evidence still records unfinished work.")

    def _validate_position(self, evidence: _Evidence | None, current: str | None) -> None:
        if evidence is None:
            return
        if evidence.state == "complete":
            if current != evidence.last_revision:
                raise MigrationError("recovery_required", "The database differs from its completed maintenance record.")
        elif current not in {evidence.last_revision, evidence.pending_revision}:
            raise MigrationError("recovery_required", "The database head is outside the stored migration intent.")

    def _recognize_source(
        self,
        inventory: dict[str, Any],
        initialized: bool,
        current: str | None,
        evidence: _Evidence | None,
    ) -> tuple[str | None, bool]:
        if not initialized:
            try:
                current = self.schema.identify(inventory)
            except MigrationError as error:
                raise MigrationError(
                    "unknown_baseline", "Schema is not a registered baseline; no changes were made."
                ) from error
            if current is not None and current not in set(self.bundle.baselines.values()):
                raise MigrationError(
                    "unknown_baseline", "This unversioned layout is not a registered adoption baseline."
                )
            return current, current is not None
        if evidence and evidence.adopting and current is None:
            current = evidence.source_revision
            if current is None:
                raise MigrationError("recovery_required", "The adoption intent has no source revision.")
            self.schema.verify(inventory, current)
            return current, True
        try:
            self.schema.verify(inventory, current)
        except MigrationError:
            if not evidence or evidence.state != "active" or evidence.pending_revision is None:
                raise MigrationError("recovery_required", "Partial DDL requires the original durable intent.") from None
            recovery = self.schema.recovery(inventory, current)
            if recovery.revision != evidence.pending_revision:
                raise MigrationError(
                    "recovery_required", "Partial DDL does not match the stored migration intent."
                ) from None
        return current, False

    def _backup_availability(
        self,
        connection: Connection | None,
        identity: BackendIdentity,
        journal: _Journal,
        evidence: _Evidence | None,
        current: str | None,
        *,
        backup_policy: BackupPolicy,
        ready: bool,
    ) -> tuple[bool, tuple[str, ...]]:
        if backup_policy != "auto" or ready or connection is None or current is None:
            return True, ()
        active = evidence if evidence and evidence.state != "complete" else None
        if active and active.backup_state == "not_required":
            return True, ()
        provider = self.backup_factory(connection, identity, journal.path.parent / "backups")
        if active and active.backup:
            available = provider.inspect_backup(active.backup).state == "completed"
            return available, () if available else ("The original automatic recovery point is unavailable.",)
        capability = provider.capabilities(self._backup_context(identity.database_id, current, "preview"))
        if capability.available and capability.method == "fork_table":
            return False, (
                "This executor has no accepted inventory and recovery workflow for same-database Fork tables.",
            )
        return capability.available, capability.reasons

    def _backup_context(self, database_id: str, revision: str | None, window: str) -> BackupContext:
        return BackupContext(
            database_id=database_id,
            source_revision=revision,
            bundle_checksum=self.checksum,
            maintenance_window_id=window,
            objects=self.bundle.affected_objects,
            writers_stopped=True,
        )

    def _prepare_evidence(
        self, plan: MigrationPlan, journal: _Journal, provider: BackupProvider, *, manual_ref: str | None
    ) -> _Evidence:
        previous = journal.read()
        if previous and previous.state != "complete":
            if self._reuse_evidence(previous, plan, journal, provider, manual_ref=manual_ref):
                return previous
            if previous.source_fingerprint != plan.schema_fingerprint:
                raise MigrationError("backup_required", "Do not replace the original backup after schema changes.")
            evidence = previous
        else:
            evidence = _Evidence(
                state="preparing",
                database_id=plan.database_id,
                source_revision=plan.source_revision,
                source_fingerprint=plan.schema_fingerprint,
                bundle_checksum=self.checksum,
                configuration_digest=self.configuration_digest,
                last_revision=plan.source_revision,
                maintenance_window_id=uuid4().hex,
                backup_policy=plan.backup_policy,
                backup_state="pending",
                release_checksums=previous.release_checksums.copy() if previous else {},
            )
        journal.write(evidence)
        if evidence.source_revision is None:
            evidence.backup_state = "not_required"
        elif plan.backup_policy == "auto":
            ref = provider.create_backup(
                self._backup_context(plan.database_id, evidence.source_revision, evidence.maintenance_window_id)
            )
            if provider.inspect_backup(ref).state != "completed":
                raise MigrationError("backup_failed", "The native recovery point did not pass provider inspection.")
            evidence.backup = ref
            evidence.backup_state = "completed"
        elif plan.backup_policy == "manual":
            evidence.backup_state = "user_confirmed"
            evidence.manual_ref = manual_ref
        else:
            evidence.backup_state = "skipped"
        evidence.state = "active"
        journal.write(evidence)
        return evidence

    @staticmethod
    def _reuse_evidence(
        previous: _Evidence,
        plan: MigrationPlan,
        journal: _Journal,
        provider: BackupProvider,
        *,
        manual_ref: str | None,
    ) -> bool:
        if previous.backup_policy != plan.backup_policy:
            previous.backup_history.append({
                key: previous.model_dump(mode="json")[key]
                for key in ("backup_policy", "backup_state", "backup", "manual_ref")
            })
            previous.backup_policy = plan.backup_policy
            previous.backup = None
            previous.manual_ref = manual_ref if plan.backup_policy == "manual" else None
            if plan.backup_policy == "auto":
                return False
            previous.backup_state = "user_confirmed" if plan.backup_policy == "manual" else "skipped"
            previous.state = "active"
            journal.write(previous)
            return True
        if previous.backup_state == "completed":
            if previous.backup is None or provider.inspect_backup(previous.backup).state != "completed":
                raise MigrationError("backup_required", "The original automatic recovery point is unavailable.")
            return True
        return previous.backup_state in {"user_confirmed", "skipped", "not_required"}


def _fork_provider(connection: Connection, identity: BackendIdentity, directory: Path) -> BackupProvider:
    # Engine feature versions are insufficient to claim a verified restoration
    # workflow. An adapter without release acceptance reports unavailable.
    return ForkBackupProvider(
        connection,
        database_name=identity.database_name,
        expected_product="seekdb" if identity.product == "seekdb" else "oceanbase_ai",
        directory=directory,
    )
