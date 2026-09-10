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
import os
import signal
import threading
import time
from pathlib import Path
from typing import Any, cast

import pytest

from powercontext.builtin.persistence.database import AsyncDatabase
from powercontext.builtin.persistence.errors import ArtifactProcessingLeadershipLostError
from powercontext.builtin.persistence.processing_intents import ArtifactProcessingIntentRepository
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.persistence.supervision import ArtifactProcessingFence, ArtifactProcessingLeaseRepository
from powercontext.builtin.persistence.tables import SHARED_TABLES
from powercontext.builtin.runtime import artifact_processing as artifact_processing_module
from powercontext.builtin.runtime.artifact_processing import (
    ArtifactProcessingBinding,
    ArtifactProcessingSupervisor,
    ArtifactProcessingWorkAssignment,
    ArtifactProcessingWorkerCompletion,
    ArtifactProcessingWorkerOutcome,
    SpawnArtifactProcessingWorkerLauncher,
)

BINDING = "topic-memory-source-window"
SPAWN_TEST_TIMEOUT_SECONDS = 10.0


async def _request(database: AsyncDatabase, scope_id: str) -> None:
    async with database.transaction() as connection:
        await ArtifactProcessingIntentRepository().request(connection, scope_id, BINDING)


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


def test_spawn_launcher_runs_a_real_child_process() -> None:
    async def scenario() -> None:
        assignment = ArtifactProcessingWorkAssignment(
            binding_name=BINDING,
            scope_id="scope-a",
            artifact_family="topic-memory",
            claimed_request_generation=1,
            fence=ArtifactProcessingFence(
                supervisor_group="global",
                holder_id="holder-a",
                supervisor_generation=1,
                lease_mode="single-process",
            ),
            worker_id="00000000-0000-4000-8000-000000000001",
        )
        handle = await SpawnArtifactProcessingWorkerLauncher(_spawn_success).start(assignment)
        try:
            assert (
                await asyncio.wait_for(handle.wait(), timeout=SPAWN_TEST_TIMEOUT_SECONDS)
                == ArtifactProcessingWorkerCompletion()
            )
        finally:
            await handle.terminate()

    asyncio.run(scenario())


def test_supervisor_close_kills_spawned_worker_and_stales_its_fence(tmp_path) -> None:
    async def scenario() -> None:
        ready = tmp_path / "worker-ready"
        config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'spawn-close.db'}")
        async with SQLiteProfile.open(config, tables=SHARED_TABLES) as profile:
            await _request(profile.database, str(ready))
            launcher = _CapturingSpawnLauncher()
            supervisor = ArtifactProcessingSupervisor(
                database=profile.database,
                bindings=(
                    ArtifactProcessingBinding(
                        BINDING, "topic-memory", launcher, max_workers=1, worker_timeout_seconds=60
                    ),
                ),
                lease_mode="single-process",
                holder_id="holder-a",
            )
            await supervisor.start()
            await asyncio.wait_for(launcher.started.wait(), timeout=SPAWN_TEST_TIMEOUT_SECONDS)

            async def child_is_ready() -> bool:
                return ready.exists()

            await _wait_until(child_is_ready, timeout_seconds=SPAWN_TEST_TIMEOUT_SECONDS)
            old_fence = launcher.assignment.fence if launcher.assignment is not None else None
            child_pid = launcher.process_pid
            assert old_fence is not None
            assert child_pid is not None

            await asyncio.wait_for(supervisor.close(), timeout=SPAWN_TEST_TIMEOUT_SECONDS)
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
                holder_id="holder-b",
            ) as successor:
                assert successor.fence is not None
                assert successor.fence.supervisor_generation > old_fence.supervisor_generation
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
            await _request(profile.database, str(tmp_path / "worker-ready"))
            supervisor = ArtifactProcessingSupervisor(
                database=profile.database,
                bindings=(
                    ArtifactProcessingBinding(
                        BINDING,
                        "topic-memory",
                        SpawnArtifactProcessingWorkerLauncher(_ignore_sigterm),
                        max_workers=1,
                        worker_timeout_seconds=60,
                    ),
                ),
                lease_mode="single-process",
                holder_id="holder-a",
            )
            await supervisor.start()
            assert await asyncio.to_thread(spawned.wait, SPAWN_TEST_TIMEOUT_SECONDS)
            old_fence = supervisor.fence
            assert old_fence is not None
            assert child_pid is not None

            close_task = asyncio.create_task(supervisor.close())
            try:
                await asyncio.sleep(0.05)
                assert not close_task.done()
            finally:
                release_start.set()
            await asyncio.wait_for(close_task, timeout=SPAWN_TEST_TIMEOUT_SECONDS)
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
                holder_id="holder-b",
            ) as successor:
                assert successor.fence is not None
                assert successor.fence.supervisor_generation > old_fence.supervisor_generation
                async with profile.database.transaction() as connection:
                    with pytest.raises(ArtifactProcessingLeadershipLostError):
                        await ArtifactProcessingLeaseRepository().require_fence(connection, old_fence)

    asyncio.run(scenario())


def test_supervisor_close_waits_for_cleanup_after_worker_start_timeout(tmp_path, monkeypatch) -> None:
    async def scenario() -> None:
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
            deadline = time.monotonic() + SPAWN_TEST_TIMEOUT_SECONDS
            while not ready.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            assert ready.exists()
            spawned.set()
            release_start.wait(timeout=5)

        monkeypatch.setattr(type(spawn_context), "Pipe", tracking_pipe)
        monkeypatch.setattr(artifact_processing_module._OwnedSpawnPopen, "_launch", stalled_launch)
        async with SQLiteProfile.open(config, tables=SHARED_TABLES) as profile:
            await _request(profile.database, str(ready))
            supervisor = ArtifactProcessingSupervisor(
                database=profile.database,
                bindings=(
                    ArtifactProcessingBinding(
                        BINDING,
                        "topic-memory",
                        SpawnArtifactProcessingWorkerLauncher(_ignore_sigterm),
                        max_workers=1,
                        worker_timeout_seconds=0.05,
                    ),
                ),
                lease_mode="single-process",
                holder_id="holder-a",
            )
            await supervisor.start()
            assert await asyncio.to_thread(spawned.wait, SPAWN_TEST_TIMEOUT_SECONDS)
            old_fence = supervisor.fence
            assert old_fence is not None
            assert child_pid is not None

            async def cleanup_started() -> bool:
                return any(
                    task.get_name().startswith("powercontext-artifact-worker-cleanup-")
                    for task in asyncio.all_tasks()
                    if not task.done()
                )

            await _wait_until(cleanup_started, timeout_seconds=SPAWN_TEST_TIMEOUT_SECONDS)
            close_task = asyncio.create_task(supervisor.close())
            await asyncio.sleep(0.05)
            closed_before_handoff = close_task.done()
            release_start.set()
            await asyncio.wait_for(close_task, timeout=SPAWN_TEST_TIMEOUT_SECONDS)

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
                holder_id="holder-b",
            ) as successor:
                assert successor.fence is not None
                assert successor.fence.supervisor_generation > old_fence.supervisor_generation
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
            deadline = time.monotonic() + SPAWN_TEST_TIMEOUT_SECONDS
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
            artifact_family="topic-memory",
            claimed_request_generation=1,
            fence=ArtifactProcessingFence(
                supervisor_group="global",
                holder_id="holder-a",
                supervisor_generation=1,
                lease_mode="single-process",
            ),
            worker_id="00000000-0000-4000-8000-000000000001",
        )
        start_task = asyncio.create_task(SpawnArtifactProcessingWorkerLauncher(_ignore_sigterm).start(assignment))
        assert await asyncio.to_thread(launched.wait, SPAWN_TEST_TIMEOUT_SECONDS)
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
