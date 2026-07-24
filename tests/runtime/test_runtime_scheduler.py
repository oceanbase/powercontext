from __future__ import annotations

import asyncio
import sqlite3

import pytest

from powercontext import MemoryEntryInput
from powercontext.memory import MemoryCandidateRequest
from powercontext.memory.backends.sqlite import SQLiteMemoryBackend
from powercontext.runtime import PowerContextRuntime, SearchMemoryRequest, SourceCursor
from powercontext.runtime.scheduler import (
    SOURCE_WINDOW_JOB_ID,
    SchedulerConfigurationError,
    SchedulerStateError,
    dispatch_source_windows,
    scheduler_database_path,
    scheduler_runtime_key,
)
from powercontext.sources import ContentCapture, ContentSource
from powercontext.sources.backends.sqlite import SQLiteScopedSourceBackend


class ContentCandidatePipeline:
    async def extract(self, request: MemoryCandidateRequest, /) -> tuple[MemoryEntryInput, ...]:
        return tuple(
            MemoryEntryInput(kind="working_note", text=source.content, sources=(source,))
            for source in request.sources
            if isinstance(source, ContentSource)
        )


class FailingCandidatePipeline:
    async def extract(self, request: MemoryCandidateRequest, /) -> tuple[MemoryEntryInput, ...]:
        raise _CandidateExtractionError


class BlockingCandidatePipeline:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.completed = False
        self.cancelled = False

    async def extract(self, request: MemoryCandidateRequest, /) -> tuple[MemoryEntryInput, ...]:
        self.started.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        self.completed = True
        return ()


class _CandidateExtractionError(RuntimeError):
    pass


class _CursorCommitError(RuntimeError):
    pass


def _stored_jobs(database) -> list[tuple[str, float | None]]:
    with sqlite3.connect(scheduler_database_path(database)) as connection:
        return connection.execute("SELECT id, next_run_time FROM powercontext_scheduler_jobs ORDER BY id").fetchall()


def test_scheduler_persists_one_stable_job_across_runtime_restarts(tmp_path) -> None:
    async def scenario() -> None:
        database = tmp_path / "runtime #1.db"
        runtime = await PowerContextRuntime.open(
            database,
            candidate_pipeline=ContentCandidatePipeline(),
            schedule_seconds=3_600,
        )
        await runtime.close()
        first_jobs = _stored_jobs(database)

        restored = await PowerContextRuntime.open(
            database,
            candidate_pipeline=ContentCandidatePipeline(),
            schedule_seconds=3_600,
        )
        await restored.close()

        assert first_jobs == _stored_jobs(database)
        assert first_jobs[0][0] == SOURCE_WINDOW_JOB_ID
        with sqlite3.connect(database) as connection:
            scheduler_table = connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'powercontext_scheduler_jobs'"
            ).fetchone()
        assert scheduler_table is None

    asyncio.run(scenario())


def test_runtime_normalizes_database_path_before_opening_scoped_backends(tmp_path, monkeypatch) -> None:
    async def scenario() -> None:
        monkeypatch.chdir(tmp_path)
        runtime = await PowerContextRuntime.open(
            "runtime.db",
            candidate_pipeline=ContentCandidatePipeline(),
            schedule_seconds=3_600,
        )
        moved_cwd = tmp_path / "nested"
        moved_cwd.mkdir()
        monkeypatch.chdir(moved_cwd)
        try:
            await runtime.sources.for_scope("scope:path").capture(
                ContentCapture(source_id="task-1", content="Keep one canonical database path.")
            )
            await runtime.memory.for_scope("scope:path").flush()
        finally:
            await runtime.close()

        assert (tmp_path / "runtime.db").is_file()
        assert (tmp_path / "runtime.db.scheduler").is_file()
        assert not (moved_cwd / "runtime.db").exists()
        assert not (moved_cwd / "runtime.db.scheduler").exists()

    asyncio.run(scenario())


def test_persisted_dispatcher_processes_pending_scopes_and_advances_cursor(tmp_path) -> None:
    async def scenario() -> None:
        database = tmp_path / "runtime.db"
        runtime = await PowerContextRuntime.open(
            database,
            candidate_pipeline=ContentCandidatePipeline(),
            schedule_seconds=3_600,
        )
        try:
            scope_id = "scope:scheduled"
            await runtime.sources.for_scope(scope_id).capture(
                ContentCapture(source_id="task-1", content="Persist scheduler state in SQLite.")
            )

            await dispatch_source_windows(scheduler_runtime_key(database))

            assert (await runtime.memory.for_scope(scope_id).cursor()).sequence == 1
        finally:
            await runtime.close()

    asyncio.run(scenario())


def test_memory_commit_is_replayed_when_cursor_commit_fails(tmp_path, monkeypatch) -> None:
    scope_id = "scope:cursor-replay"
    original_save_cursor = SQLiteScopedSourceBackend.save_cursor
    remaining_failures = 1

    async def fail_first_cursor_commit(
        backend: SQLiteScopedSourceBackend,
        trigger_name: str,
        cursor: SourceCursor,
        /,
    ) -> None:
        nonlocal remaining_failures
        if backend.scope_id == scope_id and remaining_failures:
            remaining_failures -= 1
            raise _CursorCommitError
        await original_save_cursor(backend, trigger_name, cursor)

    monkeypatch.setattr(SQLiteScopedSourceBackend, "save_cursor", fail_first_cursor_commit)

    async def scenario() -> None:
        runtime = await PowerContextRuntime.open(
            tmp_path / "runtime.db",
            candidate_pipeline=ContentCandidatePipeline(),
        )
        try:
            receipt = await runtime.sources.for_scope(scope_id).capture(
                ContentCapture(source_id="task-1", content="Replay the committed Source window.")
            )
            memory = runtime.memory.for_scope(scope_id)

            with pytest.raises(_CursorCommitError):
                await memory.flush()

            committed = await memory.list()
            assert committed.memory_ref is not None
            assert committed.memory_ref.revision == 1
            assert len(committed.entries) == 1
            assert committed.entries[0].entry.sources == (receipt.source,)
            assert (await memory.cursor()).sequence == 0

            replayed = await memory.flush()
            after_replay = await memory.list()

            assert replayed.previous_cursor == 0
            assert replayed.current_cursor == 1
            assert after_replay.memory_ref == committed.memory_ref
            assert len(after_replay.entries) == 1
            assert after_replay.entries[0].entry.sources == (receipt.source,)
        finally:
            await runtime.close()

    asyncio.run(scenario())


def test_restored_scheduler_automatically_processes_pending_sources(tmp_path) -> None:
    async def scenario() -> None:
        database = tmp_path / "runtime.db"
        scope_id = "scope:restored-scheduler"
        runtime = await PowerContextRuntime.open(
            database,
            candidate_pipeline=ContentCandidatePipeline(),
            schedule_seconds=0.5,
        )
        await runtime.sources.for_scope(scope_id).capture(
            ContentCapture(source_id="task-1", content="Resume the persisted scheduler job.")
        )
        assert (await runtime.memory.for_scope(scope_id).cursor()).sequence == 0
        await runtime.close()

        assert _stored_jobs(database)[0][0] == SOURCE_WINDOW_JOB_ID

        restored = await PowerContextRuntime.open(
            database,
            candidate_pipeline=ContentCandidatePipeline(),
            schedule_seconds=0.5,
        )
        try:
            for _ in range(200):
                if (await restored.memory.for_scope(scope_id).cursor()).sequence == 1:
                    break
                await asyncio.sleep(0.01)

            assert (await restored.memory.for_scope(scope_id).cursor()).sequence == 1
            assert [job[0] for job in _stored_jobs(database)] == [SOURCE_WINDOW_JOB_ID]
        finally:
            await restored.close()

    asyncio.run(scenario())


def test_runtime_close_waits_for_active_scheduled_processor(tmp_path) -> None:
    async def scenario() -> None:
        pipeline = BlockingCandidatePipeline()
        runtime = await PowerContextRuntime.open(
            tmp_path / "runtime.db",
            candidate_pipeline=pipeline,
            schedule_seconds=0.01,
        )
        await runtime.sources.for_scope("scope:shutdown").capture(
            ContentCapture(source_id="task-1", content="Finish this window before shutdown.")
        )
        await asyncio.wait_for(pipeline.started.wait(), timeout=2)

        close_task = asyncio.create_task(runtime.close())
        await asyncio.sleep(0.05)
        assert not close_task.done()
        assert not pipeline.cancelled

        pipeline.release.set()
        await asyncio.wait_for(close_task, timeout=2)
        assert pipeline.completed
        assert not pipeline.cancelled

    asyncio.run(scenario())


def test_runtime_close_can_resume_after_cancellation(tmp_path) -> None:
    async def scenario() -> None:
        pipeline = BlockingCandidatePipeline()
        runtime = await PowerContextRuntime.open(
            tmp_path / "runtime.db",
            candidate_pipeline=pipeline,
            schedule_seconds=0.01,
        )
        await runtime.sources.for_scope("scope:cancelled-close").capture(
            ContentCapture(source_id="task-1", content="Complete cleanup after close is cancelled.")
        )
        await asyncio.wait_for(pipeline.started.wait(), timeout=2)

        interrupted_close = asyncio.create_task(runtime.close())
        await asyncio.sleep(0)
        interrupted_close.cancel()
        with pytest.raises(asyncio.CancelledError):
            await interrupted_close
        assert not pipeline.cancelled

        pipeline.release.set()
        await asyncio.wait_for(runtime.close(), timeout=2)
        assert pipeline.completed
        with pytest.raises(RuntimeError, match="runtime is closed"):
            await runtime.memory.for_scope("scope:closed").cursor()

    asyncio.run(scenario())


def test_runtime_close_waits_for_admitted_scope_initialization(tmp_path, monkeypatch) -> None:
    async def scenario() -> None:
        runtime = await PowerContextRuntime.open(
            tmp_path / "runtime.db",
            candidate_pipeline=ContentCandidatePipeline(),
        )
        initialization_started = asyncio.Event()
        continue_initialization = asyncio.Event()
        original_initialize = SQLiteMemoryBackend.initialize

        async def blocked_initialize(backend: SQLiteMemoryBackend) -> None:
            initialization_started.set()
            await continue_initialization.wait()
            await original_initialize(backend)

        monkeypatch.setattr(SQLiteMemoryBackend, "initialize", blocked_initialize)
        cursor_task = asyncio.create_task(runtime.memory.for_scope("scope:initializing").cursor())
        await initialization_started.wait()

        close_task = asyncio.create_task(runtime.close())
        await asyncio.sleep(0)
        assert not close_task.done()

        continue_initialization.set()
        assert await cursor_task == SourceCursor()
        await asyncio.wait_for(close_task, timeout=2)

        with pytest.raises(RuntimeError, match="runtime is closed"):
            await runtime.memory.for_scope("scope:closed").cursor()

    asyncio.run(scenario())


def test_scheduler_automatically_dispatches_a_pending_scope(tmp_path) -> None:
    async def scenario() -> None:
        database = tmp_path / "runtime.db"
        runtime = await PowerContextRuntime.open(
            database,
            candidate_pipeline=ContentCandidatePipeline(),
            schedule_seconds=0.02,
        )
        try:
            scope_id = "scope:automatic"
            receipt = await runtime.sources.for_scope(scope_id).capture(
                ContentCapture(source_id="task-1", content="Run the persisted interval job.")
            )
            for _ in range(100):
                if (await runtime.memory.for_scope(scope_id).cursor()).sequence == 1:
                    break
                await asyncio.sleep(0.01)

            assert (await runtime.memory.for_scope(scope_id).cursor()).sequence == 1
            search = await runtime.memory.for_scope(scope_id).search(SearchMemoryRequest("persisted interval"))
            entries = await runtime.memory.for_scope(scope_id).list()
            assert len(search.hits) == 1
            assert search.hits[0].text == "Run the persisted interval job."
            assert len(entries.entries) == 1
            assert entries.entries[0].entry.sources == (receipt.source,)
        finally:
            await runtime.close()

    asyncio.run(scenario())


def test_failed_scheduled_window_keeps_cursor_for_retry(tmp_path) -> None:
    async def scenario() -> None:
        database = tmp_path / "runtime.db"
        runtime = await PowerContextRuntime.open(
            database,
            candidate_pipeline=FailingCandidatePipeline(),
            schedule_seconds=3_600,
        )
        try:
            scope_id = "scope:retry"
            await runtime.sources.for_scope(scope_id).capture(
                ContentCapture(source_id="task-1", content="Retry this window.")
            )

            await dispatch_source_windows(scheduler_runtime_key(database))

            assert (await runtime.memory.for_scope(scope_id).cursor()).sequence == 0
        finally:
            await runtime.close()

    asyncio.run(scenario())


def test_scheduled_runtime_requires_file_database_and_exclusive_process_owner(tmp_path) -> None:
    async def scenario() -> None:
        with pytest.raises(SchedulerConfigurationError):
            await PowerContextRuntime.open(
                ":memory:",
                candidate_pipeline=ContentCandidatePipeline(),
                schedule_seconds=60,
            )

        database = tmp_path / "runtime.db"
        runtime = await PowerContextRuntime.open(
            database,
            candidate_pipeline=ContentCandidatePipeline(),
            schedule_seconds=60,
        )
        try:
            with pytest.raises(SchedulerStateError):
                await PowerContextRuntime.open(
                    database,
                    candidate_pipeline=ContentCandidatePipeline(),
                    schedule_seconds=60,
                )
        finally:
            await runtime.close()
            await runtime.close()

    asyncio.run(scenario())
