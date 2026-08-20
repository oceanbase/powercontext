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

import pytest

from powercontext import PowerContext
from powercontext.builtin.runtime import (
    BuiltinRuntime,
    ExperienceIncubationResult,
    MemoryFlushResult,
    RuntimeCapabilities,
)
from powercontext.builtin.runtime.scheduler import (
    EXPERIENCE_INCUBATION_JOB_ID,
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


class _ScheduledExperience:
    def __init__(self) -> None:
        self.dispatched = asyncio.Event()

    async def __call__(self, scope_id: str, limit: int) -> ExperienceIncubationResult:
        del scope_id, limit
        self.dispatched.set()
        return ExperienceIncubationResult(
            previous_cursor=0,
            high_watermark=1,
            current_cursor=1,
            source_count=1,
            candidate_count=1,
        )


async def _scope_ids() -> tuple[str, ...]:
    return ("scheduled",)


def _runtime(
    triggers: object,
    *,
    scope_ids=_scope_ids,
    experience_incubator: _ScheduledExperience | None = None,
) -> BuiltinRuntime:
    return BuiltinRuntime(
        provider=_Provider(PowerContext(sources=object(), artifacts=object(), triggers=triggers)),  # type: ignore[arg-type]
        capabilities=RuntimeCapabilities(memory_extraction=True, memory_search_modes=("fts",)),
        scope_ids=scope_ids,
        experience_incubator=experience_incubator,
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


def test_scheduler_creates_missing_database_parent_directory(tmp_path) -> None:
    async def scenario() -> None:
        database = tmp_path / "missing" / "nested" / "scheduler.db"
        assert not database.parent.exists()

        runtime = _runtime(_ScheduledTriggers())
        try:
            runtime.start_scheduler(database, 3_600)
            assert database.is_file()
        finally:
            await runtime.close()

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


def test_scheduler_interval_activates_experience_incubation(tmp_path) -> None:
    async def scenario() -> None:
        incubation = _ScheduledExperience()
        runtime = _runtime(_ScheduledTriggers(), experience_incubator=incubation)
        runtime.start_scheduler(
            tmp_path / "runtime.db",
            None,
            experience_schedule_seconds=0.01,
        )
        try:
            await asyncio.wait_for(incubation.dispatched.wait(), timeout=2)
        finally:
            await runtime.close()

    asyncio.run(scenario())


def test_scheduler_persists_and_reconciles_independent_jobs(tmp_path) -> None:
    async def scenario() -> None:
        database = tmp_path / "runtime.db"
        first = _runtime(_ScheduledTriggers(), experience_incubator=_ScheduledExperience())
        first.start_scheduler(database, 3_600, experience_schedule_seconds=7_200)
        await first.close()

        assert [job_id for job_id, _ in _stored_jobs(database)] == [
            EXPERIENCE_INCUBATION_JOB_ID,
            SOURCE_WINDOW_JOB_ID,
        ]

        restored = _runtime(_ScheduledTriggers(), experience_incubator=_ScheduledExperience())
        restored.start_scheduler(database, None, experience_schedule_seconds=7_200)
        await restored.close()

        assert [job_id for job_id, _ in _stored_jobs(database)] == [EXPERIENCE_INCUBATION_JOB_ID]

    asyncio.run(scenario())


def test_scheduled_noop_logs_a_bounded_outcome(caplog) -> None:
    async def scenario() -> None:
        runtime = _runtime(_ScheduledTriggers())
        assert runtime.processor is not None
        await runtime.processor.run()

    with caplog.at_level(logging.INFO, logger="powercontext.builtin.runtime.application"):
        asyncio.run(scenario())

    record = next(record for record in caplog.records if record.event == "background.operation.completed")
    assert record.operation == "process_source_window"
    assert record.outcome == "noop"
    assert record.source_count == 0
    assert "scope_id" not in vars(record)


def test_scheduled_experience_logs_candidate_count_without_scope(caplog) -> None:
    async def scenario() -> None:
        runtime = _runtime(_ScheduledTriggers(), experience_incubator=_ScheduledExperience())
        assert runtime.experience_processor is not None
        await runtime.experience_processor.run()

    with caplog.at_level(logging.INFO, logger="powercontext.builtin.runtime.application"):
        asyncio.run(scenario())

    record = next(record for record in caplog.records if record.operation == "incubate_experience_candidates")
    assert record.outcome == "success"
    assert record.source_count == 1
    assert record.candidate_count == 1
    assert "scope_id" not in vars(record)


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
