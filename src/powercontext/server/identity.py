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

"""Durable identity for one configured Server deployment."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import TypeVar
from uuid import uuid4

from sqlalchemy import CheckConstraint, Column, Integer, MetaData, String, Table, insert, select, update
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.schema import CreateTable

from powercontext.builtin.persistence.database import AsyncDatabase
from powercontext.builtin.persistence.oceanbase import OceanBaseConfig, OceanBaseProfile
from powercontext.builtin.persistence.seekdb import SeekDBConfig, SeekDBProfile
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile, is_sqlite_lock_error
from powercontext.builtin.runtime.config import DatabaseConfig

_IDENTITY_METADATA = MetaData()
_SINGLETON_KEY = 1
_INITIALIZATION_TIMEOUT_SECONDS = 5.0
_LOCK_RETRY_SECONDS = 0.05
_T = TypeVar("_T")

SERVER_IDENTITY_TABLE = Table(
    "pc_server_identity",
    _IDENTITY_METADATA,
    Column("singleton_key", Integer, primary_key=True, autoincrement=False),
    Column("server_id", String(36), nullable=False, unique=True),
    CheckConstraint("singleton_key = 1", name="ck_pc_server_identity_singleton"),
)


class ServerIdentityRepository:
    """Own the singleton deployment identity in the primary relational backend."""

    def __init__(self, database: AsyncDatabase) -> None:
        self._database = database

    async def initialize(self) -> None:
        """Create the identity schema safely across concurrent initializers."""

        await _retry_initialization(self._initialize)

    async def _initialize(self) -> None:
        async with self._database.transaction() as connection:
            await connection.execute(CreateTable(SERVER_IDENTITY_TABLE, if_not_exists=True))

    async def load_or_create(self) -> str:
        """Return the durable identity, creating it once across concurrent replicas."""

        return await _retry_initialization(self._load_or_create)

    async def _load_or_create(self) -> str:
        server_id = await self._load()
        if server_id is not None:
            return server_id

        candidate = str(uuid4())
        try:
            async with self._database.transaction() as connection:
                await connection.execute(
                    insert(SERVER_IDENTITY_TABLE).values(singleton_key=_SINGLETON_KEY, server_id=candidate)
                )
        except IntegrityError:
            # A concurrent initializer may have committed the singleton first.
            server_id = await self._load()
            if server_id is None:
                raise
            return server_id
        return candidate

    async def rotate(self) -> str:
        """Replace the identity during an operator-confirmed offline clone procedure."""

        candidate = str(uuid4())
        async with self._database.transaction() as connection:
            result = await connection.execute(
                update(SERVER_IDENTITY_TABLE)
                .where(SERVER_IDENTITY_TABLE.c.singleton_key == _SINGLETON_KEY)
                .values(server_id=candidate)
            )
            if result.rowcount == 0:
                await connection.execute(
                    insert(SERVER_IDENTITY_TABLE).values(singleton_key=_SINGLETON_KEY, server_id=candidate)
                )
        return candidate

    async def _load(self) -> str | None:
        async with self._database.transaction() as connection:
            result = await connection.execute(
                select(SERVER_IDENTITY_TABLE.c.server_id).where(SERVER_IDENTITY_TABLE.c.singleton_key == _SINGLETON_KEY)
            )
            value = result.scalar_one_or_none()
        return None if value is None else str(value)


async def _retry_initialization(operation: Callable[[], Awaitable[_T]]) -> _T:
    # SQLite's busy timeout does not wait for shared-cache table/schema locks.
    # Replay only these rolled-back, idempotent initialization operations.
    loop = asyncio.get_running_loop()
    deadline = loop.time() + _INITIALIZATION_TIMEOUT_SECONDS
    while True:
        try:
            return await operation()
        except OperationalError as error:
            remaining = deadline - loop.time()
            if not is_sqlite_lock_error(error) or remaining <= 0:
                raise
            await asyncio.sleep(min(_LOCK_RETRY_SECONDS, remaining))
            if loop.time() >= deadline:
                raise


@asynccontextmanager
async def open_server_identity_repository(config: DatabaseConfig) -> AsyncIterator[ServerIdentityRepository]:
    """Open the configured primary database with only the Server identity schema."""

    tables: tuple[Table, ...] = ()
    if isinstance(config, SQLiteConfig):
        opened = SQLiteProfile.open(config, tables=tables)
    elif isinstance(config, OceanBaseConfig):
        opened = OceanBaseProfile.open(config, tables=tables)
    elif isinstance(config, SeekDBConfig):
        opened = SeekDBProfile.open(config, tables=tables)
    else:
        raise TypeError(f"unsupported Server identity database: {type(config).__name__}")  # noqa: TRY003
    async with opened as profile:
        repository = ServerIdentityRepository(profile.database)
        await repository.initialize()
        yield repository


__all__ = ("SERVER_IDENTITY_TABLE", "ServerIdentityRepository", "open_server_identity_repository")
