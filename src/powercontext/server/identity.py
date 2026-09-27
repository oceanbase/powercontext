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

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from uuid import uuid4

from sqlalchemy import CheckConstraint, Column, Integer, MetaData, String, Table, insert, select, update
from sqlalchemy.exc import IntegrityError

from powercontext.builtin.persistence.database import AsyncDatabase
from powercontext.builtin.persistence.oceanbase import OceanBaseConfig, OceanBaseProfile
from powercontext.builtin.persistence.seekdb import SeekDBConfig, SeekDBProfile
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.runtime.config import DatabaseConfig

_IDENTITY_METADATA = MetaData()
_SINGLETON_KEY = 1

SERVER_IDENTITY_TABLE = Table(
    "pc_server_identity",
    _IDENTITY_METADATA,
    Column("singleton_key", Integer, primary_key=True, autoincrement=False),
    Column("server_id", String(36), nullable=False, unique=True),
    CheckConstraint("singleton_key = 1", name="ck_pc_server_identity_singleton"),
)


class ServerIdentityRepository:
    """Own the singleton deployment identity in the primary relational backend."""

    def __init__(self, database: AsyncDatabase, *, id_factory: Callable[[], str] | None = None) -> None:
        self._database = database
        self._id_factory = (lambda: str(uuid4())) if id_factory is None else id_factory

    async def load_or_create(self) -> str:
        """Return the durable identity, creating it once across concurrent replicas."""

        server_id = await self._load()
        if server_id is not None:
            return server_id

        candidate = self._id_factory()
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

        candidate = self._id_factory()
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


@asynccontextmanager
async def open_server_identity_repository(config: DatabaseConfig) -> AsyncIterator[ServerIdentityRepository]:
    """Open the configured primary database with only the Server identity schema."""

    tables = (SERVER_IDENTITY_TABLE,)
    if isinstance(config, SQLiteConfig):
        opened = SQLiteProfile.open(config, tables=tables)
    elif isinstance(config, OceanBaseConfig):
        opened = OceanBaseProfile.open(config, tables=tables)
    elif isinstance(config, SeekDBConfig):
        opened = SeekDBProfile.open(config, tables=tables)
    else:
        raise TypeError(f"unsupported Server identity database: {type(config).__name__}")  # noqa: TRY003
    async with opened as profile:
        yield ServerIdentityRepository(profile.database)


__all__ = ("SERVER_IDENTITY_TABLE", "ServerIdentityRepository", "open_server_identity_repository")
