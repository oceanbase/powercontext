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
import time
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager, nullcontext

from aiosqlite import Connection as SQLiteConnection
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from powercontext.builtin.persistence.errors import DatabaseClosedError

# Repositories that read a whole Scope selection in one statement chunk it to stay
# below the lowest bind-parameter ceiling across the supported backends.
SELECTION_BATCH_SIZE = 500


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
                if connection.dialect.name == "mysql":
                    # The MySQL dialect's begin hook is a no-op. Explicitly start
                    # the owned transaction even when the server session uses autocommit.
                    await connection.exec_driver_sql("START TRANSACTION")
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

    @asynccontextmanager
    async def _model_usage_transaction(self, timeout_seconds: float) -> AsyncIterator[AsyncConnection]:
        """Own a best-effort usage transaction, never join a caller's transaction.

        Only the recorder's separate consumer task calls this method. SQLite SQL
        is interrupted natively, not by cancelling SQLAlchemy (which invalidates
        its connection and destroys a StaticPool's in-memory database).
        """

        deadline = asyncio.get_running_loop().time() + timeout_seconds
        async with self._state_changed:
            if self._closed or self._closing:
                raise DatabaseClosedError
            self._active_transactions += 1
        guard = self._shared_connection_lock
        acquired = False
        try:
            if guard is not None:
                # Cancellation is safe here: no driver operation has started.
                async with asyncio.timeout_at(deadline):
                    await guard.acquire()
                acquired = True
            connection = self._engine.connect()
            # Never cancel checkout. AsyncConnection.start assigns the pooled
            # connection only after its greenlet returns, so a cancelled start
            # abandons a connection that was already checked out. The pool's own
            # timeout bounds this, and a stalled recorder costs only telemetry.
            await connection.start()
            try:
                if connection.dialect.name == "sqlite":
                    async with _sqlite_model_usage_transaction(connection, deadline):
                        yield connection
                else:
                    async with _mysql_model_usage_transaction(connection, deadline):
                        yield connection
            finally:
                await connection.close()
        finally:
            if acquired and guard is not None:
                guard.release()
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


@asynccontextmanager
async def _sqlite_model_usage_transaction(connection: AsyncConnection, deadline: float) -> AsyncIterator[None]:
    driver = (await connection.get_raw_connection()).driver_connection
    if not isinstance(driver, SQLiteConnection):
        raise TypeError("model usage requires the aiosqlite driver")  # noqa: TRY003
    loop = asyncio.get_running_loop()
    remaining = deadline - loop.time()
    if remaining <= 0:
        raise TimeoutError
    # The worker thread uses a monotonic clock, not the event loop's clock API.
    stop_at = time.monotonic() + remaining
    async with driver.execute("PRAGMA busy_timeout") as cursor:
        row = await cursor.fetchone()
    if row is None:
        raise RuntimeError("SQLite did not return busy_timeout")  # noqa: TRY003
    busy_timeout = int(row[0])
    timer: asyncio.TimerHandle | None = None
    interrupted = False

    def _past_deadline() -> int:
        # Abort at most one statement, then stop voting. A handler left installed
        # by a cancelled cleanup would otherwise keep its already-expired deadline
        # and interrupt every later statement on this pooled connection.
        nonlocal interrupted
        if interrupted:
            return 0
        if time.monotonic() >= stop_at:
            interrupted = True
            return 1
        return 0

    try:
        # The recorder's own consumer owns this connection and its budget, so
        # waiting out a concurrent business writer here cannot consume a model
        # deadline. Bound the wait by the same deadline that stops the work:
        # abandoning on first contact would drop most records on file-backed
        # SQLite, where one writer at a time is the normal state.
        acquire_timeout_ms = max(int(remaining * 1000), 1)
        async with driver.execute(f"PRAGMA busy_timeout = {acquire_timeout_ms}"):
            pass
        await driver.set_progress_handler(_past_deadline, 1_000)
        # aiosqlite.interrupt() itself calls sqlite3.Connection.interrupt directly,
        # without queuing work. Use that same thread-safe primitive in a timer so
        # each record needs no additional asyncio task.
        timer = loop.call_at(deadline, driver._conn.interrupt)
        async with connection.begin():
            if loop.time() >= deadline:
                raise TimeoutError
            # sqlite3's legacy mode does not begin a transaction for SELECT.
            # Include the Scope existence read in the write's actual snapshot.
            await connection.exec_driver_sql("BEGIN")
            yield
            if loop.time() >= deadline:
                raise TimeoutError
        # busy_timeout stays bounded through COMMIT and any automatic ROLLBACK.
    finally:
        if timer is not None:
            timer.cancel()
        await _restore_sqlite_usage_connection(driver, busy_timeout)
    # SQLite cannot interrupt a user-defined function or a blocked filesystem
    # syscall. Await native cleanup rather than falsely report it as cancelled.


def _disabled_progress() -> int:
    """Unused callback for disabling SQLite's progress handler."""

    # SQLite disables the handler when its step count is below one, so this is
    # never invoked; it only satisfies the driver's callable-only signature.
    return 0


async def _restore_sqlite_usage_connection(driver: SQLiteConnection, busy_timeout: int) -> None:
    """Undo this write's connection-level changes, each step independently.

    A step that fails or is cancelled must not skip the rest: a pooled connection
    left with a truncated busy_timeout makes every later business write fail
    early, and one left with an open transaction keeps holding the write lock.
    """

    try:
        await driver.set_progress_handler(_disabled_progress, 0)
    finally:
        try:
            if driver.in_transaction:
                await driver.rollback()
        finally:
            async with driver.execute(f"PRAGMA busy_timeout = {busy_timeout}"):
                pass


@asynccontextmanager
async def _mysql_model_usage_transaction(connection: AsyncConnection, deadline: float) -> AsyncIterator[None]:
    # OceanBase's official async dialect uses aiomysql. Its cancellation path
    # closes the socket; reject other drivers instead of guessing server options.
    from aiomysql import Connection as MySQLConnection

    driver = (await connection.get_raw_connection()).driver_connection
    if connection.dialect.name != "mysql" or not isinstance(driver, MySQLConnection):
        raise TypeError("model usage requires the aiomysql driver")  # noqa: TRY003
    timer = asyncio.get_running_loop().call_at(deadline, driver.close)
    try:
        async with asyncio.timeout_at(deadline), connection.begin():
            await connection.exec_driver_sql("START TRANSACTION")
            yield
    finally:
        timer.cancel()
        if driver.closed:
            # Never return a timed-out socket to the pool. A lost COMMIT reply is
            # an unknown outcome, not permission to repeat the increment.
            await connection.invalidate()


def is_transaction_contention(error: OperationalError) -> bool:
    """Recognize only rollback-safe SQLite busy and MySQL transaction conflicts."""
    original = error.orig
    sqlite_code = getattr(original, "sqlite_errorcode", None)
    if isinstance(sqlite_code, int) and sqlite_code & 0xFF in {5, 6}:
        return True
    return bool(original is not None and original.args and original.args[0] in {1205, 1213})
