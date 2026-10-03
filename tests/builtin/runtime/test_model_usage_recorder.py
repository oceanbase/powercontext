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
from contextlib import asynccontextmanager, suppress
from datetime import date
from pathlib import Path
from threading import Event as ThreadEvent
from typing import cast

import pytest
from aiosqlite import Connection as SQLiteConnection
from sqlalchemy import delete, event, insert, select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine
from sqlalchemy.pool import QueuePool
from sqlalchemy.util import await_only

from powercontext.builtin.inference import InferenceUsage
from powercontext.builtin.persistence.database import AsyncDatabase, ModelUsageAttemptExpired
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
            # Keep queue overflow assertions independent of SQLite write deadlines.
            recorder = _ModelUsageRecorder(
                database, StatisticsRepository(), queue_capacity=2, write_timeout_seconds=5.0, flush_timeout_seconds=5.0
            )
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


def test_an_attempt_that_expires_at_checkout_is_repeated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An expired attempt applied nothing, so the record keeps its budget."""

    async def scenario() -> None:
        config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'slow-checkout.db'}")
        async with _database(config) as database:
            starts = 0
            original_start = AsyncConnection.start

            async def start(connection: AsyncConnection, is_ctxmanager: bool = False) -> AsyncConnection:
                nonlocal starts
                starts += 1
                if starts == 1:
                    # Outlive the whole slice without starting driver work, so
                    # the attempt expires with nothing applied.
                    await asyncio.sleep(0.08)
                return await original_start(connection, is_ctxmanager)

            monkeypatch.setattr(AsyncConnection, "start", start)
            recorder = _ModelUsageRecorder(database, StatisticsRepository(), write_timeout_seconds=0.2)
            try:
                _offer(recorder)
                await recorder.flush()
                assert starts > 1
                assert (await _rows(database))[0].requests == 1
                await _assert_connection_restored(database)
            finally:
                await recorder.close()

    asyncio.run(scenario())


class _SlowBodyRepository(StatisticsRepository):
    """Make the first write outlive its slice without leaving the event loop."""

    def __init__(self, *, delay: float) -> None:
        self.delay = delay
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
        self.calls += 1
        if self.calls == 1:
            await asyncio.sleep(self.delay)
        await super().record(connection, scope_id, usage_date, purpose, operation, usage)


def test_a_body_that_outlives_its_slice_is_repeated_not_dropped(tmp_path: Path) -> None:
    """A body that finishes after its own slice still records.

    The deadline check runs after the body, so on its own it can only discard
    work that is already done. A released usage write must not depend on the
    release landing inside one slice, which is what the stalled-request e2e test
    asserts when it waits for a visible row on a loaded machine.
    """

    async def scenario() -> None:
        config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'late-body.db'}")
        async with _database(config) as database:
            # One record's 0.2s budget is spent in 0.05s slices, so a 0.08s body
            # outlives the attempt that owns it while the record owns budget.
            repository = _SlowBodyRepository(delay=0.08)
            recorder = _ModelUsageRecorder(database, repository, write_timeout_seconds=0.2)
            try:
                _offer(recorder)
                await recorder.flush()
                assert repository.calls == 2
                assert (await _rows(database))[0].requests == 1
                await _assert_connection_restored(database)
            finally:
                await recorder.close()

    asyncio.run(scenario())


def test_a_repeat_after_an_expiry_gets_the_rest_of_the_budget(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A slice bounds a wait; a body slower than every slice must not be dropped.

    The first attempt is sliced, so a slow body expires it. Handing the repeat
    the same slice only reaches the same wall, which is how a loaded machine
    loses a record that still owns most of its budget; the repeat has to spend
    what the record has left.
    """

    async def scenario() -> None:
        config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'rest-of-budget.db'}")
        async with _database(config) as database:
            budgets: list[float] = []
            expired = False
            original = AsyncDatabase._model_usage_transaction

            @asynccontextmanager
            async def transaction(instance: AsyncDatabase, timeout_seconds: float) -> AsyncIterator[AsyncConnection]:
                nonlocal expired
                budgets.append(timeout_seconds)
                if not expired:
                    expired = True
                    raise ModelUsageAttemptExpired
                async with original(instance, timeout_seconds) as connection:
                    yield connection

            monkeypatch.setattr(AsyncDatabase, "_model_usage_transaction", transaction)
            recorder = _ModelUsageRecorder(database, StatisticsRepository(), write_timeout_seconds=1.0)
            try:
                _offer(recorder)
                await recorder.flush()
                assert budgets[0] == pytest.approx(0.25, abs=0.01)
                assert budgets[1] > 0.5
                assert (await _rows(database))[0].requests == 1
            finally:
                await recorder.close()

    asyncio.run(scenario())


def test_a_repeat_after_a_lost_race_gets_the_rest_of_the_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The slice protects the budget until the first attempt has already failed.

    A repeat is only reached once a sliced attempt failed, so the slice has
    nothing left to protect: another slice reaches the same wall and drops a
    record that still owns most of its budget. The budget stays the bound, so
    the total the record may spend is unchanged.
    """

    class _Busy(Exception):
        sqlite_errorcode = 5

    async def scenario() -> None:
        config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'lost-race.db'}")
        async with _database(config) as database:
            budgets: list[float] = []
            contended = False
            original = AsyncDatabase._model_usage_transaction

            @asynccontextmanager
            async def transaction(instance: AsyncDatabase, timeout_seconds: float) -> AsyncIterator[AsyncConnection]:
                nonlocal contended
                budgets.append(timeout_seconds)
                if not contended:
                    contended = True
                    raise OperationalError("BEGIN", None, _Busy("database is locked"))
                async with original(instance, timeout_seconds) as connection:
                    yield connection

            monkeypatch.setattr(AsyncDatabase, "_model_usage_transaction", transaction)
            recorder = _ModelUsageRecorder(database, StatisticsRepository(), write_timeout_seconds=1.0)
            try:
                _offer(recorder)
                await recorder.flush()
                assert budgets[0] == pytest.approx(0.25, abs=0.01)
                assert budgets[1] > 0.5
                assert (await _rows(database))[0].requests == 1
            finally:
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


def test_checkout_that_outlives_its_budget_does_not_hold_shutdown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'slow-checkout.db'}")
        async with _database(config) as database:
            original_start = AsyncConnection.start
            entered = asyncio.Event()
            release = asyncio.Event()
            finished = asyncio.Event()

            async def start(connection: AsyncConnection, is_ctxmanager: bool = False) -> AsyncConnection:
                if not entered.is_set():
                    entered.set()
                    try:
                        await release.wait()
                    finally:
                        finished.set()
                return await original_start(connection, is_ctxmanager)

            monkeypatch.setattr(AsyncConnection, "start", start)
            recorder = _ModelUsageRecorder(
                database, StatisticsRepository(), write_timeout_seconds=0.05, flush_timeout_seconds=0.02
            )
            try:
                _offer(recorder)
                await asyncio.wait_for(entered.wait(), 1)
                await asyncio.wait_for(recorder.close(), 1)
                await asyncio.wait_for(database.close(), 0.8)
                # Shutdown must finish the abandoned checkout, not merely stop
                # counting it while leaving another task waiting on the pool.
                assert finished.is_set()
            finally:
                release.set()
                await recorder.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("stall", ["pool", "pre_ping"])
def test_usage_checkout_budget_releases_pool_resources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stall: str
) -> None:
    async def scenario() -> None:
        engine = create_async_engine(
            f"sqlite+aiosqlite:///{tmp_path / 'checkout-pool.db'}",
            pool_size=1,
            max_overflow=0,
            pool_timeout=60,
            pool_pre_ping=True,
        )
        database = AsyncDatabase.own(engine)
        entered = asyncio.Event()
        release = asyncio.Event()
        try:
            # Warm the pool so the next checkout takes the pre-ping path.
            await database.ping()
            original_ping = engine.sync_engine.dialect.do_ping

            def stalled_ping(connection):
                entered.set()
                await_only(release.wait())
                return original_ping(connection)

            async def attempt() -> None:
                with pytest.raises(TimeoutError):
                    async with database._model_usage_transaction(0.05):
                        pytest.fail("A stalled checkout exceeded its usage budget")

            if stall == "pool":
                async with engine.connect() as business:
                    await asyncio.wait_for(attempt(), 1)
                    # Return the sole slot, then acquire it again. An abandoned
                    # checkout would take it and could hold it past shutdown.
                    assert not business.closed
            else:
                monkeypatch.setattr(engine.sync_engine.dialect, "do_ping", stalled_ping)
                await asyncio.wait_for(attempt(), 1)
                assert entered.is_set()
                monkeypatch.setattr(engine.sync_engine.dialect, "do_ping", original_ping)
            await asyncio.wait_for(database.ping(), 1)
            await asyncio.wait_for(database.close(), 1)
        finally:
            release.set()
            await database.close()

    asyncio.run(scenario())


def test_a_stalled_driver_read_is_cancelled_and_returns_its_pool_slot() -> None:
    """The stall the SQLite cases cannot reach: a driver-level socket read.

    The reviewer's stall is inside aiomysql, waiting on a server that never
    answers; `pool_timeout` does not bound that read. Nothing here is SQLite, so
    this is the only case that shows the checkout really is cancellable through
    the driver, and that the interrupted attempt gives its pool slot back
    instead of holding it past shutdown.
    """

    async def scenario() -> None:
        held: list[asyncio.StreamWriter] = []

        async def accept_and_never_answer(_reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            # Keeping the writer open leaves the client blocked on its first
            # read, with no reply and no reset, until it gives up or is cancelled.
            held.append(writer)

        server = await asyncio.start_server(accept_and_never_answer, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        engine = create_async_engine(
            f"mysql+aiomysql://root:secret@127.0.0.1:{port}/powercontext",
            pool_size=1,
            max_overflow=0,
            pool_timeout=60,
            pool_pre_ping=True,
        )
        database = AsyncDatabase.own(engine)

        async def attempt() -> None:
            async with database._model_usage_transaction(0.05):
                pytest.fail("A stalled driver read exceeded its usage budget")

        # Failing fast beats hanging: without the checkout bound this task never
        # finishes at all, so wait on it instead of awaiting it directly.
        attempt_task = asyncio.create_task(attempt())
        try:
            done, _ = await asyncio.wait({attempt_task}, timeout=5)
            assert done, "the usage budget did not bound the stalled driver read"
            with pytest.raises(TimeoutError):
                await attempt_task
            await asyncio.wait_for(database.close(), 2)
            assert cast(QueuePool, engine.pool).checkedout() == 0, "the abandoned checkout kept its pool slot"
        finally:
            server.close()
            for writer in held:
                writer.close()
            attempt_task.cancel()
            with suppress(asyncio.CancelledError, TimeoutError):
                await asyncio.wait_for(attempt_task, 1)
            with suppress(asyncio.CancelledError, TimeoutError):
                await asyncio.wait_for(database.close(), 2)

    asyncio.run(scenario())


def test_cancellation_at_checkout_returns_the_connection(tmp_path: Path) -> None:
    async def scenario() -> None:
        engine = create_async_engine(
            f"sqlite+aiosqlite:///{tmp_path / 'cancel-checkout.db'}", pool_size=1, max_overflow=0, pool_timeout=60
        )
        database = AsyncDatabase.own(engine)
        try:
            await database.ping()

            async def attempt() -> None:
                owner = asyncio.current_task()
                assert owner is not None

                def cancel_on_checkout(*args) -> None:
                    asyncio.get_running_loop().call_soon(owner.cancel)

                event.listen(engine.sync_engine, "checkout", cancel_on_checkout, once=True)
                async with database._model_usage_transaction(1):
                    pytest.fail("Cancellation must propagate before usage is written")

            attempt_task = asyncio.create_task(attempt())
            with pytest.raises(asyncio.CancelledError):
                await attempt_task
            # With only one pool slot this also detects a leaked checkout.
            await asyncio.wait_for(database.ping(), 1)
        finally:
            await database.close()

    asyncio.run(scenario())


def test_timed_out_shared_checkout_cannot_roll_back_business(monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario() -> None:
        async with _database() as database:
            original_start = AsyncConnection.start
            original_close = AsyncConnection.close
            release = asyncio.Event()
            settled = asyncio.Event()
            stalled_connection: AsyncConnection | None = None

            async def start(connection: AsyncConnection, is_ctxmanager: bool = False) -> AsyncConnection:
                nonlocal stalled_connection
                if stalled_connection is None:
                    stalled_connection = connection
                    try:
                        await release.wait()
                    except asyncio.CancelledError:
                        settled.set()
                        raise
                return await original_start(connection, is_ctxmanager)

            async def close(connection: AsyncConnection) -> None:
                try:
                    await original_close(connection)
                finally:
                    if connection is stalled_connection:
                        settled.set()

            monkeypatch.setattr(AsyncConnection, "start", start)
            monkeypatch.setattr(AsyncConnection, "close", close)
            try:
                with pytest.raises(TimeoutError):
                    async with database._model_usage_transaction(0.02):
                        pytest.fail("The stalled checkout must time out")
                async with database.transaction() as connection:
                    await connection.execute(update(SCOPES_TABLE).values(title="committed"))
                    release.set()
                    await asyncio.wait_for(settled.wait(), 1)
                    assert (await connection.execute(select(SCOPES_TABLE.c.title))).scalar_one() == "committed"
                async with database.transaction() as connection:
                    assert (await connection.execute(select(SCOPES_TABLE.c.title))).scalar_one() == "committed"
            finally:
                release.set()

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
