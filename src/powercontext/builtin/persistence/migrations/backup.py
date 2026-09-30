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

"""Native recovery points on dedicated maintenance connections.

The protocol is synchronous so async backends can use ``AsyncConnection.run_sync``.
No provider starts a business profile, verifies a user's manual backup, deletes a
recovery point, or restores a database automatically. Fork feature availability
and acceptance of the complete PC recovery workflow are separate facts.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict
from sqlalchemy import Connection, text
from sqlalchemy.exc import SQLAlchemyError

from .models import MigrationError, digest

BackupMethod = Literal["sqlite_online", "fork_database", "fork_table"]
ForkProduct = Literal["seekdb", "oceanbase_ai"]


class BackupContext(BaseModel):
    """The accepted target and maintenance window, with no credentials or rows."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    database_id: str
    source_revision: str | None
    bundle_checksum: str
    maintenance_window_id: str
    objects: tuple[str, ...] = ()
    writers_stopped: bool = False


class BackupCapabilities(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    available: bool
    method: BackupMethod | None = None
    product: str
    server_version: str | None = None
    server_identity_digest: str | None = None
    table_fork_available: bool = False
    database_fork_available: bool = False
    table_fork_minimum: str | None = None
    database_fork_minimum: str | None = None
    reasons: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()
    requires_stopped_writers: bool = True
    same_instance: bool = False
    experimental: bool = False
    recovery_workflow_accepted: bool = False


class BackupRef(BaseModel):
    """Persist outside the target database and preserve across migration retries."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    ref_id: str
    method: BackupMethod
    database_id: str
    source_revision: str | None
    bundle_checksum: str
    maintenance_window_id: str
    location: str
    objects: tuple[str, ...]
    object_mapping: dict[str, str] = {}
    checksum: str | None = None
    schema_fingerprint: str | None = None
    server_version: str | None = None
    server_identity_digest: str | None = None
    snapshot_boundary: str
    created_at: str


class BackupInspection(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    state: Literal["pending", "completed", "failed"]
    check_level: Literal["none", "metadata_checked", "integrity_checked"] = "none"
    reasons: tuple[str, ...] = ()
    # Neither an SQL success nor integrity_check is an actual recovery exercise.
    recovery_verified: bool = False


class RestorePlan(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    database_id: str
    scope: tuple[str, ...]
    steps: tuple[str, ...]
    requirements: tuple[str, ...]
    automatic: bool = False
    recovery_verified: bool = False


class BackupProvider(Protocol):
    def capabilities(self, context: BackupContext) -> BackupCapabilities: ...

    def create_backup(self, context: BackupContext) -> BackupRef: ...

    def inspect_backup(self, ref: BackupRef) -> BackupInspection: ...

    def restore_plan(self, ref: BackupRef) -> RestorePlan: ...


class SQLiteBackupProvider:
    """Create an independent Online Backup API file including committed WAL data."""

    def __init__(self, database: Path, *, directory: Path | None = None) -> None:
        self.database = database.expanduser().resolve()
        self.directory = (directory or self.database.with_name(self.database.name + ".pc-migration-backups")).resolve()

    def capabilities(self, context: BackupContext) -> BackupCapabilities:
        reasons: tuple[str, ...] = ()
        try:
            if not self.database.is_file():
                reasons = ("The target is not an existing SQLite file.",)
            else:
                with closing(self._read_database(self.database)) as connection:
                    connection.execute("PRAGMA schema_version").fetchone()
        except (OSError, sqlite3.Error):
            reasons = ("The existing SQLite target cannot be read.",)
        return BackupCapabilities(
            available=not reasons,
            method="sqlite_online" if not reasons else None,
            product="sqlite",
            server_version=sqlite3.sqlite_version,
            reasons=reasons,
            limitations=(
                "Large databases may take a long time and require additional storage.",
                "Extensions, external files, and business invariants require separate restore verification.",
            ),
            recovery_workflow_accepted=True,
        )

    def create_backup(self, context: BackupContext) -> BackupRef:
        if not context.writers_stopped:
            raise MigrationError("active_writers", "Stop all SQLite writers before creating the recovery point.")
        ref_id = _backup_ref_id(context, "sqlite_online")
        target = self.directory / f"{ref_id}.sqlite3"
        manifest = self.directory / f"{ref_id}.json"
        if target.exists() or manifest.exists():
            return self._original_backup(context, manifest)
        capabilities = self.capabilities(context)
        if not capabilities.available:
            raise MigrationError("backup_unsupported", "; ".join(capabilities.reasons))
        try:
            self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.close(os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
            with closing(self._read_database(self.database)) as source, closing(sqlite3.connect(target)) as destination:
                source.backup(destination)
                destination.execute("PRAGMA journal_mode=DELETE")
                if destination.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                    raise MigrationError("backup_failed", "The SQLite recovery point failed integrity checks.")
            _fsync_file(target)
            ref = BackupRef(
                ref_id=ref_id,
                method="sqlite_online",
                **context.model_dump(exclude={"writers_stopped"}),
                location=str(target),
                checksum=_file_checksum(target),
                server_version=sqlite3.sqlite_version,
                snapshot_boundary="Committed state copied by SQLite Online Backup API while writers are stopped.",
                created_at=datetime.now(UTC).isoformat(),
            )
            # Exclusive creation prevents a retry from replacing the first recovery point.
            with manifest.open("x", encoding="utf-8") as output:
                os.chmod(manifest, 0o600)
                output.write(ref.model_dump_json(indent=2))
                output.flush()
                os.fsync(output.fileno())
            _fsync_directory(self.directory)
        except (OSError, sqlite3.Error) as error:
            # A partial file is retained as evidence, never reported as completed.
            raise MigrationError("backup_failed", "Could not create and verify the SQLite recovery point.") from error
        else:
            return ref

    def _original_backup(self, context: BackupContext, manifest: Path) -> BackupRef:
        try:
            ref = BackupRef.model_validate_json(manifest.read_text(encoding="utf-8"))
            if not _matches_context(ref, context) or self.inspect_backup(ref).state != "completed":
                raise MigrationError("backup_failed", "The original recovery point is missing or has changed.")
        except (OSError, ValueError) as error:
            raise MigrationError(
                "backup_failed", "The original recovery point cannot be reused; do not replace it with a new snapshot."
            ) from error
        else:
            return ref

    def inspect_backup(self, ref: BackupRef) -> BackupInspection:
        if ref.method != "sqlite_online" or re.fullmatch(r"[a-f0-9]{32}", ref.ref_id) is None:
            return BackupInspection(state="failed", reasons=("The backup method does not match this provider.",))
        target = Path(ref.location)
        try:
            expected = self.directory / f"{ref.ref_id}.sqlite3"
            manifest = self.directory / f"{ref.ref_id}.json"
            if target.resolve() != expected or not target.is_file() or not manifest.is_file():
                return BackupInspection(
                    state="failed", reasons=("The original recovery point or manifest is missing.",)
                )
            persisted = BackupRef.model_validate_json(manifest.read_text(encoding="utf-8"))
            if persisted != ref or ref.checksum != _file_checksum(target):
                return BackupInspection(
                    state="failed", reasons=("The original recovery point or manifest has changed.",)
                )
            if any(Path(str(target) + suffix).exists() for suffix in ("-wal", "-journal")):
                return BackupInspection(
                    state="failed", reasons=("The recovery point has unrecorded journal sidecars.",)
                )
            with closing(sqlite3.connect(target.as_uri() + "?mode=ro&immutable=1", uri=True)) as connection:
                if connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                    return BackupInspection(state="failed", reasons=("The recovery point failed integrity checks.",))
            if ref.checksum != _file_checksum(target) or any(
                Path(str(target) + suffix).exists() for suffix in ("-wal", "-journal")
            ):
                return BackupInspection(state="failed", reasons=("The recovery point changed during inspection.",))
        except (OSError, sqlite3.Error, ValueError):
            return BackupInspection(state="failed", reasons=("The original recovery point cannot be inspected.",))
        return BackupInspection(state="completed", check_level="integrity_checked")

    def restore_plan(self, ref: BackupRef) -> RestorePlan:
        if self.inspect_backup(ref).state != "completed":
            raise MigrationError("backup_failed", "Inspect the original SQLite recovery point before planning restore.")
        return RestorePlan(
            database_id=ref.database_id,
            scope=ref.objects,
            requirements=(
                "Keep every writer stopped and hold the database maintenance lock.",
                "Use a SQLite version and extensions compatible with the backup schema.",
                "Explicit approval is required before replacing the configured database.",
            ),
            steps=(
                "Preserve the current database and its WAL/SHM files as failure evidence.",
                "Recheck the recorded checksum and absence of WAL/journal sidecars; keep the backup unchanged during restore.",
                "Open the recorded backup with mode=ro&immutable=1 and copy it to a separate file with the Online Backup API.",
                "Check integrity, schema revision, required extensions, queues, and business invariants.",
                "With all connections closed, explicitly replace the configured database and remove only stale WAL/SHM sidecars.",
                "Start a compatible application and verify readiness before restoring traffic; retain the original backup.",
            ),
        )

    @staticmethod
    def _read_database(path: Path) -> sqlite3.Connection:
        return sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)


class ForkWorkflowAcceptance(BaseModel):
    """Release-owned evidence for a tested engine, schema, DDL, and recovery path.

    This is supplied by a backend adapter, never by CLI flags or user confirmation.
    The default provider has no accepted workflow: a feature version alone cannot
    enable PC automatic backup. Evidence must cover restoring the configured name,
    especially embedded seekdb's fixed ``test`` database.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    product: ForkProduct
    server_version: str
    server_identity_digest: str
    method: Literal["fork_database", "fork_table"]
    schema_fingerprint: str
    bundle_checksum: str
    covered_objects: tuple[str, ...]
    evidence_reference: str
    restore_steps: tuple[str, ...]
    ddl_verified: bool = False
    restart_verified: bool = False
    restore_to_original_database: bool = False
    cross_table_consistency_verified: bool = False
    events_not_supported: bool = False


class ForkBackupProvider:
    """Native Fork recovery points, using an already pinned maintenance session.

    The caller owns engine configuration and lifecycle. Capabilities perform only
    reads. No unknown engine, unsupported object, missing grant, or unaccepted
    recovery path falls back to copying files or to physical backup.
    """

    def __init__(
        self,
        connection: Connection,
        *,
        database_name: str,
        expected_product: ForkProduct,
        directory: Path,
        acceptance: ForkWorkflowAcceptance | None = None,
    ) -> None:
        self.connection = connection
        self.database_name = database_name
        self.expected_product = expected_product
        self.directory = directory.expanduser().resolve()
        self.acceptance = acceptance

    def capabilities(self, context: BackupContext) -> BackupCapabilities:
        table_minimum, database_minimum = _fork_minimums(self.expected_product)
        common: dict[str, Any] = {
            "table_fork_minimum": _format_version(table_minimum),
            "database_fork_minimum": _format_version(database_minimum),
            "same_instance": True,
            "experimental": True,
            "limitations": (
                "Fork retains a same-instance recovery point, not independent disaster recovery.",
                "Large databases may take a long time; retained objects and later writes consume storage.",
                "Fork objects remain after migration and retry; cleanup requires a later explicit operation.",
                "Metadata checks do not prove that this recovery point was successfully restored.",
            ),
        }
        product: ForkProduct | None = None
        version: tuple[int, int, int] | None = None
        identity_digest: str | None = None
        table_available = False
        database_available = False
        try:
            product, version, identity_digest = self._server_identity()
            if product != self.expected_product or version is None:
                return BackupCapabilities(
                    available=False,
                    product=product or "unknown",
                    server_version=None if version is None else _format_version(version),
                    server_identity_digest=identity_digest,
                    reasons=("The actual server product or version does not match the selected backend.",),
                    **common,
                )
            table_available = version >= table_minimum
            database_available = version >= database_minimum
            reasons = self._eligibility(context, product, version, identity_digest, table_available, database_available)
            method = None if reasons else self.acceptance.method if self.acceptance is not None else None
            return BackupCapabilities(
                available=not reasons,
                method=method,
                product=product,
                server_version=_format_version(version),
                server_identity_digest=identity_digest,
                table_fork_available=table_available,
                database_fork_available=database_available,
                reasons=reasons,
                recovery_workflow_accepted=not reasons,
                **common,
            )
        except (SQLAlchemyError, ValueError, TypeError):
            return BackupCapabilities(
                available=False,
                product=product or "unknown",
                server_version=None if version is None else _format_version(version),
                server_identity_digest=identity_digest,
                table_fork_available=table_available,
                database_fork_available=database_available,
                reasons=("Server product, permissions, objects, or recovery conditions could not be verified.",),
                **common,
            )

    def create_backup(self, context: BackupContext) -> BackupRef:
        if not context.writers_stopped:
            raise MigrationError("active_writers", "Stop every writer before creating a Fork recovery point.")
        if self.acceptance is not None:
            original_id = _backup_ref_id(context, self.acceptance.method)
            original_manifest = self.directory / f"{original_id}.json"
            if original_manifest.exists():
                return self._original_backup(context, original_manifest)
        capability = self.capabilities(context)
        if not capability.available or capability.method is None:
            raise MigrationError("backup_unsupported", "; ".join(capability.reasons))
        ref_id = _backup_ref_id(context, capability.method)
        backup_name = "pc_backup_" + ref_id
        mapping: dict[str, str] = {}
        manifest = self.directory / f"{ref_id}.json"
        try:
            self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            # Reserve the external name before any nontransactional DDL. On
            # failure its pending record names every object that may exist.
            inventory, fingerprint = self._inventory()
            if capability.method == "fork_database":
                mapping = {name: f"{backup_name}.{name}" for name in inventory}
                statements = (
                    f"FORK DATABASE {_quote_identifier(self.database_name)} TO {_quote_identifier(backup_name)}",
                )
                location = backup_name
                boundary = "All database base tables share the engine's Fork snapshot."
            else:
                mapping = {name: f"{self.database_name}.{backup_name}_{index}" for index, name in enumerate(inventory)}
                statements = tuple(
                    f"FORK TABLE {_qualified(self.database_name, name)} TO {_qualified(self.database_name, backup_name + '_' + str(index))}"
                    for index, name in enumerate(inventory)
                )
                location = self.database_name
                boundary = (
                    "Separate table snapshots; consistency relies on stopped writers and accepted dependency coverage."
                )
            ref = BackupRef(
                ref_id=ref_id,
                method=capability.method,
                **context.model_dump(exclude={"writers_stopped", "objects"}),
                location=location,
                objects=tuple(inventory),
                object_mapping=mapping,
                schema_fingerprint=fingerprint,
                server_version=capability.server_version,
                server_identity_digest=capability.server_identity_digest,
                snapshot_boundary=boundary,
                created_at=datetime.now(UTC).isoformat(),
            )
            self._write_manifest(manifest, ref, state="pending", exclusive=True)
            for statement in statements:
                self.connection.exec_driver_sql(statement)
                # Native Fork is nontransactional; the dedicated connection must
                # not carry business DML which could be accidentally committed.
                self.connection.commit()
            self._write_manifest(manifest, ref, state="created", exclusive=False)
            inspection = self.inspect_backup(ref)
            if inspection.state != "completed":
                raise MigrationError(
                    "backup_failed", "The Fork recovery point is incomplete; preserve all Fork objects."
                )
        except (OSError, SQLAlchemyError, ValueError) as error:
            raise MigrationError(
                "backup_failed",
                "Fork creation or verification failed; preserve the external manifest and Fork objects.",
            ) from error
        else:
            return ref

    def _original_backup(self, context: BackupContext, manifest: Path) -> BackupRef:
        try:
            content = json.loads(manifest.read_text(encoding="utf-8"))
            ref = BackupRef.model_validate(content["backup"])
            if not _matches_context(ref, context) or self.inspect_backup(ref).state != "completed":
                raise MigrationError(
                    "backup_failed", "The original Fork point is incomplete or has changed; preserve it."
                )
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise MigrationError("backup_failed", "The original Fork manifest cannot be reused safely.") from error
        else:
            return ref

    def inspect_backup(self, ref: BackupRef) -> BackupInspection:
        if ref.method not in {"fork_database", "fork_table"} or re.fullmatch(r"[a-f0-9]{32}", ref.ref_id) is None:
            return BackupInspection(state="failed", reasons=("The backup method does not match this provider.",))
        try:
            manifest = self.directory / f"{ref.ref_id}.json"
            content = json.loads(manifest.read_text(encoding="utf-8"))
            if BackupRef.model_validate(content["backup"]) != ref:
                return BackupInspection(state="failed", reasons=("The original Fork manifest has changed.",))
            if content["state"] == "pending":
                return BackupInspection(
                    state="pending", reasons=("Fork completion was not recorded; inspect before recovery.",)
                )
            if content["state"] != "created":
                return BackupInspection(state="failed", reasons=("Fork creation did not reach the completed state.",))
            product, version, identity_digest = self._server_identity()
            if (
                product != self.expected_product
                or version is None
                or _format_version(version) != ref.server_version
                or identity_digest != ref.server_identity_digest
            ):
                return BackupInspection(state="failed", reasons=("The Fork server identity or version has changed.",))
            for destination in ref.object_mapping.values():
                schema, table_name = destination.split(".", 1)
                result = self.connection.execute(
                    text(
                        "SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA=:schema AND TABLE_NAME=:table"
                    ),
                    {"schema": schema, "table": table_name},
                ).all()
                if result != [(table_name,)]:
                    return BackupInspection(
                        state="failed", reasons=("A retained Fork object is missing or inaccessible.",)
                    )
                # Opening the snapshot is a metadata/access check, not a restore exercise.
                self.connection.exec_driver_sql(
                    f"SELECT 1 FROM {_qualified(schema, table_name)} LIMIT 0"  # noqa: S608 -- quoted identifiers
                )
            if set(ref.object_mapping) != set(ref.objects):
                return BackupInspection(
                    state="failed", reasons=("The Fork mapping does not cover its declared scope.",)
                )
            return BackupInspection(state="completed", check_level="metadata_checked")
        except (OSError, SQLAlchemyError, ValueError, KeyError, TypeError):
            return BackupInspection(
                state="failed", reasons=("The retained Fork point or manifest cannot be inspected.",)
            )

    def restore_plan(self, ref: BackupRef) -> RestorePlan:
        if self.inspect_backup(ref).state != "completed":
            raise MigrationError("backup_failed", "The Fork recovery point is not ready for a restore plan.")
        acceptance = self.acceptance
        if (
            acceptance is None
            or not acceptance.restore_to_original_database
            or acceptance.server_identity_digest != ref.server_identity_digest
            or acceptance.schema_fingerprint != ref.schema_fingerprint
            or acceptance.bundle_checksum != ref.bundle_checksum
            or acceptance.method != ref.method
            or set(acceptance.covered_objects) != set(ref.objects)
        ):
            raise MigrationError(
                "backup_unsupported", "No accepted restore path to the configured database is available."
            )
        return RestorePlan(
            database_id=ref.database_id,
            scope=ref.objects,
            requirements=(
                "Keep all writers stopped and preserve the original Fork objects.",
                "Restore only the confirmed database and dependency scope, never the entire shared tenant.",
                "Use the engine version, permissions, and object types covered by adapter acceptance.",
                "Obtain explicit approval before replacing any current object; application downgrade alone is insufficient.",
            ),
            steps=acceptance.restore_steps,
        )

    def _server_identity(self) -> tuple[ForkProduct | None, tuple[int, int, int] | None, str]:
        version = self.connection.exec_driver_sql("SELECT VERSION()").scalar_one()
        comment = self.connection.exec_driver_sql("SELECT @@version_comment").scalar_one()
        product, number = identify_fork_server(str(version), str(comment))
        return product, number, digest([version, comment])

    def _eligibility(
        self,
        context: BackupContext,
        product: ForkProduct,
        version: tuple[int, int, int],
        identity_digest: str,
        table_available: bool,
        database_available: bool,
    ) -> tuple[str, ...]:
        if not table_available:
            return ("The actual engine predates this product's Fork Table minimum version.",)
        if self.connection.dialect.name not in {"mysql", "oceanbase"}:
            return ("Fork requires a supported MySQL-compatible maintenance connection.",)
        current = self.connection.exec_driver_sql("SELECT DATABASE()").scalar_one()
        if current != self.database_name or self.database_name.lower() in {
            "oceanbase",
            "mysql",
            "information_schema",
            "sys",
        }:
            return ("The maintenance connection does not target an eligible user database.",)
        if product == "oceanbase_ai":
            rows = self.connection.exec_driver_sql("SHOW VARIABLES LIKE 'ob_compatibility_mode'").all()
            if not rows or str(rows[0][1]).upper() != "MYSQL":
                return ("OceanBase Fork requires a verified MySQL user tenant.",)
        inventory, fingerprint = self._inventory()
        if not set(context.objects).issubset(inventory):
            return ("The affected object and dependency scope is not covered by eligible base tables.",)
        return self._acceptance_reasons(
            context, product, version, identity_digest, inventory, fingerprint, database_available
        )

    def _acceptance_reasons(
        self,
        context: BackupContext,
        product: ForkProduct,
        version: tuple[int, int, int],
        identity_digest: str,
        inventory: tuple[str, ...],
        fingerprint: str,
        database_available: bool,
    ) -> tuple[str, ...]:
        acceptance = self.acceptance
        if acceptance is None:
            return (
                "PC has no accepted object, DDL, restart, and configured-target recovery workflow for this engine.",
            )
        if (
            acceptance.product != product
            or acceptance.server_version != _format_version(version)
            or acceptance.server_identity_digest != identity_digest
            or acceptance.schema_fingerprint != fingerprint
            or acceptance.bundle_checksum != context.bundle_checksum
            or set(acceptance.covered_objects) != set(inventory)
            or not acceptance.evidence_reference
            or not acceptance.restore_steps
            or not acceptance.ddl_verified
            or not acceptance.restart_verified
            or not acceptance.restore_to_original_database
        ):
            return ("Adapter recovery acceptance does not match this actual engine, schema, or required DDL.",)
        if acceptance.method == "fork_database" and not database_available:
            return ("The actual engine predates this product's Fork Database minimum version.",)
        if acceptance.method == "fork_table" and not acceptance.cross_table_consistency_verified:
            return ("Table Fork dependency coverage and stopped-write consistency have not been accepted.",)
        if not self._has_required_grants(acceptance.method):
            return (
                "Required Fork SELECT/CREATE permissions could not be verified from the current user's direct grants.",
            )
        return ()

    def _inventory(self) -> tuple[tuple[str, ...], str]:
        tables = self.connection.execute(
            text(
                "SELECT TABLE_NAME, TABLE_TYPE FROM information_schema.TABLES WHERE TABLE_SCHEMA=:schema ORDER BY TABLE_NAME"
            ),
            {"schema": self.database_name},
        ).all()
        if any(str(row[1]).upper() != "BASE TABLE" for row in tables):
            raise ValueError("Non-table objects are not covered by this Fork adapter")  # noqa: TRY003
        for query in (
            "SELECT COUNT(*) FROM information_schema.TRIGGERS WHERE TRIGGER_SCHEMA=:schema",
            "SELECT COUNT(*) FROM information_schema.ROUTINES WHERE ROUTINE_SCHEMA=:schema",
            "SELECT COUNT(*) FROM information_schema.EVENTS WHERE EVENT_SCHEMA=:schema",
        ):
            try:
                found = self.connection.execute(text(query), {"schema": self.database_name}).scalar_one()
            except SQLAlchemyError as error:
                # A verified seekdb adapter may account for its unsupported
                # Event Scheduler. Unknown catalogs and permission errors do
                # not mean that there are no affected objects.
                arguments = getattr(getattr(error, "orig", None), "args", ())
                code = arguments[0] if arguments else None
                if (
                    "information_schema.EVENTS " in query
                    and code in {1109, 1146}
                    and self.expected_product == "seekdb"
                    and self.acceptance is not None
                    and self.acceptance.events_not_supported
                ):
                    continue
                raise
            if found:
                raise ValueError("Non-table objects need a separately accepted reconstruction path")  # noqa: TRY003
        definitions: dict[str, str] = {}
        for row in tables:
            name = str(row[0])
            definition = self.connection.exec_driver_sql(
                f"SHOW CREATE TABLE {_qualified(self.database_name, name)}"
            ).one()
            definitions[name] = re.sub(r"\bAUTO_INCREMENT=\d+\b", "", str(definition[1]))
        return tuple(definitions), digest(definitions)

    def _has_required_grants(self, method: BackupMethod) -> bool:
        grants = [str(row[0]) for row in self.connection.exec_driver_sql("SHOW GRANTS").all()]
        source = False
        create = False
        for grant in grants:
            match = re.match(r"GRANT (.+?) ON (.+?) TO ", grant, re.IGNORECASE)
            if match is None:
                continue
            privileges, scope = match.groups()
            privileges = privileges.upper().split(", ")
            all_privileges = "ALL PRIVILEGES" in privileges
            # Role grants and wildcard patterns are deliberately not interpreted.
            # Failing closed is preferable to guessing effective permissions.
            if scope in {"*.*", f"{_quote_identifier(self.database_name)}.*"}:
                source = source or all_privileges or "SELECT" in privileges
            if scope == "*.*" or (method == "fork_table" and scope == f"{_quote_identifier(self.database_name)}.*"):
                create = create or all_privileges or "CREATE" in privileges
        return source and create

    @staticmethod
    def _write_manifest(path: Path, ref: BackupRef, *, state: str, exclusive: bool) -> None:
        content = json.dumps({"state": state, "backup": ref.model_dump()}, indent=2, ensure_ascii=False)
        if exclusive:
            with path.open("x", encoding="utf-8") as output:
                os.chmod(path, 0o600)
                output.write(content)
                output.flush()
                os.fsync(output.fileno())
        else:
            temporary = path.with_suffix(".pending")
            with temporary.open("x", encoding="utf-8") as output:
                os.chmod(temporary, 0o600)
                output.write(content)
                output.flush()
                os.fsync(output.fileno())
            temporary.replace(path)
        _fsync_directory(path.parent)


def identify_fork_server(version: str, comment: str) -> tuple[ForkProduct | None, tuple[int, int, int] | None]:
    """Read product-qualified engine versions, never the leading MySQL version.

    Actual embedded seekdb 1.4 reports MySQL 5.7 and OceanBase 4.3 in these
    strings; neither is the seekdb feature version.
    """
    identity = version + " " + comment
    if "seekdb" in identity.lower():
        patterns = (
            r"seekdb[\s_-]*(?:[vr])?(\d+)\.(\d+)\.(\d+)",
            r"seekdb\s*\(r(\d+)\.(\d+)\.(\d+)",
        )
        product: ForkProduct = "seekdb"
    elif "oceanbase" in identity.lower():
        patterns = (r"OceanBase(?:_CE|_EE|\s+AI)?[\s_-]*(?:v)?(\d+)\.(\d+)\.(\d+)",)
        product = "oceanbase_ai"
    else:
        return None, None
    for pattern in patterns:
        match = re.search(pattern, identity, re.IGNORECASE)
        if match is not None:
            return product, (int(match[1]), int(match[2]), int(match[3]))
    return product, None


def _fork_minimums(product: ForkProduct) -> tuple[tuple[int, int, int], tuple[int, int, int]]:
    return ((1, 1, 0), (1, 2, 0)) if product == "seekdb" else ((4, 6, 2), (4, 6, 2))


def _format_version(version: tuple[int, int, int]) -> str:
    return ".".join(str(part) for part in version)


def _quote_identifier(value: str) -> str:
    if not value or "\x00" in value or "." in value:
        raise ValueError("Invalid database or table identifier")  # noqa: TRY003
    return "`" + value.replace("`", "``") + "`"


def _qualified(database: str, table_name: str) -> str:
    return _quote_identifier(database) + "." + _quote_identifier(table_name)


def _file_checksum(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def _backup_ref_id(context: BackupContext, method: BackupMethod) -> str:
    # Reuse even if the process died after backup completion but before the
    # executor persisted its reference. A new maintenance window gets a new ID.
    return digest({
        "database_id": context.database_id,
        "maintenance_window_id": context.maintenance_window_id,
        "method": method,
    })[:32]


def _matches_context(ref: BackupRef, context: BackupContext) -> bool:
    return (
        ref.database_id == context.database_id
        and ref.source_revision == context.source_revision
        and ref.bundle_checksum == context.bundle_checksum
        and ref.maintenance_window_id == context.maintenance_window_id
        and set(context.objects).issubset(ref.objects)
    )


def _fsync_file(path: Path) -> None:
    with path.open("rb") as source:
        os.fsync(source.fileno())


def _fsync_directory(path: Path) -> None:
    if os.name != "nt":
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
