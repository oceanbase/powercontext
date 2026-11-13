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
from collections.abc import Awaitable, Callable, Iterator, Sequence
from contextlib import ExitStack, contextmanager, nullcontext, suppress
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from multiprocessing.context import SpawnProcess
from multiprocessing.process import BaseProcess
from random import SystemRandom
from typing import Literal, Protocol
from uuid import uuid4
from zoneinfo import ZoneInfo

from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncConnection
from typing_extensions import override

if sys.platform == "win32":
    from multiprocessing.popen_spawn_win32 import Popen as SpawnPopen
else:
    from multiprocessing.popen_spawn_posix import Popen as SpawnPopen

from powercontext._logging import error_category, is_inference_failure_code, log_context, log_safely
from powercontext.builtin.inference.observability import (
    ProviderObservationCollector,
    ProviderObservationReport,
    bind_provider_observer,
)
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
from powercontext.builtin.persistence.tables import ARTIFACT_PROCESSING_LEASES_TABLE
from powercontext.builtin.processing_diagnostics import (
    ProcessingDiagnostics,
    classify_block_code,
    safe_classify_processing_error,
    safe_log_fields,
)
from powercontext.builtin.runtime.observations import DurationObservations, DurationObservationSnapshot
from powercontext.builtin.runtime.processing_contracts import (
    ArtifactProcessingBlock,
    ArtifactProcessingBlockedError,
    ArtifactProcessingModelUsageDelta,
    ArtifactProcessingWorkAssignment,
    ArtifactProcessingWorkerCompletion,
    ArtifactProcessingWorkerFailure,
    ArtifactProcessingWorkerHandle,
    ArtifactProcessingWorkerLauncher,
    ArtifactProcessingWorkerOutcome,
    WorkerEntrypoint,
    WorkerInitializerPreparation,
    processing_error_code,
)
from powercontext.builtin.runtime.protocols import RuntimeSpan, RuntimeTracing
from powercontext.builtin.runtime.worker_observability import (
    WorkerModelUsageCollector,
    worker_model_usage_collector,
)
from powercontext.builtin.statistics.models import ProcessingObservation

logger = logging.getLogger(__name__)
_OCEANBASE_TICK_SECONDS = 1.0
_OCEANBASE_LEASE_SECONDS = 15.0
_RETRY_BASE_SECONDS = 30.0
_RETRY_CAP_SECONDS = 1800.0
_BLOCKED_CHECK_SECONDS = 300.0
_ADMISSION_PROBE_TIMEOUT_SECONDS = 2.0
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
    # Applies to every invocation, including explicit requests and retries.
    # Domains inspect durable metadata here; they retain their Worker guards.
    work_block: Callable[[AsyncConnection, str, str], Awaitable[ArtifactProcessingBlock | None]] | None = None
    # Hot-managed definitions can revoke a Scope even for an older accepted request.
    invocation_scope_filter: Callable[[AsyncConnection, tuple[str, ...]], Awaitable[frozenset[str]]] | None = None
    # Receives one fixed telemetry summary after every worker execution attempt.
    # It must not affect domain scheduling or completion semantics.
    worker_model_usage_observer: Callable[[ArtifactProcessingModelUsageDelta | None], None] | None = None
    work_failure: Callable[[AsyncConnection, str, str, str], Awaitable[None]] | None = None
    worker_provider_observer: Callable[[ProviderObservationReport | None], None] | None = None

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
            CronTrigger.from_crontab(self.cron, timezone=ZoneInfo(self.timezone))


class _WorkerResultSender(Protocol):
    """The child's end of the result pipe; POSIX yields `Connection`, Windows `PipeConnection`."""

    def send(self, obj: object, /) -> None: ...

    def close(self) -> None: ...


class _WorkerResultReceiver(Protocol):
    """The owner's end of the result pipe; POSIX yields `Connection`, Windows `PipeConnection`."""

    def poll(self) -> bool: ...

    def recv(self) -> object: ...

    def close(self) -> None: ...


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

    def __init__(
        self,
        entrypoint: WorkerEntrypoint,
        *,
        initializer: Callable[[], None] | WorkerInitializerPreparation | None = None,
        entrypoint_wrapper: Callable[[WorkerEntrypoint], WorkerEntrypoint] | None = None,
        collect_model_usage: bool = False,
        collect_provider_observations: bool = False,
    ) -> None:
        self._entrypoint = entrypoint
        self._initializer = initializer
        self._entrypoint_wrapper = entrypoint_wrapper
        self._collect_model_usage = collect_model_usage
        self._collect_provider_observations = collect_provider_observations

    async def start(self, assignment: ArtifactProcessingWorkAssignment) -> ArtifactProcessingWorkerHandle:
        with ExitStack() as launch_resources:
            initializer = self._initializer
            if isinstance(initializer, WorkerInitializerPreparation):
                initializer = launch_resources.enter_context(initializer.prepare())
            context = multiprocessing.get_context("spawn")
            receiver, sender = context.Pipe(duplex=False)
            entrypoint = self._entrypoint
            if self._entrypoint_wrapper is not None:
                with suppress(Exception):
                    entrypoint = self._entrypoint_wrapper(entrypoint)
            process = _OwnedSpawnProcess(
                target=_run_spawned_worker,
                args=(
                    sender,
                    entrypoint,
                    assignment,
                    initializer,
                    self._collect_model_usage,
                    self._collect_provider_observations,
                ),
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
                await _complete_cleanup(cleanup_task)
                raise
            except BaseException:
                cleanup_task = asyncio.create_task(
                    _cleanup_failed_spawn_start(owner, receiver, sender),
                    name=f"powercontext-artifact-worker-cleanup-{assignment.worker_id}",
                )
                await _complete_cleanup(cleanup_task)
                raise
            sender.close()
            return _SpawnedWorkerHandle(process, receiver)


async def _cleanup_cancelled_spawn_start(
    start_task: asyncio.Task[None],
    process: BaseProcess,
    owner: _SpawnStartOwner,
    receiver: _WorkerResultReceiver,
    sender: _WorkerResultSender,
) -> None:
    """Own and reap a partially-started child before cancellation returns."""

    try:
        await start_task
    except BaseException:
        await _cleanup_failed_spawn_start(owner, receiver, sender)
        return
    sender.close()
    await _SpawnedWorkerHandle(process, receiver).terminate()


async def _complete_cleanup(cleanup_task: asyncio.Task[None]) -> None:
    """Keep cleanup running to completion across repeated outer cancellation."""

    while not cleanup_task.done():
        try:
            await asyncio.shield(cleanup_task)
        except asyncio.CancelledError:
            continue
    cleanup_task.result()


async def _cleanup_failed_spawn_start(
    owner: _SpawnStartOwner,
    receiver: _WorkerResultReceiver,
    sender: _WorkerResultSender,
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
    def __init__(self, process: BaseProcess, receiver: _WorkerResultReceiver) -> None:
        self._process = process
        self._receiver = receiver
        self._closed = False
        self._close_lock = asyncio.Lock()
        self._join_task: asyncio.Task[None] | None = None

    async def wait(self) -> ArtifactProcessingWorkerCompletion:
        # Start reaping immediately, but drain the report while join is pending.
        # Cancellation must retain the join owner through exit-status publication.
        if self._join_task is None:
            self._join_task = asyncio.create_task(asyncio.to_thread(self._process.join))
        try:
            # Drain the bounded report before joining: send() can block on a full pipe.
            result = await asyncio.to_thread(self._receiver.recv)
        except EOFError:
            result = ArtifactProcessingWorkerFailure(
                stage="worker_process",
                error_code="worker_crash",
                exception_type="WorkerProcessExit",
                traceback="",
            )
        await self._join()
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
                try:
                    async with asyncio.timeout(_SPAWN_SIGTERM_GRACE_SECONDS):
                        await self._join()
                except TimeoutError:
                    pass
            if self._process.is_alive():
                self._process.kill()
            await self._join()
            self._receiver.close()
            self._process.close()
            self._closed = True

    async def _join(self) -> None:
        # Cancelling an async waiter cannot stop its executor thread. Keep one
        # join owner until it publishes the exit status, including on shutdown.
        if self._join_task is None:
            self._join_task = asyncio.create_task(asyncio.to_thread(self._process.join))
        await asyncio.shield(self._join_task)

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
    block: ArtifactProcessingBlock | None = None
    request_generation: int = 0
    probe_error: tuple[str, str] | None = None
    last_error_code: str | None = None
    last_error_stage: str | None = None
    last_error_at: datetime | None = None
    last_error_worker_id: str | None = None
    last_error_trace_id: str | None = None


@dataclass(slots=True)
class _RunningWorker:
    assignment: ArtifactProcessingWorkAssignment
    started_at: float
    task: asyncio.Task[ArtifactProcessingWorkerCompletion] = field(init=False)
    trace_id: str | None = None
    queue_wait_ms: float | None = None


@dataclass(frozen=True, slots=True)
class ProcessingObservationSnapshot:
    """Process-local bounded observations; reading never performs domain work."""

    attempt_durations: tuple[DurationObservationSnapshot, ...]
    discovery_durations: tuple[DurationObservationSnapshot, ...]
    error_counts: tuple[tuple[str, int], ...]
    retry_counts: tuple[tuple[str, int], ...]
    admission_blocks: int


@dataclass(slots=True)
class _FamilyState:
    binding: ArtifactProcessingBinding
    ready: deque[str] = field(default_factory=deque)
    queued: set[str] = field(default_factory=set)
    queued_at: dict[str, float] = field(default_factory=dict)
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
    blocked_rescan_at: float | None = None
    degraded: bool = False
    completed: int = 0
    failed: int = 0
    timeouts: int = 0
    unacknowledged: int = 0
    discovery_seconds: float = 0
    invocation_seconds: float = 0
    last_error: str = ""
    last_error_stage: str = ""
    last_error_at: str = ""
    last_success_at: str = ""
    last_acknowledgement_timestamp_seconds: float = 0
    requested_snapshot_timestamp_seconds: float = 0
    next_pending_observation_at: float = 0
    pending_observation_failures: int = 0
    pending_observed: dict[str, tuple[int, float]] = field(default_factory=dict)
    attempt_durations: DurationObservations = field(default_factory=DurationObservations)
    discovery_durations: DurationObservations = field(default_factory=DurationObservations)
    error_counts: dict[str, int] = field(default_factory=dict)
    retry_counts: dict[str, int] = field(default_factory=dict)
    admission_blocks: int = 0
    discovered_at: float = 0
    pending_since: dict[str, float] = field(default_factory=dict)


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
        blocked_check_seconds: float = _BLOCKED_CHECK_SECONDS,
        pending_observation_interval_seconds: float = 30.0,
        tracing: RuntimeTracing | None = None,
        processing_observer: Callable[[str, str, float], None] | None = None,
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
        if blocked_check_seconds <= 0:
            raise ValueError("blocked check interval must be positive")  # noqa: TRY003
        self._blocked_check_seconds = blocked_check_seconds
        if pending_observation_interval_seconds <= 0:
            raise ValueError("pending observation interval must be positive")  # noqa: TRY003
        self._pending_observation_interval = pending_observation_interval_seconds
        self._tracing = tracing
        self._processing_observer = processing_observer
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
        self._binding_changes: asyncio.Queue[tuple[str, ArtifactProcessingBinding | None, asyncio.Future[None]]] = (
            asyncio.Queue()
        )

    @property
    def status(self) -> ArtifactProcessingSupervisorStatus:
        if not self._families:
            return ArtifactProcessingSupervisorStatus.DISABLED
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
                "supervisor_running": self._task is not None and not self._task.done(),
                "supervisor_role": "leader" if self._fence is not None and not self._lease_lost else "standby",
                "supervisor_alive": int(self._task is not None and not self._task.done()),
                "leader": int(self._fence is not None and not self._lease_lost),
                "degraded": int(state.degraded),
                "discovered_timestamp_seconds": state.discovered_at,
                "unacknowledged_observed_timestamp_seconds": state.requested_snapshot_timestamp_seconds,
                "last_success_timestamp_seconds": (
                    datetime.fromisoformat(state.last_success_at).timestamp() if state.last_success_at else 0
                ),
                "oldest_observed_pending_timestamp_seconds": min(state.pending_since.values(), default=0),
                "blocked": sum(retry.block is not None for retry in state.retries.values()),
                "max_workers": state.binding.max_workers,
                "automatic_processing_enabled": (
                    state.binding.automatic_processing_interval is not None or state.binding.cron is not None
                ),
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
                "last_error": state.last_error,
                "last_error_stage": state.last_error_stage,
                "last_error_at": state.last_error_at,
                "last_success_at": state.last_success_at,
                "last_acknowledgement_timestamp_seconds": state.last_acknowledgement_timestamp_seconds,
                "requested_snapshot_timestamp_seconds": state.requested_snapshot_timestamp_seconds,
                "oldest_observed_pending_age_seconds": max(
                    0.0, time.monotonic() - min(observed for _, observed in state.pending_observed.values())
                )
                if state.pending_observed
                else 0.0,
                "pending_observation_failures": state.pending_observation_failures,
                "observed_pending_scopes": len(state.pending_observed),
                "blocked_wait": sum(retry.block is not None for retry in state.retries.values()),
            }
            for state in self._families.values()
        }

    @property
    def processing_observations(self) -> dict[str, ProcessingObservationSnapshot]:
        return {
            state.binding.artifact_family: ProcessingObservationSnapshot(
                state.attempt_durations.snapshot(),
                state.discovery_durations.snapshot(),
                tuple(sorted(state.error_counts.items())),
                tuple(sorted(state.retry_counts.items())),
                state.admission_blocks,
            )
            for state in self._families.values()
        }

    async def __aenter__(self) -> ArtifactProcessingSupervisor:
        await self.start()
        return self

    async def __aexit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        await self.close()

    def observe_scope(self, scope_id: str) -> tuple[ProcessingObservation, ...]:
        """Sample local scheduling state without inspecting another Scope's work."""
        if self._fence is None or self._lease_lost or self._task is None or self._task.done():
            return ()
        now = asyncio.get_running_loop().time()
        observed_at = datetime.now(UTC)
        observations = []
        for state in self._families.values():
            running = state.running.get(scope_id)
            retry = state.retries.get(scope_id)
            queued = scope_id in state.queued
            status = "idle"
            reason = "waiting_schedule" if state.next_schedule_at is not None else "idle"
            next_check = state.next_schedule_at
            if running is not None:
                status, reason, next_check = "running", "executing", None
            elif queued:
                status, reason = (
                    "queued",
                    "waiting_capacity" if len(state.running) >= state.binding.max_workers else "waiting_dispatch",
                )
                next_check = None
            elif retry is not None:
                status, reason = (
                    ("blocked", "policy_block") if retry.block is not None else ("retry_wait", "retry_backoff")
                )
                next_check = retry.deadline
            pending = state.pending_since.get(scope_id)
            block_code = None if retry is None or retry.block is None else retry.block.error_code
            # Custom adapters must not expose arbitrary provider messages through this field.
            if block_code is not None and re.fullmatch(r"[a-z][a-z0-9_]{0,127}", block_code) is None:
                block_code = None
            observations.append(
                ProcessingObservation(
                    binding=state.binding.binding_name,
                    family=state.binding.artifact_family,
                    state=status,
                    reason=reason,
                    observed_at=observed_at,
                    scheduler_observed=True,
                    pending_since=None if pending is None else datetime.fromtimestamp(pending, UTC),
                    running_seconds=None if running is None else max(0, now - running.started_at),
                    next_check_seconds=None if next_check is None else max(0, next_check - now),
                    worker_id=None if running is None else running.assignment.worker_id,
                    attempt_generation=None if running is None else running.assignment.claimed_request_generation,
                    retry_failures=None if retry is None else retry.failures,
                    block_code=block_code,
                    last_error_code=None if retry is None else retry.last_error_code,
                    last_error_stage=None if retry is None else retry.last_error_stage,
                    last_error_at=None if retry is None else retry.last_error_at,
                    last_error_worker_id=None if retry is None else retry.last_error_worker_id,
                    last_error_trace_id=None if retry is None else retry.last_error_trace_id,
                )
            )
        return tuple(observations)

    async def start(self) -> None:
        if self._task is None:
            # Control tasks inherit this context too, including lease renewal and reaping.
            with log_context({"request_id": None, "scope": None}):
                self._task = asyncio.create_task(self._run(), name=f"artifact-supervisor-{self._supervisor_group}")
            await self._started.wait()

    def wake(self, binding_name: str | None = None) -> None:
        self._notifications += 1
        for name, state in self._families.items():
            if binding_name is None or binding_name == name:
                state.discovery_pending = True
        self._wake.set()

    async def replace_binding(self, name: str, binding: ArtifactProcessingBinding | None) -> None:
        """Change one global binding at a scheduling boundary and reap its old children."""

        if self._supervisor_group != "global" or self._task is None or self._stop.is_set():
            raise ValueError("hot registration requires a running global Supervisor")  # noqa: TRY003
        if binding is not None and binding.binding_name != name:
            raise ValueError("replacement binding identity must match")  # noqa: TRY003
        done = asyncio.get_running_loop().create_future()
        self._binding_changes.put_nowait((name, binding, done))
        self._wake.set()
        await asyncio.shield(done)

    async def _apply_binding_changes(self) -> None:
        while not self._binding_changes.empty():
            name, binding, done = self._binding_changes.get_nowait()
            try:
                state = self._families.get(name)
                if state is not None:
                    tasks = [worker.task for worker in state.running.values()]
                    for task in tasks:
                        task.cancel()
                    results = await asyncio.gather(*tasks, return_exceptions=True)
                    for result in results:
                        if isinstance(result, _WorkerTerminationError):
                            raise result  # noqa: TRY301 - propagate failed child reaping to leadership revocation
                    del self._families[name]
                if binding is not None:
                    if any(item.binding.artifact_family == binding.artifact_family for item in self._families.values()):
                        raise ValueError("an Artifact family already has a binding")  # noqa: TRY003, TRY301
                    self._families[name] = _FamilyState(binding)
                done.set_result(None)
            except BaseException as error:
                done.set_exception(error)
                if isinstance(error, (asyncio.CancelledError, _WorkerTerminationError)):
                    raise

    async def close(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._task is not None:
            with suppress(asyncio.CancelledError):
                await _complete_cleanup(self._task)
            self._task = None
        if self._unreaped:
            raise _WorkerTerminationError(self._unreaped[0])

    async def _run(self) -> None:  # noqa: C901 - lifecycle and cancellation boundaries
        try:
            while not self._stop.is_set():
                self._wake.clear()
                try:
                    await self._apply_binding_changes()
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
                    self._record_failure(None, error, "supervisor")
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
            while not self._binding_changes.empty():
                _, _, done = self._binding_changes.get_nowait()
                done.set_exception(RuntimeError("Supervisor stopped before applying the binding"))
            cleanup = asyncio.create_task(
                self._lose_leadership(), name=f"artifact-supervisor-cleanup-{self._supervisor_group}"
            )
            await _complete_cleanup(cleanup)

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
            state.next_pending_observation_at = 0
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
            self._record_failure(None, error, "lease_renewal")
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
            if state.blocked_rescan_at is not None and now >= state.blocked_rescan_at:
                state.blocked_rescan_at = None
                state.discovery_pending = True
            # Reserve fresh admission before retries can occupy a newly freed
            # Worker. FIFO ready order then shares execution between channels.
            discoverable = (
                len(state.running) < state.binding.max_workers
                and self._fresh_capacity(state) > 0
                and now >= max(state.next_discovery_at, state.overflow_not_before)
            )
            if discoverable:
                discovery_outcome = "success"
                try:
                    async with asyncio.timeout(_DISCOVERY_TIMEOUT_SECONDS):
                        await self._discover(state)
                    state.degraded = False
                    state.discovered_at = time.time()
                except ArtifactProcessingLeadershipLostError:
                    discovery_outcome = "leadership_lost"
                    raise
                except asyncio.CancelledError:
                    discovery_outcome = "cancelled"
                    raise
                except Exception as error:
                    discovery_outcome = "failure"
                    state.degraded = True
                    state.next_discovery_at = now + self._tick
                    self._record_failure(state, error, "scope_discovery")
                    self._log_failure(state, None, error, "scope_discovery")
                finally:
                    state.discovery_seconds = asyncio.get_running_loop().time() - now
                    with suppress(Exception):
                        state.discovery_durations.record(discovery_outcome, state.discovery_seconds)
            await self._observe_pending_requests(state)
            await self._activate_retries(state)
            await self._dispatch(state)
            retained = state.queued | state.running.keys() | state.retries.keys()
            state.pending_observed = {
                key: observed for key, observed in state.pending_observed.items() if key in retained
            }

    async def _observe_pending_requests(self, state: _FamilyState) -> None:
        # Independent of worker capacity: a stalled worker must not freeze the
        # pending snapshot. Rate-limit aggregate reads, including after failures.
        now = asyncio.get_running_loop().time()
        if now < state.next_pending_observation_at:
            return
        state.next_pending_observation_at = now + self._pending_observation_interval
        try:
            async with asyncio.timeout(_DISCOVERY_TIMEOUT_SECONDS):
                async with self._database.transaction(consistent_snapshot=True) as connection:
                    observation = await self._intents.pending_observation(connection, state.binding.binding_name)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            state.pending_observation_failures += 1
            self._log_failure(state, None, error, "pending_observation")
            return
        state.unacknowledged = observation.pending_count
        state.requested_snapshot_timestamp_seconds = observation.observed_at.replace(tzinfo=UTC).timestamp()
        if not state.unacknowledged:
            state.pending_observed.clear()

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
            rows = await self._intents.scan(
                connection,
                state.binding.binding_name,
                after_sequence=state.requested_after,
                limit=min(capacity, _DISCOVERY_PAGE_SIZE),
                requested_only=True,
            )
        for row in rows:
            self._observe_pending(state, row.scope_id, row.requested_generation)
            state.requested_after = row.pending_sequence
            if row.scope_id not in state.running and row.scope_id not in state.retries:
                self._enqueue(state, row.scope_id)
            elif (
                (retry := state.retries.get(row.scope_id)) is not None
                and retry.block is not None
                and row.requested_generation > retry.request_generation
            ):
                self._enqueue(state, row.scope_id, retry=True)
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
                        and (row.scope_id not in state.retries or state.retries[row.scope_id].block is not None)
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

    @staticmethod
    def _observe_pending(state: _FamilyState, scope: str, generation: int) -> None:
        previous = state.pending_observed.get(scope)
        if previous is None and len(state.pending_observed) >= _RETRY_STATE_LIMIT:
            return
        if previous is None or previous[0] != generation:
            state.pending_observed[scope] = (generation, time.monotonic())

    def _enqueue(self, state: _FamilyState, scope: str, *, retry: bool = False) -> None:
        if scope not in state.queued and scope not in state.running:
            state.pending_since.setdefault(scope, time.time())
            state.queued_at[scope] = asyncio.get_running_loop().time()
            state.ready.append(scope)
            state.queued.add(scope)
            if retry:
                state.retry_queued.add(scope)
            with log_context({"request_id": None}):
                log_safely(
                    logger,
                    logging.INFO,
                    "Artifact processing Scope queued",
                    extra={
                        "event": "artifact_processing.queued",
                        "operation": "process_artifact_scope",
                        "unit": "background",
                        "stage": "queued",
                        "scope": scope,
                        "family": state.binding.artifact_family,
                        "binding": state.binding.binding_name,
                    },
                )

    def _defer(
        self,
        state: _FamilyState,
        scope: str,
        failures: int,
        deadline: float,
        *,
        block: ArtifactProcessingBlock | None = None,
        request_generation: int = 0,
        probe_error: tuple[str, str] | None = None,
        reason: Literal["failure", "conflict", "blocked"] = "failure",
        error: BaseException | None = None,
        stage: str = "worker",
        worker_id: str | None = None,
        trace_id: str | None = None,
    ) -> None:
        state.retry_counts[reason] = state.retry_counts.get(reason, 0) + 1
        if scope not in state.retries and len(state.retries) >= _RETRY_STATE_LIMIT:
            now = asyncio.get_running_loop().time()
            expired = next(
                (
                    key
                    for key, value in state.retries.items()
                    if (value.deadline <= now or value.block is not None)
                    and key not in state.queued
                    and key not in state.running
                ),
                None,
            )
            if expired is not None:
                evicted = state.retries.pop(expired)
                state.pending_since.pop(expired, None)
                if evicted.block is not None:
                    # The domain gate still protects evicted terminal keys.
                    # Revisit their durable intents without delaying fresh work.
                    state.blocked_rescan_at = (
                        evicted.deadline
                        if state.blocked_rescan_at is None
                        else min(state.blocked_rescan_at, evicted.deadline)
                    )
            elif block is not None:
                state.blocked_rescan_at = (
                    deadline if state.blocked_rescan_at is None else min(state.blocked_rescan_at, deadline)
                )
                state.pending_since.pop(scope, None)
                return
            else:
                # Durable requested > handled retains the omitted key. Pause
                # new discovery until every overflow failure's cooldown has
                # elapsed; flush cannot bypass this bounded-capacity gate.
                # Already-ready work drains, so only a finite cohort can extend it.
                state.overflow_not_before = max(state.overflow_not_before, deadline)
                self.wake(state.binding.binding_name)
                state.pending_since.pop(scope, None)
                return
        state.pending_since.setdefault(scope, time.time())
        code, failure_stage = (None, None) if error is None else _processing_failure(error, stage)
        failure_at = None if error is None else datetime.now(UTC)
        if error is None and (previous := state.retries.get(scope)) is not None:
            # A policy block or control conflict does not acknowledge recovery.
            code, failure_stage, failure_at = (
                previous.last_error_code,
                previous.last_error_stage,
                previous.last_error_at,
            )
            worker_id, trace_id = previous.last_error_worker_id, previous.last_error_trace_id
        state.retries[scope] = _RetryState(
            failures,
            deadline,
            block,
            request_generation,
            probe_error,
            last_error_code=code,
            last_error_stage=failure_stage,
            last_error_at=failure_at,
            last_error_worker_id=worker_id if error is not None or code is not None else None,
            last_error_trace_id=trace_id if error is not None or code is not None else None,
        )

    async def _dispatch(self, state: _FamilyState) -> None:  # noqa: C901 - ordered admission and worker scheduling
        # Rejections do not occupy slots. Bound their admission work so a page
        # of slow metadata probes cannot monopolize the shared control loop.
        loop = asyncio.get_running_loop()
        admission_deadline = loop.time() + _DISCOVERY_TIMEOUT_SECONDS
        while state.ready and len(state.running) < state.binding.max_workers and loop.time() < admission_deadline:
            scope = state.ready.popleft()
            queued_at = state.queued_at.pop(scope, None)
            state.queued.discard(scope)
            state.retry_queued.discard(scope)
            retry = state.retries.get(scope)
            if retry is not None and retry.block is None and retry.deadline > asyncio.get_running_loop().time():
                continue
            async with self._database.transaction() as connection:
                fence = self._require_current_fence()
                await self._leases.require_fence(connection, fence)
                allowed = state.binding.invocation_scope_filter
                if allowed is not None and scope not in await allowed(connection, (scope,)):
                    state.retries.pop(scope, None)
                    continue
                row = await self._intents.load(connection, scope, state.binding.binding_name, for_update=True)
            if row is None or row.requested_generation <= row.handled_generation:
                state.retries.pop(scope, None)
                state.pending_observed.pop(scope, None)
                state.pending_since.pop(scope, None)
                continue
            self._observe_pending(state, scope, row.requested_generation)
            assignment = ArtifactProcessingWorkAssignment(
                binding_name=state.binding.binding_name,
                scope_id=scope,
                artifact_family=state.binding.artifact_family,
                claimed_request_generation=row.requested_generation,
                fence=fence,
                worker_id=str(uuid4()),
            )
            if state.binding.work_block is not None:
                try:
                    # Each probe owns its full I/O allowance. Earlier Scopes'
                    # work must not turn this check into a spurious timeout.
                    async with (
                        asyncio.timeout(_ADMISSION_PROBE_TIMEOUT_SECONDS),
                        self._database.transaction() as connection,
                    ):
                        await self._leases.require_fence(connection, fence)
                        block = await state.binding.work_block(connection, scope, state.binding.binding_name)
                except ArtifactProcessingLeadershipLostError:
                    raise
                except Exception as error:
                    self._defer_admission_error(state, assignment, error)
                    continue
                if block is not None:
                    state.admission_blocks += 1
                    self._wait_for_block(state, assignment, block)
                    continue
            if retry is not None and retry.block is not None:
                state.retries[scope] = replace(
                    retry, deadline=asyncio.get_running_loop().time(), block=None, probe_error=None
                )
            running = _RunningWorker(assignment, asyncio.get_running_loop().time())
            running.queue_wait_ms = None if queued_at is None else max(0, running.started_at - queued_at) * 1000
            running.task = asyncio.create_task(
                self._execute(state.binding, running), name=f"artifact-worker-{assignment.worker_id}"
            )
            running.task.add_done_callback(lambda _: self._wake.set())
            state.running[scope] = running
        if state.ready and len(state.running) < state.binding.max_workers:
            self._wake.set()

    def _defer_admission_error(
        self, state: _FamilyState, assignment: ArtifactProcessingWorkAssignment, error: Exception
    ) -> None:
        retry = state.retries.get(assignment.scope_id)
        now = asyncio.get_running_loop().time()
        if retry is not None and retry.block is not None:
            probe_error = (type(error).__name__, _safe_error_attribute(error, "error_code", type(error).__name__))
            if retry.probe_error != probe_error:
                log_safely(
                    logger,
                    logging.WARNING,
                    "Artifact processing block recheck failed; retaining the last observed block",
                    extra={
                        "event": "artifact_processing.block_recheck_failed",
                        "stage": "work_admission",
                        "binding": assignment.binding_name,
                        "family": assignment.artifact_family,
                        "scope": assignment.scope_id,
                        "request_generation": assignment.claimed_request_generation,
                        "exception_type": probe_error[0],
                        "error_code": probe_error[1],
                        "retry_delay_seconds": self._blocked_check_seconds,
                    },
                )
            self._defer(
                state,
                assignment.scope_id,
                retry.failures,
                now + self._blocked_check_seconds,
                block=retry.block,
                request_generation=assignment.claimed_request_generation,
                probe_error=probe_error,
                reason="blocked",
                error=error,
                stage="work_admission",
            )
            return
        failures = 1 if retry is None else retry.failures + 1
        delay = self._retry_delay(failures)
        self._defer(state, assignment.scope_id, failures, now + delay, error=error, stage="work_admission")
        self._log_failure(state, assignment, error, "work_admission", failures, delay)

    def _wait_for_block(
        self,
        state: _FamilyState,
        assignment: ArtifactProcessingWorkAssignment,
        block: ArtifactProcessingBlock,
        *,
        trace_id: str | None = None,
        diagnostics: ProcessingDiagnostics | None = None,
    ) -> None:
        if diagnostics is None:
            diagnostics = classify_block_code(block.stage, block.error_code)
        previous = state.retries.get(assignment.scope_id)
        if previous is None or previous.block != block:
            last_error_trace_id = None if previous is None else previous.last_error_trace_id
            log_safely(
                logger,
                logging.WARNING,
                "Artifact processing blocked; operator remediation required",
                extra={
                    **dict(block.details),
                    **safe_log_fields(diagnostics),
                    "event": "artifact_processing.blocked",
                    "stage": block.stage,
                    "error_code": block.error_code,
                    "binding": assignment.binding_name,
                    "family": assignment.artifact_family,
                    "scope": assignment.scope_id,
                    "request_generation": assignment.claimed_request_generation,
                    "worker_id": assignment.worker_id,
                    "trace_id": trace_id,
                    "last_error_code": None if previous is None else previous.last_error_code,
                    "last_error_stage": None if previous is None else previous.last_error_stage,
                    "last_error_at": (
                        None
                        if previous is None or previous.last_error_at is None
                        else previous.last_error_at.isoformat()
                    ),
                    "last_error_worker_id": None if previous is None else previous.last_error_worker_id,
                    "last_error_trace_id": last_error_trace_id,
                    "supervisor_group": self._supervisor_group,
                    "supervisor_generation": assignment.fence.supervisor_generation,
                    "retry_delay_seconds": self._blocked_check_seconds,
                    "operation": "process_artifact_scope",
                    "unit": "background",
                    "outcome": "blocked",
                    "error_category": "blocked",
                },
            )
        self._defer(
            state,
            assignment.scope_id,
            0 if previous is None else previous.failures,
            asyncio.get_running_loop().time() + self._blocked_check_seconds,
            block=block,
            request_generation=assignment.claimed_request_generation,
            reason="blocked",
        )

    def _retry_delay(self, failures: int) -> float:
        return min(self._retry_cap, self._retry_base * 2 ** min(failures - 1, 20) * self._jitter())

    async def _execute(  # noqa: C901 - worker lifecycle owns setup, result and cleanup boundaries
        self, binding: ArtifactProcessingBinding, running: _RunningWorker
    ) -> ArtifactProcessingWorkerCompletion:
        assignment = running.assignment
        state = self._families[binding.binding_name]
        started_at = time.perf_counter()
        attempt_outcome = "failure"
        attributes = {
            "powercontext.artifact_processing.family": binding.artifact_family,
            "powercontext.scope.id": assignment.scope_id,
        }
        background = (
            nullcontext()
            if self._tracing is None
            else self._tracing.background(
                "artifact_processing.worker", operation="process_artifact_scope", attributes=attributes
            )
        )
        model_usage: ArtifactProcessingModelUsageDelta | None = None
        provider_observations: ProviderObservationReport | None = None
        with log_context(_worker_log_fields(assignment)), background as span, _observe_worker_error(span):
            # Optional adapter capability; unconfigured/custom SDK tracing stays valid.
            with suppress(Exception):
                trace_id = getattr(span, "trace_id", None)
                if isinstance(trace_id, str) and re.fullmatch(r"[0-9a-f]{32}", trace_id) and int(trace_id, 16):
                    running.trace_id = trace_id
            log_safely(
                logger,
                logging.INFO,
                "Artifact processing invocation dispatched",
                extra={
                    "event": "artifact_processing.dispatched",
                    "stage": "dispatched",
                    "queue_wait_ms": running.queue_wait_ms,
                },
            )
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
                        (
                            nullcontext()
                            if self._tracing is None
                            else self._tracing.stage("artifact_processing.worker.wait", attributes=attributes)
                        ) as wait_span,
                        _observe_worker_error(wait_span),
                    ):
                        completion = await handle.wait()
                    model_usage = completion.model_usage
                    provider_observations = completion.provider_observations
                    if completion.outcome is ArtifactProcessingWorkerOutcome.LEADERSHIP_LOST:
                        raise ArtifactProcessingLeadershipLostError(  # noqa: TRY301
                            self._supervisor_group, self.holder_id, 0
                        )
                    if completion.held_count:
                        self._log_completion_hold(binding, assignment, completion)
                        if span is not None:
                            span.set_attributes({
                                "powercontext.artifact_processing.held_count": completion.held_count,
                                "powercontext.artifact_processing.hold_codes": ",".join(completion.hold_codes),
                            })
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
                        log_safely(
                            logger,
                            logging.INFO,
                            "Artifact processing invocation acknowledged",
                            extra={
                                "event": "artifact_processing.worker.acknowledged",
                                "outcome": "hold"
                                if completion.held_count
                                else "noop"
                                if completion.noop
                                else "success",
                            },
                        )
                    attempt_outcome = (
                        ("hold" if completion.held_count else "noop" if completion.noop else "success")
                        if completion.outcome is ArtifactProcessingWorkerOutcome.SUCCEEDED
                        else completion.outcome.value
                    )
                    if span is not None:
                        span.set_outcome(attempt_outcome)
                    return completion
            except BaseException as error:
                attempt_outcome, category = _attempt_failure_observation(error)
                if category:
                    state.error_counts[category] = state.error_counts.get(category, 0) + 1
                if isinstance(error, _WorkerExecutionError) and error.failure.model_usage is not None:
                    model_usage = error.failure.model_usage
                if isinstance(error, _WorkerExecutionError):
                    provider_observations = error.failure.provider_observations
                if isinstance(error, (asyncio.CancelledError, TimeoutError)):
                    log_safely(
                        logger,
                        logging.INFO if isinstance(error, asyncio.CancelledError) else logging.ERROR,
                        "Artifact processing invocation interrupted",
                        extra={
                            "event": "artifact_processing.worker.interrupted",
                            "stage": "interrupted",
                            "duration_ms": max(asyncio.get_running_loop().time() - running.started_at, 0) * 1000,
                            "queue_wait_ms": running.queue_wait_ms,
                            "outcome": "cancelled" if isinstance(error, asyncio.CancelledError) else "failure",
                            "error_code": "worker_cancelled"
                            if isinstance(error, asyncio.CancelledError)
                            else "worker_timeout",
                        },
                    )
                if span is not None:
                    span.set_attributes({"powercontext.artifact_processing.failure": _worker_failure_category(error)})
                if handle is not None:
                    cleanup = asyncio.create_task(self._terminate_worker(handle))
                    await _complete_cleanup(cleanup)
                raise
            finally:
                with suppress(Exception):
                    state.attempt_durations.record(attempt_outcome, max(0.0, time.perf_counter() - started_at))
                if binding.worker_model_usage_observer is not None:
                    with suppress(Exception):
                        binding.worker_model_usage_observer(model_usage)
                if binding.worker_provider_observer is not None:
                    with suppress(Exception):
                        binding.worker_provider_observer(provider_observations)

    def _log_completion_hold(
        self,
        binding: ArtifactProcessingBinding,
        assignment: ArtifactProcessingWorkAssignment,
        completion: ArtifactProcessingWorkerCompletion,
    ) -> None:
        log_safely(
            logger,
            logging.INFO,
            "Artifact processing worker reported a held Memory write",
            extra={
                "event": "artifact_processing.worker.memory_hold",
                "outcome": "hold",
                "code": completion.hold_codes[0] if len(completion.hold_codes) == 1 else None,
                "held_count": completion.held_count,
                "hold_codes": completion.hold_codes,
                "family": binding.artifact_family,
                "binding": assignment.binding_name,
            },
        )

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
            observed_outcome = "failure"
            diagnostic_code = None
            try:
                completion = running.task.result()
                if completion.outcome is ArtifactProcessingWorkerOutcome.LEADERSHIP_LOST:
                    raise ArtifactProcessingLeadershipLostError(self._supervisor_group, self.holder_id, 0)  # noqa: TRY301
                if completion.outcome in {
                    ArtifactProcessingWorkerOutcome.CURSOR_CONFLICT,
                    ArtifactProcessingWorkerOutcome.HEAD_CONFLICT,
                }:
                    observed_outcome = "conflict"
                    diagnostic_code = completion.outcome.value
                    previous = state.retries.get(scope)
                    self._defer(
                        state,
                        scope,
                        0 if previous is None else previous.failures,
                        asyncio.get_running_loop().time() + _CONTROL_CONFLICT_RETRY_SECONDS,
                        reason="conflict",
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
                observed_outcome = "success"
                state.pending_since.pop(scope, None)
                state.completed += 1
                state.last_success_at = datetime.now(UTC).isoformat()
                state.last_acknowledgement_timestamp_seconds = time.time()
                if row.requested_generation <= row.handled_generation:
                    state.pending_observed.pop(scope, None)
                if row.requested_generation > row.handled_generation:
                    if scope in state.pending_observed:
                        state.pending_observed[scope] = (row.requested_generation, time.monotonic())
                    self.wake(state.binding.binding_name)
            except (ArtifactProcessingLeadershipLostError, _WorkerTerminationError):
                # Retain the slot until the old term is revoked and all children are reaped.
                retain_slot = True
                observed_outcome = "leadership_lost"
                raise
            except asyncio.CancelledError:
                observed_outcome = "cancelled"
                raise
            except Exception as error:
                block = (
                    error.failure.block
                    if isinstance(error, _WorkerExecutionError)
                    else error.block
                    if isinstance(error, ArtifactProcessingBlockedError)
                    else None
                )
                if block is not None:
                    observed_outcome = "blocked"
                    diagnostic_code = block.error_code
                    diagnostics = (
                        error.failure.diagnostics
                        if isinstance(error, _WorkerExecutionError)
                        else safe_classify_processing_error(error)
                    )
                    self._wait_for_block(
                        state, running.assignment, block, trace_id=running.trace_id, diagnostics=diagnostics
                    )
                    continue
                if isinstance(error, TimeoutError):
                    observed_outcome = "timeout"
                elif isinstance(error, _WorkerExecutionError) and error.failure.cancelled:
                    observed_outcome = "cancelled"
                if state.binding.work_failure is not None:
                    code = _worker_error_code(error)
                    try:
                        async with (
                            asyncio.timeout(_ADMISSION_PROBE_TIMEOUT_SECONDS),
                            self._database.transaction() as connection,
                        ):
                            await self._leases.require_fence(connection, running.assignment.fence)
                            await state.binding.work_failure(connection, scope, state.binding.binding_name, code)
                    except ArtifactProcessingLeadershipLostError:
                        raise
                    except Exception as recording_error:
                        log_safely(
                            logger,
                            logging.WARNING,
                            "Failed to retain processing failure diagnostics",
                            extra={
                                "event": "artifact_processing.failure_record_failed",
                                "family": state.binding.artifact_family,
                                "binding": state.binding.binding_name,
                                "scope": scope,
                                "exception_type": type(recording_error).__name__,
                            },
                        )
                previous = state.retries.get(scope)
                failures = 1 if previous is None else previous.failures + 1
                delay = self._retry_delay(failures)
                self._defer(
                    state,
                    scope,
                    failures,
                    asyncio.get_running_loop().time() + delay,
                    error=error,
                    worker_id=running.assignment.worker_id,
                    trace_id=running.trace_id,
                )
                state.failed += 1
                if isinstance(error, TimeoutError):
                    state.timeouts += 1
                self._record_failure(state, error, "worker", count_error=running.task.exception() is not error)
                self._log_failure(state, running.assignment, error, "worker", failures, delay)
                diagnostic_code, _ = _processing_failure(error, "worker")
            finally:
                if not retain_slot:
                    log_safely(
                        logger,
                        logging.INFO,
                        "Artifact processing attempt finalized",
                        extra={
                            **_worker_log_fields(running.assignment),
                            "event": "artifact_processing.attempt.completed",
                            "stage": "completion",
                            "outcome": observed_outcome,
                            "duration_ms": state.invocation_seconds * 1000,
                            "queue_wait_ms": running.queue_wait_ms,
                            "trace_id": running.trace_id,
                            "error_code": diagnostic_code,
                        },
                    )
                if not retain_slot and self._processing_observer is not None:
                    with suppress(Exception):
                        self._processing_observer(
                            state.binding.artifact_family, observed_outcome, state.invocation_seconds
                        )
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
            state.pending_observed.clear()
            state.pending_since.clear()
            state.discovered_at = 0
            state.ready.clear()
            state.queued.clear()
            state.queued_at.clear()
            state.retry_queued.clear()
            state.overflow_not_before = 0.0
            state.blocked_rescan_at = None
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
            deadlines.append(state.next_pending_observation_at)
            if len(state.running) >= state.binding.max_workers:
                continue
            deadlines.extend(
                retry.deadline
                for scope, retry in state.retries.items()
                if scope not in state.running and scope not in state.queued
            )
            if state.blocked_rescan_at is not None:
                deadlines.append(state.blocked_rescan_at)
            if state.overflow_not_before > now:
                deadlines.append(state.overflow_not_before)
                continue
            if state.next_schedule_at is not None:
                deadlines.append(state.next_schedule_at)
            if state.discovery_pending or state.scan_in_progress or not state.reconcile_complete or state.degraded:
                deadlines.append(max(now, state.next_discovery_at))
        return None if not deadlines else max(0.001, min(deadlines) - now)

    def _record_failure(
        self, state: _FamilyState | None, error: BaseException, stage: str, *, count_error: bool = True
    ) -> None:
        error_code, stage = _processing_failure(error, stage)
        occurred_at = datetime.now(UTC).isoformat()
        _, category = _attempt_failure_observation(error)
        for affected in self._families.values() if state is None else (state,):
            if count_error and category:
                affected.error_counts[category] = affected.error_counts.get(category, 0) + 1
            affected.last_error = error_code
            affected.last_error_stage = stage
            affected.last_error_at = occurred_at

    def _log_failure(
        self,
        state: _FamilyState | None,
        assignment: ArtifactProcessingWorkAssignment | None,
        error: BaseException,
        stage: str,
        failures: int = 0,
        delay: float = 0,
    ) -> None:
        cancelled = isinstance(error, asyncio.CancelledError) or (
            isinstance(error, _WorkerExecutionError) and error.failure.cancelled
        )
        diagnostics = (
            error.failure.diagnostics
            if isinstance(error, _WorkerExecutionError) and error.failure.diagnostics is not None
            else safe_classify_processing_error(error)
        )
        log_safely(
            logger,
            logging.INFO if cancelled else logging.ERROR,
            "Artifact processing cancelled" if cancelled else "Artifact processing failed",
            extra={
                "event": "artifact_processing.failed",
                **safe_log_fields(diagnostics),
                "stage": error.failure.stage
                if isinstance(error, _WorkerExecutionError)
                else _safe_error_attribute(error, "stage", stage),
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
                "error_code": "worker_cancelled"
                if cancelled
                else error.failure.error_code
                if isinstance(error, _WorkerExecutionError)
                else _safe_error_attribute(error, "error_code", type(error).__name__),
                "operation": "process_artifact_scope",
                "unit": "background",
                "outcome": "cancelled" if cancelled else "failure",
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
    trigger = CronTrigger.from_crontab(binding.cron, timezone=ZoneInfo(binding.timezone))
    first = trigger.get_next_fire_time(None, (checkpoint + timedelta(microseconds=1)).replace(tzinfo=UTC))
    if first is None:
        return None, datetime.max.replace(tzinfo=None)
    first = first.astimezone(UTC).replace(tzinfo=None)
    if first > now:
        return None, first
    # Binary search avoids replaying an unbounded number of missed cron fires.
    lo, hi, latest = first, now + timedelta(microseconds=1), first
    for _ in range(48):
        if (hi - lo).total_seconds() < 0.000001:
            break
        mid = lo + (hi - lo) / 2
        candidate = trigger.get_next_fire_time(None, mid.replace(tzinfo=UTC))
        if candidate is not None and candidate.astimezone(UTC).replace(tzinfo=None) <= now:
            latest = candidate.astimezone(UTC).replace(tzinfo=None)
            lo = mid
        else:
            hi = mid
    following = trigger.get_next_fire_time(None, (latest + timedelta(microseconds=1)).replace(tzinfo=UTC))
    return latest, datetime.max.replace(tzinfo=None) if following is None else following.astimezone(UTC).replace(
        tzinfo=None
    )


class ArtifactProcessingSupervisors:
    """Runtime-owned collection; global and dedicated share the same controller."""

    def __init__(self, supervisors: Sequence[ArtifactProcessingSupervisor]) -> None:
        self.supervisors = tuple(supervisors)

    @property
    def status(self) -> ArtifactProcessingSupervisorStatus:
        states = [item.status for item in self.supervisors]
        if all(state is ArtifactProcessingSupervisorStatus.DISABLED for state in states):
            return ArtifactProcessingSupervisorStatus.DISABLED
        if ArtifactProcessingSupervisorStatus.DEGRADED in states:
            return ArtifactProcessingSupervisorStatus.DEGRADED
        if ArtifactProcessingSupervisorStatus.LEADER in states:
            return ArtifactProcessingSupervisorStatus.LEADER
        return ArtifactProcessingSupervisorStatus.STANDBY if states else ArtifactProcessingSupervisorStatus.DISABLED

    @property
    def family_status(self) -> dict[str, dict[str, int | float | str]]:
        return {family: status for item in self.supervisors for family, status in item.family_status.items()}

    @property
    def processing_observations(self) -> dict[str, ProcessingObservationSnapshot]:
        return {
            family: snapshot for item in self.supervisors for family, snapshot in item.processing_observations.items()
        }

    def observe_scope(self, scope_id: str) -> tuple[ProcessingObservation, ...]:
        return tuple(row for supervisor in self.supervisors for row in supervisor.observe_scope(scope_id))

    def wake(self, binding_name: str | None = None) -> None:
        for supervisor in self.supervisors:
            supervisor.wake(binding_name)

    async def replace_binding(self, name: str, binding: ArtifactProcessingBinding | None) -> None:
        if len(self.supervisors) != 1:
            raise ValueError("hot registration requires one global Supervisor")  # noqa: TRY003
        await self.supervisors[0].replace_binding(name, binding)

    async def close(self) -> None:
        await asyncio.gather(*(item.close() for item in self.supervisors))


def _run_spawned_worker(
    sender: _WorkerResultSender,
    entrypoint: WorkerEntrypoint,
    assignment: ArtifactProcessingWorkAssignment,
    initializer: Callable[[], None] | None = None,
    collect_model_usage: bool = False,
    collect_provider_observations: bool = False,
) -> None:
    with log_context(_worker_log_fields(assignment)):
        if initializer is not None:
            try:
                initializer()
            except Exception:
                log_safely(
                    logger,
                    logging.WARNING,
                    "Worker logging initialization failed; continuing processing",
                    extra={
                        "event": "observability.worker_logging_failed",
                        "error_code": "logging_configuration_failed",
                    },
                )
        started_at = time.perf_counter()
        log_safely(
            logger,
            logging.INFO,
            "Artifact processing worker started",
            extra={"event": "artifact_processing.worker.started"},
        )
        collector = WorkerModelUsageCollector() if collect_model_usage else None
        provider_collector = ProviderObservationCollector() if collect_provider_observations else None
        token = None if collector is None else worker_model_usage_collector.set(collector)
        try:
            with (
                bind_provider_observer(provider_collector.observe) if provider_collector is not None else nullcontext()
            ):
                completion = entrypoint(assignment)
            completed = ArtifactProcessingWorkerCompletion() if completion is None else completion
            if collector is not None and isinstance(completed, ArtifactProcessingWorkerCompletion):
                completed = replace(completed, model_usage=collector.snapshot())
            if provider_collector is not None and isinstance(completed, ArtifactProcessingWorkerCompletion):
                completed = replace(completed, provider_observations=provider_collector.snapshot())
            sender.send(completed)
            log_safely(
                logger,
                logging.INFO,
                "Artifact processing worker returned; acknowledgement is checked by the supervisor",
                extra={
                    "event": "artifact_processing.worker.returned",
                    "outcome": "success"
                    if completion is None
                    else (
                        ("hold" if completion.held_count else "noop" if completion.noop else "success")
                        if completion.outcome is ArtifactProcessingWorkerOutcome.SUCCEEDED
                        else completion.outcome.value
                    ),
                    "duration_ms": max(time.perf_counter() - started_at, 0) * 1000,
                },
            )
        except BaseException as error:
            error_code = _worker_error_code(error)
            diagnostics = safe_classify_processing_error(error)
            block = error.block if isinstance(error, ArtifactProcessingBlockedError) else None
            level = logging.ERROR
            outcome = "failure"
            event = "artifact_processing.worker.failed"
            message = "Artifact processing worker interrupted"
            if block is not None:
                level = logging.WARNING
                outcome = "blocked"
                event = "artifact_processing.worker.blocked"
                message = "Artifact processing worker blocked; operator remediation required"
            elif isinstance(error, asyncio.CancelledError):
                level = logging.INFO
                outcome = "cancelled"
            log_safely(
                logger,
                level,
                message,
                extra={
                    "event": event,
                    "outcome": outcome,
                    "error_code": error_code if block is None else block.error_code,
                    "error_category": None if block is None else "blocked",
                    "stage": None if block is None else block.stage,
                    "exception_type": type(error).__name__,
                    "duration_ms": max(time.perf_counter() - started_at, 0) * 1000,
                    **safe_log_fields(diagnostics),
                },
            )
            sender.send(
                ArtifactProcessingWorkerFailure(
                    stage=_safe_error_attribute(error, "stage", "worker"),
                    error_code=(
                        _safe_error_attribute(error, "error_code", "worker_failed")
                        if error_code == "processing_failed"
                        else error_code
                    ),
                    exception_type=type(error).__name__,
                    traceback=_safe_traceback(error),
                    block=error.block if isinstance(error, ArtifactProcessingBlockedError) else None,
                    cancelled=isinstance(error, asyncio.CancelledError),
                    model_usage=None if collector is None else collector.snapshot(),
                    diagnostics=diagnostics,
                    provider_observations=None if provider_collector is None else provider_collector.snapshot(),
                )
            )
        finally:
            if token is not None:
                worker_model_usage_collector.reset(token)
            sender.close()


def _worker_log_fields(assignment: ArtifactProcessingWorkAssignment) -> dict[str, str | int | None]:
    return {
        "request_id": None,
        "operation": "process_artifact_scope",
        "unit": "background",
        "family": assignment.artifact_family,
        "binding": assignment.binding_name,
        "scope": assignment.scope_id,
        "worker_id": assignment.worker_id,
        "request_generation": assignment.claimed_request_generation,
        "supervisor_group": assignment.fence.supervisor_group,
        "supervisor_generation": assignment.fence.supervisor_generation,
    }


def _safe_error_attribute(error: BaseException, name: str, fallback: str) -> str:
    value = getattr(error, name, None)
    return value if isinstance(value, str) and value else fallback


@contextmanager
def _observe_worker_error(span: RuntimeSpan | None) -> Iterator[None]:
    """Keep child failure semantics across the worker transport wrapper."""

    try:
        yield
    except BaseException as error:
        if span is not None and isinstance(error, (_WorkerExecutionError, ArtifactProcessingBlockedError)):
            with suppress(Exception):
                outcome = "failure"
                if isinstance(error, ArtifactProcessingBlockedError) or error.failure.block is not None:
                    outcome = category = "blocked"
                elif error.failure.cancelled:
                    outcome = category = "cancelled"
                else:
                    code = processing_error_code(error.failure.exception_type, error.failure.error_code)
                    category = error_category(code, outcome=outcome)
                span.set_outcome(outcome)
                if category is not None:
                    span.set_attributes({"powercontext.error.category": category})
        raise


def _attempt_failure_observation(error: BaseException) -> tuple[str, str]:
    """Classify at the owner; never expose custom child codes as metric labels."""
    if isinstance(error, ArtifactProcessingLeadershipLostError):
        return "leadership_lost", ""
    if isinstance(error, asyncio.CancelledError):
        return "cancelled", "cancelled"
    if isinstance(error, ArtifactProcessingBlockedError) or (
        isinstance(error, _WorkerExecutionError) and error.failure.block is not None
    ):
        return "blocked", "blocked"
    if isinstance(error, _WorkerExecutionError) and error.failure.cancelled:
        return "cancelled", "cancelled"
    code = (
        processing_error_code(error.failure.exception_type, error.failure.error_code)
        if isinstance(error, _WorkerExecutionError)
        else processing_error_code(type(error))
    )
    return "failure", "timeout" if isinstance(error, TimeoutError) else error_category(code) or "internal"


def _worker_error_code(error: BaseException) -> str:
    """Keep live and retained Worker diagnostics in the same controlled category."""
    if isinstance(error, _WorkerExecutionError):
        if error.failure.exception_type == "TimeoutError":
            return "worker_timeout"
        return processing_error_code(error.failure.exception_type, error.failure.error_code)
    code = processing_error_code(type(error), _safe_error_attribute(error, "error_code", ""))
    if code == "processing_failed" and isinstance(error, TimeoutError):
        return "worker_timeout"
    return code


def _processing_failure(error: BaseException, stage: str) -> tuple[str, str]:
    """Classify local/serialized failures without exposing child messages or stages."""
    code = _worker_error_code(error)
    if stage in {"supervisor", "lease_renewal", "scope_discovery"}:
        return f"{stage}_failed", stage
    if code in {"access_denied", "access_identity_required", "access_unavailable"}:
        return code, "authorization"
    if is_inference_failure_code(code):
        return code, "inference"
    if code == "missing_durable_acknowledgement":
        return code, "completion"
    if isinstance(error, _WorkerExecutionError) and error.failure.cancelled:
        return "worker_cancelled", "worker"
    return code, "work_admission" if stage == "work_admission" else "worker"


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
