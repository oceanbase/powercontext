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

"""Forward-only Alembic schema lifecycle with legacy baseline validation."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Table, inspect
from sqlalchemy.engine import Connection
from sqlalchemy.engine.reflection import Inspector
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.builtin.persistence.coordination import CoordinationRepository, CoordinatorLease
from powercontext.builtin.persistence.database import AsyncDatabase
from powercontext.builtin.persistence.errors import PersistenceError
from powercontext.builtin.persistence.schema import create_tables
from powercontext.builtin.persistence.tables import (
    ARTIFACT_HEADS_TABLE,
    COORDINATION_TABLES,
    MEMORY_TABLES,
    SCHEDULER_LEASES_TABLE,
    SCOPE_TABLES,
    SHARED_TABLES,
    STATISTICS_TABLES,
    WORK_TABLES,
)

BASELINE_REVISION = "0001_baseline"
CURRENT_SCHEMA_REVISION = "0003_scope_source_skill"
SCHEMA_VERSION_TABLE = "pc_schema_revisions"
_MIGRATION_LEASE = "schema-migration"
_MIGRATION_LEASE_SECONDS = 600
_BASE_TABLES = SHARED_TABLES + MEMORY_TABLES + STATISTICS_TABLES
_NEW_TABLES = WORK_TABLES + COORDINATION_TABLES
_CURRENT_TABLES = SCOPE_TABLES + _BASE_TABLES + _NEW_TABLES
SchemaProvisioner = Callable[[AsyncConnection], Awaitable[None]]


class SchemaMigrationError(PersistenceError):
    """Base class for stable migration startup failures."""


class SchemaNotCurrentError(SchemaMigrationError):
    """Raised when a role process starts before the migrator has completed."""

    def __init__(self, actual: str | None) -> None:
        self.actual = actual
        super().__init__(
            f"database schema revision is {actual or 'unversioned'}; expected {CURRENT_SCHEMA_REVISION}; "
            "run `powercontext server migrate` before starting distributed roles"
        )


class SchemaCompatibilityError(SchemaMigrationError):
    """Raised when an unversioned database cannot be safely baselined."""

    def __init__(self, detail: str) -> None:
        super().__init__(f"database schema cannot be baselined safely: {detail}")


class MigrationBusyError(SchemaMigrationError):
    """Raised when another migrator owns the database lease."""

    def __init__(self) -> None:
        super().__init__("another schema migrator currently owns the database lease")


async def migrate_database(
    database: AsyncDatabase,
    *,
    provision: SchemaProvisioner | None = None,
) -> str:
    """Validate/stamp legacy state and upgrade through the current revision."""

    lease = await _acquire_migration_lease(database)
    try:
        async with database.transaction() as connection:
            await connection.run_sync(_prepare_legacy_revision)
            await connection.run_sync(_upgrade_to_head)
            if provision is not None:
                await provision(connection)
            actual = await connection.run_sync(_current_revision)
            await connection.run_sync(_validate_current_schema)
        if actual != CURRENT_SCHEMA_REVISION:
            raise SchemaNotCurrentError(actual)
        return actual
    finally:
        await _release_migration_lease(database, lease)


async def require_current_schema(database: AsyncDatabase) -> str:
    """Read-only startup guard used by every distributed role."""

    async with database.transaction() as connection:
        actual = await connection.run_sync(_current_revision)
        if actual == CURRENT_SCHEMA_REVISION:
            await connection.run_sync(_validate_current_schema)
    if actual != CURRENT_SCHEMA_REVISION:
        raise SchemaNotCurrentError(actual)
    return actual


async def _acquire_migration_lease(database: AsyncDatabase) -> CoordinatorLease:
    # OceanBase/MySQL DDL commits the active transaction implicitly.  Keep the
    # one-time coordination-table bootstrap in its own transaction so a fresh
    # database never enters the lease repository with SQLAlchemy's transaction
    # state out of sync with the server (notably before its SAVEPOINT insert).
    async with database.transaction() as connection:
        await create_tables(connection, (SCHEDULER_LEASES_TABLE,))

    async with database.transaction() as connection:
        lease = await CoordinationRepository().acquire_lease(
            connection,
            lease_name=_MIGRATION_LEASE,
            owner_id=uuid4().hex,
            lease_seconds=_MIGRATION_LEASE_SECONDS,
        )
    if lease is None:
        raise MigrationBusyError
    return lease


async def _release_migration_lease(database: AsyncDatabase, lease: CoordinatorLease) -> None:
    try:
        async with database.transaction() as connection:
            await CoordinationRepository().release_lease(connection, lease)
    except SQLAlchemyError:
        # The lease expires by database time. Never mask the migration result
        # with a best-effort release failure during connection loss.
        return


def _prepare_legacy_revision(connection: Connection) -> None:
    actual = _current_revision(connection)
    if actual is not None:
        return
    inspector = inspect(connection)
    existing = set(inspector.get_table_names())
    existing.discard(SCHEDULER_LEASES_TABLE.name)
    existing.discard(SCHEMA_VERSION_TABLE)
    powercontext_tables = {name for name in existing if name.startswith("pc_")}
    if not powercontext_tables:
        return

    required = {table.name for table in _BASE_TABLES}
    missing = sorted(required - powercontext_tables)
    if missing:
        raise SchemaCompatibilityError(  # noqa: TRY003
            f"missing baseline tables: {', '.join(missing)}"
        )
    _upgrade_known_legacy_columns(connection, inspector)
    inspector = inspect(connection)
    _require_expected_columns(inspector, _BASE_TABLES)

    new_names = {table.name for table in _NEW_TABLES if table is not SCHEDULER_LEASES_TABLE}
    present_new = new_names & powercontext_tables
    if present_new and present_new != new_names:
        missing_new = sorted(new_names - present_new)
        raise SchemaCompatibilityError(  # noqa: TRY003
            f"partially installed work-ledger tables: {', '.join(missing_new)}"
        )
    if present_new:
        _require_expected_columns(inspector, _NEW_TABLES)
    command.stamp(_alembic_config(connection), CURRENT_SCHEMA_REVISION if present_new else BASELINE_REVISION)


def _upgrade_known_legacy_columns(connection: Connection, inspector: Inspector) -> None:
    """Apply narrowly recognized pre-baseline expansions before stamping."""

    table_name = ARTIFACT_HEADS_TABLE.name
    if table_name not in set(inspector.get_table_names()):
        return
    columns = {str(column["name"]) for column in inspector.get_columns(table_name)}
    mysql = connection.dialect.name in {"mysql", "oceanbase"}
    additions = {
        "searchable_text": "MEDIUMTEXT NULL" if mysql else "TEXT NULL",
        "lifecycle_state": "VARCHAR(16) NOT NULL DEFAULT 'active'",
        "replacement_artifact_id": "VARCHAR(128) NULL",
        "governance_generation": "BIGINT NOT NULL DEFAULT 0",
    }
    for name, definition in additions.items():
        if name not in columns:
            connection.exec_driver_sql(f"ALTER TABLE {table_name} ADD COLUMN {name} {definition}")


def _require_expected_columns(inspector: Inspector, tables: Iterable[Table]) -> None:
    for table in tables:
        actual = {str(column["name"]) for column in inspector.get_columns(table.name)}
        expected = {column.name for column in table.columns}
        missing = sorted(expected - actual)
        if missing:
            raise SchemaCompatibilityError(  # noqa: TRY003
                f"table {table.name} is missing columns: {', '.join(missing)}"
            )


def _validate_current_schema(connection: Connection) -> None:
    inspector = inspect(connection)
    existing = set(inspector.get_table_names())
    expected = _CURRENT_TABLES
    missing_tables = sorted(table.name for table in expected if table.name not in existing)
    if missing_tables:
        raise SchemaCompatibilityError(f"current revision is missing tables: {', '.join(missing_tables)}")  # noqa: TRY003
    _require_expected_columns(inspector, expected)


def _upgrade_to_head(connection: Connection) -> None:
    command.upgrade(_alembic_config(connection), "head")


def _current_revision(connection: Connection) -> str | None:
    tables = set(inspect(connection).get_table_names())
    if SCHEMA_VERSION_TABLE not in tables:
        return None
    context = MigrationContext.configure(connection, opts={"version_table": SCHEMA_VERSION_TABLE})
    return context.get_current_revision()


def _alembic_config(connection: Connection | None = None) -> Config:
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).with_name("migrations")))
    if connection is not None:
        config.attributes["connection"] = connection
    return config


def migration_head() -> str:
    """Return the packaged Alembic head for contract tests and diagnostics."""

    return str(ScriptDirectory.from_config(_alembic_config()).get_current_head())


def baseline_tables() -> tuple[Table, ...]:
    """Return the explicitly managed migration baseline."""

    # Search projections and optional feature tables are provisioned by the
    # configured migrator callback. Keeping the baseline limited to the
    # invariant domain schema lets disabled features stay physically absent.
    return _BASE_TABLES


__all__ = [
    "BASELINE_REVISION",
    "CURRENT_SCHEMA_REVISION",
    "SCHEMA_VERSION_TABLE",
    "MigrationBusyError",
    "SchemaCompatibilityError",
    "SchemaMigrationError",
    "SchemaNotCurrentError",
    "baseline_tables",
    "migrate_database",
    "migration_head",
    "require_current_schema",
]
