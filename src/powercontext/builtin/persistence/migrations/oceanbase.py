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

"""Probe and hold a session lock on a pinned OceanBase connection.

This is one prerequisite for a nontransactional-DDL executor, not that executor.
Deployment acceptance must additionally test its actual node/proxy routing.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection

from .models import MigrationError, digest


@dataclass(frozen=True)
class PinnedOceanBaseLock:
    connection: AsyncConnection
    name: str
    session_id: int

    async def verify(self) -> None:
        """Call before and after each DDL; a reconnected session is not trusted."""
        if self.connection.invalidated or self.connection.closed:
            raise MigrationError("migration_lock_lost", "The lock connection was lost; inspect DDL before recovery.")
        current = await self.connection.scalar(text("SELECT CONNECTION_ID()"))
        owner = await self.connection.scalar(text("SELECT IS_USED_LOCK(:name)"), {"name": self.name})
        if current != self.session_id or owner != self.session_id:
            raise MigrationError("migration_lock_lost", "The pinned session no longer owns the migration lock.")


@asynccontextmanager
async def oceanbase_migration_lock(
    connection: AsyncConnection, contender: AsyncConnection, *, database_id: str
) -> AsyncIterator[PinnedOceanBaseLock]:
    """Test exclusion and commit survival using two independently routed sessions.

    Both connections must be dedicated to maintenance. The caller supplies a
    stable tenant/schema identity, not a hostname that changes through a proxy.
    A rejected capability never falls back to a process-local lock.
    """
    name = "pc-migrate-" + digest(database_id)[:48]
    owned = False
    try:
        try:
            owner_id = await connection.scalar(text("SELECT CONNECTION_ID()"))
            other_id = await contender.scalar(text("SELECT CONNECTION_ID()"))
            if owner_id == other_id:
                raise MigrationError("migration_lock_unsupported", "Lock probing needs two distinct physical sessions.")
            acquired = await connection.scalar(text("SELECT GET_LOCK(:name, 0)"), {"name": name})
            if acquired == 0:
                raise MigrationError("migration_locked", "Another session holds the database migration lock.")
            if acquired != 1:
                raise MigrationError("migration_lock_unsupported", "Named-lock acquisition is unavailable.")
            owned = True
            await connection.commit()
            competing = await contender.scalar(text("SELECT GET_LOCK(:name, 0)"), {"name": name})
            if competing == 1:
                await contender.scalar(text("SELECT RELEASE_LOCK(:name)"), {"name": name})
            await contender.commit()
            if competing != 0:
                raise MigrationError(
                    "migration_lock_unsupported", "The lock does not exclude a second session across commit."
                )
            if not isinstance(owner_id, int):
                raise MigrationError("migration_lock_unsupported", "The backend did not return a physical session ID.")
            lock = PinnedOceanBaseLock(connection, name, owner_id)
            await lock.verify()
        except DBAPIError as error:
            raise MigrationError(
                "migration_lock_unsupported", "Named-lock capability could not be verified."
            ) from error
        yield lock
    finally:
        if owned and not connection.invalidated and not connection.closed:
            # Never reconnect simply to release: a replacement session owns no lock.
            # Releasing a lock must not commit unfinished work in the caller.
            await connection.rollback()
            await connection.scalar(text("SELECT RELEASE_LOCK(:name)"), {"name": name})
            await connection.commit()
