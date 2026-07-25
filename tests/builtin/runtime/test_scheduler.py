from __future__ import annotations

import asyncio
import sqlite3

import pytest

from powercontext import PowerContext
from powercontext.builtin.runtime import BuiltinRuntime, MemoryFlushResult, RuntimeCapabilities
from powercontext.builtin.runtime.scheduler import (
    SOURCE_WINDOW_JOB_ID,
    SchedulerConfigurationError,
    SchedulerStateError,
    scheduler_database_path,
)
from powercontext.builtin.sources import SourceCursor


class _Provider:
    def __init__(self, context: PowerContext) -> None:
        self.context = context

    async def get(self, scope_id: str, /) -> PowerContext:
        del scope_id
        return self.context


class _ScheduledTriggers:
    def __init__(self) -> None:
        self.dispatched = asyncio.Event()

    async def flush(self, *, limit: int) -> MemoryFlushResult:
        del limit
        self.dispatched.set()
        return MemoryFlushResult(
            previous_cursor=0,
            high_watermark=0,
            current_cursor=0,
            source_count=0,
            memory_ref=None,
        )

    async def cursor(self) -> SourceCursor:
        return SourceCursor()


async def _scope_ids() -> tuple[str, ...]:
    return ("scheduled",)


def _runtime(triggers: object, *, scope_ids=_scope_ids) -> BuiltinRuntime:
    return BuiltinRuntime(
        provider=_Provider(PowerContext(sources=object(), artifacts=object(), triggers=triggers)),  # type: ignore[arg-type]
        capabilities=RuntimeCapabilities(memory_extraction=True, memory_search_modes=("fts",)),
        scope_ids=scope_ids,
    )


def _stored_jobs(database) -> list[tuple[str, float | None]]:
    with sqlite3.connect(scheduler_database_path(database)) as connection:
        return connection.execute("SELECT id, next_run_time FROM powercontext_scheduler_jobs ORDER BY id").fetchall()


def test_scheduler_persists_one_stable_source_window_job(tmp_path) -> None:
    async def scenario() -> None:
        database = tmp_path / "runtime.db"
        first = _runtime(_ScheduledTriggers())
        first.start_scheduler(database, 3_600)
        await first.close()
        jobs = _stored_jobs(database)

        restored = _runtime(_ScheduledTriggers())
        restored.start_scheduler(database, 3_600)
        await restored.close()

        assert jobs == _stored_jobs(database)
        assert len(jobs) == 1
        assert jobs[0][0] == SOURCE_WINDOW_JOB_ID

    asyncio.run(scenario())


def test_scheduler_interval_activates_the_source_window_policy(tmp_path) -> None:
    async def scenario() -> None:
        triggers = _ScheduledTriggers()
        runtime = _runtime(triggers)
        runtime.start_scheduler(tmp_path / "runtime.db", 0.01)
        try:
            await asyncio.wait_for(triggers.dispatched.wait(), timeout=2)
        finally:
            await runtime.close()

    asyncio.run(scenario())


def test_scheduler_requires_file_storage_and_one_live_owner(tmp_path) -> None:
    async def scenario() -> None:
        with pytest.raises(SchedulerConfigurationError):
            _runtime(_ScheduledTriggers()).start_scheduler(":memory:", 60)

        database = tmp_path / "runtime.db"
        first = _runtime(_ScheduledTriggers())
        second = _runtime(_ScheduledTriggers())
        first.start_scheduler(database, 60)
        try:
            with pytest.raises(SchedulerStateError):
                second.start_scheduler(database, 60)
        finally:
            await first.close()

        second.start_scheduler(database, 60)
        await second.close()

    asyncio.run(scenario())
