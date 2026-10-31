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

"""Shared locked, backed-up transactions for registered Server maintenance."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from sqlalchemy import Connection
from sqlalchemy.engine import make_url

from powercontext.builtin.persistence.sqlite import SQLiteConfig

from .backup import BackupContext, ForkBackupProvider, SQLiteBackupProvider
from .bundle import MigrationBundle, sqlite_inventory
from .connections import BackendIdentity, MaintenanceConnections
from .models import MigrationError, MigrationPlan, MigrationResult, digest
from .mysql import MySQLMigrationRunner, _Evidence, _Journal, require_completed_domain_maintenance
from .server_readiness import check_server_schema
from .sources import MigrationSources
from .sqlite import SQLiteMigrationRunner, read_maintenance_evidence, verify_release_checksums, verify_retained_objects
from .vector_profile import read_embedding_profile


class ManagedMaintenanceRunner:
    """Own the common transaction, recovery point and external maintenance journal."""

    operation: str
    affected_objects: tuple[str, ...]
    supports_vectors = False
    allow_declared_spaces = False

    def __init__(
        self,
        connections: MaintenanceConnections,
        bundle: MigrationBundle,
        *,
        configuration: dict[str, Any],
        evidence_directory: Path,
        implementation_checksum: str,
        migration_id: str = "rfc1515",
        batch_size: int = 100,
        source_bundles: tuple[MigrationBundle, ...] = (),
    ) -> None:
        self.connections = connections
        self.bundle = bundle
        self.sources = MigrationSources(bundle, source_bundles)
        self.configuration = json.loads(json.dumps(configuration))
        self.directory = evidence_directory.expanduser().resolve()
        self.migration_id = migration_id
        self.batch_size = batch_size
        self.configuration_digest = digest({"configuration": configuration, "migration_id": migration_id})
        self._checksum_inputs = {
            "operation": self.operation,
            "lifecycle": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "implementation": implementation_checksum,
        }
        self.checksum = digest({"bundle": bundle.checksum, **self._checksum_inputs})

    def _select_source(self, evidence) -> None:
        self.bundle = self.sources.from_evidence(evidence)
        self.bundle.assert_unchanged()
        self.checksum = digest({"bundle": self.bundle.checksum, **self._checksum_inputs})

    def _journal(self, identity: BackendIdentity) -> _Journal:
        return _Journal(self.directory / self.operation, identity)

    def _inspect(self, connection: Connection | None, identity: BackendIdentity, **options) -> MigrationPlan:
        self.bundle.assert_unchanged()
        if connection is None:
            raise MigrationError("migration_required", "Initialize or upgrade the full Server bundle first.")
        profile = read_embedding_profile(connection)
        if profile is not None and not self.supports_vectors:
            raise MigrationError(
                "unsupported_upgrade_configuration",
                "This partial maintenance bundle has no registered vector configuration path.",
            )
        configured = self.bundle.vector_profile
        if self.supports_vectors and profile != (None if configured is None else configured.profile):
            raise MigrationError(
                "embedding_profile_mismatch", "Maintenance requires the database's exact embedding declaration."
            )
        self._check_previous_maintenance(connection, identity)
        fingerprint, required, completed = self._state(connection, identity)
        evidence = self._journal(identity).read()
        active = evidence is not None and evidence.state != "complete"
        if active and (
            evidence.bundle_checksum != self.checksum
            or evidence.configuration_digest != self.configuration_digest
            or evidence.source_revision != self.bundle.head
        ):
            raise MigrationError(
                "recovery_required", "Resume with the original registered maintenance operation and configuration."
            )
        policy = options.get("backup_policy", "auto")
        if policy not in {"auto", "manual", "skip"}:
            raise MigrationError("invalid_backup_policy", "Choose auto, manual or skip.")
        if active and (
            evidence.backup_policy != policy
            or (fingerprint != evidence.source_fingerprint and (required or not completed))
        ):
            raise MigrationError(
                "recovery_required", "Keep the original registered maintenance recovery point and backup policy."
            )
        available = isinstance(self.connections.config, SQLiteConfig)
        coordination = self.connections.coordination(identity)
        recovering = active or "pending_run_id" in coordination
        fields = {
            "database_id": identity.database_id,
            "source_revision": self.bundle.head,
            "target_revision": self.bundle.head,
            "schema_fingerprint": fingerprint,
            "bundle_checksum": self.checksum,
            "configuration_digest": self.configuration_digest,
            "state": "recovery_required" if recovering else "migration_required" if required else "ready",
            "revisions": (),
            "domain_steps": (self.operation,) if required or active else (),
            "domain_validators": ("server_upgrade",),
            "backup_policy": policy,
            "backup_available": available,
            "backup_reason": ()
            if available
            else ("Native Fork recovery acceptance is unavailable; choose manual or skip.",),
            "affected_objects": self.affected_objects,
            "shared_database": options.get("shared_database", False),
            "service": options.get("service"),
            "coordination": coordination,
            "readiness_scope": "server",
        }
        return MigrationPlan.model_validate({"plan_id": digest(fields), **fields})

    def plan(self, **options) -> MigrationPlan:
        return self.connections.run(lambda connection, identity, guard: self._inspect(connection, identity, **options))

    def verify(self) -> MigrationResult:
        plan = self.plan()
        if plan.state != "ready":
            raise MigrationError(plan.state, "Apply the registered maintenance operation first.")
        return MigrationResult(revision=self.bundle.head, changed=False, readiness_scope="server")

    def _provider(self, connection: Connection, identity: BackendIdentity):
        config = self.connections.config
        if isinstance(config, SQLiteConfig):
            return SQLiteBackupProvider(Path(str(make_url(config.url).database)))
        return ForkBackupProvider(
            connection,
            database_name=identity.database_name,
            expected_product="seekdb" if identity.product == "seekdb" else "oceanbase_ai",
            directory=self.directory,
        )

    def apply(  # noqa: C901 -- One transaction owns backup, domain maintenance and recovery evidence.
        self,
        *,
        plan_id: str | None = None,
        accepted: bool = False,
        maintenance_confirmed: bool = False,
        backup_policy: Literal["auto", "manual", "skip"] = "auto",
        backup_confirmed: bool = False,
        accept_no_backup: bool = False,
        backup_ref: str | None = None,
        **options,
    ) -> MigrationResult:
        initial = self.plan(backup_policy=backup_policy, **options)
        pending = (initial.coordination or {}).get("pending_run_id")
        acknowledged = self.connections.acknowledge_previous_run
        if pending is not None and pending != acknowledged:
            raise MigrationError("recovery_required", "Acknowledge the exact previous remote maintenance run.")
        if pending is None and acknowledged is not None:
            raise MigrationError("stale_ack", "There is no pending run matching this acknowledgment.")
        if initial.state == "ready":
            return self.verify()
        if options.get("service") is not None:
            raise MigrationError("service_unsupported", "Use full Server maintenance to manage a local service.")
        SQLiteMigrationRunner._require_acceptance(
            initial, plan_id, accepted, maintenance_confirmed, backup_confirmed, accept_no_backup
        )

        def execute(connection, identity, guard):
            current = self._inspect(connection, identity, backup_policy=backup_policy, **options)
            if current != initial:
                raise MigrationError("plan_changed", "Maintenance state changed while acquiring the shared lock.")
            if not current.domain_steps:
                guard()
                return MigrationResult(revision=self.bundle.head, changed=False, readiness_scope="server")
            journal = self._journal(identity)
            evidence = journal.read()
            if evidence is None or evidence.state == "complete":
                evidence = _Evidence(
                    state="preparing",
                    database_id=identity.database_id,
                    source_revision=self.bundle.head,
                    source_fingerprint=initial.schema_fingerprint,
                    bundle_checksum=self.checksum,
                    configuration_digest=self.configuration_digest,
                    last_revision=self.bundle.head,
                    maintenance_window_id=uuid4().hex,
                    backup_policy=backup_policy,
                    backup_state="pending",
                )
                journal.write(evidence)
            backup_objects = self.bundle.affected_objects
            if identity.product != "sqlite":
                from .mysql_schema import mysql_inventory

                backup_objects = tuple(
                    dict.fromkeys((
                        *backup_objects,
                        *(space["physical_table"] for space in mysql_inventory(connection).get("artifact_spaces", [])),
                    ))
                )
            context = BackupContext(
                database_id=identity.database_id,
                source_revision=self.bundle.head,
                bundle_checksum=self.checksum,
                maintenance_window_id=evidence.maintenance_window_id,
                objects=backup_objects,
                writers_stopped=True,
            )
            provider = self._provider(connection, identity)
            # Backup must see committed state. The shared OS/engine lock remains held.
            connection.commit()
            if evidence.state == "preparing":
                if backup_policy == "auto":
                    evidence.backup = provider.create_backup(context)
                    evidence.backup_state = "completed"
                else:
                    evidence.backup_state = "user_confirmed" if backup_policy == "manual" else "skipped"
                    evidence.manual_ref = backup_ref
                evidence.state = "active"
                journal.write(evidence)
            if evidence.backup is not None and provider.inspect_backup(evidence.backup).state != "completed":
                raise MigrationError(
                    "backup_failed", "The original registered maintenance recovery point is unavailable."
                )
            guard()
            connection.exec_driver_sql(
                "BEGIN IMMEDIATE" if connection.dialect.name == "sqlite" else "START TRANSACTION"
            )
            locked = self._inspect(connection, identity, backup_policy=backup_policy, **options)
            if locked.schema_fingerprint != initial.schema_fingerprint:
                raise MigrationError("plan_changed", "Maintenance state changed before the write transaction.")
            self._apply_operation(connection, guard)
            guard()
            check_server_schema(connection, allow_declared_spaces=self.allow_declared_spaces)
            self.bundle.domains.validate(
                connection, self.bundle.head, backend=identity.product, configuration=self.configuration
            )
            connection.commit()
            evidence.state = "complete"
            journal.write(evidence)
            backup_state = evidence.backup_state
            if backup_state == "pending":
                raise MigrationError("backup_failed", "The registered maintenance recovery point has not completed.")
            return MigrationResult(
                revision=self.bundle.head,
                changed=True,
                readiness_scope="server",
                backup_state=backup_state,
                backup_ref=evidence.backup.location if evidence.backup else evidence.manual_ref,
            )

        return self.connections.run(execute, writable=True)

    def _state(self, connection: Connection, identity: BackendIdentity) -> tuple[str, bool, bool]:
        raise NotImplementedError

    def _apply_operation(self, connection: Connection, guard) -> None:
        raise NotImplementedError

    def _check_previous_maintenance(self, connection: Connection, identity: BackendIdentity) -> None:
        require_completed_domain_maintenance(self.directory, identity, excluding=self.operation)
        main_path = (
            self.directory / "maintenance.json"
            if isinstance(self.connections.config, SQLiteConfig)
            else self.directory / identity.database_id / "maintenance.json"
        )
        if isinstance(self.connections.config, SQLiteConfig):
            main = read_maintenance_evidence(main_path, identity.database_id)
            if main is not None and main["state"] != "complete":
                raise MigrationError("recovery_required", "Complete the original full Server maintenance first.")
            self._select_source(main)
            if main is not None:
                verify_release_checksums(main["release_checksums"], self.bundle.checksums)
            verify_retained_objects(sqlite_inventory(connection), self.bundle.head, bundle=self.bundle, evidence=main)
        else:
            main = _Journal(self.directory, identity).read()
            self._select_source(main.model_dump() if main is not None else None)
            if main is not None:
                runner = MySQLMigrationRunner(
                    self.connections,
                    self.bundle,
                    configuration=self.configuration,
                    evidence_directory=self.directory,
                )
                revisions = connection.exec_driver_sql("SELECT version_num FROM pc_schema_revision").scalars().all()
                if len(revisions) != 1:
                    raise MigrationError("recovery_required", "The full Server migration head is missing or ambiguous.")
                runner.verify_completed_evidence(main, revisions[0])
