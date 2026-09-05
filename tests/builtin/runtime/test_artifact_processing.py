# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");

from __future__ import annotations

import asyncio
import os
import signal
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.builtin.persistence.cursors import SourceCursorRepository
from powercontext.builtin.persistence.database import AsyncDatabase
from powercontext.builtin.persistence.errors import ArtifactProcessingLeadershipLostError
from powercontext.builtin.persistence.processing import (
    ArtifactProcessingAutoWaveTargetRepository,
    ArtifactProcessingPendingRepository,
    StoredArtifactProcessingPending,
)
from powercontext.builtin.persistence.sources import SourceRepository
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.persistence.supervision import (
    ArtifactProcessingBindingStateRepository,
    ArtifactProcessingFence,
    ArtifactProcessingLeaseRepository,
    StoredArtifactProcessingLease,
)
from powercontext.builtin.persistence.tables import ARTIFACT_PROCESSING_BINDING_STATES_TABLE, SHARED_TABLES
from powercontext.builtin.runtime import artifact_processing as artifact_processing_module
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
        fail_scopes: set[str] | None = None,
        control_conflicts: dict[str, ArtifactProcessingWorkerOutcome] | None = None,
        wait_for: asyncio.Event | None = None,
        inject_wave_snapshot_changes: bool = False,
    ) -> None:
        self.database = database
        self.inject_new_source = inject_new_source
        self.inject_successor_flush = inject_successor_flush
        self.fail_scopes = set() if fail_scopes is None else fail_scopes
        self.control_conflicts = {} if control_conflicts is None else control_conflicts
        self.wait_for = wait_for
        self.inject_wave_snapshot_changes = inject_wave_snapshot_changes
        self.injected = False
        self.assignments: list[ArtifactProcessingWorkAssignment] = []
        self.active: set[tuple[str, str]] = set()

    async def start(self, assignment: ArtifactProcessingWorkAssignment):
        key = (assignment.binding_name, assignment.scope_id)
        self.assignments.append(assignment)
        if assignment.scope_id in self.fail_scopes:
            raise RuntimeError
        assert key not in self.active
        self.active.add(key)
        return _PublishingHandle(self, assignment)


class _PublishingHandle:
    def __init__(self, launcher: _PublishingLauncher, assignment: ArtifactProcessingWorkAssignment) -> None:
        self.launcher = launcher
        self.assignment = assignment

    async def wait(self) -> ArtifactProcessingWorkerCompletion:
        if self.launcher.wait_for is not None:
            await self.launcher.wait_for.wait()
        await asyncio.sleep(0)
        assignment = self.assignment
        key = (assignment.binding_name, assignment.scope_id)
        conflict = self.launcher.control_conflicts.get(assignment.scope_id)
        if conflict is not None:
            self.launcher.active.remove(key)
            return ArtifactProcessingWorkerCompletion(outcome=conflict)
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
                self.launcher.inject_wave_snapshot_changes
                and not self.launcher.injected
                and assignment.wave_kind is ArtifactProcessingWaveKind.AUTOMATIC
            ):
                await pending.raise_source(connection, "scope-b", assignment.binding_name, 1)
                await pending.raise_source(connection, "scope-c", assignment.binding_name, 2)
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


class _HangingStartLauncher:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()
        self.attempts = 0

    async def start(self, assignment: ArtifactProcessingWorkAssignment):
        del assignment
        self.attempts += 1
        self.started.set()
        try:
            await asyncio.Event().wait()
        finally:
            self.cancelled.set()
        raise AssertionError("unreachable")


class _FailingStartLauncher:
    def __init__(self) -> None:
        self.attempts = 0
        self.attempted = asyncio.Event()

    async def start(self, assignment: ArtifactProcessingWorkAssignment):
        del assignment
        self.attempts += 1
        self.attempted.set()
        raise RuntimeError


class _CapturingSpawnLauncher:
    def __init__(self) -> None:
        self._launcher = SpawnArtifactProcessingWorkerLauncher(_ignore_sigterm)
        self.assignment: ArtifactProcessingWorkAssignment | None = None
        self.process_pid: int | None = None
        self.started = asyncio.Event()

    async def start(self, assignment: ArtifactProcessingWorkAssignment):
        handle = await self._launcher.start(assignment)
        self.assignment = assignment
        self.process_pid = cast(Any, handle)._process.pid
        self.started.set()
        return handle


class _TrackingPendingRepository(ArtifactProcessingPendingRepository):
    def __init__(self) -> None:
        self.scan_calls = 0
        self.scan_limits: list[int | None] = []

    async def scan(
        self,
        connection: AsyncConnection,
        /,
        *,
        binding_name: str | None = None,
        after: tuple[str, str] | None = None,
        limit: int | None = None,
        for_update: bool = False,
    ) -> tuple[StoredArtifactProcessingPending, ...]:
        self.scan_calls += 1
        self.scan_limits.append(limit)
        return await super().scan(
            connection,
            binding_name=binding_name,
            after=after,
            limit=limit,
            for_update=for_update,
        )


class _FailingPendingRepository(ArtifactProcessingPendingRepository):
    async def scan(
        self,
        connection: AsyncConnection,
        /,
        *,
        binding_name: str | None = None,
        after: tuple[str, str] | None = None,
        limit: int | None = None,
        for_update: bool = False,
    ) -> tuple[StoredArtifactProcessingPending, ...]:
        del connection, binding_name, after, limit, for_update
        raise RuntimeError


class _RenewingLeaseRepository(ArtifactProcessingLeaseRepository):
    def __init__(self) -> None:
        self.lease = StoredArtifactProcessingLease(
            supervisor_group="global",
            holder_id="holder-a",
            supervisor_generation=1,
            lease_expires_at=datetime(3000, 1, 1, tzinfo=UTC).replace(tzinfo=None),
        )
        self.acquired = False
        self.renew_count = 0
        self.renewed = asyncio.Event()

    async def try_acquire(
        self,
        connection: AsyncConnection,
        holder_id: str,
        lease_seconds: float,
        /,
        *,
        supervisor_group: str = "global",
    ) -> StoredArtifactProcessingLease | None:
        del connection, lease_seconds, supervisor_group
        assert holder_id == self.lease.holder_id
        if self.acquired:
            return None
        self.acquired = True
        return self.lease

    async def renew(
        self,
        connection: AsyncConnection,
        fence: ArtifactProcessingFence,
        lease_seconds: float,
        /,
    ) -> StoredArtifactProcessingLease:
        del connection, lease_seconds
        assert fence == self.lease.fence("oceanbase")
        self.renew_count += 1
        if self.renew_count >= 2:
            self.renewed.set()
        return self.lease

    async def require_fence(
        self,
        connection: AsyncConnection,
        fence: ArtifactProcessingFence,
        /,
    ) -> StoredArtifactProcessingLease:
        del connection
        assert fence == self.lease.fence("oceanbase")
        return self.lease


class _CycleCountingSupervisor(ArtifactProcessingSupervisor):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.cycle_count = 0

    async def _cycle(self) -> None:
        self.cycle_count += 1
        await super()._cycle()


def _spawn_success(_assignment: ArtifactProcessingWorkAssignment) -> ArtifactProcessingWorkerCompletion:
    return ArtifactProcessingWorkerCompletion(ArtifactProcessingWorkerOutcome.SUCCEEDED)


def _ignore_sigterm(assignment: ArtifactProcessingWorkAssignment) -> ArtifactProcessingWorkerCompletion:
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    Path(assignment.scope_id).touch()
    while True:
        time.sleep(0.1)


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


def test_automatic_wave_spans_pages_before_committing_binding_time(tmp_path, monkeypatch) -> None:
    async def scenario() -> None:
        monkeypatch.setattr("powercontext.builtin.runtime.artifact_processing._DISCOVERY_PAGE_SIZE", 2)
        pending = ArtifactProcessingPendingRepository()
        states = ArtifactProcessingBindingStateRepository()
        config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'automatic-pages.db'}")
        async with SQLiteProfile.open(config, tables=SHARED_TABLES) as profile:
            async with profile.database.transaction() as connection:
                for scope_id in ("scope-a", "scope-b", "scope-c"):
                    await pending.raise_source(connection, scope_id, BINDING, 1)
            launcher = _PublishingLauncher(profile.database)
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
                max_workers=2,
                worker_timeout_seconds=1,
            ):

                async def wave_completed() -> bool:
                    async with profile.database.transaction() as connection:
                        return await states.load(connection, BINDING) is not None

                await _wait_until(wave_completed)

            assert [assignment.scope_id for assignment in launcher.assignments] == [
                "scope-a",
                "scope-b",
                "scope-c",
            ]
            async with profile.database.transaction() as connection:
                assert await pending.scan(connection, binding_name=BINDING) == ()
                for scope_id in ("scope-a", "scope-b", "scope-c"):
                    cursor = await SourceCursorRepository().load(connection, scope_id, BINDING)
                    assert cursor is not None
                    assert cursor.cursor.sequence == 1

    asyncio.run(scenario())


def test_automatic_wave_pages_only_the_wave_start_snapshot(tmp_path, monkeypatch) -> None:
    async def scenario() -> None:
        monkeypatch.setattr("powercontext.builtin.runtime.artifact_processing._DISCOVERY_PAGE_SIZE", 1)
        pending = ArtifactProcessingPendingRepository()
        states = ArtifactProcessingBindingStateRepository()
        config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'automatic-snapshot.db'}")
        async with SQLiteProfile.open(config, tables=SHARED_TABLES) as profile:
            async with profile.database.transaction() as connection:
                await pending.raise_source(connection, "scope-a", BINDING, 1)
                await pending.raise_source(connection, "scope-c", BINDING, 1)
            launcher = _PublishingLauncher(
                profile.database,
                inject_wave_snapshot_changes=True,
            )
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

            assert [(item.scope_id, item.wave_target) for item in launcher.assignments] == [
                ("scope-a", 1),
                ("scope-c", 1),
            ]
            async with profile.database.transaction() as connection:
                remaining = await pending.scan(connection, binding_name=BINDING)
                cursor = await SourceCursorRepository().load(connection, "scope-c", BINDING)
            assert [(row.scope_id, row.source_through) for row in remaining] == [
                ("scope-b", 1),
                ("scope-c", 2),
            ]
            assert cursor is not None
            assert cursor.cursor.sequence == 1

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


def test_retry_deadline_is_quiet_and_dispatches_once_when_due(tmp_path) -> None:
    async def scenario() -> None:
        pending = _TrackingPendingRepository()
        launcher = _FailingStartLauncher()
        config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'retry-deadline.db'}")
        async with SQLiteProfile.open(config, tables=SHARED_TABLES) as profile:
            await _raise_and_flush(profile.database, pending, "scope-a", 1)
            supervisor = ArtifactProcessingSupervisor(
                database=profile.database,
                bindings=(ArtifactProcessingBinding(BINDING, 1, launcher),),
                lease_mode="single-process",
                max_workers=1,
                worker_timeout_seconds=1,
                pending=pending,
                retry_base_seconds=0.2,
                retry_cap_seconds=0.2,
                retry_jitter=lambda: 1,
            )
            await supervisor.start()

            async def failure_recorded() -> bool:
                return bool(supervisor._retries)

            await _wait_until(failure_recorded)
            await asyncio.sleep(0.01)
            scans_before_deadline = pending.scan_calls
            launcher.attempted.clear()
            await asyncio.sleep(0.05)
            assert launcher.attempts == 1
            assert pending.scan_calls == scans_before_deadline

            await asyncio.wait_for(launcher.attempted.wait(), timeout=0.5)
            assert launcher.attempts == 2
            await supervisor.close()

    asyncio.run(scenario())


def test_covered_explicit_retry_wakes_its_deferred_automatic_target(tmp_path, monkeypatch) -> None:
    async def scenario() -> None:
        monkeypatch.setattr("powercontext.builtin.runtime.artifact_processing._DISCOVERY_PAGE_SIZE", 1)
        pending = ArtifactProcessingPendingRepository()
        states = ArtifactProcessingBindingStateRepository()
        cursors = SourceCursorRepository()
        config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'covered-retry.db'}")
        async with SQLiteProfile.open(config, tables=SHARED_TABLES) as profile:
            await _raise_and_flush(profile.database, pending, "scope-a", 1)
            launcher = _PublishingLauncher(profile.database, fail_scopes={"scope-a"})
            supervisor = ArtifactProcessingSupervisor(
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
                retry_base_seconds=0.1,
                retry_cap_seconds=0.1,
                retry_jitter=lambda: 1,
            )
            await supervisor.start()

            async def target_is_deferred() -> bool:
                wave = supervisor._automatic_waves.get(BINDING)
                return (
                    wave is not None
                    and wave.discovery_complete
                    and (BINDING, "scope-a") in wave.deferred_targets
                    and (BINDING, "scope-a") in supervisor._retries
                )

            await _wait_until(target_is_deferred)
            async with profile.database.transaction() as connection:
                await cursors.save(
                    connection,
                    "scope-a",
                    BINDING,
                    SourceCursor(sequence=1),
                    expected_generation=None,
                )

            async def wave_completed() -> bool:
                async with profile.database.transaction() as connection:
                    return (
                        await states.load(connection, BINDING) is not None
                        and await pending.load(connection, "scope-a", BINDING) is None
                    )

            await _wait_until(wave_completed)
            await supervisor.close()
            assert [(item.scope_id, item.wave_kind) for item in launcher.assignments] == [
                ("scope-a", ArtifactProcessingWaveKind.EXPLICIT)
            ]

    asyncio.run(scenario())


def test_backoff_key_releases_discovery_budget_for_the_next_key(tmp_path, monkeypatch) -> None:
    async def scenario() -> None:
        monkeypatch.setattr("powercontext.builtin.runtime.artifact_processing._DISCOVERY_PAGE_SIZE", 1)
        pending = ArtifactProcessingPendingRepository()
        config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'retry-fairness.db'}")
        async with SQLiteProfile.open(config, tables=SHARED_TABLES) as profile:
            for scope_id in ("scope-a", "scope-b"):
                await _raise_and_flush(profile.database, pending, scope_id, 1)
            launcher = _PublishingLauncher(profile.database, fail_scopes={"scope-a"})
            supervisor = ArtifactProcessingSupervisor(
                database=profile.database,
                bindings=(ArtifactProcessingBinding(BINDING, 1, launcher),),
                lease_mode="single-process",
                max_workers=1,
                worker_timeout_seconds=1,
                retry_base_seconds=0.05,
                retry_cap_seconds=0.05,
                retry_jitter=lambda: 1,
            )
            await supervisor.start()

            async def second_key_completed() -> bool:
                async with profile.database.transaction() as connection:
                    return await pending.load(connection, "scope-b", BINDING) is None

            await _wait_until(second_key_completed)

            async def first_key_failed_multiple_times() -> bool:
                return sum(assignment.scope_id == "scope-a" for assignment in launcher.assignments) >= 3

            await _wait_until(first_key_failed_multiple_times)
            await supervisor.close()

            assert [assignment.scope_id for assignment in launcher.assignments[:2]] == [
                "scope-a",
                "scope-b",
            ]
            async with profile.database.transaction() as connection:
                assert await pending.load(connection, "scope-a", BINDING) is not None
                assert await pending.load(connection, "scope-b", BINDING) is None

    asyncio.run(scenario())


def test_failed_explicit_scope_does_not_starve_automatic_peer(tmp_path, monkeypatch) -> None:
    async def scenario() -> None:
        monkeypatch.setattr("powercontext.builtin.runtime.artifact_processing._DISCOVERY_PAGE_SIZE", 1)
        pending = ArtifactProcessingPendingRepository()
        states = ArtifactProcessingBindingStateRepository()
        config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'explicit-auto-fairness.db'}")
        async with SQLiteProfile.open(config, tables=SHARED_TABLES) as profile:
            await _raise_and_flush(profile.database, pending, "scope-a", 1)
            async with profile.database.transaction() as connection:
                await pending.raise_source(connection, "scope-b", BINDING, 1)
            launcher = _PublishingLauncher(profile.database, fail_scopes={"scope-a"})
            supervisor = ArtifactProcessingSupervisor(
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
                retry_base_seconds=60,
                retry_jitter=lambda: 1,
            )
            await supervisor.start()

            async def peer_completed() -> bool:
                async with profile.database.transaction() as connection:
                    cursor = await SourceCursorRepository().load(connection, "scope-b", BINDING)
                    return cursor is not None and cursor.cursor.sequence == 1

            await _wait_until(peer_completed)
            await supervisor.close()

            assert [(item.scope_id, item.wave_kind) for item in launcher.assignments] == [
                ("scope-a", ArtifactProcessingWaveKind.EXPLICIT),
                ("scope-b", ArtifactProcessingWaveKind.AUTOMATIC),
            ]
            async with profile.database.transaction() as connection:
                assert await pending.load(connection, "scope-a", BINDING) is not None
                assert await pending.load(connection, "scope-b", BINDING) is not None
                assert await states.load(connection, BINDING) is None

    asyncio.run(scenario())


@pytest.mark.parametrize("request_explicit", (False, True), ids=("ordinary", "explicit"))
def test_active_failed_wave_does_not_starve_new_peer(tmp_path, monkeypatch, request_explicit) -> None:
    async def scenario() -> None:
        monkeypatch.setattr("powercontext.builtin.runtime.artifact_processing._DISCOVERY_PAGE_SIZE", 1)
        pending = ArtifactProcessingPendingRepository()
        states = ArtifactProcessingBindingStateRepository()
        targets = ArtifactProcessingAutoWaveTargetRepository()
        config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / f'new-peer-{request_explicit}.db'}")
        async with SQLiteProfile.open(config, tables=SHARED_TABLES) as profile:
            await _raise_and_flush(profile.database, pending, "scope-a", 1)
            launcher = _PublishingLauncher(profile.database, fail_scopes={"scope-a"})
            supervisor = ArtifactProcessingSupervisor(
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
                retry_base_seconds=60,
                retry_jitter=lambda: 1,
            )
            await supervisor.start()

            async def old_wave_is_backing_off() -> bool:
                wave = supervisor._automatic_waves.get(BINDING)
                return wave is not None and (BINDING, "scope-a") in supervisor._retries

            await _wait_until(old_wave_is_backing_off)
            old_wave_id = supervisor._automatic_waves[BINDING].wave_id
            async with profile.database.transaction() as connection:
                stored = await SourceRepository(SOURCE_ADAPTERS).add(
                    connection,
                    "scope-b",
                    NoteSource(
                        name="late-peer",
                        materialization=SourceMaterialization.CAPTURED,
                        body="late peer",
                    ),
                )
                await pending.raise_source(connection, "scope-b", BINDING, stored.journal_position)
                if request_explicit:
                    assert await pending.request_flush(connection, "scope-b", BINDING) is not None
            supervisor.wake()

            async def new_peer_completed() -> bool:
                async with profile.database.transaction() as connection:
                    if request_explicit:
                        return await pending.load(connection, "scope-b", BINDING) is None
                    cursor = await SourceCursorRepository().load(connection, "scope-b", BINDING)
                    return cursor is not None and cursor.cursor.sequence == 1

            await _wait_until(new_peer_completed)
            await supervisor.close()

            assert [(item.scope_id, item.wave_kind) for item in launcher.assignments] == [
                ("scope-a", ArtifactProcessingWaveKind.EXPLICIT),
                (
                    "scope-b",
                    ArtifactProcessingWaveKind.EXPLICIT if request_explicit else ArtifactProcessingWaveKind.AUTOMATIC,
                ),
            ]
            async with profile.database.transaction() as connection:
                frozen = await targets.scan(connection, old_wave_id, BINDING, limit=10)
                scope_b_pending = await pending.load(connection, "scope-b", BINDING)
                assert await states.load(connection, BINDING) is None
            assert [(target.scope_id, target.source_through) for target in frozen] == [("scope-a", 1)]
            assert (scope_b_pending is None) is request_explicit

    asyncio.run(scenario())


def test_failed_automatic_scope_does_not_starve_frozen_suffix(tmp_path, monkeypatch) -> None:
    async def scenario() -> None:
        monkeypatch.setattr("powercontext.builtin.runtime.artifact_processing._DISCOVERY_PAGE_SIZE", 1)
        pending = ArtifactProcessingPendingRepository()
        states = ArtifactProcessingBindingStateRepository()
        config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'automatic-fairness.db'}")
        async with SQLiteProfile.open(config, tables=SHARED_TABLES) as profile:
            async with profile.database.transaction() as connection:
                await pending.raise_source(connection, "scope-a", BINDING, 1)
                await pending.raise_source(connection, "scope-b", BINDING, 1)
            launcher = _PublishingLauncher(profile.database, fail_scopes={"scope-a"})
            supervisor = ArtifactProcessingSupervisor(
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
                retry_base_seconds=60,
                retry_jitter=lambda: 1,
            )
            await supervisor.start()

            async def suffix_completed() -> bool:
                async with profile.database.transaction() as connection:
                    cursor = await SourceCursorRepository().load(connection, "scope-b", BINDING)
                    return cursor is not None and cursor.cursor.sequence == 1

            await _wait_until(suffix_completed)
            await supervisor.close()

            assert [(item.scope_id, item.wave_kind) for item in launcher.assignments] == [
                ("scope-a", ArtifactProcessingWaveKind.AUTOMATIC),
                ("scope-b", ArtifactProcessingWaveKind.AUTOMATIC),
            ]
            async with profile.database.transaction() as connection:
                assert await pending.load(connection, "scope-a", BINDING) is not None
                assert await pending.load(connection, "scope-b", BINDING) is not None
                assert await states.load(connection, BINDING) is None

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "outcome",
    (
        ArtifactProcessingWorkerOutcome.CURSOR_CONFLICT,
        ArtifactProcessingWorkerOutcome.HEAD_CONFLICT,
    ),
)
def test_automatic_control_conflict_is_quiet_and_does_not_starve_frozen_suffix(
    tmp_path,
    monkeypatch,
    outcome,
) -> None:
    async def scenario() -> None:
        monkeypatch.setattr("powercontext.builtin.runtime.artifact_processing._DISCOVERY_PAGE_SIZE", 1)
        monkeypatch.setattr(
            "powercontext.builtin.runtime.artifact_processing._CONTROL_CONFLICT_RETRY_SECONDS",
            1.0,
        )
        pending = ArtifactProcessingPendingRepository()
        states = ArtifactProcessingBindingStateRepository()
        config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / f'automatic-{outcome}.db'}")
        async with SQLiteProfile.open(config, tables=SHARED_TABLES) as profile:
            async with profile.database.transaction() as connection:
                await pending.raise_source(connection, "scope-a", BINDING, 1)
                await pending.raise_source(connection, "scope-b", BINDING, 1)
            launcher = _PublishingLauncher(
                profile.database,
                control_conflicts={"scope-a": outcome},
            )
            supervisor = ArtifactProcessingSupervisor(
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
            )
            await supervisor.start()

            async def suffix_completed() -> bool:
                async with profile.database.transaction() as connection:
                    cursor = await SourceCursorRepository().load(connection, "scope-b", BINDING)
                    return cursor is not None and cursor.cursor.sequence == 1

            await _wait_until(suffix_completed)
            first_key_attempts = sum(item.scope_id == "scope-a" for item in launcher.assignments)
            await asyncio.sleep(0.05)
            assert first_key_attempts == 1
            assert sum(item.scope_id == "scope-a" for item in launcher.assignments) == first_key_attempts
            retry = supervisor._retries[(BINDING, "scope-a")]
            assert retry.next_retry_at > asyncio.get_running_loop().time()
            assert retry.consecutive_failures == 0
            await supervisor.close()

            assert [(item.scope_id, item.wave_kind) for item in launcher.assignments] == [
                ("scope-a", ArtifactProcessingWaveKind.AUTOMATIC),
                ("scope-b", ArtifactProcessingWaveKind.AUTOMATIC),
            ]
            async with profile.database.transaction() as connection:
                assert await pending.load(connection, "scope-a", BINDING) is not None
                assert await pending.load(connection, "scope-b", BINDING) is not None
                assert await states.load(connection, BINDING) is None

    asyncio.run(scenario())


def test_hanging_launch_preserves_short_lease_and_bounds_discovery(tmp_path, monkeypatch) -> None:
    async def scenario() -> None:
        monkeypatch.setattr("powercontext.builtin.runtime.artifact_processing._DISCOVERY_PAGE_SIZE", 4)
        pending = _TrackingPendingRepository()
        launcher = _HangingStartLauncher()
        leases = _RenewingLeaseRepository()
        config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'bounded-discovery.db'}")
        async with SQLiteProfile.open(config, tables=SHARED_TABLES) as profile:
            async with profile.database.transaction() as connection:
                for index in range(12):
                    await pending.raise_source(connection, f"scope-{index:02d}", BINDING, 1)
            supervisor = ArtifactProcessingSupervisor(
                database=profile.database,
                bindings=(
                    ArtifactProcessingBinding(
                        BINDING,
                        1,
                        launcher,
                        automatic_processing_interval=timedelta(seconds=60),
                    ),
                ),
                lease_mode="oceanbase",
                max_workers=1,
                worker_timeout_seconds=0.08,
                pending=pending,
                leases=leases,
                holder_id="holder-a",
                oceanbase_tick_seconds=0.002,
                oceanbase_lease_seconds=0.03,
                retry_base_seconds=60,
                retry_jitter=lambda: 1,
            )
            await supervisor.start()
            await asyncio.wait_for(launcher.started.wait(), timeout=1)
            await asyncio.wait_for(leases.renewed.wait(), timeout=1)
            await asyncio.wait_for(launcher.cancelled.wait(), timeout=1)

            async def launch_failure_recorded() -> bool:
                return bool(supervisor._retries)

            await _wait_until(launch_failure_recorded)
            assert leases.renew_count >= 2
            assert pending.scan_limits and all(limit is not None and limit <= 4 for limit in pending.scan_limits)
            assert len(supervisor._work) <= 4
            assert len(supervisor._queue) <= 4
            wave = supervisor._automatic_waves[BINDING]
            assert len(wave.targets) + len(wave.deferred_targets) == 4
            await asyncio.wait_for(supervisor.close(), timeout=0.2)

    asyncio.run(scenario())


def test_full_discovery_budget_waits_for_worker_callback_without_spinning(tmp_path, monkeypatch) -> None:
    async def scenario() -> None:
        monkeypatch.setattr("powercontext.builtin.runtime.artifact_processing._DISCOVERY_PAGE_SIZE", 2)
        release = asyncio.Event()
        pending = ArtifactProcessingPendingRepository()
        config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'full-budget.db'}")
        async with SQLiteProfile.open(config, tables=SHARED_TABLES) as profile:
            for scope_id in ("scope-a", "scope-b"):
                await _raise_and_flush(profile.database, pending, scope_id, 1)
            launcher = _PublishingLauncher(profile.database, wait_for=release)
            supervisor = _CycleCountingSupervisor(
                database=profile.database,
                bindings=(ArtifactProcessingBinding(BINDING, 1, launcher),),
                lease_mode="single-process",
                max_workers=1,
                worker_timeout_seconds=60,
            )
            await supervisor.start()

            async def first_worker_started() -> bool:
                return len(launcher.assignments) == 1

            await _wait_until(first_worker_started)
            await asyncio.sleep(0.03)
            settled_cycle_count = supervisor.cycle_count
            await asyncio.sleep(0.03)

            assert supervisor.cycle_count - settled_cycle_count <= 1
            assert supervisor._next_discovery_wake_at is None
            assert supervisor._next_wake_seconds() is None
            release.set()

            async def all_work_completed() -> bool:
                async with profile.database.transaction() as connection:
                    return await pending.scan(connection, binding_name=BINDING) == ()

            await _wait_until(all_work_completed)
            assert supervisor.cycle_count > settled_cycle_count
            await supervisor.close()

    asyncio.run(scenario())


def test_close_cancels_a_worker_launch_with_a_bounded_wait(tmp_path) -> None:
    async def scenario() -> None:
        pending = ArtifactProcessingPendingRepository()
        launcher = _HangingStartLauncher()
        config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'cancel-launch.db'}")
        async with SQLiteProfile.open(config, tables=SHARED_TABLES) as profile:
            await _raise_and_flush(profile.database, pending, "scope-a", 1)
            supervisor = ArtifactProcessingSupervisor(
                database=profile.database,
                bindings=(ArtifactProcessingBinding(BINDING, 1, launcher),),
                lease_mode="single-process",
                max_workers=1,
                worker_timeout_seconds=60,
            )
            await supervisor.start()
            await asyncio.wait_for(launcher.started.wait(), timeout=1)
            await asyncio.wait_for(supervisor.close(), timeout=0.2)
            assert launcher.cancelled.is_set()

    asyncio.run(scenario())


def test_degraded_status_is_sticky_until_a_successful_control_cycle(tmp_path) -> None:
    async def scenario() -> None:
        config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'sticky-degraded.db'}")
        async with SQLiteProfile.open(config, tables=SHARED_TABLES) as profile:
            supervisor = ArtifactProcessingSupervisor(
                database=profile.database,
                bindings=(),
                lease_mode="oceanbase",
                max_workers=1,
                worker_timeout_seconds=1,
                pending=_FailingPendingRepository(),
                holder_id="holder-a",
                oceanbase_tick_seconds=0.005,
                oceanbase_lease_seconds=60,
            )
            await supervisor.start()
            assert supervisor.status is ArtifactProcessingSupervisorStatus.DEGRADED
            await asyncio.sleep(0.03)
            assert supervisor.status is ArtifactProcessingSupervisorStatus.DEGRADED
            await supervisor.close()

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


def test_supervisor_close_kills_spawned_worker_and_stales_its_fence(tmp_path) -> None:
    async def scenario() -> None:
        ready = tmp_path / "worker-ready"
        pending = ArtifactProcessingPendingRepository()
        config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'spawn-close.db'}")
        async with SQLiteProfile.open(config, tables=SHARED_TABLES) as profile:
            await _raise_and_flush(profile.database, pending, str(ready), 1)
            launcher = _CapturingSpawnLauncher()
            supervisor = ArtifactProcessingSupervisor(
                database=profile.database,
                bindings=(ArtifactProcessingBinding(BINDING, 1, launcher),),
                lease_mode="single-process",
                max_workers=1,
                worker_timeout_seconds=60,
                holder_id="holder-a",
            )
            await supervisor.start()
            await asyncio.wait_for(launcher.started.wait(), timeout=3)

            async def child_is_ready() -> bool:
                return ready.exists()

            await _wait_until(child_is_ready)
            old_fence = launcher.assignment.fence if launcher.assignment is not None else None
            child_pid = launcher.process_pid
            assert old_fence is not None
            assert child_pid is not None

            await asyncio.wait_for(supervisor.close(), timeout=3)
            child_reaped = False
            try:
                os.kill(child_pid, 0)
            except ProcessLookupError:
                child_reaped = True
            assert child_reaped

            async with ArtifactProcessingSupervisor(
                database=profile.database,
                bindings=(),
                lease_mode="single-process",
                max_workers=1,
                worker_timeout_seconds=1,
                holder_id="holder-b",
            ) as successor:
                assert successor.fence is not None
                assert successor.fence.supervisor_generation == old_fence.supervisor_generation + 1
                async with profile.database.transaction() as connection:
                    stale_fence_rejected = False
                    try:
                        await ArtifactProcessingLeaseRepository().require_fence(connection, old_fence)
                    except ArtifactProcessingLeadershipLostError:
                        stale_fence_rejected = True
                    assert stale_fence_rejected

    asyncio.run(scenario())


def test_supervisor_close_owns_cancelled_post_spawn_cleanup(tmp_path, monkeypatch) -> None:
    async def scenario() -> None:
        pending = ArtifactProcessingPendingRepository()
        config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'spawn-cancel-close.db'}")
        spawned = threading.Event()
        release_start = threading.Event()
        child_pid: int | None = None
        real_launch = cast(Any, artifact_processing_module._OwnedSpawnPopen)._launch

        def stalled_launch(popen, process):
            nonlocal child_pid
            real_launch(popen, process)
            child_pid = popen.pid
            spawned.set()
            release_start.wait(timeout=5)

        monkeypatch.setattr(artifact_processing_module._OwnedSpawnPopen, "_launch", stalled_launch)
        async with SQLiteProfile.open(config, tables=SHARED_TABLES) as profile:
            await _raise_and_flush(profile.database, pending, "scope-a", 1)
            supervisor = ArtifactProcessingSupervisor(
                database=profile.database,
                bindings=(
                    ArtifactProcessingBinding(
                        BINDING,
                        1,
                        SpawnArtifactProcessingWorkerLauncher(_ignore_sigterm),
                    ),
                ),
                lease_mode="single-process",
                max_workers=1,
                worker_timeout_seconds=60,
                holder_id="holder-a",
            )
            await supervisor.start()
            assert await asyncio.to_thread(spawned.wait, 3)
            old_fence = supervisor.fence
            assert old_fence is not None
            assert child_pid is not None

            close_task = asyncio.create_task(supervisor.close())
            try:
                await asyncio.sleep(0.05)
                assert not close_task.done()
            finally:
                release_start.set()
            await asyncio.wait_for(close_task, timeout=3)
            child_reaped = False
            try:
                os.kill(child_pid, 0)
            except ProcessLookupError:
                child_reaped = True
            assert child_reaped

            async with ArtifactProcessingSupervisor(
                database=profile.database,
                bindings=(),
                lease_mode="single-process",
                max_workers=1,
                worker_timeout_seconds=1,
                holder_id="holder-b",
            ) as successor:
                assert successor.fence is not None
                assert successor.fence.supervisor_generation == old_fence.supervisor_generation + 1
                async with profile.database.transaction() as connection:
                    with pytest.raises(ArtifactProcessingLeadershipLostError):
                        await ArtifactProcessingLeaseRepository().require_fence(connection, old_fence)

    asyncio.run(scenario())


def test_supervisor_close_waits_for_cleanup_after_worker_start_timeout(tmp_path, monkeypatch) -> None:
    async def scenario() -> None:
        pending = ArtifactProcessingPendingRepository()
        config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'spawn-timeout-close.db'}")
        ready = tmp_path / "worker-ready"
        spawned = threading.Event()
        release_start = threading.Event()
        child_pid: int | None = None
        tracked_pipes = []
        spawn_context = artifact_processing_module.multiprocessing.get_context("spawn")
        real_pipe = type(spawn_context).Pipe
        real_launch = cast(Any, artifact_processing_module._OwnedSpawnPopen)._launch

        def tracking_pipe(context, duplex=True):
            connections = real_pipe(context, duplex=duplex)
            tracked_pipes.extend(connections)
            return connections

        def stalled_launch(popen, process):
            nonlocal child_pid
            real_launch(popen, process)
            child_pid = popen.pid
            deadline = time.monotonic() + 3
            while not ready.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            assert ready.exists()
            spawned.set()
            release_start.wait(timeout=5)

        monkeypatch.setattr(type(spawn_context), "Pipe", tracking_pipe)
        monkeypatch.setattr(artifact_processing_module._OwnedSpawnPopen, "_launch", stalled_launch)
        async with SQLiteProfile.open(config, tables=SHARED_TABLES) as profile:
            await _raise_and_flush(profile.database, pending, str(ready), 1)
            supervisor = ArtifactProcessingSupervisor(
                database=profile.database,
                bindings=(
                    ArtifactProcessingBinding(
                        BINDING,
                        1,
                        SpawnArtifactProcessingWorkerLauncher(_ignore_sigterm),
                    ),
                ),
                lease_mode="single-process",
                max_workers=1,
                worker_timeout_seconds=0.05,
                holder_id="holder-a",
            )
            await supervisor.start()
            assert await asyncio.to_thread(spawned.wait, 3)
            old_fence = supervisor.fence
            assert old_fence is not None
            assert child_pid is not None

            async def cleanup_started() -> bool:
                return any(
                    task.get_name().startswith("powercontext-artifact-worker-cleanup-")
                    for task in asyncio.all_tasks()
                    if not task.done()
                )

            await _wait_until(cleanup_started)
            close_task = asyncio.create_task(supervisor.close())
            await asyncio.sleep(0.05)
            closed_before_handoff = close_task.done()
            release_start.set()
            await asyncio.wait_for(close_task, timeout=3)

            assert not closed_before_handoff
            assert all(connection.closed for connection in tracked_pipes)
            assert not [
                task
                for task in asyncio.all_tasks()
                if task is not asyncio.current_task()
                and not task.done()
                and task.get_name().startswith("powercontext-artifact-worker-")
            ]
            with pytest.raises(ProcessLookupError):
                os.kill(child_pid, 0)
            with pytest.raises(ChildProcessError):
                await asyncio.to_thread(os.waitpid, child_pid, os.WNOHANG)

            async with ArtifactProcessingSupervisor(
                database=profile.database,
                bindings=(),
                lease_mode="single-process",
                max_workers=1,
                worker_timeout_seconds=1,
                holder_id="holder-b",
            ) as successor:
                assert successor.fence is not None
                assert successor.fence.supervisor_generation == old_fence.supervisor_generation + 1
                async with profile.database.transaction() as connection:
                    with pytest.raises(ArtifactProcessingLeadershipLostError):
                        await ArtifactProcessingLeaseRepository().require_fence(connection, old_fence)

    asyncio.run(scenario())


@pytest.mark.parametrize("cancel_start", [False, True], ids=["ordinary-failure", "cancelled-failure"])
def test_spawn_launcher_reaps_child_when_popen_raises_before_publication(tmp_path, monkeypatch, cancel_start) -> None:
    async def scenario() -> None:
        ready = tmp_path / f"worker-ready-{cancel_start}"
        launched = threading.Event()
        release_failure = threading.Event()
        child_pid: int | None = None
        real_launch = cast(Any, artifact_processing_module._OwnedSpawnPopen)._launch

        def failing_launch(popen, process):
            nonlocal child_pid
            real_launch(popen, process)
            child_pid = popen.pid
            deadline = time.monotonic() + 3
            while not ready.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            assert ready.exists()
            launched.set()
            if cancel_start:
                release_failure.wait(timeout=5)
            raise RuntimeError

        monkeypatch.setattr(artifact_processing_module._OwnedSpawnPopen, "_launch", failing_launch)
        assignment = ArtifactProcessingWorkAssignment(
            binding_name=BINDING,
            scope_id=str(ready),
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
        start_task = asyncio.create_task(SpawnArtifactProcessingWorkerLauncher(_ignore_sigterm).start(assignment))
        assert await asyncio.to_thread(launched.wait, 3)
        if cancel_start:
            start_task.cancel()
            await asyncio.sleep(0.05)
            assert not start_task.done()
            release_failure.set()
            with pytest.raises(asyncio.CancelledError):
                await start_task
        else:
            with pytest.raises(RuntimeError):
                await start_task

        assert child_pid is not None
        with pytest.raises(ProcessLookupError):
            os.kill(child_pid, 0)
        with pytest.raises(ChildProcessError):
            await asyncio.to_thread(os.waitpid, child_pid, os.WNOHANG)

    asyncio.run(scenario())
