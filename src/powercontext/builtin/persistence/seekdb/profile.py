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

"""Embedded seekDB profile using its local runtime and async MySQL socket."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager, suppress
from importlib import import_module
from pathlib import Path
from types import ModuleType
from typing import Any, Literal, Protocol, TypeVar, cast

from pydantic import BaseModel, ConfigDict, field_validator
from pyobvector import AsyncOceanBaseDialect
from sqlalchemy import Table
from sqlalchemy.dialects import registry as dialect_registry
from sqlalchemy.engine import URL
from sqlalchemy.engine.interfaces import DBAPIConnection
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from typing_extensions import override

from powercontext.builtin.persistence.database import AsyncDatabase
from powercontext.builtin.persistence.errors import PersistenceError
from powercontext.builtin.persistence.schema import create_tables

_DIALECT_DRIVER = "mysql+aseekdb"
_DIALECT_REGISTRY_NAME = "mysql.aseekdb"
_T = TypeVar("_T")


class AsyncSeekDBDialect(AsyncOceanBaseDialect):
    """OceanBase-compatible dialect with seekDB-safe connection shutdown."""

    supports_statement_cache = AsyncOceanBaseDialect.supports_statement_cache

    @override
    def do_close(self, dbapi_connection: DBAPIConnection) -> None:
        # seekDB resets the socket while aiomysql drains COM_QUIT. SQLAlchemy's
        # terminate path handles that reset and falls back to closing the transport.
        self.do_terminate(dbapi_connection)


class _SeekDBInstance(Protocol):
    def connection_options(self) -> Mapping[str, object]: ...

    def close(self) -> None: ...


class SeekDBUnavailableError(PersistenceError):
    """Raised when the embedded seekDB binding is unavailable."""

    def __init__(self) -> None:
        super().__init__("Embedded seekDB requires powercontext[seekdb] on a supported Linux or macOS platform")


class SeekDBConfig(BaseModel):
    """Validated configuration for one embedded seekDB instance."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["seekdb"] = "seekdb"
    path: Path
    database: Literal["test"] = "test"
    echo: bool = False
    pool_pre_ping: bool = True

    @field_validator("path", mode="before")
    @classmethod
    def require_path(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            raise ValueError("seekDB path must not be empty")  # noqa: TRY003
        return value


class SeekDBProfile:
    """An initialized embedded seekDB profile with explicit runtime ownership."""

    def __init__(self, *, database: AsyncDatabase, tables: tuple[Table, ...]) -> None:
        self.database = database
        self.tables = tables

    @classmethod
    @asynccontextmanager
    async def open(
        cls,
        config: SeekDBConfig,
        *,
        tables: tuple[Table, ...],
    ) -> AsyncIterator[SeekDBProfile]:
        """Start seekDB locally and connect through its async Unix socket."""

        path = config.path.expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        module = _load_binding()
        instance = await _open_instance(module, path)
        try:
            engine = _create_engine(config, instance.connection_options())
            database = AsyncDatabase.own(engine)
            profile = cls(database=database, tables=tables)
            try:
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


def _create_engine(config: SeekDBConfig, connection_options: Mapping[str, object]) -> AsyncEngine:
    options = dict(connection_options)
    username = str(options.pop("user", "root"))
    password_value = options.pop("password", None)
    host = str(options.pop("host", "localhost"))
    port_value = options.pop("port", None)
    # seekDB's handshake currently omits the autocommit status flag, so
    # aiomysql otherwise mistakes the default-on session for an explicit
    # transaction and rollback becomes ineffective.
    options["init_command"] = "SET autocommit = 0"
    url = URL.create(
        _DIALECT_DRIVER,
        username=username,
        password=None if password_value is None else str(password_value),
        host=host,
        port=None if port_value is None else int(cast(int | str, port_value)),
        database=config.database,
        query={"charset": "utf8mb4"},
    )
    _register_seekdb_dialect()
    return create_async_engine(
        url,
        connect_args=options,
        echo=config.echo,
        pool_pre_ping=config.pool_pre_ping,
    )


def _register_seekdb_dialect() -> None:
    dialect_registry.register(_DIALECT_REGISTRY_NAME, __name__, "AsyncSeekDBDialect")
