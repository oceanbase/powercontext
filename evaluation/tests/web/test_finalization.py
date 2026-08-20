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

import logging
import threading
import time
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from powercontext_eval.powercontext_sut import DockerSut
from powercontext_eval.process import CommandResult
from powercontext_eval.web.finalization import DockerFinalizationRuntime, TokensFlowFinalizer
from powercontext_eval.web.models import TaskCreate, TaskResult, TaskStatus
from powercontext_eval.web.reporting import tokensflow_finalization_summary
from powercontext_eval.web.store import (
    FinalizationState,
    TaskStore,
    TokensFlowFinalizationCreate,
    TokensFlowFinalizationRecord,
)

NOW = datetime(2026, 8, 3, 1, 2, 3, tzinfo=UTC)


def _store(tmp_path: Path) -> TaskStore:
    store = TaskStore(tmp_path / "tasks.sqlite3", lease_duration=timedelta(seconds=60))
    store.initialize()
    return store


def _register(
    store: TaskStore,
    *,
    key: str,
    now: datetime = NOW,
    timeout_seconds: int = 600,
) -> TokensFlowFinalizationRecord:
    task = store.create(
        TaskCreate(
            powercontext_ref="commit:" + "a" * 40,
            benchmark="swebench-pro",
            instance_id=f"instance_owner__{key}",
            model="gpt-5.6-sol",
            reasoning_effort="medium",
            treatment_mode="off_on",
            idempotency_key=f"finalize-{key}",
        ),
        now=now,
    )[0]
    assert task.attempt_id is not None
    return store.register_tokensflow_finalization(
        TokensFlowFinalizationCreate(
            attempt_id=task.attempt_id,
            task_id=task.task_id,
            batch_id=None,
            arm="off",
            run_id=task.task_id,
            container_name=f"powercontext-eval-{task.task_id}-off",
            runtime_path=f"work/{task.task_id}/off/runtime",
            wrapper_path=f"work/{task.task_id}/off/evaluation-control/tokensflow-wrapper",
            egress_network="tokensflow-egress",
            daemon_pid_file="/runtime/tokensflow-home/.local/share/tokensflow/evaluation-daemon.pid",
            evidence_sha256="b" * 64,
            evidence_bytes=456,
        ),
        now=now,
        timeout_seconds=timeout_seconds,
    )[0]


class FakeRuntime:
    def __init__(self, checks: Sequence[tuple[bytes, int]], cleanups: Sequence[bool] = (True,)) -> None:
        self.checks = list(checks)
        self.cleanups = list(cleanups)
        self.cleanup_modes: list[tuple[str, bool]] = []
        self.quiesce_calls: list[str] = []
        self.daemon_stopped = False

    def quiesce(self, job: TokensFlowFinalizationRecord, *, timeout_seconds: float) -> bool:
        assert timeout_seconds > 0
        self.quiesce_calls.append(job.job_id)
        self.daemon_stopped = True
        return True

    def upload(self, job: TokensFlowFinalizationRecord, *, timeout_seconds: float) -> int:
        del job
        assert timeout_seconds > 0
        return 0

    def doctor(self, job: TokensFlowFinalizationRecord, *, timeout_seconds: float) -> tuple[bytes, int]:
        del job
        assert timeout_seconds > 0
        assert self.daemon_stopped
        raw, doctor_rc = self.checks.pop(0)
        return raw, doctor_rc

    def cleanup(self, job: TokensFlowFinalizationRecord, *, graceful: bool) -> bool:
        self.cleanup_modes.append((job.job_id, graceful))
        return self.cleanups.pop(0)


class BlockingPhaseRuntime(FakeRuntime):
    def __init__(self, phase: str, stop: threading.Event) -> None:
        super().__init__([(b"[PASS] queue: caught up (0 pending files)\n", 0)])
        self.phase = phase
        self.stop = stop
        self.entered = threading.Event()

    def upload(self, job: TokensFlowFinalizationRecord, *, timeout_seconds: float) -> int:
        if self.phase != "upload":
            return super().upload(job, timeout_seconds=timeout_seconds)
        self.entered.set()
        if self.stop.wait(timeout=2):
            raise RuntimeError("cancelled finalization phase")
        return 0

    def doctor(self, job: TokensFlowFinalizationRecord, *, timeout_seconds: float) -> tuple[bytes, int]:
        if self.phase != "doctor":
            return super().doctor(job, timeout_seconds=timeout_seconds)
        self.entered.set()
        if self.stop.wait(timeout=2):
            raise RuntimeError("cancelled finalization phase")
        return b"[PASS] queue: caught up (0 pending files)\n", 0


class UploadFailureRuntime:
    def __init__(self) -> None:
        self.cleanup_called = False

    def quiesce(self, job: TokensFlowFinalizationRecord, *, timeout_seconds: float) -> bool:
        del job
        assert timeout_seconds > 0
        return True

    def upload(self, job: TokensFlowFinalizationRecord, *, timeout_seconds: float) -> int:
        del job
        assert timeout_seconds > 0
        return 1

    def doctor(self, job: TokensFlowFinalizationRecord, *, timeout_seconds: float) -> tuple[bytes, int]:
        del job
        assert timeout_seconds > 0
        return b"[PASS] queue: caught up (0 pending files)\n", 0

    def cleanup(self, job: TokensFlowFinalizationRecord, *, graceful: bool) -> bool:
        del job, graceful
        self.cleanup_called = True
        return True


class FairRuntime:
    def __init__(
        self,
        *,
        warn_job_ids: set[str] | None = None,
        cleanup_fail_job_ids: set[str] | None = None,
    ) -> None:
        self.warn_job_ids = warn_job_ids or set()
        self.cleanup_fail_job_ids = cleanup_fail_job_ids or set()
        self.events: list[tuple[str, str]] = []

    def quiesce(self, job: TokensFlowFinalizationRecord, *, timeout_seconds: float) -> bool:
        assert timeout_seconds > 0
        self.events.append(("quiesce", job.job_id))
        return True

    def upload(self, job: TokensFlowFinalizationRecord, *, timeout_seconds: float) -> int:
        assert timeout_seconds > 0
        self.events.append(("upload", job.job_id))
        return 0

    def doctor(self, job: TokensFlowFinalizationRecord, *, timeout_seconds: float) -> tuple[bytes, int]:
        assert timeout_seconds > 0
        self.events.append(("doctor", job.job_id))
        if job.job_id in self.warn_job_ids:
            return b"[WARN] queue: 1 pending file\n", 1
        return b"[PASS] queue: caught up (0 pending files)\n", 0

    def cleanup(self, job: TokensFlowFinalizationRecord, *, graceful: bool) -> bool:
        assert graceful is False
        self.events.append(("cleanup", job.job_id))
        return job.job_id not in self.cleanup_fail_job_ids


class ConcurrentCleanupRuntime(FairRuntime):
    def __init__(self, concurrency: int) -> None:
        super().__init__()
        self._barrier = threading.Barrier(concurrency, timeout=2)
        self._lock = threading.Lock()
        self._active = 0
        self.max_active = 0

    def cleanup(self, job: TokensFlowFinalizationRecord, *, graceful: bool) -> bool:
        with self._lock:
            self._active += 1
            self.max_active = max(self.max_active, self._active)
        try:
            self._barrier.wait()
            return super().cleanup(job, graceful=graceful)
        finally:
            with self._lock:
                self._active -= 1


def test_exact_queue_pass_gracefully_cleans_and_finishes(tmp_path: Path) -> None:
    store = _store(tmp_path)
    job = _register(store, key="pass")
    runtime = FakeRuntime([(b"[PASS] queue: caught up (0 pending files)\n", 0)])
    finalizer = TokensFlowFinalizer(store, runtime, clock=lambda: NOW + timedelta(seconds=1), task_parallelism=10)

    assert finalizer.run_once() is True

    persisted = store.tokensflow_finalizations_for_attempt(job.attempt_id)[0]
    assert persisted.state is FinalizationState.PASSED
    assert persisted.queue_passed is True
    assert persisted.doctor_rc == 0
    assert runtime.cleanup_modes == [(job.job_id, False)]


def test_warn_releases_job_then_exact_pass_succeeds_without_sleep(tmp_path: Path) -> None:
    store = _store(tmp_path)
    job = _register(store, key="warn-pass")
    runtime = FakeRuntime(
        [
            (b"[WARN] queue: 2 pending files\n", 1),
            (b"[PASS] queue: caught up (0 pending files)\n", 0),
        ]
    )
    finalizer = TokensFlowFinalizer(
        store,
        runtime,
        clock=lambda: NOW + timedelta(seconds=1),
        task_parallelism=10,
    )

    assert finalizer.run_once() is True
    pending = store.tokensflow_finalizations_for_attempt(job.attempt_id)[0]
    assert pending.state is FinalizationState.PENDING
    assert pending.attempts == 1
    assert pending.queue_passed is False
    assert finalizer.run_once() is True
    assert store.tokensflow_finalizations_for_attempt(job.attempt_id)[0].state is FinalizationState.PASSED
    assert runtime.daemon_stopped is True
    assert runtime.quiesce_calls == [job.job_id, job.job_id]


def test_nonzero_upload_cannot_be_overridden_by_exact_doctor_pass(tmp_path: Path) -> None:
    store = _store(tmp_path)
    job = _register(store, key="upload-failure")
    runtime = UploadFailureRuntime()
    finalizer = TokensFlowFinalizer(store, runtime, clock=lambda: NOW + timedelta(seconds=1), task_parallelism=10)

    assert finalizer.run_once() is True

    persisted = store.tokensflow_finalizations_for_attempt(job.attempt_id)[0]
    assert persisted.state is FinalizationState.PENDING
    assert persisted.queue_passed is False
    assert persisted.doctor_rc == 0
    assert persisted.error_category == "upload_error"
    assert runtime.cleanup_called is False


def test_deadline_force_cleanup_preserves_success_and_does_not_pause_batch(tmp_path: Path) -> None:
    store = _store(tmp_path)
    job = _register(store, key="deadline")
    task = store.claim_next("evaluation-worker", now=NOW)
    assert task is not None
    store.succeed(
        task.task_id,
        "evaluation-worker",
        TaskResult(
            artifact_dir="runs/safe",
            report_path="runs/safe/report.md",
            off_resolved=False,
            on_resolved=True,
        ),
        now=NOW + timedelta(seconds=1),
    )
    runtime = FakeRuntime([])
    finalizer = TokensFlowFinalizer(store, runtime, clock=lambda: NOW + timedelta(seconds=600), task_parallelism=10)

    assert finalizer.run_once() is True

    persisted = store.tokensflow_finalizations_for_attempt(job.attempt_id)[0]
    assert persisted.state is FinalizationState.TIMED_OUT
    assert persisted.reason == "deadline"
    assert runtime.cleanup_modes == [(job.job_id, False)]
    assert store.get(task.task_id).status is TaskStatus.SUCCEEDED
    assert store.health_snapshot(now=NOW + timedelta(seconds=601))["active_task_pairs"] == 0


def test_deadline_cleanup_failure_stays_open_and_retries_to_timed_out(tmp_path: Path) -> None:
    store = _store(tmp_path)
    job = _register(store, key="deadline-cleanup-retry")
    task = store.claim_next("evaluation-worker", now=NOW)
    assert task is not None
    store.succeed(
        task.task_id,
        "evaluation-worker",
        TaskResult(
            artifact_dir="runs/safe",
            report_path="runs/safe/report.md",
            off_resolved=True,
            on_resolved=True,
        ),
        now=NOW + timedelta(seconds=1),
    )
    runtime = FakeRuntime([], cleanups=(False, True))
    times = iter((NOW + timedelta(seconds=600), NOW + timedelta(seconds=606)))
    finalizer = TokensFlowFinalizer(store, runtime, clock=lambda: next(times), task_parallelism=10)

    assert finalizer.run_once() is True
    retryable = store.tokensflow_finalizations_for_attempt(job.attempt_id)[0]
    assert retryable.state.value == "cleanup_pending"
    assert retryable.error_category == "resource_removal_failed"
    assert retryable.reason == "deadline"
    assert [record.job_id for record in store.list_open_tokensflow_finalizations()] == [job.job_id]
    assert store.get(task.task_id).status is TaskStatus.SUCCEEDED
    assert store.health_snapshot(now=NOW + timedelta(seconds=601))["active_task_pairs"] == 0

    assert finalizer.run_once() is True
    finished = store.tokensflow_finalizations_for_attempt(job.attempt_id)[0]
    assert finished.state is FinalizationState.TIMED_OUT
    assert store.get(task.task_id).status is TaskStatus.SUCCEEDED


@pytest.mark.parametrize(("task_parallelism", "capacity"), [(1, 2), (4, 8), (10, 20)])
def test_capacity_is_twice_task_parallelism_and_evicts_all_excess_oldest_in_one_round(
    tmp_path: Path,
    task_parallelism: int,
    capacity: int,
) -> None:
    store = _store(tmp_path)
    jobs = [
        _register(store, key=f"capacity-{task_parallelism}-{index}", now=NOW + timedelta(seconds=index))
        for index in range(capacity + 3)
    ]
    runtime = FakeRuntime([], cleanups=(True, True, True))
    finalizer = TokensFlowFinalizer(
        store,
        runtime,
        clock=lambda: NOW + timedelta(seconds=capacity + 4),
        task_parallelism=task_parallelism,
    )

    assert finalizer.run_once() is True

    assert [store.tokensflow_finalizations_for_attempt(job.attempt_id)[0].state for job in jobs[:3]] == [
        FinalizationState.CAPACITY_EVICTED
    ] * 3
    assert [store.tokensflow_finalizations_for_attempt(job.attempt_id)[0].state for job in jobs[3:]] == [
        FinalizationState.PENDING
    ] * capacity
    assert set(runtime.cleanup_modes) == {(job.job_id, False) for job in jobs[:3]}
    assert len(store.list_open_tokensflow_finalizations()) == capacity


def test_capacity_evicts_oldest_registration_even_when_a_newer_job_has_an_earlier_deadline(tmp_path: Path) -> None:
    store = _store(tmp_path)
    oldest = _register(store, key="capacity-old-long", timeout_seconds=600)
    newer_urgent = _register(
        store,
        key="capacity-new-short",
        now=NOW + timedelta(seconds=1),
        timeout_seconds=60,
    )
    newest = _register(store, key="capacity-new-long", now=NOW + timedelta(seconds=2), timeout_seconds=600)
    runtime = FakeRuntime([], cleanups=(True,))
    finalizer = TokensFlowFinalizer(
        store,
        runtime,
        clock=lambda: NOW + timedelta(seconds=3),
        task_parallelism=1,
    )

    assert finalizer.run_once() is True

    assert store.tokensflow_finalizations_for_attempt(oldest.attempt_id)[0].state is (
        FinalizationState.CAPACITY_EVICTED
    )
    assert store.tokensflow_finalizations_for_attempt(newer_urgent.attempt_id)[0].state is FinalizationState.PENDING
    assert store.tokensflow_finalizations_for_attempt(newest.attempt_id)[0].state is FinalizationState.PENDING
    assert runtime.cleanup_modes == [(oldest.job_id, False)]


def test_capacity_skips_unclaimable_job_and_still_restores_bound_in_one_round(tmp_path: Path) -> None:
    store = _store(tmp_path)
    jobs = [_register(store, key=f"capacity-leased-{index}", now=NOW + timedelta(seconds=index)) for index in range(5)]
    leased = store.claim_tokensflow_finalization("other-finalizer", now=NOW, lease_seconds=300)
    assert leased is not None
    runtime = FakeRuntime([], cleanups=(True, True, True))
    finalizer = TokensFlowFinalizer(
        store,
        runtime,
        clock=lambda: NOW + timedelta(seconds=6),
        task_parallelism=1,
    )

    assert finalizer.run_once() is True

    assert store.tokensflow_finalizations_for_attempt(jobs[0].attempt_id)[0].state is FinalizationState.RUNNING
    assert [store.tokensflow_finalizations_for_attempt(job.attempt_id)[0].state for job in jobs[1:4]] == [
        FinalizationState.CAPACITY_EVICTED
    ] * 3
    assert store.tokensflow_finalizations_for_attempt(jobs[4].attempt_id)[0].state is FinalizationState.PENDING
    assert len(store.list_open_tokensflow_finalizations()) == 2


def test_capacity_cleanup_failure_stays_open_while_later_oldest_is_evicted(tmp_path: Path) -> None:
    store = _store(tmp_path)
    jobs = [
        _register(store, key=f"capacity-cleanup-retry-{index}", now=NOW + timedelta(seconds=index))
        for index in range(3)
    ]
    runtime = FakeRuntime([], cleanups=(False, True, True))
    times = iter(
        (
            NOW + timedelta(seconds=4),
            NOW + timedelta(seconds=4),
            NOW + timedelta(seconds=10),
            NOW + timedelta(seconds=10),
            NOW + timedelta(seconds=10),
        )
    )
    finalizer = TokensFlowFinalizer(store, runtime, clock=lambda: next(times), task_parallelism=1)

    assert finalizer.run_once() is True
    states = [store.tokensflow_finalizations_for_attempt(job.attempt_id)[0].state.value for job in jobs]
    assert states == ["cleanup_pending", "capacity_evicted", "pending"]
    assert len(store.list_open_tokensflow_finalizations()) == 2

    assert finalizer.run_once() is True
    assert store.tokensflow_finalizations_for_attempt(jobs[0].attempt_id)[0].state is (
        FinalizationState.CAPACITY_EVICTED
    )


def test_twenty_simultaneous_deadlines_enter_force_cleanup_in_one_drain(tmp_path: Path) -> None:
    store = _store(tmp_path)
    jobs = [_register(store, key=f"simultaneous-deadline-{index}") for index in range(20)]
    runtime = ConcurrentCleanupRuntime(concurrency=10)
    finalizer = TokensFlowFinalizer(
        store,
        runtime,
        clock=lambda: NOW + timedelta(seconds=600),
        task_parallelism=10,
    )

    assert finalizer.run_once() is True

    assert [store.tokensflow_finalizations_for_attempt(job.attempt_id)[0].state for job in jobs] == [
        FinalizationState.TIMED_OUT
    ] * 20
    assert {job_id for event, job_id in runtime.events if event == "cleanup"} == {job.job_id for job in jobs}
    assert runtime.max_active == 10


def test_failed_oldest_deadline_cleanup_does_not_block_later_deadlines(tmp_path: Path) -> None:
    store = _store(tmp_path)
    jobs = [_register(store, key=f"fair-deadline-{index}") for index in range(3)]
    runtime = FairRuntime(cleanup_fail_job_ids={jobs[0].job_id})
    finalizer = TokensFlowFinalizer(
        store,
        runtime,
        clock=lambda: NOW + timedelta(seconds=600),
        task_parallelism=2,
    )

    assert finalizer.run_once() is True

    assert [store.tokensflow_finalizations_for_attempt(job.attempt_id)[0].state for job in jobs] == [
        FinalizationState.CLEANUP_PENDING,
        FinalizationState.TIMED_OUT,
        FinalizationState.TIMED_OUT,
    ]
    assert {job_id for event, job_id in runtime.events if event == "cleanup"} == {job.job_id for job in jobs}


def test_capacity_twenty_fairly_attempts_later_jobs_after_oldest_warn(tmp_path: Path) -> None:
    store = _store(tmp_path)
    jobs = [_register(store, key=f"capacity-fair-{index}") for index in range(20)]
    runtime = FairRuntime(warn_job_ids={jobs[0].job_id})
    finalizer = TokensFlowFinalizer(
        store,
        runtime,
        clock=lambda: NOW + timedelta(seconds=1),
        task_parallelism=10,
    )

    assert finalizer.run_once() is True

    assert store.tokensflow_finalizations_for_attempt(jobs[0].attempt_id)[0].state is FinalizationState.PENDING
    assert [store.tokensflow_finalizations_for_attempt(job.attempt_id)[0].state for job in jobs[1:]] == [
        FinalizationState.PASSED
    ] * 19


def test_cleanup_deadline_priority_precedes_older_non_deadline_job(tmp_path: Path) -> None:
    store = _store(tmp_path)
    older = _register(store, key="priority-old", timeout_seconds=600)
    due = _register(store, key="priority-due", now=NOW + timedelta(seconds=1), timeout_seconds=100)
    runtime = FairRuntime(warn_job_ids={older.job_id})
    finalizer = TokensFlowFinalizer(
        store,
        runtime,
        clock=lambda: NOW + timedelta(seconds=200),
        task_parallelism=1,
    )

    assert finalizer.run_once() is True

    assert runtime.events[0] == ("cleanup", due.job_id)
    assert store.tokensflow_finalizations_for_attempt(due.attempt_id)[0].state is FinalizationState.TIMED_OUT
    assert store.tokensflow_finalizations_for_attempt(older.attempt_id)[0].state is FinalizationState.PENDING


class DeadlineBoundaryRuntime(FakeRuntime):
    def __init__(self) -> None:
        super().__init__([], cleanups=(True,))
        self.events: list[str] = []

    def quiesce(self, job: TokensFlowFinalizationRecord, *, timeout_seconds: float) -> bool:
        self.events.append("quiesce")
        return super().quiesce(job, timeout_seconds=timeout_seconds)

    def upload(self, job: TokensFlowFinalizationRecord, *, timeout_seconds: float) -> int:
        self.events.append("check")
        return super().upload(job, timeout_seconds=timeout_seconds)

    def doctor(self, job: TokensFlowFinalizationRecord, *, timeout_seconds: float) -> tuple[bytes, int]:
        del job, timeout_seconds
        return b"[PASS] queue: caught up (0 pending files)\n", 0

    def cleanup(self, job: TokensFlowFinalizationRecord, *, graceful: bool) -> bool:
        self.events.append("cleanup")
        return super().cleanup(job, graceful=graceful)


class BudgetedPhaseRuntime:
    def __init__(self) -> None:
        self.events: list[tuple[str, float | None]] = []

    def quiesce(self, job: TokensFlowFinalizationRecord, *, timeout_seconds: float) -> bool:
        del job
        self.events.append(("quiesce", timeout_seconds))
        return True

    def upload(self, job: TokensFlowFinalizationRecord, *, timeout_seconds: float) -> int:
        del job
        self.events.append(("upload", timeout_seconds))
        return 0

    def doctor(self, job: TokensFlowFinalizationRecord, *, timeout_seconds: float) -> tuple[bytes, int]:
        del job
        self.events.append(("doctor", timeout_seconds))
        return b"[PASS] queue: caught up (0 pending files)\n", 0

    def cleanup(self, job: TokensFlowFinalizationRecord, *, graceful: bool) -> bool:
        del job, graceful
        self.events.append(("cleanup", None))
        return True


class FailingBudgetedPhaseRuntime(BudgetedPhaseRuntime):
    def upload(self, job: TokensFlowFinalizationRecord, *, timeout_seconds: float) -> int:
        del job
        self.events.append(("upload_error", timeout_seconds))
        raise RuntimeError("private upload failure")


def test_near_deadline_forces_cleanup_without_starting_queue_commands(tmp_path: Path) -> None:
    store = _store(tmp_path)
    job = _register(store, key="deadline-command-budget")
    runtime = DeadlineBoundaryRuntime()
    finalizer = TokensFlowFinalizer(
        store,
        runtime,
        clock=lambda: NOW + timedelta(seconds=599),
        task_parallelism=10,
    )

    assert finalizer.run_once() is True

    assert runtime.events == ["cleanup"]
    assert store.tokensflow_finalizations_for_attempt(job.attempt_id)[0].state is FinalizationState.TIMED_OUT


def test_deadline_is_rechecked_after_quiesce_before_upload_or_doctor(tmp_path: Path) -> None:
    store = _store(tmp_path)
    job = _register(store, key="deadline-after-quiesce")
    runtime = DeadlineBoundaryRuntime()
    times = iter((NOW + timedelta(seconds=500), NOW + timedelta(seconds=599)))
    finalizer = TokensFlowFinalizer(store, runtime, clock=lambda: next(times), task_parallelism=10)

    assert finalizer.run_once() is True

    assert runtime.events == ["quiesce", "cleanup"]
    assert store.tokensflow_finalizations_for_attempt(job.attempt_id)[0].state is FinalizationState.TIMED_OUT


def test_remaining_budget_is_passed_to_each_phase_and_rechecked_before_doctor(tmp_path: Path) -> None:
    store = _store(tmp_path)
    job = _register(store, key="deadline-between-upload-doctor")
    runtime = BudgetedPhaseRuntime()
    times = iter(
        (
            NOW + timedelta(seconds=400),
            NOW + timedelta(seconds=500),
            NOW + timedelta(seconds=599),
        )
    )
    finalizer = TokensFlowFinalizer(store, runtime, clock=lambda: next(times), task_parallelism=10)

    assert finalizer.run_once() is True

    assert [event for event, _timeout in runtime.events] == ["quiesce", "upload", "cleanup"]
    assert runtime.events[0][1] == 10
    assert runtime.events[1][1] == 40
    assert store.tokensflow_finalizations_for_attempt(job.attempt_id)[0].state is FinalizationState.TIMED_OUT


def test_phase_exception_rechecks_deadline_before_deciding_to_retry(tmp_path: Path) -> None:
    store = _store(tmp_path)
    job = _register(store, key="deadline-upload-exception")
    runtime = FailingBudgetedPhaseRuntime()
    times = iter(
        (
            NOW + timedelta(seconds=400),
            NOW + timedelta(seconds=500),
            NOW + timedelta(seconds=599),
        )
    )
    finalizer = TokensFlowFinalizer(store, runtime, clock=lambda: next(times), task_parallelism=10)

    assert finalizer.run_once() is True

    assert [event for event, _timeout in runtime.events] == ["quiesce", "upload_error", "cleanup"]
    assert store.tokensflow_finalizations_for_attempt(job.attempt_id)[0].state is FinalizationState.TIMED_OUT


def test_cleanup_failure_retries_before_deadline_then_retains_health_alert(tmp_path: Path) -> None:
    store = _store(tmp_path)
    job = _register(store, key="cleanup-failure")
    runtime = FakeRuntime(
        [(b"[PASS] queue: caught up (0 pending files)\n", 0)],
        cleanups=(False, False),
    )
    times = iter((*(NOW + timedelta(seconds=1) for _ in range(4)), NOW + timedelta(seconds=600)))
    finalizer = TokensFlowFinalizer(store, runtime, clock=lambda: next(times), task_parallelism=10)

    assert finalizer.run_once() is True
    retry = store.tokensflow_finalizations_for_attempt(job.attempt_id)[0]
    assert retry.state is FinalizationState.PENDING
    assert retry.queue_passed is True
    assert retry.error_category == "cleanup_error"

    assert finalizer.run_once() is True
    failed = store.tokensflow_finalizations_for_attempt(job.attempt_id)[0]
    assert failed.state is FinalizationState.CLEANUP_PENDING
    assert failed.reason == "deadline"
    assert failed.error_category == "resource_removal_failed"


class RecordingRunner:
    def __init__(self) -> None:
        self.commands: list[tuple[str, ...]] = []

    def run(self, argv: Sequence[str], **kwargs: object) -> CommandResult:
        del kwargs
        command = tuple(argv)
        self.commands.append(command)
        stdout = "[PASS] queue: caught up (0 pending files)\n" if command[-1] == "doctor" else ""
        return CommandResult(command, "/safe", 0, stdout, "")


class LifecycleRunner:
    def __init__(self) -> None:
        self.events: list[str] = []
        self.daemon_stopped = False

    def run(self, argv: Sequence[str], **kwargs: object) -> CommandResult:
        del kwargs
        command = tuple(argv)
        if command[:2] == ("docker", "exec") and "/bin/sh" in command:
            self.events.append("stop")
            self.daemon_stopped = True
            return CommandResult(command, "/safe", 0, "", "")
        if command[-3:] == ("tokensflow", "upload", "--all"):
            self.events.append("upload")
            return CommandResult(command, "/safe", 0, "", "")
        if command[-2:] == ("tokensflow", "doctor"):
            self.events.append("doctor")
            result = CommandResult(command, "/safe", 0, "[PASS] queue: caught up (0 pending files)\n", "")
            if not self.daemon_stopped:
                self.events.append("late-daemon-write")
            return result
        if command[:3] == ("docker", "rm", "-f"):
            self.events.append("cleanup")
            return CommandResult(command, "/safe", 0, "", "")
        raise AssertionError(f"unexpected command: {command!r}")


def test_finalizer_quiesces_daemon_before_upload_doctor_and_cleanup(tmp_path: Path) -> None:
    store = _store(tmp_path)
    job = _register(store, key="quiesce-order")
    (tmp_path / job.runtime_path / "root-home").mkdir(parents=True)
    runner = LifecycleRunner()
    runtime = DockerFinalizationRuntime(tmp_path, runner=runner)
    finalizer = TokensFlowFinalizer(store, runtime, clock=lambda: NOW + timedelta(seconds=1), task_parallelism=10)

    assert finalizer.run_once() is True

    assert runner.events == ["stop", "upload", "doctor", "cleanup"]
    assert store.tokensflow_finalizations_for_attempt(job.attempt_id)[0].state is FinalizationState.PASSED


class AbsentContainerRunner:
    def __init__(self, container_name: str) -> None:
        self.container_name = container_name
        self.commands: list[tuple[str, ...]] = []

    def run(self, argv: Sequence[str], **kwargs: object) -> CommandResult:
        del kwargs
        command = tuple(argv)
        self.commands.append(command)
        stderr = (
            f"Error: No such object: {self.container_name}"
            if command[:3] == ("docker", "container", "inspect")
            else f"Error response from daemon: No such container: {self.container_name}"
        )
        return CommandResult(command, "/safe", 1, "", stderr)


class CleanupBudgetRunner:
    def __init__(self, container_name: str) -> None:
        self.container_name = container_name
        self.timeouts: list[float] = []

    def run(self, argv: Sequence[str], **kwargs: object) -> CommandResult:
        command = tuple(argv)
        timeout = kwargs.get("timeout")
        assert isinstance(timeout, (int, float))
        self.timeouts.append(float(timeout))
        if command[:3] == ("docker", "rm", "-f"):
            return CommandResult(command, "/safe", 1, "", "removal failed")
        if command[:3] == ("docker", "container", "inspect"):
            return CommandResult(command, "/safe", 1, "", f"Error: No such container: {self.container_name}")
        raise AssertionError(f"unexpected command: {command!r}")


def test_force_cleanup_commands_share_one_total_timeout_budget(tmp_path: Path) -> None:
    store = _store(tmp_path)
    job = _register(store, key="cleanup-command-budget")
    (tmp_path / job.runtime_path / "root-home").mkdir(parents=True)
    runner = CleanupBudgetRunner(job.container_name)
    monotonic_times = iter((0.0, 0.0, 20.0))
    runtime = DockerFinalizationRuntime(tmp_path, runner=runner, monotonic=lambda: next(monotonic_times))

    assert runtime.cleanup(job, graceful=False) is True

    assert runner.timeouts == [30.0, 10.0]


def test_finalizer_cleanup_removes_tokensflow_binary_snapshot_with_wrapper(tmp_path: Path) -> None:
    store = _store(tmp_path)
    job = _register(store, key="binary-snapshot-cleanup")
    runtime_path = tmp_path / job.runtime_path
    runtime_path.mkdir(parents=True)
    paths = SimpleNamespace(runtime=runtime_path)
    source = tmp_path / "tokensflow"
    source.write_bytes(b"immutable snapshot")
    source.chmod(0o755)
    DockerSut._stage_tokensflow_wrapper(paths)
    DockerSut._stage_tokensflow_binary(SimpleNamespace(tokensflow_binary=source), paths)
    (runtime_path / "root-home").mkdir()
    control = runtime_path.parent / "evaluation-control"

    runtime = DockerFinalizationRuntime(tmp_path, runner=LifecycleRunner())
    assert runtime.cleanup(job, graceful=False) is True

    assert not control.exists()


def test_restart_after_queue_pass_and_container_removal_finishes_idempotent_cleanup(tmp_path: Path) -> None:
    store = _store(tmp_path)
    job = _register(store, key="restart-after-remove")
    claimed = store.claim_tokensflow_finalization("crashed-finalizer", now=NOW, lease_seconds=30)
    assert claimed is not None
    store.record_tokensflow_finalization_check(
        job.job_id,
        "crashed-finalizer",
        queue_passed=True,
        doctor_rc=0,
        now=NOW,
    )
    store.release_tokensflow_finalization(job.job_id, "crashed-finalizer", now=NOW)
    private_home = tmp_path / job.runtime_path / "root-home"
    private_home.mkdir(parents=True)
    (private_home / "owned-state").write_text("remove me")
    runner = AbsentContainerRunner(job.container_name)
    runtime = DockerFinalizationRuntime(tmp_path, runner=runner)
    finalizer = TokensFlowFinalizer(store, runtime, clock=lambda: NOW + timedelta(seconds=1), task_parallelism=10)

    assert finalizer.run_once() is True

    persisted = store.tokensflow_finalizations_for_attempt(job.attempt_id)[0]
    assert persisted.state is FinalizationState.PASSED
    assert persisted.queue_passed is True
    assert not private_home.exists()
    assert ("docker", "container", "inspect", job.container_name) in runner.commands


def test_runtime_reuses_container_binary_home_environment_and_direct_egress(tmp_path: Path) -> None:
    store = _store(tmp_path)
    job = _register(store, key="dynamic")
    runner = RecordingRunner()
    runtime = DockerFinalizationRuntime(tmp_path, runner=runner)

    assert runtime.upload(job, timeout_seconds=60) == 0
    assert runtime.doctor(job, timeout_seconds=60) == (b"[PASS] queue: caught up (0 pending files)\n", 0)

    assert runner.commands == [
        ("docker", "exec", job.container_name, "tokensflow", "upload", "--all"),
        ("docker", "exec", job.container_name, "tokensflow", "doctor"),
    ]
    assert all(
        "HOME" not in command and not any(part.startswith("TOKENSFLOW_") for part in command)
        for command in runner.commands
    )


def test_public_summary_is_allowlisted_and_maps_running_to_pending(tmp_path: Path) -> None:
    store = _store(tmp_path)
    job = _register(store, key="public-summary")
    claimed = store.claim_tokensflow_finalization("finalizer", now=NOW, lease_seconds=30)
    assert claimed is not None

    summary = tokensflow_finalization_summary(claimed)
    payload = summary.model_dump(mode="json")

    assert payload == {
        "state": "pending",
        "registered_at": "2026-08-03T01:02:03Z",
        "deadline_at": "2026-08-03T01:12:03Z",
        "finished_at": None,
        "attempts": 1,
        "queue_passed": False,
        "doctor_rc": None,
        "error_category": None,
        "reason": None,
    }
    serialized = str(payload).casefold()
    for forbidden in ("container", "path", "home", "env", "credential", "token", "doctor_output"):
        assert forbidden not in serialized
    assert job.evidence_sha256 not in serialized


def test_run_forever_recovers_from_one_exception_without_logging_its_message(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    finalizer = TokensFlowFinalizer(
        _store(tmp_path),
        FakeRuntime([]),
        task_parallelism=1,
    )
    stop = threading.Event()
    second_call_entered = threading.Event()
    release_second_call = threading.Event()
    calls = 0

    def flaky_run_once(*, stop: threading.Event | None = None) -> bool:
        nonlocal calls
        del stop
        calls += 1
        if calls == 1:
            raise RuntimeError("secret exception detail")
        second_call_entered.set()
        assert release_second_call.wait(timeout=2)
        return False

    monkeypatch.setattr(finalizer, "run_once", flaky_run_once)
    with caplog.at_level(logging.WARNING):
        thread = threading.Thread(target=finalizer.run_forever, args=(stop, 0.001))
        thread.start()
        assert second_call_entered.wait(timeout=2)
        assert thread.is_alive()
        stop.set()
        release_second_call.set()
        thread.join(timeout=2)

    assert not thread.is_alive()
    assert calls == 2
    assert "RuntimeError" in caplog.text
    assert "secret exception detail" not in caplog.text


@pytest.mark.parametrize("phase", ["upload", "doctor"])
def test_stop_cancels_blocked_phase_and_releases_job_for_immediate_reclaim(tmp_path: Path, phase: str) -> None:
    store = _store(tmp_path)
    registered = _register(store, key=f"shutdown-{phase}")
    stop = threading.Event()
    runtime = BlockingPhaseRuntime(phase, stop)
    finalizer = TokensFlowFinalizer(
        store,
        runtime,
        clock=lambda: NOW + timedelta(seconds=1),
        task_parallelism=1,
        worker_id="stopping-finalizer",
    )
    supervisor = threading.Thread(target=finalizer.run_forever, args=(stop, 60))
    supervisor.start()
    try:
        assert runtime.entered.wait(timeout=2)
        started = time.monotonic()
        stop.set()
        supervisor.join(timeout=1)

        assert not supervisor.is_alive()
        assert time.monotonic() - started < 1
        pending = store.tokensflow_finalizations_for_attempt(registered.attempt_id)[0]
        assert pending.state is FinalizationState.PENDING
        assert pending.lease_owner is None
        assert pending.lease_expires_at is None
        assert pending.error_category == "shutdown"
        reclaimed = store.claim_tokensflow_finalization(
            "replacement-finalizer",
            now=NOW + timedelta(seconds=2),
            lease_seconds=300,
            job_id=registered.job_id,
        )
        assert reclaimed is not None
        assert reclaimed.lease_owner == "replacement-finalizer"
    finally:
        stop.set()
        supervisor.join(timeout=2)


def test_run_forever_drains_processed_jobs_without_fixed_sleep(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    finalizer = TokensFlowFinalizer(
        _store(tmp_path),
        FakeRuntime([]),
        task_parallelism=1,
    )
    stop = threading.Event()
    results = iter((True, True, False))
    calls = 0
    waits: list[float] = []

    def run_once(*, stop: threading.Event | None = None) -> bool:
        nonlocal calls
        del stop
        calls += 1
        return next(results)

    def wait(timeout: float) -> bool:
        waits.append(timeout)
        stop.set()
        return True

    monkeypatch.setattr(finalizer, "run_once", run_once)
    monkeypatch.setattr(stop, "wait", wait)

    finalizer.run_forever(stop, 5.0)

    assert calls == 3
    assert waits == [5.0]


class FakeFinalizationClock:
    def __init__(self, now: datetime) -> None:
        self._now = now
        self._lock = threading.Lock()

    def __call__(self) -> datetime:
        with self._lock:
            return self._now

    def advance(self, seconds: float) -> None:
        with self._lock:
            self._now += timedelta(seconds=seconds)


class TwoWaveCleanupRuntime(FairRuntime):
    def __init__(self, clock: FakeFinalizationClock, wave_size: int) -> None:
        super().__init__()
        self._wave_barrier = threading.Barrier(wave_size, action=lambda: clock.advance(30))

    def cleanup(self, job: TokensFlowFinalizationRecord, *, graceful: bool) -> bool:
        self._wave_barrier.wait(timeout=2)
        return super().cleanup(job, graceful=graceful)


def test_restarted_finalizer_takes_over_stale_running_jobs_at_cleanup_threshold(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _store(tmp_path)
    jobs = [_register(store, key=f"restart-deadline-{index}") for index in range(20)]
    crash_clock = FakeFinalizationClock(NOW + timedelta(seconds=500))
    for job in jobs:
        claimed = store.claim_tokensflow_finalization(
            "crashed-finalizer",
            now=crash_clock(),
            lease_seconds=300,
            job_id=job.job_id,
        )
        assert claimed is not None

    reopened = TaskStore(tmp_path / "tasks.sqlite3", lease_duration=timedelta(seconds=60))
    reopened.initialize()
    assert [reopened.tokensflow_finalizations_for_attempt(job.attempt_id)[0].state for job in jobs] == [
        FinalizationState.RUNNING
    ] * 20
    assert (
        reopened.claim_tokensflow_finalization(
            "ordinary-second-finalizer",
            now=NOW + timedelta(seconds=540),
            lease_seconds=300,
            job_id=jobs[0].job_id,
        )
        is None
    )

    clock = FakeFinalizationClock(NOW + timedelta(seconds=539))
    runtime = TwoWaveCleanupRuntime(clock, wave_size=10)
    recovered = TokensFlowFinalizer(
        reopened,
        runtime,
        clock=clock,
        task_parallelism=10,
        worker_id="restarted-finalizer",
    )
    stop = threading.Event()
    waits: list[float] = []

    def wait(timeout: float) -> bool:
        waits.append(timeout)
        if not reopened.list_open_tokensflow_finalizations():
            stop.set()
            return True
        if len(waits) == 1 and timeout != 1:
            stop.set()
            return True
        clock.advance(timeout)
        return False

    monkeypatch.setattr(stop, "wait", wait)

    recovered.run_forever(stop, poll_seconds=60)

    assert waits == [1.0, 60]
    assert clock() == NOW + timedelta(seconds=600)
    assert [reopened.tokensflow_finalizations_for_attempt(job.attempt_id)[0].state for job in jobs] == [
        FinalizationState.TIMED_OUT
    ] * 20
    assert {job_id for event, job_id in runtime.events if event == "cleanup"} == {job.job_id for job in jobs}


def test_run_forever_wakes_at_cleanup_threshold_before_poll_jitter_for_two_cleanup_waves(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _store(tmp_path)
    jobs = [_register(store, key=f"poll-deadline-{index}") for index in range(20)]
    clock = FakeFinalizationClock(NOW + timedelta(seconds=539))
    runtime = TwoWaveCleanupRuntime(clock, wave_size=10)
    runtime.warn_job_ids = {job.job_id for job in jobs}
    finalizer = TokensFlowFinalizer(store, runtime, clock=clock, task_parallelism=10)
    stop = threading.Event()
    waits: list[float] = []

    def wait(timeout: float) -> bool:
        waits.append(timeout)
        if not store.list_open_tokensflow_finalizations():
            stop.set()
            return True
        clock.advance(timeout)
        return False

    monkeypatch.setattr(stop, "wait", wait)

    finalizer.run_forever(stop, poll_seconds=60)

    assert waits == [1.0, 60]
    assert clock() == NOW + timedelta(seconds=600)
    assert [store.tokensflow_finalizations_for_attempt(job.attempt_id)[0].state for job in jobs] == [
        FinalizationState.TIMED_OUT
    ] * 20
    assert {job_id for event, job_id in runtime.events if event == "cleanup"} == {job.job_id for job in jobs}


def test_run_forever_does_not_swallow_base_exception(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    finalizer = TokensFlowFinalizer(
        _store(tmp_path),
        FakeRuntime([]),
        task_parallelism=1,
    )

    def interrupt(*, stop: threading.Event | None = None) -> bool:
        del stop
        raise KeyboardInterrupt

    monkeypatch.setattr(finalizer, "run_once", interrupt)

    with pytest.raises(KeyboardInterrupt):
        finalizer.run_forever(threading.Event(), 0.001)
