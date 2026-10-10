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

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import cast

import pytest
from sqlalchemy import Column, Integer, MetaData, Table, insert, select, update
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from powercontext.builtin.persistence.database import AsyncDatabase
from powercontext.builtin.persistence.errors import PersistenceError
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile

_TABLE = Table("snapshot_values", MetaData(), Column("value", Integer, nullable=False))


def test_snapshot_transaction_keeps_sqlite_reads_at_the_same_revision(tmp_path) -> None:
    async def scenario() -> None:
        config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'snapshot.db'}")
        async with SQLiteProfile.open(config, tables=(_TABLE,)) as profile:
            async with profile.database.transaction() as writer:
                await writer.execute(insert(_TABLE).values(value=1))
            async with profile.database.transaction(consistent_snapshot=True) as reader:
                assert await reader.scalar(select(_TABLE.c.value)) == 1
                async with profile.database.transaction() as writer:
                    await writer.execute(update(_TABLE).values(value=2))
                assert await reader.scalar(select(_TABLE.c.value)) == 1
            async with profile.database.transaction() as reader:
                assert await reader.scalar(select(_TABLE.c.value)) == 2

    asyncio.run(scenario())


def test_nested_snapshot_does_not_commit_the_shared_sqlite_write() -> None:
    async def scenario() -> None:
        async with SQLiteProfile.open(SQLiteConfig(), tables=(_TABLE,)) as profile:
            with pytest.raises(RuntimeError, match="abort outer write"):
                async with profile.database.transaction() as writer:
                    await writer.execute(insert(_TABLE).values(value=1))
                    async with profile.database.transaction(consistent_snapshot=True) as reader:
                        assert await reader.scalar(select(_TABLE.c.value)) == 1
                    assert await writer.scalar(select(_TABLE.c.value)) == 1
                    raise RuntimeError("abort outer write")  # noqa: TRY003
            async with profile.database.transaction() as reader:
                assert (await reader.execute(select(_TABLE.c.value))).all() == []

    asyncio.run(scenario())


class _MySQLConnection:
    """Expose the startup boundary without requiring an external backend.

    Actual SQL/isolation behavior is covered by the OceanBase E2E parameter.
    These faults represent a driver failing after applying a one-shot setting.
    """

    dialect = SimpleNamespace(name="mysql")

    def __init__(self, *, fail_at: str | None = None, cancellation: bool = False) -> None:
        self.fail_at = fail_at
        self.cancellation = cancellation
        self.statements: list[str] = []
        self.invalidated = False
        self.reached = asyncio.Event()

    async def exec_driver_sql(self, statement: str) -> None:
        self.statements.append(statement)
        if self.fail_at is not None and statement.startswith(self.fail_at):
            if self.cancellation:
                self.reached.set()
                await asyncio.Event().wait()
            raise RuntimeError("snapshot setup failed")  # noqa: TRY003

    async def invalidate(self) -> None:
        self.invalidated = True


class _MySQLEngine:
    def __init__(self, connection: _MySQLConnection) -> None:
        self.connection = connection
        self.disposed = False

    @asynccontextmanager
    async def begin(self):
        yield cast(AsyncConnection, self.connection)

    async def dispose(self) -> None:
        self.disposed = True


@pytest.mark.parametrize("fail_at", ["SET", "START"])
@pytest.mark.parametrize("cancellation", [False, True])
def test_failed_mysql_snapshot_setup_discards_the_connection(fail_at: str, cancellation: bool) -> None:
    async def scenario() -> None:
        connection = _MySQLConnection(fail_at=fail_at, cancellation=cancellation)
        engine = _MySQLEngine(connection)
        database = AsyncDatabase.own(cast(AsyncEngine, engine))

        async def enter() -> None:
            async with database.transaction(consistent_snapshot=True):
                pytest.fail("failed setup must not enter the transaction body")

        if cancellation:
            task = asyncio.create_task(enter())
            try:
                await asyncio.wait_for(connection.reached.wait(), timeout=1)
            finally:
                task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            with pytest.raises(RuntimeError, match="snapshot setup failed"):
                await enter()
        assert connection.invalidated, "an unconsumed next-transaction setting must not return to the pool"
        # Failure/cancellation must release lifecycle accounting as well.
        await asyncio.wait_for(database.close(), timeout=1)
        assert engine.disposed

    asyncio.run(scenario())


def test_mysql_snapshot_is_opt_in_and_does_not_change_the_session_default() -> None:
    async def scenario() -> None:
        connection = _MySQLConnection()
        database = AsyncDatabase.attach(cast(AsyncEngine, _MySQLEngine(connection)))
        async with database.transaction():
            pass
        assert connection.statements == ["START TRANSACTION"]
        connection.statements.clear()
        async with database.transaction(consistent_snapshot=True):
            pass
        assert connection.statements == [
            "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ",
            "START TRANSACTION WITH CONSISTENT SNAPSHOT",
        ]
        connection.statements.clear()
        async with database.transaction():
            pass
        assert connection.statements == ["START TRANSACTION"]
        assert not connection.invalidated
        await database.close()

    asyncio.run(scenario())


def test_snapshot_request_cannot_upgrade_a_borrowed_mysql_transaction() -> None:
    async def scenario() -> None:
        connection = _MySQLConnection()
        database = AsyncDatabase.own(cast(AsyncEngine, _MySQLEngine(connection)), shared_connection=True)
        async with database.transaction():
            with pytest.raises(PersistenceError, match="existing transaction"):
                async with database.transaction(consistent_snapshot=True):
                    pytest.fail("a borrowed READ COMMITTED transaction cannot become a stable snapshot")
        assert connection.statements == ["START TRANSACTION"]
        assert not connection.invalidated, "the outer transaction still belongs to its caller"
        await database.close()

    asyncio.run(scenario())
