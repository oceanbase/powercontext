# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");

"""Global Artifact Processing Supervisor and child-Worker lifecycle."""

from __future__ import annotations

import asyncio
import logging
import multiprocessing
import traceback as traceback_module
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from multiprocessing.connection import Connection
from multiprocessing.process import BaseProcess
from random import SystemRandom
from typing import Protocol
from uuid import uuid4

from powercontext._logging import log_safely
from powercontext.builtin.persistence.cursors import SourceCursorRepository
from powercontext.builtin.persistence.database import AsyncDatabase
from powercontext.builtin.persistence.errors import (
    ArtifactProcessingLeadershipLostError,
    ArtifactProcessingWaveIncompleteError,
)
from powercontext.builtin.persistence.processing import (
    ArtifactProcessingAutoWaveTargetRepository,
    ArtifactProcessingPendingRepository,
    StoredArtifactProcessingPending,
)
from powercontext.builtin.persistence.supervision import (
    ArtifactProcessingBindingStateRepository,
    ArtifactProcessingFence,
    ArtifactProcessingLeaseMode,
    ArtifactProcessingLeaseRepository,
    StoredArtifactProcessingBindingState,
    database_utc_now,
)

logger = logging.getLogger(__name__)

_OCEANBASE_TICK_SECONDS = 1.0
_OCEANBASE_LEASE_SECONDS = 15.0
_RETRY_BASE_SECONDS = 30.0
_RETRY_CAP_SECONDS = 30.0 * 60.0
_DISCOVERY_PAGE_SIZE = 100
_DISCOVERY_PAGE_DELAY_SECONDS = 0.01
_RETRY_STATE_LIMIT = 1000
_SPAWN_SIGTERM_GRACE_SECONDS = 1.0
_WORKER_SHUTDOWN_TIMEOUT_SECONDS = 5.0

_spawn_cleanup_tasks: set[asyncio.Task[None]] = set()

ProcessingKey = tuple[str, str]


class ArtifactProcessingSupervisorStatus(StrEnum):
    """Safe process status exposed through Runtime readiness."""

    DISABLED = "disabled"
    LEADER = "leader"
    STANDBY = "standby"
    DEGRADED = "degraded"


class ArtifactProcessingWaveKind(StrEnum):
    """The persistence semantics used when one frozen wave completes."""

    EXPLICIT = "explicit"
    AUTOMATIC = "automatic"


class ArtifactProcessingWorkerOutcome(StrEnum):
    """Control outcomes returned after a Worker publication attempt."""

    SUCCEEDED = "succeeded"
    CURSOR_CONFLICT = "cursor_conflict"
    HEAD_CONFLICT = "head_conflict"
    LEADERSHIP_LOST = "leadership_lost"


@dataclass(frozen=True, slots=True)
class ArtifactProcessingWorkAssignment:
    """One bounded Source Window assigned to a child Worker."""

    binding_name: str
    scope_id: str
    source_after: int
    source_through: int
    wave_target: int
    claimed_flush_generation: int
    cursor_generation: int | None
    wave_kind: ArtifactProcessingWaveKind
    fence: ArtifactProcessingFence
    worker_id: str


@dataclass(frozen=True, slots=True)
class ArtifactProcessingWorkerFailure:
    """Sanitized Worker failure metadata safe to log in the Supervisor."""

    stage: str
    error_code: str
    exception_type: str
    traceback: str


@dataclass(frozen=True, slots=True)
class ArtifactProcessingWorkerCompletion:
    """A child Worker's terminal control result."""

    outcome: ArtifactProcessingWorkerOutcome = ArtifactProcessingWorkerOutcome.SUCCEEDED


class ArtifactProcessingWorkerHandle(Protocol):
    """One independently terminable Worker child."""

    async def wait(self) -> ArtifactProcessingWorkerCompletion: ...

    async def terminate(self) -> None: ...


class ArtifactProcessingWorkerLauncher(Protocol):
    """Spawn exactly one independently terminable Worker.

    Implementations must release partially-created resources if ``start`` is
    cancelled. The Supervisor bounds and cancels startup independently from a
    Worker's execution timeout.
    """

    async def start(self, assignment: ArtifactProcessingWorkAssignment) -> ArtifactProcessingWorkerHandle: ...


WorkerEntrypoint = Callable[[ArtifactProcessingWorkAssignment], ArtifactProcessingWorkerCompletion | None]


@dataclass(frozen=True, slots=True)
class ArtifactProcessingBinding:
    """Startup-only registration for one Source-driven Artifact processor."""

    binding_name: str
    source_window_limit: int
    launcher: ArtifactProcessingWorkerLauncher
    automatic_processing_interval: timedelta | None = None

    def __post_init__(self) -> None:
        if not self.binding_name.strip() or self.binding_name != self.binding_name.strip():
            raise ValueError("artifact processing binding_name must be non-empty and trimmed")  # noqa: TRY003
        if self.source_window_limit < 1:
            raise ValueError("artifact processing source_window_limit must be positive")  # noqa: TRY003
        if self.automatic_processing_interval is not None and self.automatic_processing_interval.total_seconds() <= 0:
            raise ValueError("artifact processing automatic interval must be positive")  # noqa: TRY003


class SpawnArtifactProcessingWorkerLauncher:
    """Launch a picklable Worker entrypoint in a fresh spawn child process."""

    def __init__(self, entrypoint: WorkerEntrypoint) -> None:
        self._entrypoint = entrypoint

    async def start(self, assignment: ArtifactProcessingWorkAssignment) -> ArtifactProcessingWorkerHandle:
        context = multiprocessing.get_context("spawn")
        receiver, sender = context.Pipe(duplex=False)
        process = context.Process(
            target=_run_spawned_worker,
            args=(sender, self._entrypoint, assignment),
            name=f"powercontext-artifact-worker-{assignment.worker_id}",
            daemon=False,
        )
        start_task = asyncio.create_task(
            asyncio.to_thread(process.start),
            name=f"powercontext-artifact-worker-start-{assignment.worker_id}",
        )
        try:
            await asyncio.shield(start_task)
        except asyncio.CancelledError:
            cleanup_task = asyncio.create_task(
                _cleanup_cancelled_spawn_start(start_task, process, receiver, sender),
                name=f"powercontext-artifact-worker-start-cleanup-{assignment.worker_id}",
            )
            _spawn_cleanup_tasks.add(cleanup_task)
            cleanup_task.add_done_callback(_spawn_cleanup_tasks.discard)
            raise
        except BaseException:
            receiver.close()
            sender.close()
            raise
        sender.close()
        return _SpawnedWorkerHandle(process, receiver)


async def _cleanup_cancelled_spawn_start(
    start_task: asyncio.Task[None],
    process: BaseProcess,
    receiver: Connection,
    sender: Connection,
) -> None:
    """Reap a spawn that completed after its supervising launch was cancelled."""

    try:
        await start_task
    except BaseException:
        receiver.close()
        sender.close()
        return
    sender.close()
    await _SpawnedWorkerHandle(process, receiver).terminate()


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
        self.failure = failure
        super().__init__(f"artifact Worker failed at {failure.stage}: {failure.error_code}")


class _WorkerTerminationError(RuntimeError):
    """The Supervisor could not confirm termination inside its fixed bound."""


class _RetryCapacityError(RuntimeError):
    """An automatic target cannot be safely evicted from the retry frontier."""


class _AutomaticWaveStateError(RuntimeError):
    def __init__(self, wave_id: str, detail: str) -> None:
        super().__init__(f"automatic wave {wave_id} has inconsistent target state: {detail}")


@dataclass(slots=True)
class _WaveWork:
    key: ProcessingKey
    wave_kind: ArtifactProcessingWaveKind
    wave_target: int
    claimed_flush_generation: int


@dataclass(slots=True)
class _AutomaticWave:
    wave_id: str
    binding_name: str
    end_scope_id: str
    targets: dict[ProcessingKey, int]
    discovery_after_scope_id: str | None = None
    discovery_complete: bool = False
    completed: set[ProcessingKey] = field(default_factory=set)


@dataclass(slots=True)
class _RetryState:
    work: _WaveWork
    consecutive_failures: int
    next_retry_at: float


@dataclass(slots=True)
class _RunningWorker:
    work: _WaveWork
    assignment: ArtifactProcessingWorkAssignment
    handle: ArtifactProcessingWorkerHandle
    task: asyncio.Task[ArtifactProcessingWorkerCompletion]


@dataclass(slots=True)
class _LaunchingWorker:
    work: _WaveWork
    assignment: ArtifactProcessingWorkAssignment
    task: asyncio.Task[ArtifactProcessingWorkerHandle]


class ArtifactProcessingSupervisor:
    """Coordinate durable waves through a single fenced, fair global queue."""

    def __init__(
        self,
        *,
        database: AsyncDatabase,
        bindings: Sequence[ArtifactProcessingBinding],
        lease_mode: ArtifactProcessingLeaseMode,
        max_workers: int,
        worker_timeout_seconds: float,
        pending: ArtifactProcessingPendingRepository | None = None,
        auto_wave_targets: ArtifactProcessingAutoWaveTargetRepository | None = None,
        cursors: SourceCursorRepository | None = None,
        leases: ArtifactProcessingLeaseRepository | None = None,
        binding_states: ArtifactProcessingBindingStateRepository | None = None,
        holder_id: str | None = None,
        oceanbase_tick_seconds: float = _OCEANBASE_TICK_SECONDS,
        oceanbase_lease_seconds: float = _OCEANBASE_LEASE_SECONDS,
        retry_base_seconds: float = _RETRY_BASE_SECONDS,
        retry_cap_seconds: float = _RETRY_CAP_SECONDS,
        retry_jitter: Callable[[], float] | None = None,
    ) -> None:
        if max_workers < 1:
            raise ValueError("artifact_processing_max_workers must be positive")  # noqa: TRY003
        if worker_timeout_seconds <= 0:
            raise ValueError("artifact_processing_worker_timeout_seconds must be positive")  # noqa: TRY003
        names = [binding.binding_name for binding in bindings]
        if len(names) != len(set(names)):
            raise ValueError("artifact processing binding names must be unique")  # noqa: TRY003
        self._database = database
        self._bindings = {binding.binding_name: binding for binding in bindings}
        self._lease_mode = lease_mode
        self._max_workers = max_workers
        self._worker_timeout_seconds = worker_timeout_seconds
        self._pending = ArtifactProcessingPendingRepository() if pending is None else pending
        self._auto_wave_targets = (
            ArtifactProcessingAutoWaveTargetRepository() if auto_wave_targets is None else auto_wave_targets
        )
        self._cursors = SourceCursorRepository() if cursors is None else cursors
        self._leases = ArtifactProcessingLeaseRepository() if leases is None else leases
        self._binding_states = ArtifactProcessingBindingStateRepository() if binding_states is None else binding_states
        self.holder_id = str(uuid4()) if holder_id is None else holder_id
        self._oceanbase_tick_seconds = oceanbase_tick_seconds
        self._oceanbase_lease_seconds = oceanbase_lease_seconds
        self._retry_base_seconds = retry_base_seconds
        self._retry_cap_seconds = retry_cap_seconds
        self._retry_jitter = (lambda: SystemRandom().uniform(0.8, 1.2)) if retry_jitter is None else retry_jitter
        self._status = ArtifactProcessingSupervisorStatus.STANDBY
        self._fence: ArtifactProcessingFence | None = None
        self._renew_at = 0.0
        self._queue: deque[ProcessingKey] = deque()
        self._queued: set[ProcessingKey] = set()
        self._work: dict[ProcessingKey, _WaveWork] = {}
        self._automatic_waves: dict[str, _AutomaticWave] = {}
        self._automatic_discovery_queue: deque[str] = deque()
        self._automatic_discovery_queued: set[str] = set()
        self._discover_automatic_next = False
        self._next_automatic_wake_at: float | None = None
        self._discovery_after: ProcessingKey | None = None
        self._next_discovery_wake_at: float | None = None
        self._retries: dict[ProcessingKey, _RetryState] = {}
        self._launching: dict[ProcessingKey, _LaunchingWorker] = {}
        self._running: dict[ProcessingKey, _RunningWorker] = {}
        self._wake = asyncio.Event()
        self._stop = asyncio.Event()
        self._started = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    @property
    def status(self) -> ArtifactProcessingSupervisorStatus:
        """Return the current safe readiness state."""

        return self._status

    @property
    def fence(self) -> ArtifactProcessingFence | None:
        """Return the current immutable term, if this candidate is Leader."""

        return self._fence

    async def __aenter__(self) -> ArtifactProcessingSupervisor:
        await self.start()
        return self

    async def __aexit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        await self.close()

    async def start(self) -> None:
        """Start election and wait for the first bounded candidate cycle."""

        if self._task is not None:
            raise RuntimeError("Artifact Processing Supervisor is already started")  # noqa: TRY003
        self._task = asyncio.create_task(self._run(), name="powercontext-artifact-processing-supervisor")
        await self._started.wait()

    def wake(self) -> None:
        """Reduce local flush latency; durable database state remains authoritative."""

        self._wake.set()

    async def close(self) -> None:
        """Stop dispatching, terminate child Workers, and await the control loop."""

        task = self._task
        if task is None:
            return
        self._stop.set()
        self._wake.set()
        await task
        self._task = None

    async def _run(self) -> None:
        try:
            while not self._stop.is_set():
                self._wake.clear()
                try:
                    await self._cycle()
                except asyncio.CancelledError:
                    raise
                except ArtifactProcessingLeadershipLostError:
                    await self._lose_leadership(ArtifactProcessingSupervisorStatus.STANDBY)
                except Exception as error:
                    await self._lose_leadership(ArtifactProcessingSupervisorStatus.DEGRADED)
                    log_safely(
                        logger,
                        logging.ERROR,
                        "Artifact Processing Supervisor control cycle failed",
                        exc_info=error,
                        extra={
                            "event": "artifact_processing.supervisor.failed",
                            "stage": "supervisor",
                            "error_code": "control_cycle_failed",
                            "outcome": "failure",
                            "unit": "artifact_processing",
                        },
                    )
                finally:
                    self._started.set()
                if self._stop.is_set():
                    break
                timeout = self._next_wake_seconds()
                if self._wake.is_set():
                    continue
                try:
                    if timeout is None:
                        await self._wake.wait()
                    else:
                        await asyncio.wait_for(self._wake.wait(), timeout=timeout)
                except TimeoutError:
                    pass
        finally:
            await self._lose_leadership(ArtifactProcessingSupervisorStatus.STANDBY)

    async def _cycle(self) -> None:
        await self._establish_or_renew_leadership()
        if self._fence is None:
            return
        await self._drain_launches()
        await self._drain_workers()
        if self._fence is None:
            return
        await self._finish_ready_automatic_waves()
        await self._discover_waves()
        await self._dispatch_workers()

    async def _establish_or_renew_leadership(self) -> None:
        loop = asyncio.get_running_loop()
        if self._fence is not None:
            if self._lease_mode == "single-process" or loop.time() < self._renew_at:
                return
            async with self._database.transaction() as connection:
                renewed = await self._leases.renew(
                    connection,
                    self._fence,
                    self._oceanbase_lease_seconds,
                )
            self._fence = renewed.fence(self._lease_mode)
            self._renew_at = loop.time() + self._oceanbase_lease_seconds / 3.0
            return
        async with self._database.transaction() as connection:
            if self._lease_mode == "single-process":
                acquired = await self._leases.start_single_process_term(connection, self.holder_id)
            else:
                acquired = await self._leases.try_acquire(
                    connection,
                    self.holder_id,
                    self._oceanbase_lease_seconds,
                )
            if acquired is not None:
                await self._auto_wave_targets.clear_all(connection)
        if acquired is None:
            if self._status is not ArtifactProcessingSupervisorStatus.DEGRADED:
                self._status = ArtifactProcessingSupervisorStatus.STANDBY
            return
        self._fence = acquired.fence(self._lease_mode)
        self._renew_at = loop.time() + self._oceanbase_lease_seconds / 3.0
        self._status = ArtifactProcessingSupervisorStatus.LEADER

    async def _discover_waves(self) -> None:
        self._activate_due_retries()
        available = _DISCOVERY_PAGE_SIZE - len(self._work)
        if available <= 0:
            # The frontier cannot advance until active work frees capacity. Its
            # callbacks (or the OceanBase control tick) will wake us; retaining
            # an already-due page deadline here would spin the control loop.
            self._next_discovery_wake_at = None
            self._next_automatic_wake_at = None
            return
        if self._automatic_discovery_queue and self._discover_automatic_next:
            await self._discover_automatic_page(available)
            self._discover_automatic_next = False
            return
        await self._discover_pending_page(available)
        self._discover_automatic_next = bool(self._automatic_discovery_queue)

    def _activate_due_retries(self) -> None:
        now = asyncio.get_running_loop().time()
        for key, retry in sorted(self._retries.items(), key=lambda item: item[1].next_retry_at):
            if len(self._work) >= _DISCOVERY_PAGE_SIZE:
                return
            if key in self._work or retry.next_retry_at > now:
                continue
            self._work[key] = retry.work
            self._enqueue(key)

    async def _discover_pending_page(self, available: int) -> None:
        async with self._database.transaction() as connection:
            await self._leases.require_fence(connection, self._require_current_fence())
            pending_rows = await self._pending.scan(
                connection,
                after=self._discovery_after,
                limit=available,
            )
            now = await database_utc_now(connection)
            binding_states = {
                name: await self._binding_states.load(connection, name)
                for name, binding in self._bindings.items()
                if binding.automatic_processing_interval is not None
            }
            automatic_page_bindings = {row.binding_name for row in pending_rows if row.binding_name in binding_states}
            end_scope_ids = {
                name: await self._pending.last_scope_id(connection, name) for name in automatic_page_bindings
            }
            unhandled_flushes = {
                name: await self._pending.has_unhandled_flush(connection, name) for name in automatic_page_bindings
            }
        if pending_rows:
            last = pending_rows[-1]
            self._discovery_after = (last.binding_name, last.scope_id)
        if len(pending_rows) < available:
            self._discovery_after = None
            self._next_discovery_wake_at = None
        else:
            self._next_discovery_wake_at = asyncio.get_running_loop().time() + _DISCOVERY_PAGE_DELAY_SECONDS
        registered = tuple(row for row in pending_rows if row.binding_name in self._bindings)
        rows_by_binding: dict[str, list[StoredArtifactProcessingPending]] = {}
        for pending in registered:
            key = (pending.binding_name, pending.scope_id)
            if (
                pending.binding_name not in self._automatic_waves
                and key not in self._work
                and key not in self._retries
                and pending.flush_generation > pending.handled_flush_generation
            ):
                self._register_work(pending, ArtifactProcessingWaveKind.EXPLICIT)
            rows_by_binding.setdefault(pending.binding_name, []).append(pending)
        self._refresh_automatic_timer(rows_by_binding, binding_states, now)
        self._register_automatic_waves(
            rows_by_binding,
            binding_states,
            end_scope_ids,
            unhandled_flushes,
            now,
        )

    async def _discover_automatic_page(self, available: int) -> None:
        wave: _AutomaticWave | None = None
        while self._automatic_discovery_queue:
            binding_name = self._automatic_discovery_queue.popleft()
            self._automatic_discovery_queued.discard(binding_name)
            candidate = self._automatic_waves.get(binding_name)
            if candidate is not None and not candidate.targets and not candidate.discovery_complete:
                wave = candidate
                break
        if wave is None:
            self._wake.set()
            return
        after = None if wave.discovery_after_scope_id is None else (wave.binding_name, wave.discovery_after_scope_id)
        async with self._database.transaction() as connection:
            await self._leases.require_fence(connection, self._require_current_fence())
            scanned = await self._pending.scan(
                connection,
                binding_name=wave.binding_name,
                after=after,
                limit=available,
            )
            pending_rows = tuple(row for row in scanned if row.scope_id <= wave.end_scope_id)
            targets = {row.scope_id: row.source_through for row in pending_rows}
            await self._auto_wave_targets.add_page(
                connection,
                wave.wave_id,
                wave.binding_name,
                targets,
            )
        if pending_rows:
            wave.discovery_after_scope_id = pending_rows[-1].scope_id
        if (
            not pending_rows
            or len(pending_rows) < len(scanned)
            or len(scanned) < available
            or pending_rows[-1].scope_id == wave.end_scope_id
        ):
            wave.discovery_complete = True
        wave.targets = {(wave.binding_name, row.scope_id): row.source_through for row in pending_rows}
        for pending in pending_rows:
            self._register_work(pending, ArtifactProcessingWaveKind.AUTOMATIC)
        if not wave.targets:
            self._wake.set()

    def _refresh_automatic_timer(
        self,
        rows_by_binding: Mapping[str, Sequence[StoredArtifactProcessingPending]],
        binding_states: Mapping[str, StoredArtifactProcessingBindingState | None],
        now: datetime,
    ) -> None:
        automatic_delays: list[float] = []
        loop_time = asyncio.get_running_loop().time()
        for binding_name, state in binding_states.items():
            binding = self._bindings[binding_name]
            interval = binding.automatic_processing_interval
            if interval is None:
                continue
            rows = rows_by_binding.get(binding_name, ())
            if state is None or state.last_auto_wave_completed_at is None:
                remaining = 0.0 if rows else interval.total_seconds()
            else:
                remaining = max(0.0, (state.last_auto_wave_completed_at + interval - now).total_seconds())
            if not rows and remaining <= 0:
                remaining = interval.total_seconds()
            binding_busy = (
                binding_name in self._automatic_waves
                or any(key[0] == binding_name for key in self._work)
                or any(retry.work.key[0] == binding_name for retry in self._retries.values())
            )
            if remaining > 0 or not binding_busy:
                automatic_delays.append(max(remaining, 0.001))
        self._next_automatic_wake_at = None if not automatic_delays else loop_time + min(automatic_delays)

    def _register_automatic_waves(
        self,
        rows_by_binding: Mapping[str, Sequence[StoredArtifactProcessingPending]],
        binding_states: Mapping[str, StoredArtifactProcessingBindingState | None],
        end_scope_ids: Mapping[str, str | None],
        unhandled_flushes: Mapping[str, bool],
        now: datetime,
    ) -> None:
        for binding_name in rows_by_binding:
            binding = self._bindings[binding_name]
            interval = binding.automatic_processing_interval
            if interval is None or binding_name in self._automatic_waves:
                continue
            if unhandled_flushes[binding_name]:
                continue
            if len(self._automatic_waves) >= _DISCOVERY_PAGE_SIZE:
                continue
            if any(key[0] == binding_name for key in self._work) or any(
                retry.work.key[0] == binding_name for retry in self._retries.values()
            ):
                continue
            state = binding_states[binding_name]
            if (
                state is not None
                and state.last_auto_wave_completed_at is not None
                and now < state.last_auto_wave_completed_at + interval
            ):
                continue
            end_scope_id = end_scope_ids[binding_name]
            if end_scope_id is None:
                continue
            wave = _AutomaticWave(
                wave_id=str(uuid4()),
                binding_name=binding_name,
                end_scope_id=end_scope_id,
                targets={},
            )
            self._automatic_waves[binding_name] = wave
            self._enqueue_automatic_discovery(binding_name)

    def _enqueue_automatic_discovery(self, binding_name: str) -> None:
        wave = self._automatic_waves.get(binding_name)
        if (
            wave is not None
            and not wave.targets
            and not wave.discovery_complete
            and binding_name not in self._automatic_discovery_queued
        ):
            self._automatic_discovery_queue.append(binding_name)
            self._automatic_discovery_queued.add(binding_name)
            self._wake.set()

    def _register_work(
        self,
        pending: StoredArtifactProcessingPending,
        wave_kind: ArtifactProcessingWaveKind,
    ) -> None:
        key = (pending.binding_name, pending.scope_id)
        if key in self._work or key in self._retries:
            return
        self._work[key] = _WaveWork(
            key=key,
            wave_kind=wave_kind,
            wave_target=pending.source_through,
            claimed_flush_generation=pending.flush_generation,
        )
        self._enqueue(key)

    async def _dispatch_workers(self) -> None:
        loop = asyncio.get_running_loop()
        attempts = len(self._queue)
        while self._queue and len(self._launching) + len(self._running) < self._max_workers and attempts > 0:
            attempts -= 1
            key = self._queue.popleft()
            self._queued.discard(key)
            work = self._work.get(key)
            if work is None or key in self._launching or key in self._running:
                continue
            retry = self._retries.get(key)
            if retry is not None and retry.next_retry_at > loop.time():
                self._enqueue(key)
                continue
            assignment = await self._prepare_assignment(work)
            if assignment is None:
                await self._finish_covered_work(work)
                continue
            task = asyncio.create_task(
                self._start_worker(self._bindings[work.key[0]].launcher, assignment),
                name=f"powercontext-artifact-worker-launch-{assignment.worker_id}",
            )
            task.add_done_callback(lambda _task: self._wake.set())
            self._launching[key] = _LaunchingWorker(
                work=work,
                assignment=assignment,
                task=task,
            )

    async def _start_worker(
        self,
        launcher: ArtifactProcessingWorkerLauncher,
        assignment: ArtifactProcessingWorkAssignment,
    ) -> ArtifactProcessingWorkerHandle:
        try:
            async with asyncio.timeout(self._worker_timeout_seconds):
                return await launcher.start(assignment)
        except TimeoutError as error:
            raise _WorkerExecutionError(
                ArtifactProcessingWorkerFailure(
                    stage="worker_start",
                    error_code="worker_start_timeout",
                    exception_type=type(error).__name__,
                    traceback="",
                )
            ) from None

    async def _drain_launches(self) -> None:
        for key, launching in tuple(self._launching.items()):
            if not launching.task.done():
                continue
            del self._launching[key]
            try:
                handle = launching.task.result()
            except asyncio.CancelledError:
                raise
            except _WorkerExecutionError as error:
                self._record_failure(launching.work, launching.assignment, error.failure)
                continue
            except Exception as error:
                self._record_failure(
                    launching.work,
                    launching.assignment,
                    ArtifactProcessingWorkerFailure(
                        stage="worker_start",
                        error_code="worker_start_failed",
                        exception_type=type(error).__name__,
                        traceback=_safe_traceback(error),
                    ),
                )
                continue
            task = asyncio.create_task(
                self._wait_for_worker(handle),
                name=f"powercontext-artifact-worker-wait-{launching.assignment.worker_id}",
            )
            task.add_done_callback(lambda _task: self._wake.set())
            self._running[key] = _RunningWorker(
                work=launching.work,
                assignment=launching.assignment,
                handle=handle,
                task=task,
            )

    async def _prepare_assignment(
        self,
        work: _WaveWork,
    ) -> ArtifactProcessingWorkAssignment | None:
        binding_name, scope_id = work.key
        async with self._database.transaction() as connection:
            await self._leases.require_fence(connection, self._require_current_fence())
            cursor = await self._cursors.load(connection, scope_id, binding_name)
        source_after = 0 if cursor is None else cursor.cursor.sequence
        if source_after >= work.wave_target:
            return None
        source_through = min(
            source_after + self._bindings[binding_name].source_window_limit,
            work.wave_target,
        )
        return ArtifactProcessingWorkAssignment(
            binding_name=binding_name,
            scope_id=scope_id,
            source_after=source_after,
            source_through=source_through,
            wave_target=work.wave_target,
            claimed_flush_generation=work.claimed_flush_generation,
            cursor_generation=None if cursor is None else cursor.generation,
            wave_kind=work.wave_kind,
            fence=self._require_current_fence(),
            worker_id=str(uuid4()),
        )

    async def _wait_for_worker(
        self,
        handle: ArtifactProcessingWorkerHandle,
    ) -> ArtifactProcessingWorkerCompletion:
        try:
            async with asyncio.timeout(self._worker_timeout_seconds):
                return await handle.wait()
        except TimeoutError as error:
            await asyncio.shield(self._terminate_worker(handle))
            raise _WorkerExecutionError(
                ArtifactProcessingWorkerFailure(
                    stage="worker_process",
                    error_code="worker_timeout",
                    exception_type=type(error).__name__,
                    traceback="",
                )
            ) from None
        except asyncio.CancelledError:
            await asyncio.shield(self._terminate_worker(handle))
            raise

    async def _terminate_worker(self, handle: ArtifactProcessingWorkerHandle) -> None:
        try:
            async with asyncio.timeout(_WORKER_SHUTDOWN_TIMEOUT_SECONDS):
                await handle.terminate()
        except TimeoutError:
            log_safely(
                logger,
                logging.ERROR,
                "Artifact processing Worker termination timed out",
                extra={
                    "event": "artifact_processing.worker.termination_timeout",
                    "stage": "worker_termination",
                    "error_code": "worker_termination_timeout",
                    "outcome": "failure",
                    "unit": "artifact_processing",
                },
            )
            raise _WorkerTerminationError from None

    async def _drain_workers(self) -> None:
        for key, running in tuple(self._running.items()):
            if not running.task.done():
                continue
            del self._running[key]
            try:
                completion = running.task.result()
            except asyncio.CancelledError:
                raise
            except _WorkerTerminationError:
                await self._lose_leadership(ArtifactProcessingSupervisorStatus.DEGRADED)
                return
            except _WorkerExecutionError as error:
                self._record_failure(running.work, running.assignment, error.failure)
                continue
            except Exception as error:
                self._record_failure(
                    running.work,
                    running.assignment,
                    ArtifactProcessingWorkerFailure(
                        stage="worker_process",
                        error_code="worker_wait_failed",
                        exception_type=type(error).__name__,
                        traceback=_safe_traceback(error),
                    ),
                )
                continue
            self._retries.pop(key, None)
            if completion.outcome is ArtifactProcessingWorkerOutcome.LEADERSHIP_LOST:
                await self._lose_leadership(ArtifactProcessingSupervisorStatus.STANDBY)
                return
            if completion.outcome in {
                ArtifactProcessingWorkerOutcome.CURSOR_CONFLICT,
                ArtifactProcessingWorkerOutcome.HEAD_CONFLICT,
            }:
                self._enqueue(key)
                continue
            assignment = await self._prepare_assignment(running.work)
            if assignment is None:
                await self._finish_completed_work(running.work)
            else:
                self._enqueue(key)

    async def _finish_covered_work(self, work: _WaveWork) -> None:
        if work.wave_kind is ArtifactProcessingWaveKind.EXPLICIT:
            binding_name, scope_id = work.key
            async with self._database.transaction() as connection:
                await self._leases.require_fence(connection, self._require_current_fence())
                cursor = await self._cursors.load(connection, scope_id, binding_name)
                cursor_position = 0 if cursor is None else cursor.cursor.sequence
                if cursor_position < work.wave_target:
                    raise ArtifactProcessingWaveIncompleteError(
                        binding_name,
                        scope_id,
                        work.wave_target,
                        cursor_position,
                    )
                await self._pending.mark_flush_handled(
                    connection,
                    scope_id,
                    binding_name,
                    work.claimed_flush_generation,
                )
                await self._pending.delete_if_covered(
                    connection,
                    scope_id,
                    binding_name,
                    cursor=cursor_position,
                    source_through_limit=work.wave_target,
                )
        await self._finish_completed_work(work)

    async def _finish_completed_work(self, work: _WaveWork) -> None:
        wave = self._automatic_waves.get(work.key[0])
        if work.wave_kind is ArtifactProcessingWaveKind.AUTOMATIC:
            if wave is None or work.key not in wave.targets:
                raise _AutomaticWaveStateError("unknown", "completed target is not active")
            binding_name, scope_id = work.key
            async with self._database.transaction() as connection:
                await self._leases.require_fence(connection, self._require_current_fence())
                cursor = await self._cursors.load(connection, scope_id, binding_name)
                cursor_position = 0 if cursor is None else cursor.cursor.sequence
                if cursor_position < work.wave_target:
                    raise ArtifactProcessingWaveIncompleteError(
                        binding_name,
                        scope_id,
                        work.wave_target,
                        cursor_position,
                    )
                marked = await self._auto_wave_targets.mark_completed(
                    connection,
                    wave.wave_id,
                    scope_id,
                )
                if not marked:
                    raise _AutomaticWaveStateError(wave.wave_id, "completed target row is missing")
        self._work.pop(work.key, None)
        self._queued.discard(work.key)
        self._retries.pop(work.key, None)
        if wave is not None and work.wave_kind is ArtifactProcessingWaveKind.AUTOMATIC:
            wave.completed.add(work.key)
            if wave.completed == set(wave.targets):
                self._wake.set()

    async def _complete_automatic_wave(self, wave: _AutomaticWave) -> None:
        async with self._database.transaction() as connection:
            await self._leases.require_fence(connection, self._require_current_fence())
            if not await self._auto_wave_targets.all_completed(connection, wave.wave_id):
                raise _AutomaticWaveStateError(wave.wave_id, "incomplete target rows remain")
            await self._binding_states.mark_auto_wave_completed(connection, wave.binding_name)
            await self._auto_wave_targets.delete_covered_pending(
                connection,
                wave.wave_id,
                wave.binding_name,
            )
            await self._auto_wave_targets.clear_wave(connection, wave.wave_id)
        self._automatic_waves.pop(wave.binding_name, None)
        self._automatic_discovery_queued.discard(wave.binding_name)

    def _record_failure(
        self,
        work: _WaveWork,
        assignment: ArtifactProcessingWorkAssignment,
        failure: ArtifactProcessingWorkerFailure,
    ) -> None:
        loop = asyncio.get_running_loop()
        previous = self._retries.get(work.key)
        failures = 1 if previous is None else previous.consecutive_failures + 1
        base_delay = min(self._retry_base_seconds * (2 ** (failures - 1)), self._retry_cap_seconds)
        retry_delay = base_delay * self._retry_jitter()
        self._work.pop(work.key, None)
        self._queued.discard(work.key)
        with suppress(ValueError):
            self._queue.remove(work.key)
        if previous is None and len(self._retries) >= _RETRY_STATE_LIMIT:
            evicted = next(
                (
                    key
                    for key, retry in self._retries.items()
                    if retry.work.wave_kind is ArtifactProcessingWaveKind.EXPLICIT and key not in self._work
                ),
                None,
            )
            if evicted is None:
                if work.wave_kind is ArtifactProcessingWaveKind.AUTOMATIC:
                    raise _RetryCapacityError
                self._next_discovery_wake_at = loop.time() + retry_delay
            else:
                self._retries.pop(evicted)
        if previous is not None or len(self._retries) < _RETRY_STATE_LIMIT:
            self._retries[work.key] = _RetryState(
                work=work,
                consecutive_failures=failures,
                next_retry_at=loop.time() + retry_delay,
            )
        log_safely(
            logger,
            logging.ERROR,
            "Artifact processing Worker failed",
            extra={
                "event": "artifact_processing.worker.failed",
                "binding_name": assignment.binding_name,
                "scope_id": assignment.scope_id,
                "source_after": assignment.source_after,
                "source_through": assignment.source_through,
                "stage": failure.stage,
                "error_code": failure.error_code,
                "exception_type": failure.exception_type,
                "failure_count": failures,
                "retry_delay_seconds": retry_delay,
                "supervisor_generation": assignment.fence.supervisor_generation,
                "worker_id": assignment.worker_id,
                "traceback": failure.traceback,
                "outcome": "failure",
                "unit": "artifact_processing",
            },
        )

    async def _finish_ready_automatic_waves(self) -> None:
        for wave in tuple(self._automatic_waves.values()):
            if wave.completed != set(wave.targets):
                continue
            if wave.discovery_complete:
                await self._complete_automatic_wave(wave)
                continue
            wave.targets.clear()
            wave.completed.clear()
            self._enqueue_automatic_discovery(wave.binding_name)

    async def _lose_leadership(self, status: ArtifactProcessingSupervisorStatus) -> None:
        self._fence = None
        self._status = status
        launching = tuple(self._launching.values())
        self._launching.clear()
        running = tuple(self._running.values())
        self._running.clear()
        for item in launching:
            item.task.cancel()
        for item in running:
            item.task.cancel()
        tasks = tuple(item.task for item in (*launching, *running))
        if tasks:
            done, _pending = await asyncio.wait(tasks, timeout=_WORKER_SHUTDOWN_TIMEOUT_SECONDS)
            await asyncio.gather(*done, return_exceptions=True)
        late_handles: list[ArtifactProcessingWorkerHandle] = []
        for item in launching:
            if not item.task.done() or item.task.cancelled():
                continue
            with suppress(Exception):
                late_handles.append(item.task.result())
        await asyncio.gather(*(self._terminate_worker(handle) for handle in late_handles), return_exceptions=True)
        self._queue.clear()
        self._queued.clear()
        self._work.clear()
        self._automatic_waves.clear()
        self._automatic_discovery_queue.clear()
        self._automatic_discovery_queued.clear()
        self._discover_automatic_next = False
        self._next_automatic_wake_at = None
        self._discovery_after = None
        self._next_discovery_wake_at = None
        self._retries.clear()

    def _enqueue(self, key: ProcessingKey) -> None:
        if key not in self._queued and key not in self._launching and key not in self._running and key in self._work:
            self._queue.append(key)
            self._queued.add(key)

    def _require_current_fence(self) -> ArtifactProcessingFence:
        if self._fence is None:
            raise ArtifactProcessingLeadershipLostError("global", self.holder_id, 0)
        return self._fence

    def _next_wake_seconds(self) -> float | None:
        if self._status is ArtifactProcessingSupervisorStatus.DEGRADED:
            return self._oceanbase_tick_seconds
        if self._lease_mode == "oceanbase":
            return self._oceanbase_tick_seconds
        retry_delays = tuple(
            max(0.0, retry.next_retry_at - asyncio.get_running_loop().time())
            for key, retry in self._retries.items()
            if key not in self._work
        )
        automatic_delays = (
            ()
            if self._next_automatic_wake_at is None
            else (max(0.0, self._next_automatic_wake_at - asyncio.get_running_loop().time()),)
        )
        discovery_delays = (
            ()
            if self._next_discovery_wake_at is None
            else (max(0.0, self._next_discovery_wake_at - asyncio.get_running_loop().time()),)
        )
        candidates = (*automatic_delays, *discovery_delays, *retry_delays)
        return None if not candidates else min(candidates)


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


def _safe_traceback(error: BaseException) -> str:
    """Format stack locations without exception values that may contain Source/model data."""

    return "".join(traceback_module.format_list(traceback_module.extract_tb(error.__traceback__)))


__all__ = [
    "ArtifactProcessingBinding",
    "ArtifactProcessingSupervisor",
    "ArtifactProcessingSupervisorStatus",
    "ArtifactProcessingWaveKind",
    "ArtifactProcessingWorkAssignment",
    "ArtifactProcessingWorkerCompletion",
    "ArtifactProcessingWorkerFailure",
    "ArtifactProcessingWorkerHandle",
    "ArtifactProcessingWorkerLauncher",
    "ArtifactProcessingWorkerOutcome",
    "SpawnArtifactProcessingWorkerLauncher",
]
