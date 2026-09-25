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
import logging
import sqlite3
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from threading import Event as ThreadEvent

import pytest
from aiosqlite import Connection as SQLiteConnection
from sqlalchemy import delete, insert, select, update
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.builtin.inference import InferenceUsage
from powercontext.builtin.persistence.database import AsyncDatabase
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.persistence.statistics import StatisticsRepository, StoredModelUsage
from powercontext.builtin.persistence.tables import MODEL_USAGE_DAILY_TABLE, SCOPES_TABLE
from powercontext.builtin.runtime._model_usage import _ModelUsageRecorder
from powercontext.builtin.statistics import ModelUsageOperation, ModelUsagePurpose

_DAY = date(2026, 1, 2)
_SCOPE = "usage-secret-scope"
_PURPOSE = ModelUsagePurpose.MEMORY_EXTRACTION
_OPERATION = ModelUsageOperation.GENERATION


@asynccontextmanager
async def _database(config: SQLiteConfig | None = None) -> AsyncIterator[AsyncDatabase]:
    async with SQLiteProfile.open(config or SQLiteConfig(), tables=(SCOPES_TABLE, MODEL_USAGE_DAILY_TABLE)) as profile:
        async with profile.database.transaction() as connection:
            await connection.execute(
                insert(SCOPES_TABLE).values(
                    scope_id=_SCOPE,
                    title="original",
                    summary="",
                    scope_id_search=_SCOPE,
                    title_search="original",
                    summary_search="",
                    version=1,
                )
            )
        yield profile.database


def _offer(recorder: _ModelUsageRecorder, usage: InferenceUsage | None = None) -> None:
    recorder.offer(_SCOPE, _PURPOSE, _OPERATION, usage or InferenceUsage(requests=1), _DAY)


async def _rows(database: AsyncDatabase) -> tuple[StoredModelUsage, ...]:
    async with database.transaction() as connection:
        return await StatisticsRepository().usage(connection, _SCOPE, _DAY, _DAY)


async def _assert_connection_restored(database: AsyncDatabase, busy_timeout: int = 5_000) -> None:
    async with database.transaction() as connection:
        assert (await connection.exec_driver_sql("PRAGMA busy_timeout")).scalar_one() == busy_timeout
        assert (await connection.exec_driver_sql("PRAGMA foreign_keys")).scalar_one() == 1
        # Enough VM steps to expose a deadline progress handler left installed.
        assert (
            await connection.exec_driver_sql(
                "WITH RECURSIVE n(x) AS (VALUES(1) UNION ALL SELECT x+1 FROM n WHERE x<2000) SELECT sum(x) FROM n"
            )
        ).scalar_one() == 2_001_000


@pytest.mark.parametrize("file_backed", [False, True])
def test_flush_persists_copied_usage_with_unknown_tokens_and_original_date(tmp_path: Path, file_backed: bool) -> None:
    async def scenario() -> None:
        config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'usage.db'}") if file_backed else SQLiteConfig()
        async with _database(config) as database:
            recorder = _ModelUsageRecorder(database, StatisticsRepository())
            try:
                usage = InferenceUsage(requests=2, input_tokens=None, output_tokens=4)
                _offer(recorder, usage)
                usage.requests = 900
                usage.output_tokens = 900
                _offer(recorder, InferenceUsage(requests=1, input_tokens=3, output_tokens=5))
                await recorder.flush()
                rows = await _rows(database)
                assert len(rows) == 1
                assert rows[0].usage_date == _DAY
                assert rows[0].requests == 3
                assert rows[0].input_tokens == 3
                assert not rows[0].input_complete
                assert rows[0].output_tokens == 9
                assert rows[0].output_complete
                await _assert_connection_restored(database)
            finally:
                await recorder.close()

    asyncio.run(scenario())


def test_queue_capacity_drops_without_sql_or_sensitive_logs(caplog: pytest.LogCaptureFixture) -> None:
    async def scenario() -> None:
        async with _database() as database:
            recorder = _ModelUsageRecorder(database, StatisticsRepository(), queue_capacity=2)
            try:
                for _ in range(8):
                    _offer(recorder)
                assert recorder.checkpoint() == 2
                await recorder.flush()
                assert (await _rows(database))[0].requests == 2
            finally:
                await recorder.close()

    with caplog.at_level(logging.WARNING):
        asyncio.run(scenario())
    assert "queue full" in caplog.text
    assert _SCOPE not in caplog.text
    assert "INSERT" not in caplog.text


def test_in_memory_usage_does_not_join_outer_rollback() -> None:
    async def scenario() -> None:
        async with _database() as database:
            recorder = _ModelUsageRecorder(database, StatisticsRepository())
            try:
                with pytest.raises(ValueError, match="business rollback"):
                    async with database.transaction() as connection:
                        await connection.execute(update(SCOPES_TABLE).values(title="uncommitted"))
                        _offer(recorder)
                        raise ValueError("business rollback")  # noqa: TRY003
                await recorder.flush()
                assert (await _rows(database))[0].requests == 1
                async with database.transaction() as connection:
                    assert (await connection.execute(select(SCOPES_TABLE.c.title))).scalar_one() == "original"
                await _assert_connection_restored(database)
            finally:
                await recorder.close()

    asyncio.run(scenario())


def test_long_shared_transaction_drops_usage_without_invalidating_memory() -> None:
    async def scenario() -> None:
        async with _database() as database:
            recorder = _ModelUsageRecorder(database, StatisticsRepository(), write_timeout_seconds=0.03)
            try:
                with pytest.raises(ValueError, match="business rollback"):
                    async with database.transaction() as connection:
                        await connection.execute(update(SCOPES_TABLE).values(title="uncommitted"))
                        start = asyncio.get_running_loop().time()
                        _offer(recorder)
                        await recorder.flush()
                        assert asyncio.get_running_loop().time() - start < 0.5
                        assert (await connection.execute(select(SCOPES_TABLE.c.title))).scalar_one() == "uncommitted"
                        raise ValueError("business rollback")  # noqa: TRY003
                assert await _rows(database) == ()
                await _assert_connection_restored(database)
                _offer(recorder)
                await recorder.flush()
                assert (await _rows(database))[0].requests == 1
            finally:
                await recorder.close()

    asyncio.run(scenario())


def test_file_writer_lock_does_not_consume_busy_timeout(tmp_path: Path) -> None:
    async def scenario() -> None:
        config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'locked.db'}", busy_timeout_ms=5_000)
        async with _database(config) as database:
            recorder = _ModelUsageRecorder(database, StatisticsRepository(), write_timeout_seconds=0.04)
            try:
                async with database.transaction() as connection:
                    await connection.execute(update(SCOPES_TABLE).values(title="locked"))
                    start = asyncio.get_running_loop().time()
                    _offer(recorder)
                    await recorder.flush()
                    assert asyncio.get_running_loop().time() - start < 0.5
                assert await _rows(database) == ()
                await _assert_connection_restored(database)
                _offer(recorder)
                await recorder.flush()
                assert (await _rows(database))[0].requests == 1
            finally:
                await recorder.close()

    asyncio.run(scenario())


def test_writer_lock_released_inside_the_budget_still_records(tmp_path: Path) -> None:
    async def scenario() -> None:
        path = tmp_path / "retry.db"
        config = SQLiteConfig(url=f"sqlite+aiosqlite:///{path}", busy_timeout_ms=5_000)
        async with _database(config) as database:
            # One record's budget is spent in slices, so a writer that lets go
            # partway through still yields a recorded usage row.
            recorder = _ModelUsageRecorder(database, StatisticsRepository(), write_timeout_seconds=2.0)
            holder = sqlite3.connect(path)
            try:
                holder.execute("BEGIN IMMEDIATE")
                holder.execute("SELECT * FROM pc_scopes").fetchall()

                async def release_later() -> None:
                    await asyncio.sleep(0.15)
                    holder.rollback()

                releasing = asyncio.create_task(release_later())
                _offer(recorder)
                await recorder.flush()
                await releasing
                assert (await _rows(database))[0].requests == 1
                await _assert_connection_restored(database)
            finally:
                holder.rollback()
                holder.close()
                await recorder.close()

    asyncio.run(scenario())


def test_close_under_a_held_writer_lock_is_bounded(tmp_path: Path) -> None:
    async def scenario() -> None:
        path = tmp_path / "close-locked.db"
        config = SQLiteConfig(url=f"sqlite+aiosqlite:///{path}", busy_timeout_ms=5_000)
        async with _database(config) as database:
            recorder = _ModelUsageRecorder(
                database, StatisticsRepository(), write_timeout_seconds=0.2, flush_timeout_seconds=0.1
            )
            holder = sqlite3.connect(path)
            try:
                holder.execute("BEGIN IMMEDIATE")
                holder.execute("SELECT * FROM pc_scopes").fetchall()
                _offer(recorder)
                start = asyncio.get_running_loop().time()
                await recorder.close()
                assert asyncio.get_running_loop().time() - start < 1.0
            finally:
                holder.rollback()
                holder.close()
            # Dropping the contended record is allowed; wedging the database is not.
            assert await _rows(database) == ()
            await _assert_connection_restored(database)

    asyncio.run(scenario())


def test_commit_lock_rolls_back_usage_and_restores_connection(tmp_path: Path) -> None:
    async def scenario() -> None:
        path = tmp_path / "commit-locked.db"
        config = SQLiteConfig(url=f"sqlite+aiosqlite:///{path}", journal_mode="DELETE")
        async with _database(config) as database:
            recorder = _ModelUsageRecorder(database, StatisticsRepository(), write_timeout_seconds=0.04)
            reader = sqlite3.connect(path)
            try:
                reader.execute("BEGIN")
                reader.execute("SELECT * FROM pc_scopes").fetchall()
                start = asyncio.get_running_loop().time()
                _offer(recorder)
                await recorder.flush()
                assert asyncio.get_running_loop().time() - start < 0.5
                reader.rollback()
                assert await _rows(database) == ()
                await _assert_connection_restored(database)
                _offer(recorder)
                await recorder.flush()
                assert (await _rows(database))[0].requests == 1
            finally:
                reader.close()
                await recorder.close()

    asyncio.run(scenario())


class _SlowQueryRepository(StatisticsRepository):
    slow = True

    async def record(
        self,
        connection: AsyncConnection,
        scope_id: str,
        usage_date: date,
        purpose: ModelUsagePurpose,
        operation: ModelUsageOperation,
        usage: InferenceUsage,
        /,
    ) -> None:
        if self.slow:
            await connection.exec_driver_sql(
                "WITH RECURSIVE n(x) AS (VALUES(1) UNION ALL SELECT x+1 FROM n WHERE x<1000000000) SELECT sum(x) FROM n"
            )
        await super().record(connection, scope_id, usage_date, purpose, operation, usage)


def test_native_sqlite_deadline_stops_vm_work_and_preserves_in_memory_database() -> None:
    async def scenario() -> None:
        async with _database() as database:
            repository = _SlowQueryRepository()
            recorder = _ModelUsageRecorder(database, repository, write_timeout_seconds=0.03)
            try:
                start = asyncio.get_running_loop().time()
                _offer(recorder)
                await recorder.flush()
                assert asyncio.get_running_loop().time() - start < 0.5
                assert await _rows(database) == ()
                await _assert_connection_restored(database)
                repository.slow = False
                _offer(recorder)
                await recorder.flush()
                assert (await _rows(database))[0].requests == 1
            finally:
                await recorder.close()

    asyncio.run(scenario())


class _BlockingNativeRepository(StatisticsRepository):
    def __init__(self) -> None:
        self.entered = ThreadEvent()
        self.release = ThreadEvent()

    def _block(self) -> int:
        self.entered.set()
        self.release.wait()
        return 1

    async def record(
        self,
        connection: AsyncConnection,
        scope_id: str,
        usage_date: date,
        purpose: ModelUsagePurpose,
        operation: ModelUsageOperation,
        usage: InferenceUsage,
        /,
    ) -> None:
        driver = (await connection.get_raw_connection()).driver_connection
        assert isinstance(driver, SQLiteConnection)
        await driver.create_function("wait_for_release", 0, self._block)
        await connection.exec_driver_sql("SELECT wait_for_release()")
        await super().record(connection, scope_id, usage_date, purpose, operation, usage)


def test_uninterruptible_native_call_keeps_connection_owned_until_real_cleanup() -> None:
    async def scenario() -> None:
        async with _database(SQLiteConfig(busy_timeout_ms=137)) as database:
            repository = _BlockingNativeRepository()
            recorder = _ModelUsageRecorder(database, repository, write_timeout_seconds=0.03, flush_timeout_seconds=0.02)
            try:
                _offer(recorder)
                assert await asyncio.to_thread(repository.entered.wait, 1)
                await recorder.close()
                # sqlite3_interrupt cannot preempt a native user-defined
                # function. Do not report cancellation or reuse the connection.
                assert recorder._consumer is not None and not recorder._consumer.done()
                reader = asyncio.create_task(database.ping())
                await asyncio.sleep(0)
                assert not reader.done()
                repository.release.set()
                await asyncio.wait_for(reader, 1)
                await recorder.close()
                assert recorder._consumer.done()
                assert await _rows(database) == ()
                await _assert_connection_restored(database, busy_timeout=137)
            finally:
                repository.release.set()
                await recorder.close()

    asyncio.run(scenario())


def test_cancelled_flush_propagates_without_cancelling_consumer() -> None:
    async def scenario() -> None:
        async with _database() as database:
            recorder = _ModelUsageRecorder(database, StatisticsRepository())
            try:
                async with database.transaction():
                    _offer(recorder)
                    waiter = asyncio.create_task(recorder.flush())
                    await asyncio.sleep(0)
                    waiter.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await waiter
                await recorder.flush()
                assert (await _rows(database))[0].requests == 1
                await _assert_connection_restored(database)
            finally:
                await recorder.close()

    asyncio.run(scenario())


def test_close_drops_backlog_and_leaves_no_consumer_waiting_for_shared_guard() -> None:
    async def scenario() -> None:
        async with _database() as database:
            recorder = _ModelUsageRecorder(
                database, StatisticsRepository(), write_timeout_seconds=0.05, flush_timeout_seconds=0.01
            )
            async with database.transaction():
                for _ in range(20):
                    _offer(recorder)
                start = asyncio.get_running_loop().time()
                await recorder.close()
                assert asyncio.get_running_loop().time() - start < 0.5
                assert recorder._consumer is not None and recorder._consumer.done()
            _offer(recorder)
            await recorder.close()
            assert await _rows(database) == ()
            await _assert_connection_restored(database)

    asyncio.run(scenario())


class _GatedRepository(StatisticsRepository):
    def __init__(self) -> None:
        self.entered = (asyncio.Event(), asyncio.Event())
        self.release = (asyncio.Event(), asyncio.Event())
        self.calls = 0

    async def record(
        self,
        connection: AsyncConnection,
        scope_id: str,
        usage_date: date,
        purpose: ModelUsagePurpose,
        operation: ModelUsageOperation,
        usage: InferenceUsage,
        /,
    ) -> None:
        index = self.calls
        self.calls += 1
        self.entered[index].set()
        await self.release[index].wait()
        await super().record(connection, scope_id, usage_date, purpose, operation, usage)


@pytest.mark.parametrize("explicit_checkpoint", [False, True])
def test_flush_waits_only_for_its_entry_prefix(explicit_checkpoint: bool) -> None:
    async def scenario() -> None:
        async with _database() as database:
            repository = _GatedRepository()
            recorder = _ModelUsageRecorder(database, repository, write_timeout_seconds=1)
            try:
                _offer(recorder)
                await repository.entered[0].wait()
                prefix = recorder.checkpoint()
                waiter = asyncio.create_task(recorder.flush(prefix if explicit_checkpoint else None))
                await asyncio.sleep(0)
                _offer(recorder)
                repository.release[0].set()
                await repository.entered[1].wait()
                await asyncio.wait_for(waiter, 0.1)
                assert not repository.release[1].is_set()
                repository.release[1].set()
                await recorder.flush()
                assert (await _rows(database))[0].requests == 2
            finally:
                for gate in repository.release:
                    gate.set()
                await recorder.close()

    asyncio.run(scenario())


def test_deleted_scope_is_not_resurrected_by_queued_usage() -> None:
    async def scenario() -> None:
        async with _database() as database:
            recorder = _ModelUsageRecorder(database, StatisticsRepository())
            try:
                async with database.transaction() as connection:
                    _offer(recorder)
                    await connection.execute(delete(SCOPES_TABLE))
                await recorder.flush()
                assert await _rows(database) == ()
            finally:
                await recorder.close()

    asyncio.run(scenario())


def test_commit_unknown_is_not_retried(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    async def scenario() -> None:
        async with _database() as database:
            transaction = database._model_usage_transaction

            @asynccontextmanager
            async def lost_commit_reply(timeout_seconds: float) -> AsyncIterator[AsyncConnection]:
                async with transaction(timeout_seconds) as connection:
                    yield connection
                raise RuntimeError("secret credentials and SQL that must not reach logs")  # noqa: TRY003

            monkeypatch.setattr(database, "_model_usage_transaction", lost_commit_reply)
            recorder = _ModelUsageRecorder(database, StatisticsRepository())
            try:
                _offer(recorder)
                await recorder.flush()
                await recorder.flush()
                assert (await _rows(database))[0].requests == 1
            finally:
                await recorder.close()

    with caplog.at_level(logging.WARNING):
        asyncio.run(scenario())
    assert "will not be retried" in caplog.text
    assert "secret credentials" not in caplog.text
    assert _SCOPE not in caplog.text
