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

"""Fair, recoverable Scope scheduling and owned child-Worker lifecycles."""

from __future__ import annotations

import asyncio
import logging
import multiprocessing
import re
import sys
import time
import traceback as traceback_module
from collections import deque
from collections.abc import Awaitable, Callable, Sequence
from contextlib import nullcontext, suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from multiprocessing.connection import Connection
from multiprocessing.context import SpawnProcess
from multiprocessing.process import BaseProcess
from random import SystemRandom
from typing import Protocol
from uuid import uuid4

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncConnection
from typing_extensions import override

if sys.platform == "win32":
    from multiprocessing.popen_spawn_win32 import Popen as SpawnPopen
else:
    from multiprocessing.popen_spawn_posix import Popen as SpawnPopen

from powercontext._logging import log_safely
from powercontext.builtin.persistence.database import AsyncDatabase
from powercontext.builtin.persistence.errors import ArtifactProcessingLeadershipLostError
from powercontext.builtin.persistence.processing_intents import ArtifactProcessingIntentRepository
from powercontext.builtin.persistence.supervision import (
    ArtifactProcessingBindingStateRepository,
    ArtifactProcessingFence,
    ArtifactProcessingLeaseMode,
    ArtifactProcessingLeaseRepository,
    database_utc_now,
)
from powercontext.builtin.persistence.tables import ARTIFACT_PROCESSING_INTENTS_TABLE, ARTIFACT_PROCESSING_LEASES_TABLE
from powercontext.builtin.runtime.cron import CronSchedule
from powercontext.builtin.runtime.processing_contracts import (
    ArtifactProcessingWorkAssignment,
    ArtifactProcessingWorkerCompletion,
    ArtifactProcessingWorkerFailure,
    ArtifactProcessingWorkerHandle,
    ArtifactProcessingWorkerLauncher,
    ArtifactProcessingWorkerOutcome,
    WorkerEntrypoint,
)
from powercontext.builtin.runtime.protocols import RuntimeTracing

logger = logging.getLogger(__name__)
_OCEANBASE_TICK_SECONDS = 1.0
_OCEANBASE_LEASE_SECONDS = 15.0
_RETRY_BASE_SECONDS = 30.0
_RETRY_CAP_SECONDS = 1800.0
_CONTROL_CONFLICT_RETRY_SECONDS = 0.1
_DISCOVERY_PAGE_SIZE = 100
_RETRY_STATE_LIMIT = 1000
_CHANNEL_QUEUE_LIMIT = _DISCOVERY_PAGE_SIZE // 2
_DISCOVERY_PAGE_DELAY_SECONDS = 0.01
_DISCOVERY_TIMEOUT_SECONDS = 2.0
_SPAWN_SIGTERM_GRACE_SECONDS = 1.0
_WORKER_SHUTDOWN_TIMEOUT_SECONDS = 5.0
ProcessingKey = tuple[str, str]


class ArtifactProcessingSupervisorStatus(StrEnum):
    DISABLED = "disabled"
    LEADER = "leader"
    STANDBY = "standby"
    DEGRADED = "degraded"


class ArtifactProcessingPendingProvider(Protocol):
    """Reconcile a bounded page of domain progress without doing business work."""

    async def reconcile(self, after_scope_id: str | None, limit: int, *, fence: ArtifactProcessingFence) -> str | None:
        """Return the next keyset position, or None when this pass is complete."""
        ...


@dataclass(frozen=True, slots=True)
class ArtifactProcessingBinding:
    """Startup registration of one Family and its independently budgeted Worker."""

    binding_name: str
    artifact_family: str
    launcher: ArtifactProcessingWorkerLauncher
    max_workers: int = 1
    worker_timeout_seconds: float = 600.0
    automatic_processing_interval: timedelta | None = None
    cron: str | None = None
    timezone: str = "Asia/Shanghai"
    config_prefix: str | None = None
    pending_provider: ArtifactProcessingPendingProvider | None = None
    # Only automatic admission is filtered; already accepted requests retain
    # their own Worker authorization and domain completion semantics.
    automatic_scope_filter: Callable[[AsyncConnection, tuple[str, ...]], Awaitable[frozenset[str]]] | None = None

    def __post_init__(self) -> None:
        if not self.binding_name or self.binding_name != self.binding_name.strip():
            raise ValueError("binding_name must be nonempty and trimmed")  # noqa: TRY003
        if re.fullmatch(r"[a-z][a-z0-9-]*", self.artifact_family) is None:
            raise ValueError("artifact_family must be a canonical Family name")  # noqa: TRY003
        prefix = self.config_prefix or self.artifact_family.replace("-", "_").upper()
        if re.fullmatch(r"[A-Z][A-Z0-9_]*", prefix) is None:
            raise ValueError("config_prefix must be an uppercase configuration prefix")  # noqa: TRY003
        object.__setattr__(self, "config_prefix", prefix)
        if type(self.max_workers) is not int or self.max_workers < 1:
            raise ValueError("max_workers must be a positive integer")  # noqa: TRY003
        if self.worker_timeout_seconds <= 0:
            raise ValueError("worker_timeout_seconds must be positive")  # noqa: TRY003
        if self.automatic_processing_interval is not None:
            if self.automatic_processing_interval.total_seconds() <= 0:
                raise ValueError("automatic interval must be positive")  # noqa: TRY003
            if self.cron is not None:
                raise ValueError("interval and cron cannot both be enabled")  # noqa: TRY003
        if self.cron is not None:
            CronSchedule.parse(self.cron, self.timezone)


@dataclass(slots=True)
class _SpawnStartOwner:
    """Retain Popen ownership before BaseProcess can publish the handle."""

    popen: SpawnPopen | None = None


_spawn_start_owners: dict[int, _SpawnStartOwner] = {}


class _OwnedSpawnPopen(SpawnPopen):
    """Publish this Popen before its initializer can create a child or fail."""

    def __new__(cls, process_obj: BaseProcess) -> _OwnedSpawnPopen:
        popen = object.__new__(cls)
        _spawn_start_owners[id(process_obj)].popen = popen
        return popen


class _OwnedSpawnProcess(SpawnProcess):
    @staticmethod
    @override
    def _Popen(process_obj: BaseProcess) -> SpawnPopen:
        return _OwnedSpawnPopen(process_obj)


def _start_owned_spawn_process(process: _OwnedSpawnProcess, owner: _SpawnStartOwner) -> None:
    process_id = id(process)
    _spawn_start_owners[process_id] = owner
    try:
        process.start()
    finally:
        _spawn_start_owners.pop(process_id, None)


class SpawnArtifactProcessingWorkerLauncher:
    """Launch a picklable Worker entrypoint in a fresh spawn child process."""

    def __init__(self, entrypoint: WorkerEntrypoint) -> None:
        self._entrypoint = entrypoint

    async def start(self, assignment: ArtifactProcessingWorkAssignment) -> ArtifactProcessingWorkerHandle:
        context = multiprocessing.get_context("spawn")
        receiver, sender = context.Pipe(duplex=False)
        process = _OwnedSpawnProcess(
            target=_run_spawned_worker,
            args=(sender, self._entrypoint, assignment),
            name=f"powercontext-artifact-worker-{assignment.worker_id}",
            daemon=False,
        )
        owner = _SpawnStartOwner()
        start_task = asyncio.create_task(
            asyncio.to_thread(_start_owned_spawn_process, process, owner),
            name=f"powercontext-artifact-worker-start-{assignment.worker_id}",
        )
        try:
            await asyncio.shield(start_task)
        except asyncio.CancelledError:
            cleanup_task = asyncio.create_task(
                _cleanup_cancelled_spawn_start(start_task, process, owner, receiver, sender),
                name=f"powercontext-artifact-worker-cleanup-{assignment.worker_id}",
            )
            await _complete_spawn_cleanup(cleanup_task)
            raise
        except BaseException:
            cleanup_task = asyncio.create_task(
                _cleanup_failed_spawn_start(owner, receiver, sender),
                name=f"powercontext-artifact-worker-cleanup-{assignment.worker_id}",
            )
            await _complete_spawn_cleanup(cleanup_task)
            raise
        sender.close()
        return _SpawnedWorkerHandle(process, receiver)


async def _cleanup_cancelled_spawn_start(
    start_task: asyncio.Task[None],
    process: BaseProcess,
    owner: _SpawnStartOwner,
    receiver: Connection,
    sender: Connection,
) -> None:
    """Own and reap a partially-started child before cancellation returns."""

    try:
        await start_task
    except BaseException:
        await _cleanup_failed_spawn_start(owner, receiver, sender)
        return
    sender.close()
    await _SpawnedWorkerHandle(process, receiver).terminate()


async def _complete_spawn_cleanup(cleanup_task: asyncio.Task[None]) -> None:
    """Keep ownership of spawn cleanup across repeated outer cancellation."""

    while not cleanup_task.done():
        try:
            await asyncio.shield(cleanup_task)
        except asyncio.CancelledError:
            continue
    cleanup_task.result()


async def _cleanup_failed_spawn_start(
    owner: _SpawnStartOwner,
    receiver: Connection,
    sender: Connection,
) -> None:
    """Terminate a child whose Popen exists but was never published by BaseProcess."""

    receiver.close()
    sender.close()
    popen = owner.popen
    if popen is None:
        return
    try:
        if hasattr(popen, "pid") and popen.poll() is None:
            popen.terminate()
            await asyncio.to_thread(_wait_for_spawn_popen_exit, popen, _SPAWN_SIGTERM_GRACE_SECONDS)
        if hasattr(popen, "pid") and popen.poll() is None:
            popen.kill()
        if hasattr(popen, "pid"):
            await asyncio.to_thread(popen.wait)
    finally:
        with suppress(AttributeError):
            popen.close()
        owner.popen = None


def _wait_for_spawn_popen_exit(popen: SpawnPopen, timeout_seconds: float) -> None:
    deadline = time.monotonic() + timeout_seconds
    while popen.poll() is None and time.monotonic() < deadline:
        time.sleep(0.01)


class _SpawnedWorkerHandle:
    def __init__(self, process: BaseProcess, receiver: Connection) -> None:
        self._process = process
        self._receiver = receiver
        self._closed = False
        self._close_lock = asyncio.Lock()

    async def wait(self) -> ArtifactProcessingWorkerCompletion:
        await asyncio.to_thread(self._process.join)
        try:
            if self._receiver.poll():
                result = self._receiver.recv()
            else:
                result = ArtifactProcessingWorkerFailure(
                    stage="worker_process",
                    error_code="worker_crash",
                    exception_type="WorkerProcessExit",
                    traceback="",
                )
        except EOFError:
            result = ArtifactProcessingWorkerFailure(
                stage="worker_process",
                error_code="worker_crash",
                exception_type="WorkerProcessExit",
                traceback="",
            )
        finally:
            await self._close()
        if isinstance(result, ArtifactProcessingWorkerFailure):
            raise _WorkerExecutionError(result)
        if not isinstance(result, ArtifactProcessingWorkerCompletion):
            raise _WorkerExecutionError(
                ArtifactProcessingWorkerFailure(
                    stage="worker_process",
                    error_code="invalid_worker_result",
                    exception_type=type(result).__name__,
                    traceback="",
                )
            )
        return result

    async def terminate(self) -> None:
        async with self._close_lock:
            if self._closed:
                return
            if self._process.is_alive():
                self._process.terminate()
                await asyncio.to_thread(self._process.join, _SPAWN_SIGTERM_GRACE_SECONDS)
            if self._process.is_alive():
                self._process.kill()
                await asyncio.to_thread(self._process.join)
            self._receiver.close()
            self._process.close()
            self._closed = True

    async def _close(self) -> None:
        async with self._close_lock:
            if self._closed:
                return
            self._receiver.close()
            self._process.close()
            self._closed = True


class _WorkerExecutionError(RuntimeError):
    def __init__(self, failure: ArtifactProcessingWorkerFailure) -> None:
        super().__init__(failure.error_code)
        self.failure = failure


class _WorkerTerminationError(RuntimeError):
    def __init__(self, handle: ArtifactProcessingWorkerHandle) -> None:
        super().__init__("Worker exit could not be confirmed")
        self.handle = handle


@dataclass(slots=True)
class _RetryState:
    failures: int
    deadline: float


@dataclass(slots=True)
class _RunningWorker:
    assignment: ArtifactProcessingWorkAssignment
    task: asyncio.Task[ArtifactProcessingWorkerCompletion]
    started_at: float


@dataclass(slots=True)
class _FamilyState:
    binding: ArtifactProcessingBinding
    ready: deque[str] = field(default_factory=deque)
    queued: set[str] = field(default_factory=set)
    retry_queued: set[str] = field(default_factory=set)
    running: dict[str, _RunningWorker] = field(default_factory=dict)
    retries: dict[str, _RetryState] = field(default_factory=dict)
    requested_after: int = 0
    scan_after: int = 0
    scan_generation: int = 0
    scan_in_progress: bool = False
    reconcile_after: str | None = None
    reconcile_complete: bool = False
    notification_at_start: int = 0
    discovery_pending: bool = True
    next_schedule_at: float | None = None
    next_discovery_at: float = 0.0
    automatic_next: bool = False
    overflow_not_before: float = 0.0
    degraded: bool = False
    completed: int = 0
    failed: int = 0
    timeouts: int = 0
    unacknowledged: int = 0
    discovery_seconds: float = 0
    invocation_seconds: float = 0


class ArtifactProcessingSupervisor:
    """One Lease and scheduling loop, with independent capacity for each Family."""

    def __init__(
        self,
        *,
        database: AsyncDatabase,
        bindings: Sequence[ArtifactProcessingBinding],
        lease_mode: ArtifactProcessingLeaseMode,
        supervisor_group: str = "global",
        intents: ArtifactProcessingIntentRepository | None = None,
        leases: ArtifactProcessingLeaseRepository | None = None,
        binding_states: ArtifactProcessingBindingStateRepository | None = None,
        holder_id: str | None = None,
        oceanbase_tick_seconds: float = _OCEANBASE_TICK_SECONDS,
        oceanbase_lease_seconds: float = _OCEANBASE_LEASE_SECONDS,
        retry_base_seconds: float = _RETRY_BASE_SECONDS,
        retry_cap_seconds: float = _RETRY_CAP_SECONDS,
        retry_jitter: Callable[[], float] | None = None,
        tracing: RuntimeTracing | None = None,
    ) -> None:
        for attribute in ("binding_name", "artifact_family", "config_prefix"):
            names = [getattr(binding, attribute) for binding in bindings]
            if len(names) != len(set(names)):
                raise ValueError(f"artifact processing {attribute} values must be unique")  # noqa: TRY003
        if supervisor_group != "global" and (
            len(bindings) != 1 or supervisor_group != f"artifact:{bindings[0].artifact_family}"
        ):
            raise ValueError("dedicated Supervisor must own exactly its named Family")  # noqa: TRY003
        self._database = database
        self._families = {binding.binding_name: _FamilyState(binding) for binding in bindings}
        self._lease_mode = lease_mode
        self._supervisor_group = supervisor_group
        self._intents = intents or ArtifactProcessingIntentRepository()
        self._leases = leases or ArtifactProcessingLeaseRepository()
        self._binding_states = binding_states or ArtifactProcessingBindingStateRepository()
        self.holder_id = holder_id or str(uuid4())
        self._tick = oceanbase_tick_seconds
        self._lease_seconds = oceanbase_lease_seconds
        self._retry_base = retry_base_seconds
        self._retry_cap = retry_cap_seconds
        self._jitter = retry_jitter or (lambda: SystemRandom().uniform(0.8, 1.2))
        self._tracing = tracing
        self._fence: ArtifactProcessingFence | None = None
        self._status = ArtifactProcessingSupervisorStatus.STANDBY
        self._wake = asyncio.Event()
        self._stop = asyncio.Event()
        self._started = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._renew_task: asyncio.Task[None] | None = None
        self._notifications = 0
        self._rotation = 0
        self._lease_lost = False
        self._unreaped: list[ArtifactProcessingWorkerHandle] = []

    @property
    def status(self) -> ArtifactProcessingSupervisorStatus:
        if self._fence is not None and any(state.degraded for state in self._families.values()):
            return ArtifactProcessingSupervisorStatus.DEGRADED
        return self._status

    @property
    def fence(self) -> ArtifactProcessingFence | None:
        return self._fence

    @property
    def family_status(self) -> dict[str, dict[str, int | float | str]]:
        return {
            state.binding.artifact_family: {
                "status": "degraded" if state.degraded else self._status.value,
                "max_workers": state.binding.max_workers,
                "used_workers": len(state.running),
                "available_workers": max(0, state.binding.max_workers - len(state.running)),
                "unacknowledged_requests": state.unacknowledged,
                "discovery_seconds": state.discovery_seconds,
                "last_invocation_seconds": state.invocation_seconds,
                "ready": len(state.ready),
                "retry_wait": sum(scope not in state.running and scope not in state.queued for scope in state.retries),
                "completed": state.completed,
                "failed": state.failed,
                "timeouts": state.timeouts,
            }
            for state in self._families.values()
        }

    async def __aenter__(self) -> ArtifactProcessingSupervisor:
        await self.start()
        return self

    async def __aexit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        await self.close()

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name=f"artifact-supervisor-{self._supervisor_group}")
            await self._started.wait()

    def wake(self, binding_name: str | None = None) -> None:
        self._notifications += 1
        for name, state in self._families.items():
            if binding_name is None or binding_name == name:
                state.discovery_pending = True
        self._wake.set()

    async def close(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        if self._unreaped:
            raise _WorkerTerminationError(self._unreaped[0])

    async def _run(self) -> None:  # noqa: C901 - lifecycle and cancellation boundaries
        try:
            while not self._stop.is_set():
                self._wake.clear()
                try:
                    if self._lease_lost:
                        await self._lose_leadership()
                    if self._fence is None:
                        await self._acquire()
                    if self._fence is not None:
                        await self._cycle()
                except asyncio.CancelledError:
                    raise
                except ArtifactProcessingLeadershipLostError:
                    await self._lose_leadership()
                except Exception as error:
                    await self._lose_leadership()
                    self._status = ArtifactProcessingSupervisorStatus.DEGRADED
                    self._log_failure(None, None, error, "supervisor")
                finally:
                    self._started.set()
                if self._wake.is_set():
                    continue
                timeout = self._next_wake_seconds()
                try:
                    if timeout is None:
                        await self._wake.wait()
                    else:
                        await asyncio.wait_for(self._wake.wait(), timeout=max(timeout, 0.001))
                except TimeoutError:
                    pass
        finally:
            await self._lose_leadership()

    async def _acquire(self) -> None:
        await self._reap_unconfirmed_exits()
        if self._unreaped:
            self._status = ArtifactProcessingSupervisorStatus.DEGRADED
            return
        async with self._database.transaction() as connection:
            if self._lease_mode == "single-process":
                acquired = await self._leases.start_single_process_term(
                    connection, self.holder_id, supervisor_group=self._supervisor_group
                )
            else:
                acquired = await self._leases.try_acquire(
                    connection, self.holder_id, self._lease_seconds, supervisor_group=self._supervisor_group
                )
        if acquired is None:
            self._status = ArtifactProcessingSupervisorStatus.STANDBY
            return
        self._fence = acquired.fence(self._lease_mode)
        self._lease_lost = False
        self._status = ArtifactProcessingSupervisorStatus.LEADER
        for state in self._families.values():
            state.discovery_pending = True
            state.reconcile_complete = False
            state.reconcile_after = None
        if self._lease_mode == "oceanbase":
            self._renew_task = asyncio.create_task(self._renew(), name=f"artifact-lease-{self._supervisor_group}")

    async def _renew(self) -> None:
        try:
            while self._fence is not None:
                await asyncio.sleep(self._lease_seconds / 3)
                fence = self._require_current_fence()
                async with self._database.transaction() as connection:
                    await self._leases.renew(connection, fence, self._lease_seconds)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self._log_failure(None, None, error, "lease_renewal")
            self._lease_lost = True
            self._wake.set()

    async def _cycle(self) -> None:
        states = tuple(self._families.values())
        if not states:
            return
        ordered = states[self._rotation :] + states[: self._rotation]
        self._rotation = (self._rotation + 1) % len(states)
        for state in ordered:
            await self._reap(state)
            if self._fence is None or self._lease_lost:
                raise ArtifactProcessingLeadershipLostError(self._supervisor_group, self.holder_id, 0)
            now = asyncio.get_running_loop().time()
            # Reserve fresh admission before retries can occupy a newly freed
            # Worker. FIFO ready order then shares execution between channels.
            discoverable = (
                len(state.running) < state.binding.max_workers
                and self._fresh_capacity(state) > 0
                and now >= max(state.next_discovery_at, state.overflow_not_before)
            )
            if discoverable:
                try:
                    async with asyncio.timeout(_DISCOVERY_TIMEOUT_SECONDS):
                        await self._discover(state)
                    state.degraded = False
                except ArtifactProcessingLeadershipLostError:
                    raise
                except Exception as error:
                    state.degraded = True
                    state.next_discovery_at = now + self._tick
                    self._log_failure(state, None, error, "scope_discovery")
                finally:
                    state.discovery_seconds = asyncio.get_running_loop().time() - now
            await self._activate_retries(state)
            await self._dispatch(state)

    async def _discover(self, state: _FamilyState) -> None:
        binding = state.binding
        loop = asyncio.get_running_loop()
        if not state.reconcile_complete and binding.pending_provider is not None:
            state.reconcile_after = await binding.pending_provider.reconcile(
                state.reconcile_after, _DISCOVERY_PAGE_SIZE, fence=self._require_current_fence()
            )
            state.reconcile_complete = state.reconcile_after is None
            if not state.reconcile_complete:
                state.next_discovery_at = loop.time() + _DISCOVERY_PAGE_DELAY_SECONDS
        else:
            state.reconcile_complete = True
        # Alternate discovery channels so explicit traffic cannot monopolize admission.
        state.automatic_next = not state.automatic_next
        if state.automatic_next:
            await self._discover_automatic(state)
            await self._discover_requested(state)
        else:
            await self._discover_requested(state)
            await self._discover_automatic(state)

    async def _discover_requested(self, state: _FamilyState) -> None:
        capacity = self._fresh_capacity(state)
        if capacity <= 0:
            return
        if not state.discovery_pending and self._lease_mode == "single-process":
            return
        if state.requested_after == 0:
            state.notification_at_start = self._notifications
        async with self._database.transaction() as connection:
            await self._leases.require_fence(connection, self._require_current_fence())
            if state.requested_after == 0:
                table = ARTIFACT_PROCESSING_INTENTS_TABLE
                state.unacknowledged = int(
                    await connection.scalar(
                        select(func.count())
                        .select_from(table)
                        .where(
                            table.c.binding_name == state.binding.binding_name,
                            table.c.requested_generation > table.c.handled_generation,
                        )
                    )
                    or 0
                )
            rows = await self._intents.scan(
                connection,
                state.binding.binding_name,
                after_sequence=state.requested_after,
                limit=min(capacity, _DISCOVERY_PAGE_SIZE),
                requested_only=True,
            )
        for row in rows:
            state.requested_after = row.pending_sequence
            if row.scope_id not in state.running and row.scope_id not in state.retries:
                self._enqueue(state, row.scope_id)
        if len(rows) < min(capacity, _DISCOVERY_PAGE_SIZE):
            state.requested_after = 0
            state.discovery_pending = state.notification_at_start != self._notifications
        else:
            state.discovery_pending = True
            state.next_discovery_at = asyncio.get_running_loop().time() + _DISCOVERY_PAGE_DELAY_SECONDS

    async def _discover_automatic(self, state: _FamilyState) -> None:  # noqa: C901 - bounded admission state
        binding = state.binding
        capacity = self._fresh_capacity(state)
        if capacity <= 0:
            return
        # Publish RAM progress only after the transaction commits. Advancing a
        # page cursor before admit/finish/commit succeeds would skip rolled-back
        # rows on the next discovery pass of this same persisted generation.
        scan_after = state.scan_after
        scan_generation = state.scan_generation
        scan_in_progress = False
        next_schedule_at = state.next_schedule_at
        next_discovery_at = state.next_discovery_at
        admitted: list[str] = []
        async with self._database.transaction() as connection:
            await self._leases.require_fence(connection, self._require_current_fence())
            persisted = await self._binding_states.load(connection, binding.binding_name, for_update=True)
            enabled = binding.automatic_processing_interval is not None or binding.cron is not None
            if not enabled:
                if persisted is not None and persisted.scan_in_progress:
                    await self._binding_states.finish_scan(connection, binding.binding_name)
                next_schedule_at = None
                scan_after = 0
            else:
                now = await database_utc_now(connection)
                if persisted is None or not persisted.scan_in_progress:
                    checkpoint = None if persisted is None else persisted.last_schedule_checkpoint_at
                    due, next_time = _schedule_deadline(binding, checkpoint, now)
                    next_schedule_at = asyncio.get_running_loop().time() + max(0, (next_time - now).total_seconds())
                    if due is not None:
                        persisted = await self._binding_states.start_scan(connection, binding.binding_name, due)
                        scan_after = 0
                if persisted is not None and persisted.scan_in_progress:
                    scan_in_progress = True
                    if scan_generation != persisted.scan_generation:
                        scan_generation = persisted.scan_generation
                        scan_after = 0
                    rows = await self._intents.scan(
                        connection,
                        binding.binding_name,
                        after_sequence=scan_after,
                        limit=min(capacity, _DISCOVERY_PAGE_SIZE),
                        dirty_only=True,
                        upper_sequence=persisted.scan_upper_pending_sequence,
                    )
                    candidates = tuple(
                        row.scope_id
                        for row in rows
                        if row.scope_id not in state.queued
                        and row.scope_id not in state.running
                        and row.scope_id not in state.retries
                        and row.last_auto_scan_generation != persisted.scan_generation
                    )
                    eligible = frozenset(candidates)
                    if candidates and binding.automatic_scope_filter is not None:
                        eligible &= await binding.automatic_scope_filter(connection, candidates)
                    for row in rows:
                        # A full page of ineligible rows still advances discovery;
                        # keep their dirty Source and request counters untouched.
                        scan_after = row.pending_sequence
                        if row.scope_id not in eligible:
                            continue
                        await self._intents.admit(
                            connection, row.scope_id, binding.binding_name, persisted.scan_generation
                        )
                        admitted.append(row.scope_id)
                    scan_in_progress = len(rows) == min(capacity, _DISCOVERY_PAGE_SIZE)
                    if not scan_in_progress:
                        await self._binding_states.finish_scan(connection, binding.binding_name)
                        scan_after = 0
                        # Starting a scan established its checkpoint, so a
                        # completed page must not restart the interval here.
                        _, next_time = _schedule_deadline(binding, persisted.last_schedule_checkpoint_at, now)
                        next_schedule_at = asyncio.get_running_loop().time() + max(0, (next_time - now).total_seconds())
                    else:
                        next_discovery_at = asyncio.get_running_loop().time() + _DISCOVERY_PAGE_DELAY_SECONDS
        state.scan_after = scan_after
        state.scan_generation = scan_generation
        state.scan_in_progress = scan_in_progress
        state.next_schedule_at = next_schedule_at
        state.next_discovery_at = next_discovery_at
        for scope in admitted:
            self._enqueue(state, scope)

    @staticmethod
    def _fresh_capacity(state: _FamilyState) -> int:
        return min(
            _DISCOVERY_PAGE_SIZE - len(state.ready), _CHANNEL_QUEUE_LIMIT - (len(state.ready) - len(state.retry_queued))
        )

    async def _activate_retries(self, state: _FamilyState) -> None:
        now = asyncio.get_running_loop().time()
        # Each channel reserves half the bounded queue. Rotate due keys so a
        # repeatedly failing prefix cannot refill every free slot indefinitely.
        allowance = min(_DISCOVERY_PAGE_SIZE - len(state.ready), _CHANNEL_QUEUE_LIMIT - len(state.retry_queued))
        for scope, retry in tuple(state.retries.items()):
            if allowance <= 0:
                break
            if retry.deadline <= now and scope not in state.running and scope not in state.queued:
                self._enqueue(state, scope, retry=True)
                state.retries.pop(scope)
                state.retries[scope] = retry
                allowance -= 1

    def _enqueue(self, state: _FamilyState, scope: str, *, retry: bool = False) -> None:
        if scope not in state.queued and scope not in state.running:
            state.ready.append(scope)
            state.queued.add(scope)
            if retry:
                state.retry_queued.add(scope)

    def _defer(self, state: _FamilyState, scope: str, failures: int, deadline: float) -> None:
        if scope not in state.retries and len(state.retries) >= _RETRY_STATE_LIMIT:
            now = asyncio.get_running_loop().time()
            expired = next(
                (
                    key
                    for key, value in state.retries.items()
                    if value.deadline <= now and key not in state.queued and key not in state.running
                ),
                None,
            )
            if expired is not None:
                # Like the previous bounded retry cache, eviction forgets
                # volatile history. Only expired backoff can be evicted.
                state.retries.pop(expired)
            else:
                # Durable requested > handled retains the omitted key. Pause
                # new discovery until every overflow failure's cooldown has
                # elapsed; flush cannot bypass this bounded-capacity gate.
                # Already-ready work drains, so only a finite cohort can extend it.
                state.overflow_not_before = max(state.overflow_not_before, deadline)
                self.wake(state.binding.binding_name)
                return
        state.retries[scope] = _RetryState(failures, deadline)

    async def _dispatch(self, state: _FamilyState) -> None:
        while state.ready and len(state.running) < state.binding.max_workers:
            scope = state.ready.popleft()
            state.queued.discard(scope)
            state.retry_queued.discard(scope)
            retry = state.retries.get(scope)
            if retry is not None and retry.deadline > asyncio.get_running_loop().time():
                continue
            async with self._database.transaction() as connection:
                fence = self._require_current_fence()
                await self._leases.require_fence(connection, fence)
                row = await self._intents.load(connection, scope, state.binding.binding_name, for_update=True)
            if row is None or row.requested_generation <= row.handled_generation:
                state.retries.pop(scope, None)
                continue
            assignment = ArtifactProcessingWorkAssignment(
                binding_name=state.binding.binding_name,
                scope_id=scope,
                artifact_family=state.binding.artifact_family,
                claimed_request_generation=row.requested_generation,
                fence=fence,
                worker_id=str(uuid4()),
            )
            task = asyncio.create_task(
                self._execute(state.binding, assignment), name=f"artifact-worker-{assignment.worker_id}"
            )
            task.add_done_callback(lambda _: self._wake.set())
            state.running[scope] = _RunningWorker(assignment, task, asyncio.get_running_loop().time())

    async def _execute(
        self, binding: ArtifactProcessingBinding, assignment: ArtifactProcessingWorkAssignment
    ) -> ArtifactProcessingWorkerCompletion:
        attributes = {"powercontext.artifact_processing.family": binding.artifact_family}
        background = (
            nullcontext()
            if self._tracing is None
            else self._tracing.background(
                "artifact_processing.worker", operation="process_artifact_scope", attributes=attributes
            )
        )
        with background as span:
            handle: ArtifactProcessingWorkerHandle | None = None
            try:
                async with asyncio.timeout(binding.worker_timeout_seconds):
                    with (
                        nullcontext()
                        if self._tracing is None
                        else self._tracing.stage("artifact_processing.worker.start", attributes=attributes)
                    ):
                        handle = await binding.launcher.start(assignment)
                    with (
                        nullcontext()
                        if self._tracing is None
                        else self._tracing.stage("artifact_processing.worker.wait", attributes=attributes)
                    ):
                        completion = await handle.wait()
                    if completion.outcome is ArtifactProcessingWorkerOutcome.LEADERSHIP_LOST:
                        raise ArtifactProcessingLeadershipLostError(  # noqa: TRY301
                            self._supervisor_group, self.holder_id, 0
                        )
                    if completion.outcome is ArtifactProcessingWorkerOutcome.SUCCEEDED:
                        # A successful process exit is not a successful invocation.
                        # Check the durable acknowledgement before finishing the trace;
                        # the reaper still checks the current fence before dispatching a successor.
                        with (
                            nullcontext()
                            if self._tracing is None
                            else self._tracing.stage("artifact_processing.worker.acknowledge", attributes=attributes)
                        ):
                            await self._verify_acknowledgement(assignment)
                    elif span is not None:
                        span.set_outcome(completion.outcome.value)
                    return completion
            except BaseException as error:
                if span is not None:
                    span.set_attributes({"powercontext.artifact_processing.failure": _worker_failure_category(error)})
                if handle is not None:
                    cleanup = asyncio.create_task(self._terminate_worker(handle))
                    await _complete_spawn_cleanup(cleanup)
                raise

    async def _verify_acknowledgement(self, assignment: ArtifactProcessingWorkAssignment) -> None:
        async with self._database.transaction() as connection:
            await self._leases.require_fence(connection, assignment.fence)
            row = await self._intents.load(connection, assignment.scope_id, assignment.binding_name)
        if row is None or row.handled_generation < assignment.claimed_request_generation:
            raise _WorkerExecutionError(
                ArtifactProcessingWorkerFailure("completion", "missing_durable_acknowledgement", "RuntimeError", "")
            )

    async def _terminate_worker(self, handle: ArtifactProcessingWorkerHandle) -> None:
        try:
            async with asyncio.timeout(_WORKER_SHUTDOWN_TIMEOUT_SECONDS):
                await handle.terminate()
        except BaseException as error:
            if isinstance(error, asyncio.CancelledError):
                raise
            raise _WorkerTerminationError(handle) from error

    async def _reap(self, state: _FamilyState) -> None:  # noqa: C901 - distinct Worker exit outcomes
        for scope, running in tuple(state.running.items()):
            if not running.task.done():
                continue
            state.invocation_seconds = asyncio.get_running_loop().time() - running.started_at
            retain_slot = False
            try:
                completion = running.task.result()
                if completion.outcome is ArtifactProcessingWorkerOutcome.LEADERSHIP_LOST:
                    raise ArtifactProcessingLeadershipLostError(self._supervisor_group, self.holder_id, 0)  # noqa: TRY301
                if completion.outcome in {
                    ArtifactProcessingWorkerOutcome.CURSOR_CONFLICT,
                    ArtifactProcessingWorkerOutcome.HEAD_CONFLICT,
                }:
                    previous = state.retries.get(scope)
                    self._defer(
                        state,
                        scope,
                        0 if previous is None else previous.failures,
                        asyncio.get_running_loop().time() + _CONTROL_CONFLICT_RETRY_SECONDS,
                    )
                    continue
                async with self._database.transaction() as connection:
                    await self._leases.require_fence(connection, self._require_current_fence())
                    row = await self._intents.load(connection, scope, state.binding.binding_name)
                if row is None or row.handled_generation < running.assignment.claimed_request_generation:
                    raise _WorkerExecutionError(  # noqa: TRY301
                        ArtifactProcessingWorkerFailure(
                            "completion", "missing_durable_acknowledgement", "RuntimeError", ""
                        )
                    )
                state.retries.pop(scope, None)
                state.completed += 1
                if row.requested_generation > row.handled_generation:
                    self.wake(state.binding.binding_name)
            except (ArtifactProcessingLeadershipLostError, _WorkerTerminationError):
                # Retain the slot until the old term is revoked and all children are reaped.
                retain_slot = True
                raise
            except asyncio.CancelledError:
                raise
            except Exception as error:
                previous = state.retries.get(scope)
                failures = 1 if previous is None else previous.failures + 1
                delay = min(self._retry_cap, self._retry_base * 2 ** min(failures - 1, 20) * self._jitter())
                self._defer(state, scope, failures, asyncio.get_running_loop().time() + delay)
                state.failed += 1
                if isinstance(error, TimeoutError):
                    state.timeouts += 1
                self._log_failure(state, running.assignment, error, "worker", failures, delay)
            finally:
                if not retain_slot:
                    state.running.pop(scope, None)
                    self.wake(state.binding.binding_name)

    async def _lose_leadership(self) -> None:
        fence, self._fence = self._fence, None
        self._status = ArtifactProcessingSupervisorStatus.STANDBY
        self._lease_lost = False
        if self._renew_task is not None:
            self._renew_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._renew_task
            self._renew_task = None
        # Revoke the term before cancellation can leave an unobservable child.
        if fence is not None:
            try:
                async with self._database.transaction() as connection:
                    await self._leases.require_fence(connection, fence)
                    await connection.execute(
                        update(ARTIFACT_PROCESSING_LEASES_TABLE)
                        .where(ARTIFACT_PROCESSING_LEASES_TABLE.c.supervisor_group == fence.supervisor_group)
                        .values(
                            holder_id=str(uuid4()),
                            supervisor_generation=fence.supervisor_generation + 1,
                            lease_expires_at=datetime(1970, 1, 1, tzinfo=UTC).replace(tzinfo=None),
                        )
                    )
            except Exception as error:
                self._log_failure(None, None, error, "term_revocation")
        tasks = [worker.task for state in self._families.values() for worker in state.running.values()]
        for task in tasks:
            task.cancel()
        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for result in results:
                if isinstance(result, _WorkerTerminationError) and result.handle not in self._unreaped:
                    self._unreaped.append(result.handle)
        await self._reap_unconfirmed_exits()
        for state in self._families.values():
            state.running.clear()
            state.ready.clear()
            state.queued.clear()
            state.retry_queued.clear()
            state.overflow_not_before = 0.0
            state.retries.clear()
            state.requested_after = state.scan_after = state.scan_generation = 0
            state.scan_in_progress = False
            state.discovery_pending = True

    async def _reap_unconfirmed_exits(self) -> None:
        for handle in tuple(self._unreaped):
            try:
                await self._terminate_worker(handle)
            except _WorkerTerminationError as error:
                self._log_failure(None, None, error, "worker_termination")
            else:
                self._unreaped.remove(handle)

    def _require_current_fence(self) -> ArtifactProcessingFence:
        if self._fence is None or self._lease_lost:
            raise ArtifactProcessingLeadershipLostError(self._supervisor_group, self.holder_id, 0)
        return self._fence

    def _next_wake_seconds(self) -> float | None:
        if self._fence is None or self._lease_mode == "oceanbase":
            return self._tick
        now = asyncio.get_running_loop().time()
        deadlines: list[float] = []
        for state in self._families.values():
            if len(state.running) >= state.binding.max_workers:
                continue
            deadlines.extend(
                retry.deadline
                for scope, retry in state.retries.items()
                if scope not in state.running and scope not in state.queued
            )
            if state.overflow_not_before > now:
                deadlines.append(state.overflow_not_before)
                continue
            if state.next_schedule_at is not None:
                deadlines.append(state.next_schedule_at)
            if state.discovery_pending or state.scan_in_progress or not state.reconcile_complete or state.degraded:
                deadlines.append(max(now, state.next_discovery_at))
        return None if not deadlines else max(0.001, min(deadlines) - now)

    def _log_failure(
        self,
        state: _FamilyState | None,
        assignment: ArtifactProcessingWorkAssignment | None,
        error: BaseException,
        stage: str,
        failures: int = 0,
        delay: float = 0,
    ) -> None:
        log_safely(
            logger,
            logging.ERROR,
            "Artifact processing failed",
            extra={
                "event": "artifact_processing.failed",
                "stage": stage,
                "binding": None if state is None else state.binding.binding_name,
                "family": None if state is None else state.binding.artifact_family,
                "scope": None if assignment is None else assignment.scope_id,
                "worker_id": None if assignment is None else assignment.worker_id,
                "request_generation": None if assignment is None else assignment.claimed_request_generation,
                "supervisor_group": self._supervisor_group,
                "supervisor_generation": None if self._fence is None else self._fence.supervisor_generation,
                "trigger": "accepted_request" if assignment is not None else "control",
                "exception_type": error.failure.exception_type
                if isinstance(error, _WorkerExecutionError)
                else type(error).__name__,
                "traceback": error.failure.traceback if isinstance(error, _WorkerExecutionError) else "",
                "retry_count": failures,
                "retry_delay_seconds": delay,
                "error_code": error.failure.error_code
                if isinstance(error, _WorkerExecutionError)
                else type(error).__name__,
            },
        )


def _schedule_deadline(
    binding: ArtifactProcessingBinding, checkpoint: datetime | None, now: datetime
) -> tuple[datetime | None, datetime]:
    if checkpoint is None:
        return now, now
    if binding.automatic_processing_interval is not None:
        next_time = checkpoint + binding.automatic_processing_interval
        return (now if now >= next_time else None), next_time
    if binding.cron is None:
        return None, now
    schedule = CronSchedule.parse(binding.cron, binding.timezone)
    first = schedule.next_after(checkpoint)
    if first > now:
        return None, first
    # Binary search avoids replaying an unbounded number of missed cron fires.
    lo, hi, latest = first, now + timedelta(microseconds=1), first
    for _ in range(48):
        if (hi - lo).total_seconds() < 0.000001:
            break
        mid = lo + (hi - lo) / 2
        candidate = schedule.next_after(mid)
        if candidate <= now:
            latest = candidate
            lo = mid
        else:
            hi = mid
    return latest, schedule.next_after(latest)


class ArtifactProcessingSupervisors:
    """Runtime-owned collection; global and dedicated share the same controller."""

    def __init__(self, supervisors: Sequence[ArtifactProcessingSupervisor]) -> None:
        self.supervisors = tuple(supervisors)

    @property
    def status(self) -> ArtifactProcessingSupervisorStatus:
        states = [item.status for item in self.supervisors]
        if ArtifactProcessingSupervisorStatus.DEGRADED in states:
            return ArtifactProcessingSupervisorStatus.DEGRADED
        if ArtifactProcessingSupervisorStatus.LEADER in states:
            return ArtifactProcessingSupervisorStatus.LEADER
        return ArtifactProcessingSupervisorStatus.STANDBY if states else ArtifactProcessingSupervisorStatus.DISABLED

    @property
    def family_status(self) -> dict[str, dict[str, int | float | str]]:
        return {family: status for item in self.supervisors for family, status in item.family_status.items()}

    def wake(self, binding_name: str | None = None) -> None:
        for supervisor in self.supervisors:
            supervisor.wake(binding_name)

    async def close(self) -> None:
        await asyncio.gather(*(item.close() for item in self.supervisors))


def _run_spawned_worker(
    sender: Connection,
    entrypoint: WorkerEntrypoint,
    assignment: ArtifactProcessingWorkAssignment,
) -> None:
    try:
        completion = entrypoint(assignment)
        sender.send(ArtifactProcessingWorkerCompletion() if completion is None else completion)
    except BaseException as error:
        sender.send(
            ArtifactProcessingWorkerFailure(
                stage=_safe_error_attribute(error, "stage", "worker"),
                error_code=_safe_error_attribute(error, "error_code", "worker_failed"),
                exception_type=type(error).__name__,
                traceback=_safe_traceback(error),
            )
        )
    finally:
        sender.close()


def _safe_error_attribute(error: BaseException, name: str, fallback: str) -> str:
    value = getattr(error, name, None)
    return value if isinstance(value, str) and value else fallback


def _worker_failure_category(error: BaseException) -> str:
    """Bound span attributes without copying exception messages or arbitrary child codes."""
    if isinstance(error, asyncio.CancelledError):
        return "cancelled"
    if isinstance(error, TimeoutError):
        return "timeout"
    if isinstance(error, ArtifactProcessingLeadershipLostError):
        return "leadership_lost"
    if isinstance(error, _WorkerExecutionError) and error.failure.error_code == "missing_durable_acknowledgement":
        return "missing_durable_acknowledgement"
    return "worker_failed"


def _safe_traceback(error: BaseException) -> str:
    """Format stack locations without exception values that may contain Source/model data."""

    return "".join(traceback_module.format_list(traceback_module.extract_tb(error.__traceback__)))


__all__ = [
    "ArtifactProcessingBinding",
    "ArtifactProcessingPendingProvider",
    "ArtifactProcessingSupervisor",
    "ArtifactProcessingSupervisorStatus",
    "ArtifactProcessingSupervisors",
    "ArtifactProcessingWorkAssignment",
    "ArtifactProcessingWorkerCompletion",
    "ArtifactProcessingWorkerFailure",
    "ArtifactProcessingWorkerHandle",
    "ArtifactProcessingWorkerLauncher",
    "ArtifactProcessingWorkerOutcome",
    "SpawnArtifactProcessingWorkerLauncher",
]
