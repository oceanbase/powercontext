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
from functools import partial
from threading import Event as ThreadEvent
from typing import cast

import aiosqlite
import anyio
import pytest
from pydantic import ValidationError
from sqlalchemy import func, insert, select
from sqlalchemy.exc import IntegrityError, PendingRollbackError
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine
from sqlalchemy.pool import QueuePool

from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.persistence.sqlite import profile as sqlite_profile
from powercontext.builtin.persistence.tables import (
    ARTIFACT_LINEAGE_SOURCES_TABLE,
    ARTIFACTS_TABLE,
    SHARED_TABLES,
)


def test_sqlite_stop_waiters_finish_when_another_waiter_is_cancelled(tmp_path) -> None:
    async def scenario() -> None:
        config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'stop.db'}")
        async with SQLiteProfile.open(config, tables=()) as profile, profile.database.engine.connect() as connection:
            driver = cast(aiosqlite.Connection, (await connection.get_raw_connection()).driver_connection)
            entered = ThreadEvent()
            release = ThreadEvent()

            def hold_worker() -> None:
                entered.set()
                assert release.wait(5)

            blocked = asyncio.create_task(driver._execute(hold_worker))
            try:
                assert await asyncio.to_thread(entered.wait, 2)
                first, second = driver.stop(), driver.stop()
                assert first is not None and second is not None
                first.cancel()
                release.set()
                await asyncio.wait_for(blocked, 2)
                await asyncio.wait_for(second, 2)
                assert not second.cancelled()
            finally:
                release.set()
                await asyncio.gather(blocked, return_exceptions=True)
                await connection.invalidate()

    asyncio.run(scenario())


@pytest.mark.parametrize("phase", ["rollback", "close"])
def test_transaction_finishes_cleanup_before_propagating_repeated_cancellation(tmp_path, monkeypatch, phase) -> None:
    async def scenario() -> None:
        entered = asyncio.Event()
        cleaning = asyncio.Event()
        release = asyncio.Event()
        config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'cleanup.db'}")
        async with SQLiteProfile.open(config, tables=()) as profile:
            database = profile.database
            pool = cast(QueuePool, database.engine.pool)
            async with database.transaction() as connection:
                await connection.exec_driver_sql("CREATE TABLE probe (value INTEGER)")

            async def operation() -> None:
                async with database.transaction() as connection:
                    if phase == "rollback":
                        driver = cast(aiosqlite.Connection, (await connection.get_raw_connection()).driver_connection)
                        original_rollback = driver.rollback

                        async def delayed_rollback() -> None:
                            if not cleaning.is_set():
                                cleaning.set()
                                await release.wait()
                            await original_rollback()

                        monkeypatch.setattr(driver, "rollback", delayed_rollback)
                    else:
                        original_close = AsyncConnection.close

                        async def delayed_close(closing: AsyncConnection) -> None:
                            if closing is connection:
                                cleaning.set()
                                await release.wait()
                            await original_close(closing)

                        monkeypatch.setattr(AsyncConnection, "close", delayed_close)
                    await connection.exec_driver_sql("INSERT INTO probe VALUES (1)")
                    entered.set()
                    await asyncio.Event().wait()

            pending = asyncio.create_task(operation())
            try:
                await asyncio.wait_for(entered.wait(), 2)
                pending.cancel()
                await asyncio.wait_for(cleaning.wait(), 2)
                pending.cancel()
                done, _ = await asyncio.wait({pending}, timeout=0.02)
                assert not done, "cancellation returned before transaction cleanup finished"
                assert pool.checkedout() == 1
                assert database._active_transactions == 1
            finally:
                release.set()
                if not pending.done():
                    pending.cancel()
                await asyncio.wait_for(asyncio.gather(pending, return_exceptions=True), 2)
            assert pending.cancelled()
            assert pool.checkedout() == 0
            async with database.transaction() as connection:
                assert (await connection.exec_driver_sql("SELECT count(*) FROM probe")).scalar_one() == 0

    asyncio.run(scenario())


def test_shared_transaction_keeps_its_lock_until_cancelled_rollback_finishes(monkeypatch) -> None:
    async def scenario() -> None:
        entered, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        contender_started, contender_entered = asyncio.Event(), asyncio.Event()
        async with SQLiteProfile.open(SQLiteConfig(), tables=()) as profile:
            database = profile.database
            async with database.transaction() as connection:
                await connection.exec_driver_sql("CREATE TABLE probe (value INTEGER)")

            async def operation() -> None:
                async with database.transaction() as connection:
                    async with database.transaction() as nested:
                        assert nested is connection
                        await nested.exec_driver_sql("INSERT INTO probe VALUES (1)")
                    driver = cast(aiosqlite.Connection, (await connection.get_raw_connection()).driver_connection)
                    original = driver.rollback

                    async def delayed_rollback() -> None:
                        if not cleaning.is_set():
                            cleaning.set()
                            await release.wait()
                        await original()

                    monkeypatch.setattr(driver, "rollback", delayed_rollback)
                    entered.set()
                    await asyncio.Event().wait()

            async def contender() -> None:
                contender_started.set()
                async with database.transaction() as connection:
                    contender_entered.set()
                    assert (await connection.exec_driver_sql("SELECT count(*) FROM probe")).scalar_one() == 0
                    await connection.exec_driver_sql("INSERT INTO probe VALUES (2)")

            pending = asyncio.create_task(operation())
            competing = None
            try:
                await asyncio.wait_for(entered.wait(), 2)
                pending.cancel()
                await asyncio.wait_for(cleaning.wait(), 2)
                competing = asyncio.create_task(contender())
                await asyncio.wait_for(contender_started.wait(), 2)
                pending.cancel()
                done, _ = await asyncio.wait({competing}, timeout=0.02)
                assert not done
                assert not contender_entered.is_set()
            finally:
                release.set()
                if not pending.done():
                    pending.cancel()
                await asyncio.wait_for(asyncio.gather(pending, return_exceptions=True), 2)
            assert pending.cancelled()
            assert competing is not None
            await asyncio.wait_for(competing, 2)
            async with database.transaction() as connection:
                assert (await connection.exec_driver_sql("SELECT value FROM probe")).scalar_one() == 2

    asyncio.run(scenario())


@pytest.mark.parametrize("phase", ["body", "sql"])
def test_repeated_transaction_cancellation_does_not_exhaust_the_pool(tmp_path, monkeypatch, phase) -> None:
    monkeypatch.setattr(
        sqlite_profile,
        "create_async_engine",
        partial(create_async_engine, pool_size=1, max_overflow=0, pool_timeout=0.5),
    )

    async def scenario() -> None:
        config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'repeated.db'}")
        async with SQLiteProfile.open(config, tables=()) as profile:
            database = profile.database
            pool = cast(QueuePool, database.engine.pool)
            async with database.transaction() as connection:
                await connection.exec_driver_sql("CREATE TABLE probe (value INTEGER)")
            for _ in range(10):
                entered = asyncio.Event()
                release = ThreadEvent()

                async def operation(entered: asyncio.Event = entered, release: ThreadEvent = release) -> None:
                    async with database.transaction() as connection:
                        await connection.exec_driver_sql("INSERT INTO probe VALUES (1)")
                        if phase == "sql":
                            driver = cast(
                                aiosqlite.Connection, (await connection.get_raw_connection()).driver_connection
                            )
                            loop = asyncio.get_running_loop()

                            def wait_for_release() -> int:
                                loop.call_soon_threadsafe(entered.set)
                                assert release.wait(5)
                                return 1

                            await driver.create_function("wait_for_release", 0, wait_for_release)
                            await connection.exec_driver_sql("SELECT wait_for_release()")
                        else:
                            entered.set()
                            await asyncio.Event().wait()

                try:
                    async with anyio.create_task_group() as group:
                        group.start_soon(operation)
                        await asyncio.wait_for(entered.wait(), 2)
                        group.cancel_scope.cancel()
                        release.set()
                finally:
                    release.set()
                assert pool.checkedout() == 0
                await asyncio.wait_for(database.ping(), 2)
                async with database.transaction() as connection:
                    assert (await connection.exec_driver_sql("SELECT count(*) FROM probe")).scalar_one() == 0

    asyncio.run(scenario())


def test_cancelled_checkout_does_not_keep_the_only_pool_slot(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        sqlite_profile,
        "create_async_engine",
        partial(create_async_engine, pool_size=1, max_overflow=0, pool_timeout=0.5),
    )

    async def scenario() -> None:
        entered = asyncio.Event()
        config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'checkout.db'}")
        async with SQLiteProfile.open(config, tables=()) as profile:
            database = profile.database
            pool = cast(QueuePool, database.engine.pool)

            async def blocked_checkout() -> None:
                entered.set()
                async with database.transaction():
                    pytest.fail("the held pool slot was unexpectedly available")

            async with database.transaction():
                pending = asyncio.create_task(blocked_checkout())
                await asyncio.wait_for(entered.wait(), 2)
                pending.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(pending, 2)
                assert pool.checkedout() == 1
            assert pool.checkedout() == 0
            await asyncio.wait_for(database.ping(), 2)

    asyncio.run(scenario())


@pytest.mark.parametrize("in_memory", [False, True], ids=["file", "memory"])
def test_interrupted_write_cannot_commit_earlier_work(tmp_path, in_memory) -> None:
    """An interrupted write rolls SQLite's whole native transaction back.

    SQLite discards the entire transaction when an INSERT, UPDATE or DELETE is
    interrupted, so a caller that catches the error and keeps writing must not
    be able to commit the later work on top of that rollback.
    """

    release = ThreadEvent()

    async def scenario() -> None:
        url = "sqlite+aiosqlite:///:memory:" if in_memory else f"sqlite+aiosqlite:///{tmp_path / 'atomicity.db'}"

        def slow(value: int) -> int:
            release.wait(10)
            return value

        async with SQLiteProfile.open(SQLiteConfig(url=url), tables=()) as profile:
            database = profile.database
            async with database.transaction() as connection:
                await connection.exec_driver_sql("CREATE TABLE probe (value INTEGER)")

            try:
                async with database.transaction() as connection:
                    await connection.exec_driver_sql("INSERT INTO probe VALUES (3)")
                    driver = cast(aiosqlite.Connection, (await connection.get_raw_connection()).driver_connection)
                    await driver.create_function("slow", 1, slow)
                    try:
                        async with asyncio.timeout(0.5):
                            await connection.exec_driver_sql("UPDATE probe SET value = slow(value)")
                    except TimeoutError:
                        release.set()
                    await connection.exec_driver_sql("INSERT INTO probe VALUES (4)")
            except PendingRollbackError:
                pass
            else:
                pytest.fail("an interrupted transaction committed instead of failing")

            async with database.transaction() as connection:
                rows = (await connection.exec_driver_sql("SELECT value FROM probe")).fetchall()
                assert [row[0] for row in rows] == []

    try:
        asyncio.run(scenario())
    finally:
        release.set()


def test_sqlite_config_requires_the_async_dialect() -> None:
    with pytest.raises(ValidationError, match=r"sqlite\+aiosqlite"):
        SQLiteConfig(url="sqlite:///:memory:")


def test_sqlite_profile_creates_a_missing_database_directory(tmp_path) -> None:
    async def scenario() -> None:
        database = tmp_path / "nested" / "powercontext.db"
        async with SQLiteProfile.open(
            SQLiteConfig(url=f"sqlite+aiosqlite:///{database}"),
            tables=SHARED_TABLES,
        ):
            assert database.is_file()

    asyncio.run(scenario())


def test_sqlite_pragmas_enforce_lineage_source_foreign_keys() -> None:
    async def scenario() -> None:
        async with SQLiteProfile.open(SQLiteConfig(), tables=SHARED_TABLES) as profile:
            async with profile.database.transaction() as connection:
                assert await connection.scalar(select(func.sqlite_version())) is not None
                pragma = await connection.exec_driver_sql("PRAGMA foreign_keys")
                assert int(pragma.scalar() or 0) == 1
                await connection.execute(
                    insert(ARTIFACTS_TABLE).values(
                        scope_id="scope",
                        family="memory",
                        artifact_id="memory-1",
                        revision=1,
                        content=b"{}",
                    )
                )

            with pytest.raises(IntegrityError):
                async with profile.database.transaction() as connection:
                    await connection.execute(
                        insert(ARTIFACT_LINEAGE_SOURCES_TABLE).values(
                            scope_id="scope",
                            family="memory",
                            artifact_id="memory-1",
                            revision=1,
                            ordinal=0,
                            source_type="content",
                            source_id="missing",
                        )
                    )

    asyncio.run(scenario())
