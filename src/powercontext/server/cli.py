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

"""CLI commands owned by the ready-to-run service entry point."""

from __future__ import annotations

import asyncio
import json
import signal
from collections.abc import Callable, Iterator
from contextlib import AsyncExitStack, ExitStack, contextmanager, nullcontext
from pathlib import Path
from sqlite3 import SQLITE_CANTOPEN
from typing import TYPE_CHECKING, Annotated, Any, Literal, Never

import typer
from pydantic import ValidationError
from sqlalchemy import inspect
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError
from typing_extensions import override

from powercontext.builtin.persistence.bootstrap import (
    DatabaseAccessError,
    DatabaseCreationError,
    MissingDatabaseError,
)
from powercontext.builtin.persistence.oceanbase import OceanBaseConfig, OceanBaseProfile
from powercontext.builtin.persistence.oceanbase.atomic_memory_index import OceanBaseAtomicMemoryIndex
from powercontext.builtin.persistence.processing_migration import (
    apply_processing_migration,
    plan_processing_migration,
    verify_processing_migration,
)
from powercontext.builtin.persistence.seekdb import (
    LegacySeekDBDatabaseError,
    SeekDBConfig,
    SeekDBInspectionError,
    SeekDBProfile,
)
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.persistence.sqlite.atomic_memory_index import SQLiteAtomicMemoryIndex
from powercontext.builtin.runtime.atomic_memory_rebuild import rebuild_atomic_memory_projection
from powercontext.builtin.runtime.composition import _embedding_models, open_builtin_runtime
from powercontext.builtin.runtime.config import BuiltinConfig
from powercontext.builtin.runtime.processing_registry import canonical_processing_manifest
from powercontext.cli.env_file import environment_context
from powercontext.cli.inference_notice import write_inference_capability_notice
from powercontext.server import database_migration
from powercontext.server.app import access_readiness_checks
from powercontext.server.authz import PrincipalRef
from powercontext.server.authz.composition import open_builtin_access_control
from powercontext.server.background_metrics import serve_background_metrics
from powercontext.server.configuration import (
    ServerConfigurationError,
    resolve_server_environment_file,
    server_settings_context,
)
from powercontext.server.database_migration import app as database_migration_app
from powercontext.server.factory import _ServerReadinessProbe, create_background_metrics_app, create_server_app
from powercontext.server.identity import open_server_identity_repository
from powercontext.server.logging import configure_server_logging, shutdown_server_logging, worker_logging
from powercontext.server.metrics import ServerMetrics
from powercontext.server.processing_security import build_worker_security
from powercontext.server.settings import (
    MissingAuthenticationProviderError,
    MissingBearerTokenError,
    ServerSettings,
    UnauthenticatedNonLoopbackBindError,
)
from powercontext.server.tracing import configure_server_tracing
from powercontext.server.worker_tracing import WorkerTracing

if TYPE_CHECKING:
    from socket import socket

    from powercontext.builtin.persistence.migrations.atomic_memory_references import CandidateDecision
    from powercontext.builtin.persistence.migrations.atomic_memory_v1 import OwnerDecision

HELP_OPTION_NAMES = ("-h", "--help")

# Shown when the merged bind fails the unauthenticated-non-loopback policy. It repeats the
# operator's concrete levers -- authenticate, stay on loopback, or opt in via the full env var --
# instead of surfacing pydantic's internal validation dump (see ``_friendly_bad_parameter``).
_UNSAFE_BIND_CLI_MESSAGE = (
    "refusing to bind an unauthenticated Server to a non-loopback address; "
    "enable authentication, keep the bind on loopback, or set "
    "POWERCONTEXT_SERVER_ALLOW_UNAUTHENTICATED_NON_LOOPBACK=true to opt in"
)

# Shown when authentication is enabled without a token; names the concrete env-var levers the
# operator can set instead of surfacing pydantic's internal validation dump.
_MISSING_BEARER_CLI_MESSAGE = (
    "authentication is enabled but no bearer token is configured; "
    "set POWERCONTEXT_SERVER_AUTH_TOKEN=... or disable Access Control with "
    "POWERCONTEXT_SERVER_ACCESS_MODE=disabled"
)

app = typer.Typer(
    name="server",
    context_settings={"help_option_names": HELP_OPTION_NAMES},
    help="Run a configured PowerContext service.",
    no_args_is_help=True,
)
app.add_typer(database_migration_app, name="db-migrate")


@app.callback()
def main() -> None:
    """Manage the PowerContext service process."""


@app.command("processing-migrate")
def processing_migrate(
    action: Annotated[Literal["plan", "apply", "verify"], typer.Option(help="Offline migration action.")] = "plan",
    env_file: Annotated[Path | None, typer.Option(help="Load deployment settings from this environment file.")] = None,
    maintenance_confirmed: Annotated[
        bool,
        typer.Option(help="Confirm all old background candidates, Workers, writes and explicit triggers are stopped."),
    ] = False,
    migration_id: Annotated[
        str, typer.Option(help="Stable resume ID; use a new ID for an offline mode switch.")
    ] = "rfc1515",
    batch_size: Annotated[int, typer.Option(min=1, max=10000, help="Maximum rows committed per migration step.")] = 100,
    evidence_dir: database_migration.EvidenceDir = None,
    lock_coordination: database_migration.LockCoordination = None,
    backup: Annotated[
        database_migration.BackupPolicy | None, typer.Option(help="Managed storage backup policy.")
    ] = None,
    plan_id: Annotated[str | None, typer.Option(help="Exact managed Processing plan to accept.")] = None,
    yes: Annotated[bool, typer.Option("--yes", help="Accept the managed Processing plan.")] = False,
    backup_confirmed: Annotated[bool, typer.Option(help="Declare a manual backup exists.")] = False,
    accept_no_backup: Annotated[bool, typer.Option(help="Accept the no-backup risk.")] = False,
    backup_ref: Annotated[str | None, typer.Option(help="Operator manual backup reference.")] = None,
    acknowledge_previous_run: Annotated[
        str | None, typer.Option(help="OceanBase token confirming the previous remote maintenance ended.")
    ] = None,
) -> None:
    """Plan, apply or verify the resumable artifact-processing schema migration."""

    if action == "apply" and not maintenance_confirmed:
        raise typer.BadParameter("apply requires --maintenance-confirmed after stopping old workers and writes")  # noqa: TRY003
    env_file = resolve_server_environment_file(env_file, discover=True)
    with database_migration._operator_errors(), server_settings_context(env_file=env_file) as settings:
        managed = _managed_storage(settings.database)
    if managed:
        if action == "plan":
            database_migration.plan(
                env_file=env_file,
                evidence_dir=evidence_dir,
                lock_coordination=lock_coordination,
                backup=backup or "auto",
                bundle="processing",
                migration_id=migration_id,
                batch_size=batch_size,
            )
        elif action == "verify":
            database_migration.verify(
                env_file=env_file,
                evidence_dir=evidence_dir,
                lock_coordination=lock_coordination,
                bundle="processing",
                migration_id=migration_id,
                batch_size=batch_size,
            )
        else:
            database_migration.apply(
                env_file=env_file,
                evidence_dir=evidence_dir,
                lock_coordination=lock_coordination,
                bundle="processing",
                migration_id=migration_id,
                batch_size=batch_size,
                backup=backup,
                plan_id=plan_id,
                yes=yes,
                maintenance_confirmed=maintenance_confirmed,
                backup_confirmed=backup_confirmed,
                accept_no_backup=accept_no_backup,
                backup_ref=backup_ref,
                acknowledge_previous_run=acknowledge_previous_run,
            )
        return
    with server_settings_context(env_file=env_file) as settings:
        try:
            ready = asyncio.run(
                _processing_maintenance(settings, action, migration_id=migration_id, batch_size=batch_size)
            )
        except MissingDatabaseError as error:
            if action == "apply":
                raise
            raise typer.BadParameter(_missing_read_only_database(error)) from error
        except (SeekDBInspectionError, LegacySeekDBDatabaseError) as error:
            raise typer.BadParameter(str(error)) from error
        except (DatabaseAccessError, DatabaseCreationError) as error:
            _report_database_guidance(error)
    if not ready:
        raise typer.Exit(code=1)


def _report_database_guidance(error: DatabaseAccessError | DatabaseCreationError) -> Never:
    """Print provisioning guidance verbatim; the rich error panel truncates long statements."""
    typer.echo(str(error), err=True)
    raise typer.Exit(code=2)


def _missing_read_only_database(error: MissingDatabaseError) -> str:
    return (
        f"plan and verify require an existing database: {error.database_name!r} is missing. "
        "Provision it first, or start the Server once to create the configured database"
    )


def _managed_storage(
    database: SQLiteConfig | SeekDBConfig | OceanBaseConfig, *, allow_historical: bool = False
) -> bool:
    """Inspect ownership before a compatibility command may open a writable profile."""
    from powercontext.builtin.persistence.migrations.connections import MaintenanceConnections
    from powercontext.server.identity import SERVER_IDENTITY_TABLE

    def owned(connection):
        if connection is None:
            return False
        tables = inspect(connection).get_table_names()
        if "pc_schema_revision" in tables:
            return True
        # Ownership is independent of release and optional configuration. The
        # shared runner must validate the complete source before any writes;
        # an unknown or misconfigured Server must not fall back to SDK upkeep.
        server_owned = SERVER_IDENTITY_TABLE.name in tables
        if server_owned and not allow_historical:
            raise database_migration.MigrationError(
                "migration_required",
                "Complete the registered full Server upgrade before partial maintenance.",
            )
        return server_owned

    if isinstance(database, SQLiteConfig) and not database.is_persistent:
        return False
    if not isinstance(database, SQLiteConfig) or not make_url(database.url).query:
        return MaintenanceConnections(database).run(lambda connection, identity, guard: owned(connection))

    async def inspect_sqlite():
        try:
            async with (
                SQLiteProfile.open_readonly(database) as profile,
                profile.database.transaction() as connection,
            ):
                return await connection.run_sync(owned)
        except OperationalError as error:
            code = getattr(error.orig, "sqlite_errorcode", None)
            if isinstance(code, int) and code & 0xFF == SQLITE_CANTOPEN:
                return False
            raise

    return asyncio.run(inspect_sqlite())


async def _processing_maintenance(
    settings: ServerSettings,
    action: Literal["plan", "apply", "verify"],
    *,
    migration_id: str,
    batch_size: int,
) -> bool:
    config = BuiltinConfig(
        code=settings.code,
        runtime=settings.runtime,
        database=settings.database,
        inference=settings.inference,
        handoff_report=settings.handoff_report,
        external_skills=settings.external_skills,
    )
    manifest = canonical_processing_manifest(config)
    database = settings.database
    # A plan or verify only inspects the configured database: opening it read-only
    # keeps the command from creating the directory, the file or a journal mode it
    # reports on.
    read_only = action in {"plan", "verify"}
    if isinstance(database, SQLiteConfig):
        if not database.is_persistent:
            raise typer.BadParameter("offline migration requires a persistent database")  # noqa: TRY003
        opened = SQLiteProfile.open_readonly(database) if read_only else SQLiteProfile.open(database, tables=())
    elif isinstance(database, OceanBaseConfig):
        opened = (
            OceanBaseProfile.open_existing(database, tables=())
            if read_only
            else OceanBaseProfile.open(database, tables=())
        )
    elif isinstance(database, SeekDBConfig):
        opened = (
            SeekDBProfile.open_existing(database, tables=()) if read_only else SeekDBProfile.open(database, tables=())
        )
    else:
        raise typer.BadParameter("unsupported migration database")  # noqa: TRY003
    async with opened as profile:
        if action == "plan":
            async with profile.database.transaction() as connection:
                plan = await plan_processing_migration(connection, config_manifest=manifest)
            typer.echo(plan.model_dump_json())
            return True
        if action == "apply":
            while True:
                async with profile.database.transaction() as connection:
                    progress = await apply_processing_migration(
                        connection,
                        config_manifest=manifest,
                        migration_id=migration_id,
                        batch_size=batch_size,
                    )
                if progress.complete:
                    break
        async with profile.database.transaction() as connection:
            verification = await verify_processing_migration(connection, config_manifest=manifest)
        typer.echo(verification.model_dump_json())
        return verification.ready


@app.command("identity-reset")
def identity_reset(
    env_file: Annotated[Path | None, typer.Option(help="Load deployment settings from this environment file.")] = None,
    maintenance_confirmed: Annotated[
        bool,
        typer.Option(help="Confirm every Server process using the primary database is stopped."),
    ] = False,
) -> None:
    """Rotate the durable Server identity after cloning a stopped deployment."""

    if not maintenance_confirmed:
        raise typer.BadParameter(  # noqa: TRY003
            "identity reset requires --maintenance-confirmed after stopping every Server process"
        )
    with server_settings_context(env_file=env_file) as settings:
        if isinstance(settings.database, SQLiteConfig) and not settings.database.is_persistent:
            raise typer.BadParameter("identity reset requires a persistent database")  # noqa: TRY003
        try:
            server_id = asyncio.run(_reset_server_identity(settings))
        except LegacySeekDBDatabaseError as error:
            raise typer.BadParameter(str(error)) from error
        except (DatabaseAccessError, DatabaseCreationError) as error:
            _report_database_guidance(error)
    typer.echo(server_id)


async def _reset_server_identity(settings: ServerSettings) -> str:
    async with open_server_identity_repository(settings.database) as repository:
        return await repository.rotate()


@app.command("atomic-memory-migrate")
def atomic_memory_migrate(  # noqa: C901
    action: Annotated[
        Literal["plan", "apply", "verify"], typer.Option(help="Offline domain migration action.")
    ] = "plan",
    env_file: Annotated[Path | None, typer.Option(help="Load the deployment's settings.")] = None,
    maintenance_confirmed: Annotated[
        bool, typer.Option(help="Confirm every old API, host and Worker is stopped and input writes are paused.")
    ] = False,
    decisions: Annotated[
        Path | None,
        typer.Option(help="JSON decisions replacing Candidate evidence that cited only whole Memory collections."),
    ] = None,
    snapshot_budget_mib: Annotated[
        int,
        typer.Option(
            min=1, help="Conservative processing-snapshot working-set budget in MiB, not total process memory."
        ),
    ] = 256,
    owner_decisions: Annotated[
        Path | None,
        typer.Option(help="JSON attestations establishing Owners for ownerless legacy Memory entries."),
    ] = None,
    evidence_dir: database_migration.EvidenceDir = None,
    lock_coordination: database_migration.LockCoordination = None,
    backup: Annotated[
        database_migration.BackupPolicy | None, typer.Option(help="Managed storage backup policy.")
    ] = None,
    plan_id: Annotated[str | None, typer.Option(help="Exact managed Server plan to accept.")] = None,
    yes: Annotated[bool, typer.Option("--yes", help="Accept the managed Server plan.")] = False,
    backup_confirmed: Annotated[bool, typer.Option(help="Declare a manual backup exists.")] = False,
    accept_no_backup: Annotated[bool, typer.Option(help="Accept the no-backup risk.")] = False,
    backup_ref: Annotated[str | None, typer.Option(help="Operator manual backup reference.")] = None,
    acknowledge_previous_run: Annotated[
        str | None, typer.Option(help="OceanBase token confirming the previous remote maintenance ended.")
    ] = None,
) -> None:
    """Archive Memory v1, convert its history and references to Atomic Memory, and remove it from public tables."""

    from powercontext.builtin.persistence.migrations.atomic_memory_references import load_decisions
    from powercontext.builtin.persistence.migrations.atomic_memory_v1 import load_owner_decisions

    if action == "apply" and not maintenance_confirmed:
        raise typer.BadParameter("apply requires --maintenance-confirmed after stopping every old writer")  # noqa: TRY003
    try:
        loaded = {} if decisions is None else load_decisions(json.loads(decisions.read_text(encoding="utf-8")))
        loaded_owners = (
            {}
            if owner_decisions is None
            else load_owner_decisions(json.loads(owner_decisions.read_text(encoding="utf-8")))
        )
    except (OSError, ValueError, TypeError, KeyError) as error:
        raise typer.BadParameter(f"invalid decision file: {error}") from error  # noqa: TRY003
    env_file = resolve_server_environment_file(env_file, discover=True)
    with database_migration._operator_errors(), server_settings_context(env_file=env_file) as settings:
        managed = _managed_storage(settings.database, allow_historical=True)
    if managed:
        if decisions is not None or owner_decisions is not None:
            raise typer.BadParameter(  # noqa: TRY003
                "decision files apply only to the unmanaged legacy Memory conversion, not the registered Server chain"
            )
        if action == "plan":
            database_migration.plan(
                env_file=env_file,
                evidence_dir=evidence_dir,
                lock_coordination=lock_coordination,
                backup=backup or "auto",
                bundle="server",
            )
        elif action == "verify":
            database_migration.verify(
                env_file=env_file,
                evidence_dir=evidence_dir,
                lock_coordination=lock_coordination,
                bundle="server",
            )
        else:
            database_migration.apply(
                env_file=env_file,
                evidence_dir=evidence_dir,
                lock_coordination=lock_coordination,
                bundle="server",
                backup=backup,
                plan_id=plan_id,
                yes=yes,
                maintenance_confirmed=maintenance_confirmed,
                backup_confirmed=backup_confirmed,
                accept_no_backup=accept_no_backup,
                backup_ref=backup_ref,
                acknowledge_previous_run=acknowledge_previous_run,
            )
        return
    with server_settings_context(env_file=env_file) as settings:
        try:
            ready = asyncio.run(
                _atomic_memory_maintenance(
                    settings,
                    action,
                    maintenance_confirmed=maintenance_confirmed,
                    decisions=loaded,
                    snapshot_budget_bytes=snapshot_budget_mib * 1024 * 1024,
                    owner_decisions=loaded_owners,
                )
            )
        except MissingDatabaseError as error:
            if action == "apply":
                raise
            raise typer.BadParameter(_missing_read_only_database(error)) from error
        except (SeekDBInspectionError, LegacySeekDBDatabaseError) as error:
            raise typer.BadParameter(str(error)) from error
        except (DatabaseAccessError, DatabaseCreationError) as error:
            _report_database_guidance(error)
        except OperationalError as error:
            sqlite_code = getattr(error.orig, "sqlite_errorcode", None)
            if (
                action == "apply"
                or not isinstance(settings.database, SQLiteConfig)
                or not isinstance(sqlite_code, int)
                or sqlite_code & 0xFF != SQLITE_CANTOPEN
            ):
                raise
            raise typer.BadParameter("plan and verify require a readable existing SQLite database") from error  # noqa: TRY003
    if not ready:
        raise typer.Exit(code=1)


@app.command("atomic-memory-rebuild-projection")
def atomic_memory_rebuild_projection(
    env_file: Annotated[Path | None, typer.Option(help="Load the deployment's settings.")] = None,
    action: Annotated[
        Literal["plan", "apply", "verify"], typer.Option(help="Managed projection maintenance action.")
    ] = "apply",
    maintenance_confirmed: Annotated[
        bool, typer.Option(help="Confirm every API, host and Worker is stopped and input writes are paused.")
    ] = False,
    batch_size: Annotated[int, typer.Option(min=1, max=1000, help="Identities read per maintenance batch.")] = 100,
    evidence_dir: database_migration.EvidenceDir = None,
    lock_coordination: database_migration.LockCoordination = None,
    backup: Annotated[
        database_migration.BackupPolicy | None, typer.Option(help="Managed storage backup policy.")
    ] = None,
    plan_id: Annotated[str | None, typer.Option(help="Exact managed projection plan to accept.")] = None,
    yes: Annotated[bool, typer.Option("--yes", help="Accept the managed projection plan.")] = False,
    backup_confirmed: Annotated[bool, typer.Option(help="Declare a manual backup exists.")] = False,
    accept_no_backup: Annotated[bool, typer.Option(help="Accept the no-backup risk.")] = False,
    backup_ref: Annotated[str | None, typer.Option(help="Operator manual backup reference.")] = None,
    acknowledge_previous_run: Annotated[
        str | None, typer.Option(help="OceanBase token confirming the previous remote maintenance ended.")
    ] = None,
) -> None:
    """Rebuild active Atomic Memory search rows from existing authority."""

    env_file = resolve_server_environment_file(env_file, discover=True)
    with database_migration._operator_errors(), server_settings_context(env_file=env_file) as settings:
        managed = _managed_storage(settings.database)
    if managed:
        if action == "plan":
            database_migration.plan(
                env_file=env_file,
                evidence_dir=evidence_dir,
                lock_coordination=lock_coordination,
                backup=backup or "auto",
                bundle="atomic-projection",
                batch_size=batch_size,
            )
        elif action == "verify":
            database_migration.verify(
                env_file=env_file,
                evidence_dir=evidence_dir,
                lock_coordination=lock_coordination,
                bundle="atomic-projection",
                batch_size=batch_size,
            )
        else:
            database_migration.apply(
                env_file=env_file,
                evidence_dir=evidence_dir,
                lock_coordination=lock_coordination,
                bundle="atomic-projection",
                batch_size=batch_size,
                backup=backup,
                plan_id=plan_id,
                yes=yes,
                maintenance_confirmed=maintenance_confirmed,
                backup_confirmed=backup_confirmed,
                accept_no_backup=accept_no_backup,
                backup_ref=backup_ref,
                acknowledge_previous_run=acknowledge_previous_run,
            )
        return
    if action != "apply":
        raise typer.BadParameter("plan and verify require managed Server storage")  # noqa: TRY003
    if not maintenance_confirmed:
        raise typer.BadParameter("projection rebuild requires --maintenance-confirmed after stopping every writer")  # noqa: TRY003
    with server_settings_context(env_file=env_file) as settings:
        try:
            ready = asyncio.run(
                _atomic_memory_maintenance(
                    settings, "rebuild-projection", maintenance_confirmed=maintenance_confirmed, batch_size=batch_size
                )
            )
        except LegacySeekDBDatabaseError as error:
            raise typer.BadParameter(str(error)) from error
        except (DatabaseAccessError, DatabaseCreationError) as error:
            _report_database_guidance(error)
    if not ready:
        raise typer.Exit(code=1)


async def _sqlite_snapshot_budget_preflight(database: SQLiteConfig, snapshot_budget_bytes: int) -> bool:
    from powercontext.builtin.persistence.migrations.atomic_memory_v1 import (
        AtomicMemoryMigrationReport,
        AtomicMemorySnapshotBudgetError,
        preflight_processing_snapshot,
    )

    try:
        # Reject existing inputs before writable opening can switch journal
        # mode. Apply retains creation behavior when no database can be opened;
        # its initial inventory repeats admission before migration writes.
        async with (
            SQLiteProfile.open_readonly(database) as inspection,
            inspection.database.transaction() as connection,
        ):
            await preflight_processing_snapshot(connection, snapshot_budget_bytes=snapshot_budget_bytes)
    except AtomicMemorySnapshotBudgetError as error:
        typer.echo(AtomicMemoryMigrationReport(action="apply", errors=error.errors).model_dump_json())
        return False
    except OperationalError as error:
        sqlite_code = getattr(error.orig, "sqlite_errorcode", None)
        if not isinstance(sqlite_code, int) or sqlite_code & 0xFF != SQLITE_CANTOPEN:
            raise
    return True


async def _atomic_memory_maintenance(
    settings: ServerSettings,
    action: Literal["plan", "apply", "verify", "rebuild-projection"],
    *,
    maintenance_confirmed: bool,
    batch_size: int = 100,
    decisions: dict[tuple[str, str, int], CandidateDecision] | None = None,
    snapshot_budget_bytes: int = 256 * 1024 * 1024,
    owner_decisions: dict[tuple[str, str, str], OwnerDecision] | None = None,
) -> bool:
    from powercontext.builtin.artifacts.memory import EmbeddingProfile

    inference = settings.inference
    embedding_profile = None
    if inference.embedding_model is not None:
        if inference.embedding_profile_id is None or inference.embedding_dimension is None:
            raise typer.BadParameter("embedding deployment requires an explicit profile and dimension")  # noqa: TRY003
        embedding_profile = EmbeddingProfile(
            profile_id=inference.embedding_profile_id,
            model=inference.embedding_model,
            dimension=inference.embedding_dimension,
            distance="l2",
            normalization=inference.embedding_normalization,
        )
    database = settings.database
    read_only = action in {"plan", "verify"}
    if isinstance(database, SQLiteConfig):
        if not database.is_persistent:
            raise typer.BadParameter("offline migration requires a persistent database")  # noqa: TRY003
        if action == "apply" and not await _sqlite_snapshot_budget_preflight(database, snapshot_budget_bytes):
            return False
        opened = (
            SQLiteProfile.open_readonly(database, load_vector_extension=embedding_profile is not None)
            if read_only
            else SQLiteProfile.open(database, tables=(), load_vector_extension=embedding_profile is not None)
        )
        index = SQLiteAtomicMemoryIndex(embedding_profile)
    elif isinstance(database, OceanBaseConfig):
        opened = (
            OceanBaseProfile.open_existing(database, tables=())
            if read_only
            else OceanBaseProfile.open(database, tables=())
        )
        index = OceanBaseAtomicMemoryIndex(embedding_profile)
    elif isinstance(database, SeekDBConfig):
        opened = (
            SeekDBProfile.open_existing(database, tables=()) if read_only else SeekDBProfile.open(database, tables=())
        )
        index = OceanBaseAtomicMemoryIndex(embedding_profile)
    else:
        raise typer.BadParameter("unsupported migration database")  # noqa: TRY003
    async with AsyncExitStack() as resources:
        profile = await resources.enter_async_context(opened)
        if action in {"apply", "rebuild-projection"}:
            embedding_model, _readiness = await _embedding_models(inference, resources, None)
            if action == "rebuild-projection":
                report = await rebuild_atomic_memory_projection(
                    profile.database,
                    index,
                    maintenance_confirmed=maintenance_confirmed,
                    embedding_model=embedding_model,
                    batch_size=batch_size,
                )
            else:
                from powercontext.builtin.persistence.migrations.atomic_memory_v1 import apply_atomic_memory_migration

                report = await apply_atomic_memory_migration(
                    profile.database,
                    index,
                    maintenance_confirmed=maintenance_confirmed,
                    embedding_model=embedding_model,
                    decisions=decisions,
                    snapshot_budget_bytes=snapshot_budget_bytes,
                    owner_decisions=owner_decisions,
                    require_owners=settings.access.mode == "enforced",
                )
        else:
            from powercontext.builtin.persistence.migrations.atomic_memory_v1 import (
                plan_atomic_memory_migration,
                verify_atomic_memory_migration,
            )

            async with profile.database.transaction() as connection:
                report = (
                    await plan_atomic_memory_migration(
                        connection,
                        index=index,
                        decisions=decisions,
                        snapshot_budget_bytes=snapshot_budget_bytes,
                        owner_decisions=owner_decisions,
                        require_owners=settings.access.mode == "enforced",
                    )
                    if action == "plan"
                    else await verify_atomic_memory_migration(
                        connection,
                        index=index,
                        snapshot_budget_bytes=snapshot_budget_bytes,
                        owner_decisions=owner_decisions,
                        require_owners=settings.access.mode == "enforced",
                    )
                )
        typer.echo(report.model_dump_json())
        return report.ready if action != "plan" else not report.errors


@app.command()
def run(
    host: Annotated[str | None, typer.Option(help="Address to bind.")] = None,
    port: Annotated[int | None, typer.Option(min=1, max=65535, help="Port to bind.")] = None,
    uds: Annotated[
        Path | None, typer.Option(help="Listen only on this absolute Unix socket path (Linux/macOS).")
    ] = None,
    env_file: Annotated[
        Path | None,
        typer.Option(help="Load Server and provider settings from this environment file."),
    ] = None,
    no_env_file: Annotated[
        bool,
        typer.Option("--no-env-file", help="Do not discover or load an environment file."),
    ] = False,
    role: Annotated[
        Literal["all", "api", "background"] | None,
        typer.Option(help="Run all components, only APIs, or only background processing."),
    ] = None,
) -> None:
    """Run the configured API and/or background service in the foreground."""

    if env_file is not None and no_env_file:
        raise typer.BadParameter("cannot be combined with --env-file", param_hint="--no-env-file")  # noqa: TRY003
    selected_env_file = resolve_server_environment_file(env_file, discover=not no_env_file)
    role_context = (
        nullcontext()
        if role is None
        else environment_context(
            {"POWERCONTEXT_SERVER_RUNTIME_ARTIFACT_PROCESSING_ROLE": role},
            override=True,
        )
    )
    try:
        with (
            role_context,
            server_settings_context(
                host=host,
                port=port,
                uds=uds,
                env_file=selected_env_file,
                process_environment_overrides=True,
            ) as settings,
        ):
            if selected_env_file is not None:
                typer.echo(f"Loaded environment file: {selected_env_file}")
            _run_configured_server(settings)
    except ServerConfigurationError as error:
        if isinstance(error.cause, ValidationError):
            raise _friendly_bad_parameter(error.cause) from error
        if env_file is not None:
            hint = "Invalid value for --env-file"
        elif selected_env_file is not None:
            hint = "Invalid default environment file"
        else:
            hint = "Invalid Server configuration"
        typer.echo(f"{hint}: {error}", err=True)
        raise typer.Exit(code=2) from error
    except MissingAuthenticationProviderError as error:
        raise typer.BadParameter(_MISSING_BEARER_CLI_MESSAGE) from error


def _run_configured_server(settings: ServerSettings) -> None:
    """Run one already-validated configuration in the current process."""

    configure_server_logging(settings.logging, role=settings.runtime.artifact_processing_role)
    try:
        tracing = configure_server_tracing(settings.tracing, role=settings.runtime.artifact_processing_role)
        try:
            write_inference_capability_notice(
                generation_model=settings.inference.generation_model,
                embedding_model=settings.inference.embedding_model,
            )
            if settings.runtime.artifact_processing_role == "background":
                _run_background(settings, tracing)
                return
            if settings.http.uds is None:
                application = create_server_app(settings=settings, tracing=tracing)
                _run_server(application, host=settings.http.host, port=settings.http.port)
            else:
                from powercontext.server.uds import UnixSocketError, UnixSocketExternalScheme, unix_socket_listener

                try:
                    with ExitStack() as resources:
                        listener = resources.enter_context(unix_socket_listener(settings.http.uds))
                        application = create_server_app(settings=settings, tracing=tracing)
                        uds_application = (
                            UnixSocketExternalScheme(application, settings.http.uds_external_scheme)
                            if settings.http.uds_external_scheme is not None
                            else application
                        )
                        _run_unix_server(uds_application, listener=listener, cleanup=resources.close)
                except UnixSocketError as error:
                    raise typer.BadParameter(str(error), param_hint="--uds / POWERCONTEXT_SERVER_HTTP_UDS") from error
        finally:
            tracing.shutdown()
    finally:
        shutdown_server_logging()


def _friendly_bad_parameter(error: ValidationError) -> typer.BadParameter:
    """Translate a settings ``ValidationError`` into an actionable CLI parameter error.

    ``ServerSettings`` enforces its policies at construction time, so a rejected ``--host`` /
    environment combination arrives here wrapped in pydantic's generic validation report. The
    policy failures an operator can act on directly are recognised by identity via pydantic's
    ``ctx['error']`` -- not by matching the raw text -- and translated into a concrete lever.
    Anything else falls back to pydantic's message unchanged.
    """

    for detail in error.errors(include_context=True):
        cause = (detail.get("ctx") or {}).get("error")
        if isinstance(cause, UnauthenticatedNonLoopbackBindError):
            return typer.BadParameter(_UNSAFE_BIND_CLI_MESSAGE, param_hint="--host")
        if isinstance(cause, MissingBearerTokenError):
            return typer.BadParameter(_MISSING_BEARER_CLI_MESSAGE)
    return typer.BadParameter(str(error))


def _run_server(application: Any, *, host: str, port: int) -> None:
    import uvicorn

    uvicorn.run(application, host=host, port=port, access_log=False, log_config=None)


def _run_unix_server(application: Any, *, listener: socket, cleanup: Callable[[], None]) -> None:
    import uvicorn

    class UnixServer(uvicorn.Server):
        @contextmanager
        @override
        def capture_signals(self) -> Iterator[None]:
            with super().capture_signals():
                try:
                    yield
                finally:
                    # Uvicorn replays SIGTERM after draining the app. Release the
                    # socket and lock before that signal can terminate the process.
                    cleanup()

    server = UnixServer(uvicorn.Config(application, access_log=False, log_config=None, proxy_headers=False))
    server.run(sockets=[listener])
    if not server.started:
        raise typer.Exit(code=3)


def _run_background(settings: ServerSettings, tracing: Any) -> None:
    """Run a Supervisor and optional metrics listener until SIGINT or SIGTERM."""

    asyncio.run(_run_background_async(settings, tracing))


async def _run_background_async(settings: ServerSettings, tracing: Any) -> None:
    config = BuiltinConfig(
        code=settings.code,
        runtime=settings.runtime,
        database=settings.database,
        handoff_report=settings.handoff_report,
        inference=settings.inference,
        external_skills=settings.external_skills,
    )
    from powercontext.builtin.persistence.migrations.server_readiness import prepare_server_storage

    await asyncio.to_thread(prepare_server_storage, settings)
    metrics = ServerMetrics(role="background") if settings.metrics.enabled else None
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    installed: list[signal.Signals] = []
    for signum in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signum, stopped.set)
        except (NotImplementedError, RuntimeError):
            continue
        installed.append(signum)
    try:
        async with AsyncExitStack() as resources:
            logging_bootstrap = resources.enter_context(worker_logging(settings.logging, role="background"))
            principal = (
                PrincipalRef(type="service", id="server-token", description="PowerContext static bearer")
                if settings.auth.token is not None
                else None
            )
            access = None
            if settings.access.mode == "enforced":
                access = await resources.enter_async_context(
                    open_builtin_access_control(
                        settings.database,
                        bootstrap_administrators=() if principal is None else (principal,),
                        deployment_id=settings.access.deployment_id,
                    )
                )
            worker_security = build_worker_security(settings, access, legacy_static_principal=principal)
            runtime = await resources.enter_async_context(
                open_builtin_runtime(
                    config,
                    instrumentation=tracing.instrumentation,
                    tracing=tracing,
                    worker_security=worker_security,
                    model_usage_observer=None if metrics is None else metrics.observe_model_usage,
                    worker_model_usage_observer=None if metrics is None else metrics.observe_worker_model_usage,
                    processing_observer=None if metrics is None else metrics.observe_processing,
                    worker_initializer=logging_bootstrap,
                    worker_entrypoint_wrapper=WorkerTracing(settings.tracing).wrap,
                )
            )
            if metrics is None or stopped.is_set():
                await stopped.wait()
            else:
                application = create_background_metrics_app(
                    settings, runtime=runtime, metrics=metrics, access_control=access
                )

                async def background_access_checks() -> dict[str, str]:
                    return await access_readiness_checks(application.state)

                probe = _ServerReadinessProbe(metrics, tracing=tracing, access_checks=background_access_checks)
                probe.bind(runtime)
                resources.callback(probe.unbind)
                await probe()
                await resources.enter_async_context(probe.monitor())
                await serve_background_metrics(application, settings, stopped)
    finally:
        for signum in installed:
            loop.remove_signal_handler(signum)
