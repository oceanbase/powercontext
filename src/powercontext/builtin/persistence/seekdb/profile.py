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

"""Embedded seekdb profile using its local runtime and async MySQL socket."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager, suppress
from importlib import import_module
from pathlib import Path
from types import ModuleType
from typing import Any, Literal, Protocol, TypeVar, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pyobvector import AsyncOceanBaseDialect
from sqlalchemy import Table
from sqlalchemy.dialects import registry as dialect_registry
from sqlalchemy.engine import URL
from sqlalchemy.engine.interfaces import DBAPIConnection
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from typing_extensions import override

from powercontext.builtin.persistence.bootstrap import (
    DEFAULT_DATABASE,
    dispose_engine,
    ensure_database,
    mysql_error_code,
    require_existing_database,
    validate_database_name,
)
from powercontext.builtin.persistence.database import AsyncDatabase
from powercontext.builtin.persistence.dream_schema import assert_dream_schema_ready
from powercontext.builtin.persistence.errors import PersistenceError
from powercontext.builtin.persistence.schema import create_tables
from powercontext.builtin.persistence.topic_memory_recovery_schema import BUDGET, prepare_topic_memory_recovery_schema

_DIALECT_DRIVER = "mysql+aseekdb"
_DIALECT_REGISTRY_NAME = "mysql.aseekdb"
_T = TypeVar("_T")


class AsyncSeekDBDialect(AsyncOceanBaseDialect):
    """OceanBase-compatible dialect with seekdb-safe connection shutdown."""

    supports_statement_cache = AsyncOceanBaseDialect.supports_statement_cache

    @override
    def do_close(self, dbapi_connection: DBAPIConnection) -> None:
        # seekdb resets the socket while aiomysql drains COM_QUIT. SQLAlchemy's
        # terminate path handles that reset and falls back to closing the transport.
        self.do_terminate(dbapi_connection)


class _SeekDBInstance(Protocol):
    def connection_options(self) -> Mapping[str, object]: ...

    def close(self) -> None: ...


class SeekDBUnavailableError(PersistenceError):
    """Raised when the embedded seekdb binding is unavailable."""

    def __init__(self) -> None:
        super().__init__("Embedded seekdb requires powercontext[seekdb] on a supported Linux or macOS platform")


class SeekDBInspectionError(PersistenceError):
    """Read-only inspection cannot initialize or replace native seekdb storage."""


class SeekDBConfig(BaseModel):
    """Validated configuration for one embedded seekdb instance."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["seekdb"] = "seekdb"
    path: Path
    database: str = Field(
        default=DEFAULT_DATABASE,
        description="Database to create or reuse; backticks are unsupported; explicitly select test to retain legacy seekdb data",
    )
    echo: bool = False
    pool_pre_ping: bool = True

    @field_validator("database")
    @classmethod
    def require_database_name(cls, value: str) -> str:
        return validate_database_name(value)

    @field_validator("path", mode="before")
    @classmethod
    def require_path(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            raise ValueError("seekdb path must not be empty")  # noqa: TRY003
        return value


class SeekDBProfile:
    """An initialized embedded seekdb profile with explicit runtime ownership."""

    def __init__(
        self,
        *,
        database: AsyncDatabase,
        tables: tuple[Table, ...],
        connection_options: Mapping[str, object] | None = None,
    ) -> None:
        self.database = database
        self.tables = tables
        self.connection_options = dict(connection_options or {})

    @classmethod
    @asynccontextmanager
    async def open(
        cls,
        config: SeekDBConfig,
        *,
        tables: tuple[Table, ...],
        _archive_recovery: bool = False,
    ) -> AsyncIterator[SeekDBProfile]:
        """Start seekdb locally and connect through its async Unix socket."""

        async with cls._connect(config, tables=tables, provision=True, _archive_recovery=_archive_recovery) as profile:
            yield profile

    @classmethod
    @asynccontextmanager
    async def open_existing(
        cls,
        config: SeekDBConfig,
        *,
        tables: tuple[Table, ...],
    ) -> AsyncIterator[SeekDBProfile]:
        """Start seekdb locally and connect to an already provisioned database."""

        async with cls._connect(config, tables=tables, provision=False) as profile:
            yield profile

    @classmethod
    @asynccontextmanager
    async def _connect(
        cls,
        config: SeekDBConfig,
        *,
        tables: tuple[Table, ...],
        provision: bool,
        _archive_recovery: bool = False,
    ) -> AsyncIterator[SeekDBProfile]:
        from powercontext.builtin.persistence.migrations.locking import assert_no_local_migration

        path = config.path.expanduser().resolve()
        assert_no_local_migration(path)
        if provision:
            path.parent.mkdir(parents=True, exist_ok=True)
        else:
            _require_existing_storage(path)
        module = _load_binding()
        instance = await _open_instance(module, path)
        try:
            engine = _create_engine(config, instance.connection_options())
            database = AsyncDatabase.own(engine)
            profile = cls(database=database, tables=tables, connection_options=instance.connection_options())
            try:
                await _require_explicit_legacy_database(config, instance.connection_options())
                if provision:
                    await ensure_database(
                        engine,
                        lambda: _create_engine(config, instance.connection_options(), database=""),
                        name=config.database,
                    )
                else:
                    await require_existing_database(engine, name=config.database)
                await assert_dream_schema_ready(engine, _archive_recovery=_archive_recovery)
                if tables:
                    await prepare_topic_memory_recovery_schema(
                        engine, initialize=any(table.name == BUDGET for table in tables)
                    )
                async with database.transaction() as connection:
                    await create_tables(connection, tables)
                yield profile
            finally:
                close_task = asyncio.create_task(database.close())
                try:
                    await asyncio.shield(close_task)
                except asyncio.CancelledError:
                    await _finish_task(close_task)
                    raise
        finally:
            instance.close()


def has_native_storage(path: Path) -> bool:
    """Recognize the native storage markers used for seekdb reopen and maintenance."""
    return (path / "etc" / "seekdb.data_version.bin").is_file() and (path / "store" / "sstable" / "meta.db").is_file()


def _require_existing_storage(path: Path) -> None:
    if not path.exists() or (path.is_dir() and not any(path.iterdir())):
        raise SeekDBInspectionError(  # noqa: TRY003
            "Read-only inspection requires an existing seekdb instance. The configured path is missing or empty; "
            "start the Server to initialize it, then repeat the inspection."
        )
    if not path.is_dir() or not has_native_storage(path):
        raise SeekDBInspectionError(  # noqa: TRY003
            "Read-only inspection requires an existing seekdb instance. The configured path is not a directory "
            "with recognized native storage; select an initialized seekdb directory. Existing contents were preserved."
        )


def _load_binding() -> ModuleType:
    try:
        return import_module("pylibseekdb")
    except ModuleNotFoundError as error:
        if error.name != "pylibseekdb":
            raise
        raise SeekDBUnavailableError from None


async def _open_instance(module: ModuleType, path: Path) -> _SeekDBInstance:
    open_task = asyncio.create_task(cast(Any, module).aopen(str(path)))
    try:
        return cast(_SeekDBInstance, await asyncio.shield(open_task))
    except asyncio.CancelledError:
        with suppress(BaseException):
            instance = cast(_SeekDBInstance, await _finish_task(open_task))
            instance.close()
        raise


async def _finish_task(task: asyncio.Task[_T]) -> _T:
    """Wait for cleanup to finish without passing further cancellations to it."""

    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            continue
    return task.result()


async def _require_explicit_legacy_database(config: SeekDBConfig, options: Mapping[str, object]) -> None:
    """Never hide old PowerContext tables behind the changed implicit default.

    This probe is diagnostic: it never echoes SQL, because the caller may own the
    process's machine-readable output and the echo handler writes to stdout.
    """
    if "database" in config.model_fields_set:
        return
    legacy = _create_engine(config.model_copy(update={"echo": False}), options, database="test")
    try:
        try:
            async with legacy.connect() as connection:
                result = await connection.exec_driver_sql(
                    "SELECT TABLE_NAME FROM information_schema.TABLES "
                    "WHERE TABLE_SCHEMA = DATABASE() AND LEFT(TABLE_NAME, 3) = 'pc_' LIMIT 1"
                )
                if result.first() is not None:
                    raise LegacySeekDBDatabaseError(config.path)
        except DBAPIError as error:
            if mysql_error_code(error) != 1049:
                raise
    finally:
        await dispose_engine(legacy)


class LegacySeekDBDatabaseError(PersistenceError):
    """An existing seekdb installation needs an explicit database choice."""

    def __init__(self, path: Path) -> None:
        super().__init__(
            f"Existing PowerContext tables were found in seekdb database 'test' at {str(path)!r}. "
            "The default database is now 'powercontext'. Set database='test' "
            "(POWERCONTEXT_SERVER_DATABASE_DATABASE=test) to keep using existing data, "
            "or explicitly set database='powercontext' to select the new database. "
            "No application tables were created and no data was moved or deleted. "
            "See https://powercontext.oceanbase.io/en/docs/get-started/install-and-run/#existing-seekdb-installations"
        )


def _create_engine(
    config: SeekDBConfig,
    connection_options: Mapping[str, object],
    *,
    database: str | None = None,
) -> AsyncEngine:
    options = dict(connection_options)
    # Instance connection hints must not override PowerContext's database selection.
    options.pop("database", None)
    options.pop("db", None)
    username = str(options.pop("user", "root"))
    password_value = options.pop("password", None)
    host = str(options.pop("host", "localhost"))
    port_value = options.pop("port", None)
    # seekdb's handshake currently omits the autocommit status flag, so
    # aiomysql otherwise mistakes the default-on session for an explicit
    # transaction and rollback becomes ineffective.
    options["init_command"] = "SET autocommit = 0"
    url = URL.create(
        _DIALECT_DRIVER,
        username=username,
        password=None if password_value is None else str(password_value),
        host=host,
        port=None if port_value is None else int(cast(int | str, port_value)),
        database=config.database if database is None else database,
        query={"charset": "utf8mb4"},
    )
    _register_seekdb_dialect()
    return create_async_engine(
        url,
        connect_args=options,
        echo=config.echo,
        hide_parameters=True,
        pool_pre_ping=config.pool_pre_ping,
    )


def _register_seekdb_dialect() -> None:
    dialect_registry.register(_DIALECT_REGISTRY_NAME, __name__, "AsyncSeekDBDialect")
