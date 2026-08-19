"""Durable, task-independent TokensFlow resource finalization."""

from __future__ import annotations

import logging
import shutil
import stat
import threading
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from functools import partial
from pathlib import Path
from time import monotonic
from types import SimpleNamespace
from typing import Protocol, cast
from uuid import uuid4

from powercontext_eval.errors import CommandError
from powercontext_eval.powercontext_sut import ArmPaths, DockerSut, UnsafeSutConfiguration
from powercontext_eval.process import CommandResult, ProcessRunner
from powercontext_eval.tokensflow import tokensflow_queue_caught_up
from powercontext_eval.web.config import MAX_TASK_PARALLELISM
from powercontext_eval.web.store import (
    FinalizationState,
    TaskOwnershipError,
    TaskStore,
    TokensFlowFinalizationRecord,
)

_LOGGER = logging.getLogger(__name__)
_QUIESCE_TIMEOUT_SECONDS = 10.0
_UPLOAD_TIMEOUT_SECONDS = 60.0
_DOCTOR_TIMEOUT_SECONDS = 60.0
_CONTAINER_CLEANUP_TIMEOUT_SECONDS = 30.0
_CONTAINER_CLEANUP_RESERVE_SECONDS = _CONTAINER_CLEANUP_TIMEOUT_SECONDS * 2


class FinalizationRuntime(Protocol):
    """Container operations kept outside the durable state machine."""

    def quiesce(self, job: TokensFlowFinalizationRecord, *, timeout_seconds: float) -> bool: ...

    def upload(self, job: TokensFlowFinalizationRecord, *, timeout_seconds: float) -> int: ...

    def doctor(self, job: TokensFlowFinalizationRecord, *, timeout_seconds: float) -> tuple[bytes, int]: ...

    def cleanup(self, job: TokensFlowFinalizationRecord, *, graceful: bool) -> bool: ...


class Process(Protocol):
    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: str | Path,
        timeout: float | None = None,
        cancel_event: threading.Event | None = None,
        check: bool = True,
    ) -> CommandResult: ...


class DockerFinalizationRuntime:
    """Reuse the handed-off container's actual wrapper, HOME, and environment."""

    def __init__(
        self,
        run_root: Path,
        *,
        runner: Process | None = None,
        monotonic: Callable[[], float] = monotonic,
        cancel_event: threading.Event | None = None,
    ) -> None:
        self._run_root = run_root
        self._runner = runner or ProcessRunner()
        self._monotonic = monotonic
        self._cancel_event = cancel_event

    def quiesce(self, job: TokensFlowFinalizationRecord, *, timeout_seconds: float) -> bool:
        runtime = self._safe_runtime(job)
        deadline = self._monotonic() + timeout_seconds
        try:
            if self._stop_daemon(
                job,
                runtime,
                timeout_seconds=self._remaining_timeout(deadline),
            ):
                return True
            remaining = self._remaining_timeout(deadline)
            return remaining > 0 and self._container_absent(
                job,
                runtime,
                timeout_seconds=remaining,
            )
        except (CommandError, OSError):
            return False

    def upload(self, job: TokensFlowFinalizationRecord, *, timeout_seconds: float) -> int:
        runtime = self._safe_runtime(job)
        # Deliberately do not pass -e, HOME, a host binary path, or TOKENSFLOW_*.
        # Docker exec inherits the exact environment and PATH captured by this arm.
        upload = self._runner.run(
            ("docker", "exec", job.container_name, "tokensflow", "upload", "--all"),
            cwd=runtime,
            timeout=timeout_seconds,
            cancel_event=self._cancel_event,
            check=False,
        )
        return upload.returncode

    def doctor(self, job: TokensFlowFinalizationRecord, *, timeout_seconds: float) -> tuple[bytes, int]:
        runtime = self._safe_runtime(job)
        doctor = self._runner.run(
            ("docker", "exec", job.container_name, "tokensflow", "doctor"),
            cwd=runtime,
            timeout=timeout_seconds,
            cancel_event=self._cancel_event,
            check=False,
        )
        return doctor.stdout.encode("utf-8"), doctor.returncode

    def cleanup(self, job: TokensFlowFinalizationRecord, *, graceful: bool) -> bool:
        runtime = self._safe_runtime(job)
        deadline = self._monotonic() + _CONTAINER_CLEANUP_TIMEOUT_SECONDS
        try:
            container_absent = False
            if graceful and not self._stop_daemon(
                job,
                runtime,
                timeout_seconds=min(_QUIESCE_TIMEOUT_SECONDS, self._remaining_timeout(deadline)),
            ):
                remaining = self._remaining_timeout(deadline)
                container_absent = remaining > 0 and self._container_absent(
                    job,
                    runtime,
                    timeout_seconds=remaining,
                )
                if not container_absent:
                    return False
            if not container_absent and not self._remove_container(
                job,
                runtime,
                deadline=deadline,
            ):
                return False
            paths = cast(ArmPaths, SimpleNamespace(runtime=runtime))
            DockerSut._cleanup_tokensflow_binary(paths)
            DockerSut._cleanup_tokensflow_wrapper(paths)
            self._remove_private_home(runtime / "root-home")
        except (CommandError, OSError, UnsafeSutConfiguration):
            return False
        return True

    def _safe_runtime(self, job: TokensFlowFinalizationRecord) -> Path:
        runtime = self._run_root / job.runtime_path
        try:
            runtime.relative_to(self._run_root)
        except ValueError:
            raise ValueError("TokensFlow finalization runtime escaped its run root") from None
        return runtime

    def _stop_daemon(
        self,
        job: TokensFlowFinalizationRecord,
        runtime: Path,
        *,
        timeout_seconds: float,
    ) -> bool:
        script = (
            'test -s "$1" || exit 20\n'
            'pid="$(cat "$1")" || exit 20\n'
            'case "$pid" in ""|0|*[!0-9]*) exit 20;; esac\n'
            'test -e "/proc/$pid" || exit 0\n'
            'exe="$(readlink "/proc/$pid/exe" 2>/dev/null)" || exit 20\n'
            'case "$exe" in */tokensflow) ;; *) exit 20;; esac\n'
            'cmdline="$(tr "\\000" "\\n" < "/proc/$pid/cmdline")" || exit 20\n'
            'case "$cmdline" in *daemon*) ;; *) exit 20;; esac\n'
            'kill -TERM "$pid" || { test ! -e "/proc/$pid" && exit 0; exit 20; }\n'
            "tries=0\n"
            'while test -e "/proc/$pid"; do\n'
            '  exe="$(readlink "/proc/$pid/exe" 2>/dev/null)" || exit 0\n'
            '  case "$exe" in */tokensflow) ;; *) exit 0;; esac\n'
            '  cmdline="$(tr "\\000" "\\n" < "/proc/$pid/cmdline")" || exit 0\n'
            '  case "$cmdline" in *daemon*) ;; *) exit 0;; esac\n'
            '  tries=$((tries + 1)); test "$tries" -lt 100 || exit 20\n'
            "  sleep 0.05\n"
            "done"
        )
        result = self._runner.run(
            (
                "docker",
                "exec",
                job.container_name,
                "/bin/sh",
                "-c",
                script,
                "tokensflow-finalizer-stop",
                job.daemon_pid_file,
            ),
            cwd=runtime,
            timeout=timeout_seconds,
            cancel_event=self._cancel_event,
            check=False,
        )
        return result.returncode == 0

    def _remove_container(
        self,
        job: TokensFlowFinalizationRecord,
        runtime: Path,
        *,
        deadline: float,
    ) -> bool:
        remaining = self._remaining_timeout(deadline)
        if remaining <= 0:
            return False
        removal = self._runner.run(
            ("docker", "rm", "-f", job.container_name),
            cwd=runtime,
            timeout=remaining,
            cancel_event=self._cancel_event,
            check=False,
        )
        if removal.returncode == 0:
            return True
        remaining = self._remaining_timeout(deadline)
        return remaining > 0 and self._container_absent(
            job,
            runtime,
            timeout_seconds=remaining,
        )

    def _container_absent(
        self,
        job: TokensFlowFinalizationRecord,
        runtime: Path,
        *,
        timeout_seconds: float,
    ) -> bool:
        inspection = self._runner.run(
            ("docker", "container", "inspect", job.container_name),
            cwd=runtime,
            timeout=timeout_seconds,
            cancel_event=self._cancel_event,
            check=False,
        )
        return inspection.returncode != 0 and inspection.stderr.strip() in {
            f"Error: No such container: {job.container_name}",
            f"Error: No such object: {job.container_name}",
            f"Error response from daemon: No such container: {job.container_name}",
        }

    def _remaining_timeout(self, deadline: float) -> float:
        return max(deadline - self._monotonic(), 0.0)

    @staticmethod
    def _remove_private_home(path: Path) -> None:
        try:
            metadata = path.lstat()
        except FileNotFoundError:
            return
        if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
            raise OSError("TokensFlow private home is unsafe")
        shutil.rmtree(path)


class TokensFlowFinalizer:
    """Drain one bounded durable-job snapshot without owning task leases."""

    def __init__(
        self,
        store: TaskStore,
        runtime: FinalizationRuntime,
        *,
        clock: Callable[[], datetime] | None = None,
        task_parallelism: int,
        lease_seconds: int = 300,
        cleanup_retry_seconds: float = 5.0,
        worker_id: str | None = None,
    ) -> None:
        if (
            isinstance(task_parallelism, bool)
            or not isinstance(task_parallelism, int)
            or not 1 <= task_parallelism <= MAX_TASK_PARALLELISM
        ):
            raise ValueError("TokensFlow finalizer task parallelism is invalid")
        self._store = store
        self._runtime = runtime
        self._clock = clock or (lambda: datetime.now(UTC))
        self._task_parallelism = task_parallelism
        self._max_jobs = task_parallelism * 2
        self._lease_seconds = lease_seconds
        self._last_run_terminal_progress = True
        if cleanup_retry_seconds <= 0:
            raise ValueError("TokensFlow cleanup retry interval must be positive")
        self._cleanup_retry_seconds = cleanup_retry_seconds
        self._worker_id = worker_id or f"tokensflow-finalizer-{uuid4().hex}"

    def run_once(self, *, stop: threading.Event | None = None) -> bool:
        self._last_run_terminal_progress = False
        if stop is not None and stop.is_set():
            return False
        open_jobs = self._store.list_open_tokensflow_finalizations()
        if not open_jobs:
            return False
        excess = len(open_jobs) - self._max_jobs
        if excess > 0:
            # The store supplies finalization_seq as the tie-breaker; Python's stable sort preserves it here.
            capacity_candidates = sorted(open_jobs, key=lambda job: job.registered_at)
            return self._restore_capacity(capacity_candidates, excess, stop=stop)
        open_jobs.sort(key=lambda job: (job.deadline_at, job.registered_at))
        with ThreadPoolExecutor(
            max_workers=min(self._task_parallelism, len(open_jobs)),
            thread_name_prefix="tokensflow-finalizer-job",
        ) as executor:
            results = tuple(executor.map(partial(self._claim_and_process, stop=stop), open_jobs))
        self._last_run_terminal_progress = any(finalized for _attempted, finalized in results)
        return any(attempted for attempted, _finalized in results)

    def _restore_capacity(
        self,
        candidates: Sequence[TokensFlowFinalizationRecord],
        excess: int,
        *,
        stop: threading.Event | None,
    ) -> bool:
        finalized = 0
        attempted = False
        cursor = 0
        while finalized < excess and cursor < len(candidates):
            batch_size = min(excess - finalized, len(candidates) - cursor)
            batch = candidates[cursor : cursor + batch_size]
            with ThreadPoolExecutor(
                max_workers=min(self._task_parallelism, len(batch)),
                thread_name_prefix="tokensflow-finalizer-capacity",
            ) as executor:
                results = tuple(executor.map(partial(self._claim_and_force_capacity_cleanup, stop=stop), batch))
            attempted = attempted or any(result_attempted for result_attempted, _finalized in results)
            finalized += sum(result_finalized for _attempted, result_finalized in results)
            cursor += batch_size
        self._last_run_terminal_progress = finalized > 0
        return attempted

    def _claim_and_force_capacity_cleanup(
        self,
        candidate: TokensFlowFinalizationRecord,
        *,
        stop: threading.Event | None,
    ) -> tuple[bool, bool]:
        if stop is not None and stop.is_set():
            return False, False
        now = self._clock()
        job = self._store.claim_tokensflow_finalization(
            self._worker_id,
            now=now,
            lease_seconds=self._lease_seconds,
            process_lock_recovery_at=self._cleanup_threshold(candidate),
            job_id=candidate.job_id,
        )
        if job is None:
            return False, False
        try:
            if stop is not None and stop.is_set():
                self._release_for_shutdown(job, now=now)
                return True, False
            cleanup_state = FinalizationState.CAPACITY_EVICTED
            cleanup_reason = "capacity_eviction"
            if job.state is FinalizationState.CLEANUP_PENDING:
                cleanup_reason = job.reason or "deadline"
                if cleanup_reason != "capacity_eviction":
                    cleanup_state = FinalizationState.TIMED_OUT
            elif self._remaining_phase_budget(job, now) <= 0:
                cleanup_state = FinalizationState.TIMED_OUT
                cleanup_reason = "deadline"
            return True, self._force_cleanup(
                job,
                now=now,
                state=cleanup_state,
                reason=cleanup_reason,
                stop=stop,
            )
        except Exception as error:  # noqa: BLE001 - one capacity candidate must not starve later jobs
            _LOGGER.warning(
                "TokensFlow capacity cleanup failed (error_type=%s)",
                type(error).__name__,
            )
            return True, False

    def _claim_and_process(
        self,
        candidate: TokensFlowFinalizationRecord,
        *,
        stop: threading.Event | None,
    ) -> tuple[bool, bool]:
        if stop is not None and stop.is_set():
            return False, False
        now = self._clock()
        job = self._store.claim_tokensflow_finalization(
            self._worker_id,
            now=now,
            lease_seconds=self._lease_seconds,
            process_lock_recovery_at=self._cleanup_threshold(candidate),
            job_id=candidate.job_id,
        )
        if job is None:
            return False, False
        try:
            return True, self._process_claimed(job, now=now, stop=stop)
        except Exception as error:  # noqa: BLE001 - one candidate must not starve later jobs
            _LOGGER.warning(
                "TokensFlow finalization job failed (error_type=%s)",
                type(error).__name__,
            )
            return True, False

    def _process_claimed(
        self,
        job: TokensFlowFinalizationRecord,
        *,
        now: datetime,
        stop: threading.Event | None,
    ) -> bool:
        try:
            if stop is not None and stop.is_set():
                return self._release_for_shutdown(job, now=now)
            if job.state is FinalizationState.CLEANUP_PENDING:
                terminal_state = (
                    FinalizationState.CAPACITY_EVICTED
                    if job.reason == "capacity_eviction"
                    else FinalizationState.TIMED_OUT
                )
                return self._force_cleanup(
                    job,
                    now=now,
                    state=terminal_state,
                    reason=job.reason or "deadline",
                    stop=stop,
                )
            if self._remaining_phase_budget(job, now) <= 0:
                return self._force_cleanup(
                    job,
                    now=now,
                    state=FinalizationState.TIMED_OUT,
                    reason="deadline",
                    stop=stop,
                )
            if not job.queue_passed:
                try:
                    quiesced = self._runtime.quiesce(
                        job,
                        timeout_seconds=min(_QUIESCE_TIMEOUT_SECONDS, self._remaining_phase_budget(job, now)),
                    )
                    phase_now = self._clock()
                    if stop is not None and stop.is_set():
                        return self._release_for_shutdown(job, now=phase_now)
                    if self._remaining_phase_budget(job, phase_now) <= 0:
                        return self._force_cleanup(
                            job,
                            now=phase_now,
                            state=FinalizationState.TIMED_OUT,
                            reason="deadline",
                            stop=stop,
                        )
                    if not quiesced:
                        self._store.release_tokensflow_finalization(
                            job.job_id,
                            self._worker_id,
                            now=phase_now,
                            error_category="daemon_quiesce_error",
                        )
                        return False
                    upload_rc = self._runtime.upload(
                        job,
                        timeout_seconds=min(_UPLOAD_TIMEOUT_SECONDS, self._remaining_phase_budget(job, phase_now)),
                    )
                    phase_now = self._clock()
                    if stop is not None and stop.is_set():
                        return self._release_for_shutdown(job, now=phase_now)
                    if self._remaining_phase_budget(job, phase_now) <= 0:
                        return self._force_cleanup(
                            job,
                            now=phase_now,
                            state=FinalizationState.TIMED_OUT,
                            reason="deadline",
                            stop=stop,
                        )
                    raw, doctor_rc = self._runtime.doctor(
                        job,
                        timeout_seconds=min(_DOCTOR_TIMEOUT_SECONDS, self._remaining_phase_budget(job, phase_now)),
                    )
                    phase_now = self._clock()
                    if stop is not None and stop.is_set():
                        return self._release_for_shutdown(job, now=phase_now)
                    if self._remaining_phase_budget(job, phase_now) <= 0:
                        return self._force_cleanup(
                            job,
                            now=phase_now,
                            state=FinalizationState.TIMED_OUT,
                            reason="deadline",
                            stop=stop,
                        )
                except Exception:  # noqa: BLE001 - retry only a sanitized category before the durable deadline
                    failure_now = self._clock()
                    if stop is not None and stop.is_set():
                        return self._release_for_shutdown(job, now=failure_now)
                    if self._remaining_phase_budget(job, failure_now) <= 0:
                        return self._force_cleanup(
                            job,
                            now=failure_now,
                            state=FinalizationState.TIMED_OUT,
                            reason="deadline",
                            stop=stop,
                        )
                    self._store.release_tokensflow_finalization(
                        job.job_id,
                        self._worker_id,
                        now=failure_now,
                        error_category="doctor_error",
                    )
                    return False
                error_category = None if upload_rc == 0 else "upload_error"
                job = self._store.record_tokensflow_finalization_check(
                    job.job_id,
                    self._worker_id,
                    queue_passed=upload_rc == 0 and tokensflow_queue_caught_up(raw),
                    doctor_rc=doctor_rc,
                    now=phase_now,
                    error_category=error_category,
                )
                if not job.queue_passed:
                    self._store.release_tokensflow_finalization(
                        job.job_id,
                        self._worker_id,
                        now=phase_now,
                        error_category=error_category,
                    )
                    return False
            if stop is not None and stop.is_set():
                return self._release_for_shutdown(job, now=self._clock())
            if self._runtime.cleanup(job, graceful=False):
                self._store.finish_tokensflow_finalization(
                    job.job_id,
                    self._worker_id,
                    state=FinalizationState.PASSED,
                    now=now,
                )
                return True
            else:
                if stop is not None and stop.is_set():
                    return self._release_for_shutdown(job, now=self._clock())
                self._store.release_tokensflow_finalization(
                    job.job_id,
                    self._worker_id,
                    now=now,
                    error_category="cleanup_error",
                )
        except TaskOwnershipError:
            return False
        return False

    @staticmethod
    def _remaining_phase_budget(job: TokensFlowFinalizationRecord, now: datetime) -> float:
        return (TokensFlowFinalizer._cleanup_threshold(job) - now).total_seconds()

    @staticmethod
    def _cleanup_threshold(job: TokensFlowFinalizationRecord) -> datetime:
        return job.deadline_at - timedelta(seconds=_CONTAINER_CLEANUP_RESERVE_SECONDS)

    def run_forever(self, stop: threading.Event, poll_seconds: float) -> None:
        """Poll finitely and remain promptly stoppable at every job boundary."""

        if poll_seconds <= 0:
            raise ValueError("TokensFlow finalizer poll interval must be positive")
        while not stop.is_set():
            try:
                made_progress = self.run_once(stop=stop)
            except Exception as error:  # noqa: BLE001 - one durable job failure must not kill the supervisor
                _LOGGER.warning(
                    "TokensFlow finalizer poll failed (error_type=%s)",
                    type(error).__name__,
                )
                stop.wait(self._next_wait_seconds(poll_seconds))
                continue
            if not made_progress or not self._last_run_terminal_progress:
                stop.wait(self._next_wait_seconds(poll_seconds))

    def _next_wait_seconds(self, poll_seconds: float) -> float:
        now = self._clock()
        wait_seconds = poll_seconds
        for job in self._store.list_open_tokensflow_finalizations():
            if job.state is FinalizationState.RUNNING and job.lease_expires_at is not None:
                wake_at = min(job.lease_expires_at, self._cleanup_threshold(job))
            elif (
                job.state is FinalizationState.CLEANUP_PENDING
                and job.lease_expires_at is not None
                and job.lease_expires_at > now
            ):
                wake_at = job.lease_expires_at
            else:
                wake_at = self._cleanup_threshold(job)
            wait_seconds = min(wait_seconds, max((wake_at - now).total_seconds(), 0.0))
        return wait_seconds

    def _force_cleanup(
        self,
        job: TokensFlowFinalizationRecord,
        *,
        now: datetime,
        state: FinalizationState,
        reason: str,
        stop: threading.Event | None,
    ) -> bool:
        if stop is not None and stop.is_set():
            return self._release_for_shutdown(job, now=now)
        if self._runtime.cleanup(job, graceful=False):
            self._store.finish_tokensflow_finalization(
                job.job_id,
                self._worker_id,
                state=state,
                now=now,
                reason=reason,
            )
            return True
        if stop is not None and stop.is_set():
            return self._release_for_shutdown(job, now=self._clock())
        self._store.defer_tokensflow_finalization_cleanup(
            job.job_id,
            self._worker_id,
            now=now,
            reason=reason,
            retry_seconds=self._cleanup_retry_seconds,
        )
        return False

    def _release_for_shutdown(self, job: TokensFlowFinalizationRecord, *, now: datetime) -> bool:
        self._store.release_tokensflow_finalization(
            job.job_id,
            self._worker_id,
            now=now,
            error_category="shutdown",
        )
        return False
