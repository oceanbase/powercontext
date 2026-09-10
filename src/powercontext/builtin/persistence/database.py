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

"""Async engine ownership and explicit transaction boundaries."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping
from contextlib import AbstractAsyncContextManager, asynccontextmanager, nullcontext
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Table, insert
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from powercontext.builtin.persistence.errors import DatabaseClosedError, InvalidStoredColumnError


async def database_now(connection: AsyncConnection, /) -> datetime:
    """Return normalized UTC-naive database time for coordination decisions."""

    statement = "SELECT UTC_TIMESTAMP(6)" if connection.dialect.name == "mysql" else "SELECT CURRENT_TIMESTAMP"
    value = (await connection.exec_driver_sql(statement)).scalar_one()
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime):
        raise InvalidStoredColumnError("CURRENT_TIMESTAMP", "a datetime")
    if value.tzinfo is not None:
        return value.astimezone(UTC).replace(tzinfo=None)
    return value


async def insert_if_absent(
    connection: AsyncConnection,
    table: Table,
    values: Mapping[str, Any],
    /,
) -> bool:
    """Insert validated values without aborting the transaction on a unique-key race.

    OceanBase's async dialect deliberately leaves ``do_begin`` empty.  A
    zero-row locking update therefore may not establish a server transaction,
    which makes a SAVEPOINT-based insert race unsafe.  The supported SQL
    dialects provide a conflict-tolerant insert that starts the real
    transaction and reports whether this caller created the row.
    """

    statement = insert(table).values(**values)
    if connection.dialect.name == "mysql":
        result = await connection.execute(statement.prefix_with("IGNORE"))
        return result.rowcount == 1
    if connection.dialect.name == "sqlite":
        result = await connection.execute(statement.prefix_with("OR IGNORE"))
        return result.rowcount == 1

    try:
        async with connection.begin_nested():
            await connection.execute(statement)
    except IntegrityError:
        return False
    return True


class AsyncDatabase:
    """Own or attach to one SQLAlchemy async engine.

    Repositories receive the yielded ``AsyncConnection`` and never own this
    object. Closing an attached database leaves the caller's engine available.
    """

    def __init__(self, engine: AsyncEngine, *, owns_engine: bool, shared_connection: bool = False) -> None:
        self._engine = engine
        self._owns_engine = owns_engine
        self._closed = False
        self._closing = False
        self._active_transactions = 0
        self._state_changed = asyncio.Condition()
        self._close_lock = asyncio.Lock()
        # In-memory SQLite shares one physical connection. Its transactions
        # cannot overlap, including read snapshots used by tag pagination.
        self._shared_connection_lock = asyncio.Lock() if shared_connection else None
        self._transaction_owner: asyncio.Task[object] | None = None
        self._shared_connection: AsyncConnection | None = None

    @classmethod
    def attach(cls, engine: AsyncEngine, /) -> AsyncDatabase:
        """Use a caller-owned async engine without taking disposal ownership."""

        return cls(engine, owns_engine=False)

    @classmethod
    def own(cls, engine: AsyncEngine, /, *, shared_connection: bool = False) -> AsyncDatabase:
        """Take disposal ownership of an already configured async engine."""

        return cls(engine, owns_engine=True, shared_connection=shared_connection)

    @property
    def engine(self) -> AsyncEngine:
        """Return the upstream engine used by this database."""

        return self._engine

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[AsyncConnection]:
        """Yield a connection in a transaction owned by the calling use case."""

        owner = asyncio.current_task()
        if self._shared_connection is not None and self._transaction_owner is owner:
            # Nested lookups on a single-connection profile must join their
            # caller's transaction, not acquire or commit that connection again.
            yield self._shared_connection
            return
        async with self._state_changed:
            if self._closed or self._closing:
                raise DatabaseClosedError
            self._active_transactions += 1
        try:
            guard = self._shared_connection_lock if self._shared_connection_lock is not None else nullcontext()
            async with guard, self._engine.begin() as connection:
                if self._shared_connection_lock is not None:
                    self._transaction_owner = owner
                    self._shared_connection = connection
                try:
                    yield connection
                finally:
                    self._transaction_owner = None
                    self._shared_connection = None
        finally:
            async with self._state_changed:
                self._active_transactions -= 1
                self._state_changed.notify_all()

    def connection(
        self,
        bound: AsyncConnection | None = None,
    ) -> AbstractAsyncContextManager[AsyncConnection]:
        """Use ``bound`` when supplied, otherwise own a transaction."""

        return self.transaction() if bound is None else nullcontext(bound)

    async def ping(self) -> None:
        """Verify that the configured database can execute a trivial query."""

        async with self.transaction() as connection:
            await connection.exec_driver_sql("SELECT 1")

    async def close(self) -> None:
        """Drain active transactions, then dispose only an owned engine."""

        async with self._close_lock:
            if self._closed:
                return
            async with self._state_changed:
                self._closing = True
                try:
                    await self._state_changed.wait_for(lambda: self._active_transactions == 0)
                except BaseException:
                    self._closing = False
                    self._state_changed.notify_all()
                    raise
            try:
                if self._owns_engine:
                    await self._engine.dispose()
            except BaseException:
                async with self._state_changed:
                    self._closing = False
                    self._state_changed.notify_all()
                raise
            async with self._state_changed:
                self._closed = True
                self._closing = False
                self._state_changed.notify_all()


def is_transaction_contention(error: OperationalError) -> bool:
    """Recognize only rollback-safe SQLite busy and MySQL transaction conflicts."""
    original = error.orig
    sqlite_code = getattr(original, "sqlite_errorcode", None)
    if isinstance(sqlite_code, int) and sqlite_code & 0xFF in {5, 6}:
        return True
    return bool(original is not None and original.args and original.args[0] in {1205, 1213})
