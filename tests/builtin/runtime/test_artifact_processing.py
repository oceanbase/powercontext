# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy import update

from powercontext.builtin.persistence.cursors import SourceCursorRepository
from powercontext.builtin.persistence.database import AsyncDatabase
from powercontext.builtin.persistence.processing import ArtifactProcessingPendingRepository
from powercontext.builtin.persistence.sources import SourceRepository
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.persistence.supervision import (
    ArtifactProcessingBindingStateRepository,
    ArtifactProcessingFence,
    ArtifactProcessingLeaseRepository,
)
from powercontext.builtin.persistence.tables import ARTIFACT_PROCESSING_BINDING_STATES_TABLE, SHARED_TABLES
from powercontext.builtin.runtime.artifact_processing import (
    ArtifactProcessingBinding,
    ArtifactProcessingSupervisor,
    ArtifactProcessingSupervisorStatus,
    ArtifactProcessingWaveKind,
    ArtifactProcessingWorkAssignment,
    ArtifactProcessingWorkerCompletion,
    ArtifactProcessingWorkerOutcome,
    SpawnArtifactProcessingWorkerLauncher,
)
from powercontext.builtin.sources import SourceCursor
from powercontext.sources import SourceMaterialization
from tests.builtin.persistence.contract import SOURCE_ADAPTERS, NoteSource

BINDING = "topic-memory-source-window"


class _PublishingLauncher:
    def __init__(
        self,
        database: AsyncDatabase,
        *,
        inject_new_source: bool = False,
        inject_successor_flush: bool = False,
    ) -> None:
        self.database = database
        self.inject_new_source = inject_new_source
        self.inject_successor_flush = inject_successor_flush
        self.injected = False
        self.assignments: list[ArtifactProcessingWorkAssignment] = []
        self.active: set[tuple[str, str]] = set()

    async def start(self, assignment: ArtifactProcessingWorkAssignment):
        key = (assignment.binding_name, assignment.scope_id)
        assert key not in self.active
        self.active.add(key)
        self.assignments.append(assignment)
        return _PublishingHandle(self, assignment)


class _PublishingHandle:
    def __init__(self, launcher: _PublishingLauncher, assignment: ArtifactProcessingWorkAssignment) -> None:
        self.launcher = launcher
        self.assignment = assignment

    async def wait(self) -> ArtifactProcessingWorkerCompletion:
        await asyncio.sleep(0)
        assignment = self.assignment
        key = (assignment.binding_name, assignment.scope_id)
        leases = ArtifactProcessingLeaseRepository()
        cursors = SourceCursorRepository()
        pending = ArtifactProcessingPendingRepository()
        if self.launcher.inject_successor_flush and not self.launcher.injected:
            sources = SourceRepository(SOURCE_ADAPTERS)
            async with self.launcher.database.transaction() as connection:
                stored = await sources.add(
                    connection,
                    assignment.scope_id,
                    NoteSource(
                        name="successor",
                        materialization=SourceMaterialization.CAPTURED,
                        body="successor",
                    ),
                )
                await pending.raise_source(
                    connection,
                    assignment.scope_id,
                    assignment.binding_name,
                    stored.journal_position,
                )
                assert (
                    await pending.request_flush(
                        connection,
                        assignment.scope_id,
                        assignment.binding_name,
                    )
                    is not None
                )
            self.launcher.injected = True
        async with self.launcher.database.transaction() as connection:
            await leases.require_fence(connection, assignment.fence)
            await cursors.save(
                connection,
                assignment.scope_id,
                assignment.binding_name,
                SourceCursor(sequence=assignment.source_through),
                expected_generation=assignment.cursor_generation,
            )
            if (
                self.launcher.inject_new_source
                and not self.launcher.injected
                and assignment.wave_kind is ArtifactProcessingWaveKind.AUTOMATIC
            ):
                await pending.raise_source(
                    connection,
                    assignment.scope_id,
                    assignment.binding_name,
                    assignment.wave_target + 1,
                )
                self.launcher.injected = True
            if (
                assignment.wave_kind is ArtifactProcessingWaveKind.EXPLICIT
                and assignment.source_through == assignment.wave_target
            ):
                await pending.mark_flush_handled(
                    connection,
                    assignment.scope_id,
                    assignment.binding_name,
                    assignment.claimed_flush_generation,
                )
                await pending.delete_if_covered(
                    connection,
                    assignment.scope_id,
                    assignment.binding_name,
                    cursor=assignment.source_through,
                    source_through_limit=assignment.wave_target,
                )
        self.launcher.active.remove(key)
        return ArtifactProcessingWorkerCompletion()

    async def terminate(self) -> None:
        self.launcher.active.discard((self.assignment.binding_name, self.assignment.scope_id))


class _HangingLauncher:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.terminated = asyncio.Event()

    async def start(self, assignment: ArtifactProcessingWorkAssignment):
        del assignment
        self.started.set()
        return _HangingHandle(self.terminated)


class _HangingHandle:
    def __init__(self, terminated: asyncio.Event) -> None:
        self.terminated = terminated

    async def wait(self) -> ArtifactProcessingWorkerCompletion:
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    async def terminate(self) -> None:
        self.terminated.set()


def _spawn_success(_assignment: ArtifactProcessingWorkAssignment) -> ArtifactProcessingWorkerCompletion:
    return ArtifactProcessingWorkerCompletion(ArtifactProcessingWorkerOutcome.SUCCEEDED)


async def _wait_until(predicate, *, timeout_seconds: float = 3.0) -> None:
    async with asyncio.timeout(timeout_seconds):
        while not await predicate():  # noqa: ASYNC110 - bounded observation of committed database state
            await asyncio.sleep(0.01)


async def _raise_and_flush(
    database: AsyncDatabase,
    pending: ArtifactProcessingPendingRepository,
    scope_id: str,
    source_count: int,
) -> None:
    sources = SourceRepository(SOURCE_ADAPTERS)
    async with database.transaction() as connection:
        for position in range(1, source_count + 1):
            stored = await sources.add(
                connection,
                scope_id,
                NoteSource(
                    name=f"note-{position}",
                    materialization=SourceMaterialization.CAPTURED,
                    body=f"body-{position}",
                ),
            )
            await pending.raise_source(connection, scope_id, BINDING, stored.journal_position)
        assert await pending.request_flush(connection, scope_id, BINDING) is not None


def test_explicit_windows_are_single_flight_and_requeue_fairly(tmp_path) -> None:
    async def scenario() -> None:
        pending = ArtifactProcessingPendingRepository()
        config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'fair.db'}")
        async with SQLiteProfile.open(config, tables=SHARED_TABLES) as profile:
            for scope_id in ("scope-a", "scope-b"):
                await _raise_and_flush(profile.database, pending, scope_id, 3)
            launcher = _PublishingLauncher(profile.database)
            async with ArtifactProcessingSupervisor(
                database=profile.database,
                bindings=(ArtifactProcessingBinding(BINDING, 1, launcher),),
                lease_mode="single-process",
                max_workers=2,
                worker_timeout_seconds=1,
            ) as supervisor:

                async def completed() -> bool:
                    async with profile.database.transaction() as connection:
                        return not await pending.scan(connection)

                await _wait_until(completed)
                assert supervisor.status is ArtifactProcessingSupervisorStatus.LEADER

            keys = [(item.binding_name, item.scope_id) for item in launcher.assignments]
            assert keys[:2] == [(BINDING, "scope-a"), (BINDING, "scope-b")]
            assert keys == [
                (BINDING, "scope-a"),
                (BINDING, "scope-b"),
                (BINDING, "scope-a"),
                (BINDING, "scope-b"),
                (BINDING, "scope-a"),
                (BINDING, "scope-b"),
            ]

    asyncio.run(scenario())


def test_automatic_wave_commits_binding_time_but_preserves_newer_sources(tmp_path) -> None:
    async def scenario() -> None:
        pending = ArtifactProcessingPendingRepository()
        states = ArtifactProcessingBindingStateRepository()
        config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'automatic.db'}")
        async with SQLiteProfile.open(config, tables=SHARED_TABLES) as profile:
            async with profile.database.transaction() as connection:
                await pending.raise_source(connection, "scope-a", BINDING, 2)
            launcher = _PublishingLauncher(profile.database, inject_new_source=True)
            async with ArtifactProcessingSupervisor(
                database=profile.database,
                bindings=(
                    ArtifactProcessingBinding(
                        BINDING,
                        1,
                        launcher,
                        automatic_processing_interval=timedelta(seconds=60),
                    ),
                ),
                lease_mode="single-process",
                max_workers=1,
                worker_timeout_seconds=1,
            ):

                async def wave_completed() -> bool:
                    async with profile.database.transaction() as connection:
                        return await states.load(connection, BINDING) is not None

                await _wait_until(wave_completed)

            async with profile.database.transaction() as connection:
                stored = await pending.load(connection, "scope-a", BINDING)
                state = await states.load(connection, BINDING)
            assert stored is not None
            assert stored.source_through == 3
            assert state is not None
            assert state.last_auto_wave_completed_at is not None
            assert [item.wave_target for item in launcher.assignments] == [2, 2]

    asyncio.run(scenario())


def test_due_automatic_binding_without_pending_waits_for_the_next_interval(tmp_path) -> None:
    async def scenario() -> None:
        states = ArtifactProcessingBindingStateRepository()
        config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'automatic-idle.db'}")
        async with SQLiteProfile.open(config, tables=SHARED_TABLES) as profile:
            async with profile.database.transaction() as connection:
                await states.mark_auto_wave_completed(connection, BINDING)
                await connection.execute(
                    update(ARTIFACT_PROCESSING_BINDING_STATES_TABLE)
                    .where(ARTIFACT_PROCESSING_BINDING_STATES_TABLE.c.binding_name == BINDING)
                    .values(last_auto_wave_completed_at=datetime(2000, 1, 1, tzinfo=UTC).replace(tzinfo=None))
                )
            launcher = _PublishingLauncher(profile.database)
            supervisor = ArtifactProcessingSupervisor(
                database=profile.database,
                bindings=(
                    ArtifactProcessingBinding(
                        BINDING,
                        1,
                        launcher,
                        automatic_processing_interval=timedelta(seconds=10),
                    ),
                ),
                lease_mode="single-process",
                max_workers=1,
                worker_timeout_seconds=1,
            )
            await supervisor.start()

            assert supervisor.status is ArtifactProcessingSupervisorStatus.LEADER
            next_wake_seconds = supervisor._next_wake_seconds()
            assert next_wake_seconds is not None
            assert next_wake_seconds > 9
            assert not launcher.assignments
            await supervisor.close()

    asyncio.run(scenario())


def test_flush_during_a_wave_creates_one_successor_from_the_new_snapshot(tmp_path) -> None:
    async def scenario() -> None:
        pending = ArtifactProcessingPendingRepository()
        config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'successor.db'}")
        async with SQLiteProfile.open(config, tables=SHARED_TABLES) as profile:
            await _raise_and_flush(profile.database, pending, "scope-a", 2)
            launcher = _PublishingLauncher(profile.database, inject_successor_flush=True)
            async with ArtifactProcessingSupervisor(
                database=profile.database,
                bindings=(ArtifactProcessingBinding(BINDING, 2, launcher),),
                lease_mode="single-process",
                max_workers=1,
                worker_timeout_seconds=1,
            ):

                async def completed() -> bool:
                    async with profile.database.transaction() as connection:
                        return await pending.load(connection, "scope-a", BINDING) is None

                await _wait_until(completed)

            assert [item.wave_target for item in launcher.assignments] == [2, 3]
            assert [item.claimed_flush_generation for item in launcher.assignments] == [1, 2]
            assert [item.source_after for item in launcher.assignments] == [0, 2]

    asyncio.run(scenario())


def test_worker_timeout_terminates_child_and_keeps_durable_work(tmp_path) -> None:
    async def scenario() -> None:
        pending = ArtifactProcessingPendingRepository()
        launcher = _HangingLauncher()
        config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'timeout.db'}")
        async with SQLiteProfile.open(config, tables=SHARED_TABLES) as profile:
            await _raise_and_flush(profile.database, pending, "scope-a", 1)
            async with ArtifactProcessingSupervisor(
                database=profile.database,
                bindings=(ArtifactProcessingBinding(BINDING, 1, launcher),),
                lease_mode="single-process",
                max_workers=1,
                worker_timeout_seconds=0.01,
                retry_base_seconds=60,
                retry_jitter=lambda: 1,
            ):
                await asyncio.wait_for(launcher.terminated.wait(), timeout=1)
            async with profile.database.transaction() as connection:
                assert await pending.load(connection, "scope-a", BINDING) is not None
                assert await SourceCursorRepository().load(connection, "scope-a", BINDING) is None

    asyncio.run(scenario())


def test_restart_increments_the_term_and_recovers_only_from_durable_state(tmp_path) -> None:
    async def scenario() -> None:
        pending = ArtifactProcessingPendingRepository()
        config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'recovery.db'}")
        async with SQLiteProfile.open(config, tables=SHARED_TABLES) as profile:
            await _raise_and_flush(profile.database, pending, "scope-a", 1)
            orphan = _HangingLauncher()
            first = ArtifactProcessingSupervisor(
                database=profile.database,
                bindings=(ArtifactProcessingBinding(BINDING, 1, orphan),),
                lease_mode="single-process",
                max_workers=1,
                worker_timeout_seconds=60,
            )
            await first.start()
            await asyncio.wait_for(orphan.started.wait(), timeout=1)
            first_generation = first.fence.supervisor_generation if first.fence is not None else 0
            await first.close()

            recovered = _PublishingLauncher(profile.database)
            async with ArtifactProcessingSupervisor(
                database=profile.database,
                bindings=(ArtifactProcessingBinding(BINDING, 1, recovered),),
                lease_mode="single-process",
                max_workers=1,
                worker_timeout_seconds=1,
            ) as second:

                async def completed() -> bool:
                    async with profile.database.transaction() as connection:
                        return await pending.load(connection, "scope-a", BINDING) is None

                await _wait_until(completed)
                assert second.fence is not None
                assert second.fence.supervisor_generation == first_generation + 1
            assert recovered.assignments[0].source_after == 0

    asyncio.run(scenario())


def test_spawn_launcher_runs_a_real_child_process() -> None:
    async def scenario() -> None:
        assignment = ArtifactProcessingWorkAssignment(
            binding_name=BINDING,
            scope_id="scope-a",
            source_after=0,
            source_through=1,
            wave_target=1,
            claimed_flush_generation=1,
            cursor_generation=None,
            wave_kind=ArtifactProcessingWaveKind.EXPLICIT,
            fence=ArtifactProcessingFence(
                supervisor_group="global",
                holder_id="holder-a",
                supervisor_generation=1,
                lease_mode="single-process",
            ),
            worker_id="00000000-0000-4000-8000-000000000001",
        )
        handle = await SpawnArtifactProcessingWorkerLauncher(_spawn_success).start(assignment)
        assert await asyncio.wait_for(handle.wait(), timeout=5) == ArtifactProcessingWorkerCompletion()

    asyncio.run(scenario())
