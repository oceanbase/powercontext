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

"""One-engine SQLite profile using SQLAlchemy's aiosqlite dialect."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from weakref import WeakKeyDictionary

import sqlite_vec
from aiosqlite import Connection
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import Table, event
from sqlalchemy.engine import make_url
from sqlalchemy.engine.interfaces import DBAPIConnection
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import StaticPool

from powercontext.builtin.persistence.database import AsyncDatabase
from powercontext.builtin.persistence.schema import create_tables

_WARMUP_RETRY_SECONDS = 0.05
_WARMUP_LOCKS: WeakKeyDictionary[asyncio.AbstractEventLoop, dict[str, asyncio.Lock]] = WeakKeyDictionary()


class SQLiteConfig(BaseModel):
    """Validated component configuration for a SQLite database profile."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["sqlite"] = "sqlite"
    url: str = "sqlite+aiosqlite:///:memory:"
    busy_timeout_ms: int = Field(default=5_000, ge=0)
    journal_mode: Literal["WAL", "DELETE", "MEMORY"] = "WAL"
    foreign_keys: bool = True
    echo: bool = False

    @field_validator("url")
    @classmethod
    def require_async_sqlite(cls, value: str) -> str:
        """Reject sync or non-SQLite dialects at the configuration boundary."""

        url = make_url(value)
        if url.drivername != "sqlite+aiosqlite":
            raise ValueError("SQLite profile URL must use sqlite+aiosqlite")  # noqa: TRY003
        return value

    @property
    def is_in_memory(self) -> bool:
        """Return whether this profile stores its database only in process memory."""

        return _is_memory_url(self.url)


class SQLiteProfile:
    """An initialized SQLite database profile."""

    def __init__(self, *, database: AsyncDatabase, tables: tuple[Table, ...]) -> None:
        self.database = database
        self.tables = tables

    @classmethod
    @asynccontextmanager
    async def open(
        cls,
        config: SQLiteConfig,
        *,
        tables: tuple[Table, ...],
        load_vector_extension: bool = False,
    ) -> AsyncIterator[SQLiteProfile]:
        """Create, initialize and exclusively own one SQLite engine."""

        _create_database_directory(config.url)
        engine_options: dict[str, object] = {"echo": config.echo, "hide_parameters": True}
        if config.is_in_memory:
            engine_options["poolclass"] = StaticPool
        engine = create_async_engine(config.url, **engine_options)
        _configure_sqlite(engine, config, load_vector_extension=load_vector_extension)
        database = AsyncDatabase.own(engine)
        profile = cls(database=database, tables=tables)
        try:
            await _warm_sqlite(engine, config)
            async with database.transaction() as connection:
                await create_tables(connection, tables)
            yield profile
        finally:
            await database.close()


def _is_memory_url(value: str) -> bool:
    database = make_url(value).database
    return database in {None, "", ":memory:"}


def _create_database_directory(value: str) -> None:
    database = make_url(value).database
    if not database or database == ":memory:":
        return
    Path(database).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)


def _configure_sqlite(
    engine: AsyncEngine,
    config: SQLiteConfig,
    *,
    load_vector_extension: bool,
) -> None:
    @event.listens_for(engine.sync_engine, "connect")
    def set_pragmas(dbapi_connection: DBAPIConnection, _connection_record: object) -> None:
        if load_vector_extension:
            dbapi_connection.run_async(_load_sqlite_vec)
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute(f"PRAGMA busy_timeout = {config.busy_timeout_ms}")
            cursor.execute(f"PRAGMA foreign_keys = {'ON' if config.foreign_keys else 'OFF'}")
        finally:
            cursor.close()


async def _load_sqlite_vec(connection: Connection) -> None:
    await connection.enable_load_extension(True)
    try:
        await connection.load_extension(sqlite_vec.loadable_path())
    finally:
        await connection.enable_load_extension(False)


async def _warm_sqlite(engine: AsyncEngine, config: SQLiteConfig) -> None:
    """Set the database-wide journal mode before concurrent schema initialization."""

    async with _warmup_lock(config.url):
        loop = asyncio.get_running_loop()
        deadline = loop.time() + config.busy_timeout_ms / 1_000
        while True:
            try:
                async with engine.connect() as connection:
                    await connection.exec_driver_sql(f"PRAGMA journal_mode = {config.journal_mode}")
                    await connection.commit()
            except OperationalError as error:
                remaining = deadline - loop.time()
                if not _database_is_locked(error) or remaining <= 0:
                    raise
                await asyncio.sleep(min(_WARMUP_RETRY_SECONDS, remaining))
            else:
                return


def _warmup_lock(value: str) -> asyncio.Lock:
    if _is_memory_url(value):
        return asyncio.Lock()
    loop = asyncio.get_running_loop()
    locks = _WARMUP_LOCKS.setdefault(loop, {})
    key = str(Path(make_url(value).database or "").expanduser().resolve())
    return locks.setdefault(key, asyncio.Lock())


def _database_is_locked(error: OperationalError) -> bool:
    return "database is locked" in str(error.orig).lower()
