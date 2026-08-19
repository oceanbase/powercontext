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

import sqlite3
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from powercontext_eval.benchmarks.base import GoldCheckFailed
from powercontext_eval.benchmarks.swebench_pro.adapter import DatasetSchemaError, SweBenchProInstance
from powercontext_eval.benchmarks.swebench_pro.evaluator import OfficialResultError
from powercontext_eval.codex import CodexCapacityError, CodexInfrastructureError
from powercontext_eval.errors import CommandFailed, GitSourceError
from powercontext_eval.models import Arm
from powercontext_eval.powercontext_sut import (
    InvalidTreatment,
    PluginInspectionFailure,
    PluginInspectionFailureReason,
    ReadinessFailure,
    ReadinessFailureReason,
    UnsafeSutConfiguration,
)
from powercontext_eval.process import CommandResult
from powercontext_eval.runner import MinimalRunConfig, MinimalRunResult, RunConfig, RunPhase
from powercontext_eval.tokensflow import TokensFlowFinalizationDescriptor, TokensFlowInfrastructureError
from powercontext_eval.web.batches import BatchControlEventType, BatchCreate, BatchStatus
from powercontext_eval.web.config import WebConfig
from powercontext_eval.web.controls import BatchControlIntent, BatchPauseReason
from powercontext_eval.web.models import (
    FailureCategory,
    FailureCode,
    RetryDisposition,
    SafeFailure,
    TaskCreate,
    TaskPhase,
    TaskStatus,
)
from powercontext_eval.web.resources import FilesystemCapacity, FilesystemResourceProbe
from powercontext_eval.web.revision import RUNTIME_SCHEMA_VERSION, current_build_revision
from powercontext_eval.web.store import TaskOwnershipError, TaskStore
from powercontext_eval.web.usage import CodexUsageProbe, UsageSnapshot, UsageUnavailable
from powercontext_eval.web.worker import EvaluationWorker, TaskPairWorker

NOW = datetime(2026, 7, 29, 1, 2, 3, tzinfo=UTC)


def _usage(used_percent: int, *, observed_at: datetime = NOW) -> UsageSnapshot:
    return UsageSnapshot(
        limit_id="codex",
        used_percent=used_percent,
        remaining_percent=100 - used_percent,
        window_duration_minutes=10_080,
        resets_at=NOW + timedelta(days=7),
        observed_at=observed_at,
        plan_type="pro",
        account_tokens=1_234,
    )


class FakeUsageProbe:
    def __init__(self, observations: list[UsageSnapshot | Exception]) -> None:
        self.observations = observations
        self.calls: list[datetime] = []

    def read(self, *, now: datetime) -> UsageSnapshot:
        self.calls.append(now)
        observation = self.observations.pop(0)
        if isinstance(observation, Exception):
            raise observation
        return observation.model_copy(update={"observed_at": now})


@pytest.fixture(autouse=True)
def _default_safe_usage_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        CodexUsageProbe,
        "read",
        lambda _self, *, now: _usage(9, observed_at=now),
    )
    monkeypatch.setattr(
        FilesystemResourceProbe,
        "read",
        lambda _self: FilesystemCapacity(
            free_bytes=1024**5,
            total_bytes=2 * 1024**5,
            free_inodes=100_000_000,
            total_inodes=200_000_000,
        ),
    )
    monkeypatch.setattr("powercontext_eval.web.resources.DockerDependencyProbe.check", lambda _self: None)


def _config(
    root: Path,
    *,
    lease_seconds: int = 2,
    poll_seconds: float = 0.01,
    usage_probe_seconds: int = 60,
    task_parallelism: int = 1,
    max_attempts: int = 5,
) -> WebConfig:
    return WebConfig.for_root(
        root,
        max_attempts=max_attempts,
        tokensflow_enabled=True,
        tokensflow_egress_network="bridge",
        run_root=root / "artifacts",
        powercontext_source=root / "source",
        harness_root=root / "harness",
        harness_python=root / "venv/bin/python",
        raw_sample_path=root / "sample.jsonl",
        codex_binary=root / "bin/codex",
        tokensflow_binary=root / "bin/tokensflow",
        tokensflow_user_home=root / "tokensflow-profile",
        uv_binary=root / "bin/uv",
        auth_json=root / "codex/auth.json",
        proxy_url="http://127.0.0.1:18080",
        lease_seconds=lease_seconds,
        poll_seconds=poll_seconds,
        usage_probe_seconds=usage_probe_seconds,
        task_parallelism=task_parallelism,
    )


def _store(config: WebConfig) -> TaskStore:
    store = TaskStore(
        config.database_path,
        lease_duration=timedelta(seconds=config.lease_seconds),
        max_attempts=config.max_attempts,
    )
    store.initialize()
    store.record_runtime_revision(
        "web",
        build_revision=current_build_revision(),
        schema_version=RUNTIME_SCHEMA_VERSION,
        now=NOW,
    )
    return store


def _create(store: TaskStore, *, key: str = "worker-test", now: datetime = NOW) -> Any:
    return store.create(
        TaskCreate(
            powercontext_ref="commit:" + "a" * 40,
            benchmark="swebench-pro",
            instance_id="instance_flipt-io__flipt-518ec324b66a07fdd95464a5e9ca5fe7681ad8f9",
            model="gpt-5.6-sol",
            reasoning_effort="medium",
            treatment_mode="off_on",
            idempotency_key=key,
        ),
        now=now,
    )[0]


def _create_batch(
    store: TaskStore,
    *,
    key: str = "batch-worker-test",
    instance_ids: tuple[str, ...] = ("instance_owner__repo-a", "instance_owner__repo-b"),
    model: str = "gpt-5.6-sol",
) -> Any:
    return store.create_batch(
        BatchCreate(
            powercontext_ref="latest",
            benchmark="swebench-pro",
            task_set="swebench-pro-public-v2",
            model=model,
            reasoning_effort="medium",
            treatment_mode="off_on",
            idempotency_key=key,
        ),
        instance_ids,
        now=NOW,
    )[0]


class FakeCatalog:
    def __init__(self, instance_ids: tuple[str, ...]) -> None:
        self.instances = {instance_id: _fake_instance(instance_id) for instance_id in instance_ids}

    def require(self, instance_id: str) -> SweBenchProInstance:
        return self.instances[instance_id]


class FakeSource:
    def __init__(self, sha: str = "c" * 40) -> None:
        self.sha = sha
        self.resolve_calls: list[tuple[object, object]] = []

    def resolve(self, source: object, requested: object) -> object:
        self.resolve_calls.append((source, requested))
        return SimpleNamespace(sha=self.sha)


def _successful_batch_runner(
    config: WebConfig,
    calls: list[tuple[RunConfig, SweBenchProInstance]],
) -> Callable[..., MinimalRunResult]:
    def runner(
        run_config: RunConfig,
        *,
        instance: SweBenchProInstance,
        on_phase: Any,
    ) -> MinimalRunResult:
        del on_phase
        calls.append((run_config, instance))
        run_dir = config.run_root / "runs" / run_config.run_id
        run_dir.mkdir(parents=True)
        report_path = run_dir / "report.md"
        report_path.write_text("safe")
        return MinimalRunResult(run_config.run_id, report_path, True, False)

    return runner


def _fake_instance(instance_id: str) -> SweBenchProInstance:
    return SweBenchProInstance(
        repo="owner/repo",
        instance_id=instance_id,
        base_commit="a" * 40,
        patch="",
        test_patch="",
        problem_statement="problem",
        fail_to_pass=(),
        pass_to_pass=(),
        before_repo_set_cmd="",
        selected_test_files_to_run="",
        task_image="docker.io/example/task:latest",
        raw_row={},
    )


def _unexpected_runner(calls: list[str]) -> Callable[..., MinimalRunResult]:
    def runner(run_config: MinimalRunConfig | RunConfig, **_kwargs: Any) -> MinimalRunResult:
        assert run_config.run_id is not None
        calls.append(run_config.run_id)
        pytest.fail("runner should not run")

    return runner


def test_worker_usage_threshold_blocks_claim_without_pausing_batch(tmp_path: Path) -> None:
    config = _config(tmp_path)
    store = _store(config)
    batch = _create_batch(store, instance_ids=("instance_owner__repo-a",))
    calls: list[str] = []
    worker = EvaluationWorker(
        config,
        store,
        usage_probe=FakeUsageProbe([_usage(80)]),
        runner=_unexpected_runner(calls),
        source=FakeSource(),
        catalog=FakeCatalog(("instance_owner__repo-a",)),
        clock=lambda: NOW,
    )

    assert worker.run_once() is False
    assert calls == []
    current = store.get_batch(batch.batch_id)
    assert current.status is BatchStatus.QUEUED
    assert current.control.intent is BatchControlIntent.RUN
    assert current.control.pause_reason is None
    assert store.latest_usage_snapshot() == _usage(80)


def test_worker_reuses_usage_until_the_snapshot_max_age_expires(tmp_path: Path) -> None:
    config = _config(tmp_path, usage_probe_seconds=60)
    store = _store(config)
    store.save_usage_snapshot(_usage(10, observed_at=NOW))
    probe = FakeUsageProbe([_usage(11), _usage(12)])
    observations = iter(
        (
            NOW + timedelta(seconds=119),
            NOW + timedelta(seconds=119),
            NOW + timedelta(seconds=119),
            NOW + timedelta(seconds=121),
            NOW + timedelta(seconds=121),
            NOW + timedelta(seconds=121),
        )
    )
    worker = EvaluationWorker(
        config,
        store,
        usage_probe=probe,
        clock=lambda: next(observations),
    )

    assert worker.run_once() is False
    assert probe.calls == []

    assert worker.run_once() is False
    assert probe.calls == [NOW + timedelta(seconds=121)]
    assert store.latest_usage_snapshot() == _usage(11, observed_at=NOW + timedelta(seconds=121))


def test_worker_finishes_current_task_before_honoring_user_pause(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    store = _store(config)
    batch = _create_batch(store)
    calls: list[str] = []

    def runner(run_config: Any, *, instance: object, on_phase: Any) -> MinimalRunResult:
        del instance, on_phase
        calls.append(run_config.run_id)
        store.request_pause(batch.batch_id, reason=BatchPauseReason.USER, now=NOW)
        report = config.run_root / "runs" / run_config.run_id / "report.md"
        report.parent.mkdir(parents=True)
        report.write_text("safe")
        return MinimalRunResult(run_config.run_id, report, True, True)

    monkeypatch.setattr("powercontext_eval.web.worker.load_report", lambda *args: object())
    worker = EvaluationWorker(
        config,
        store,
        usage_probe=FakeUsageProbe([_usage(9), _usage(10)]),
        runner=runner,
        source=FakeSource(),
        catalog=FakeCatalog(("instance_owner__repo-a", "instance_owner__repo-b")),
        clock=lambda: NOW,
    )

    assert worker.run_once() is True

    assert len(calls) == 1
    assert [task.status for task in store.list_batch_tasks(batch.batch_id)] == [
        TaskStatus.SUCCEEDED,
        TaskStatus.QUEUED,
    ]
    assert store.get_batch(batch.batch_id).status is BatchStatus.PAUSED


def test_worker_finishes_current_task_before_cancelling_remaining_tasks(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    store = _store(config)
    batch = _create_batch(store)

    def runner(run_config: Any, *, instance: object, on_phase: Any) -> MinimalRunResult:
        del instance, on_phase
        store.request_cancel(batch.batch_id, now=NOW)
        report = config.run_root / "runs" / run_config.run_id / "report.md"
        report.parent.mkdir(parents=True)
        report.write_text("safe")
        return MinimalRunResult(run_config.run_id, report, False, False)

    monkeypatch.setattr("powercontext_eval.web.worker.load_report", lambda *args: object())
    worker = EvaluationWorker(
        config,
        store,
        usage_probe=FakeUsageProbe([_usage(9), _usage(10)]),
        runner=runner,
        source=FakeSource(),
        catalog=FakeCatalog(("instance_owner__repo-a", "instance_owner__repo-b")),
        clock=lambda: NOW,
    )

    assert worker.run_once() is True

    assert [task.status for task in store.list_batch_tasks(batch.batch_id)] == [
        TaskStatus.SUCCEEDED,
        TaskStatus.CANCELLED,
    ]
    assert store.get_batch(batch.batch_id).status is BatchStatus.CANCELLED


def test_worker_skips_paused_oldest_batch_and_claims_next_runnable_batch(tmp_path: Path) -> None:
    config = _config(tmp_path)
    store = _store(config)
    oldest = _create_batch(
        store,
        key="batch-paused-oldest",
        instance_ids=("instance_owner__repo-a",),
    )
    runnable = _create_batch(
        store,
        key="batch-runnable-next",
        instance_ids=("instance_owner__repo-b",),
    )
    store.request_pause(oldest.batch_id, reason=BatchPauseReason.USER, now=NOW)
    calls: list[str] = []

    def runner(run_config: Any, *, instance: object, on_phase: Any) -> MinimalRunResult:
        del instance, on_phase
        calls.append(run_config.run_id)
        raise RuntimeError("stop after claim")

    worker = EvaluationWorker(
        config,
        store,
        usage_probe=FakeUsageProbe([_usage(9), _usage(10)]),
        runner=runner,
        source=FakeSource(),
        catalog=FakeCatalog(("instance_owner__repo-a", "instance_owner__repo-b")),
        clock=lambda: NOW,
    )

    assert worker.run_once() is True
    assert calls == [store.list_batch_tasks(runnable.batch_id)[0].task_id]
    assert store.list_batch_tasks(oldest.batch_id)[0].status is TaskStatus.QUEUED


def test_worker_usage_unavailable_blocks_claim_without_pausing_batch(tmp_path: Path) -> None:
    config = _config(tmp_path)
    store = _store(config)
    batch = _create_batch(store, instance_ids=("instance_owner__repo-a",))
    calls: list[str] = []
    worker = EvaluationWorker(
        config,
        store,
        usage_probe=FakeUsageProbe([UsageUnavailable("private failure")]),
        runner=_unexpected_runner(calls),
        source=FakeSource(),
        catalog=FakeCatalog(("instance_owner__repo-a",)),
        clock=lambda: NOW,
    )

    assert worker.run_once() is False

    assert calls == []
    current = store.get_batch(batch.batch_id)
    assert current.status is BatchStatus.QUEUED
    assert current.control.pause_reason is None


def test_worker_executes_only_the_new_attempt_when_a_failed_task_is_retried(tmp_path: Path) -> None:
    config = _config(tmp_path)
    store = _store(config)
    batch = _create_batch(store, instance_ids=("instance_owner__repo-a",))
    task = store.list_batch_tasks(batch.batch_id)[0]
    claimed = store.claim_next("setup-worker", now=NOW)
    assert claimed is not None
    store.fail(
        task.task_id,
        "setup-worker",
        SafeFailure(
            category=FailureCategory.CODEX_EXECUTION,
            phase=TaskPhase.RUNNING_OFF,
            summary="First attempt failed",
        ),
        now=NOW,
    )
    candidate = store.list_attempt_cleanup_candidates(limit=1, now=NOW)[0]
    store.mark_attempt_evidence_exported(candidate.attempt_id)
    assert store.complete_attempt_cleanup_and_schedule_retry(candidate.attempt_id, now=NOW) is True
    retry = store.list_task_attempts(batch.batch_id, task.task_id)[-1]
    store.request_resume(batch.batch_id, now=NOW)
    calls: list[str] = []

    def runner(run_config: Any, *, instance: object, on_phase: Any) -> MinimalRunResult:
        del instance, on_phase
        calls.append(run_config.run_id)
        raise CodexInfrastructureError("retry failed")

    worker = EvaluationWorker(
        config,
        store,
        usage_probe=FakeUsageProbe([_usage(9), _usage(10)]),
        runner=runner,
        source=FakeSource(),
        catalog=FakeCatalog(("instance_owner__repo-a",)),
        clock=lambda: NOW + timedelta(seconds=31),
    )

    assert worker.run_once() is True

    assert retry.attempt_id == f"{task.task_id}.attempt-0002"
    assert calls == [f"{task.task_id}-attempt-0002"]
    attempts = store.list_task_attempts(batch.batch_id, task.task_id)
    assert [attempt.status for attempt in attempts] == [TaskStatus.FAILED, TaskStatus.FAILED, TaskStatus.QUEUED]
    assert attempts[0].failure_summary == "First attempt failed"


def test_latest_is_pinned_once_and_every_child_uses_catalog_instance(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = _config(tmp_path)
    store = _store(config)
    instance_ids = ("instance_owner__repo-a", "instance_owner__repo-b")
    batch = _create_batch(store, instance_ids=instance_ids)
    source = FakeSource()
    catalog = FakeCatalog(instance_ids)
    calls: list[tuple[RunConfig, SweBenchProInstance]] = []
    monkeypatch.setattr("powercontext_eval.web.worker.load_report", lambda *args: object())
    worker = EvaluationWorker(
        config,
        store,
        runner=_successful_batch_runner(config, calls),
        source=source,
        catalog=catalog,
        clock=lambda: NOW,
    )

    assert worker.run_once() is True
    assert worker.run_once() is True

    assert len(source.resolve_calls) == 1
    assert [call[0].powercontext_ref for call in calls] == ["commit:" + source.sha, "commit:" + source.sha]
    assert [call[1] for call in calls] == [catalog.instances[instance_id] for instance_id in instance_ids]
    persisted = store.get_batch(batch.batch_id)
    assert persisted.resolved_powercontext_sha == source.sha
    assert persisted.status is BatchStatus.COMPLETED


def test_worker_uses_each_batch_immutable_model_for_runner_configuration(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = _config(tmp_path)
    store = _store(config)
    batch = _create_batch(
        store,
        key="luna-worker-model",
        instance_ids=("instance_owner__repo-luna",),
        model="gpt-5.6-luna",
    )
    calls: list[tuple[RunConfig, SweBenchProInstance]] = []
    monkeypatch.setattr("powercontext_eval.web.worker.load_report", lambda *args: object())
    worker = EvaluationWorker(
        config,
        store,
        runner=_successful_batch_runner(config, calls),
        source=FakeSource(),
        catalog=FakeCatalog(("instance_owner__repo-luna",)),
        clock=lambda: NOW,
    )

    assert worker.run_once() is True
    assert calls[0][0].model == batch.request.model == "gpt-5.6-luna"
    assert calls[0][0].reasoning_effort == batch.request.reasoning_effort == "medium"


def test_only_one_child_runs_physically_across_multiple_batches(tmp_path: Path) -> None:
    config = _config(tmp_path)
    store = _store(config)
    first_batch = _create_batch(
        store,
        key="batch-global-first",
        instance_ids=("instance_owner__repo-a",),
    )
    _create_batch(
        store,
        key="batch-global-second",
        instance_ids=("instance_owner__repo-b",),
    )
    catalog = FakeCatalog(("instance_owner__repo-a", "instance_owner__repo-b"))
    entered = threading.Event()
    release = threading.Event()
    calls: list[str] = []

    def runner(run_config: Any, *, instance: object, on_phase: Any) -> MinimalRunResult:
        calls.append(run_config.run_id)
        entered.set()
        release.wait(timeout=2)
        raise RuntimeError("stop")

    first = EvaluationWorker(
        config,
        store,
        runner=runner,
        source=FakeSource(),
        catalog=catalog,
        worker_id="first",
    )
    second = EvaluationWorker(
        config,
        store,
        runner=runner,
        source=FakeSource(),
        catalog=catalog,
        worker_id="second",
    )
    thread = threading.Thread(target=first.run_once)
    thread.start()
    assert entered.wait(timeout=2)

    assert second.run_once() is False

    release.set()
    thread.join(timeout=2)
    assert calls == [store.list_batch_tasks(first_batch.batch_id)[0].task_id]


def test_failed_batch_child_does_not_block_later_children(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    config = _config(tmp_path)
    store = _store(config)
    instance_ids = ("instance_owner__repo-a", "instance_owner__repo-b")
    batch = _create_batch(store, instance_ids=instance_ids)
    catalog = FakeCatalog(instance_ids)
    calls: list[str] = []

    def runner(run_config: Any, *, instance: object, on_phase: Any) -> MinimalRunResult:
        calls.append(run_config.run_id)
        if len(calls) == 1:
            raise CodexInfrastructureError("first child failed")
        run_dir = config.run_root / "runs" / run_config.run_id
        run_dir.mkdir(parents=True)
        report = run_dir / "report.md"
        report.write_text("safe")
        return MinimalRunResult(run_config.run_id, report, True, True)

    monkeypatch.setattr("powercontext_eval.web.worker.load_report", lambda *args: object())
    worker = EvaluationWorker(
        config,
        store,
        runner=runner,
        source=FakeSource(),
        catalog=catalog,
        clock=lambda: NOW,
    )

    assert worker.run_once() is True
    assert worker.run_once() is True

    children = store.list_batch_tasks(batch.batch_id)
    assert [child.status for child in children] == [TaskStatus.QUEUED, TaskStatus.SUCCEEDED]
    current = store.get_batch(batch.batch_id)
    assert current.status is BatchStatus.RUNNING
    assert current.control.intent is BatchControlIntent.RUN
    assert current.control.pause_reason is None


def _capacity_worker(
    config: WebConfig,
    store: TaskStore,
    instance_ids: tuple[str, ...],
    runner: Callable[..., MinimalRunResult],
) -> EvaluationWorker:
    return EvaluationWorker(
        config,
        store,
        runner=runner,
        source=FakeSource(),
        catalog=FakeCatalog(instance_ids),
        clock=lambda: NOW,
    )


def test_upstream_capacity_failure_requeues_the_child_without_pausing_the_batch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    store = _store(config)
    instance_ids = ("instance_owner__repo-a", "instance_owner__repo-b")
    batch = _create_batch(store, instance_ids=instance_ids)
    calls: list[str] = []

    def runner(run_config: Any, *, instance: object, on_phase: Any) -> MinimalRunResult:
        calls.append(run_config.run_id)
        if len(calls) == 1:
            on_phase(RunPhase.RUNNING_OFF)
            raise CodexCapacityError("upstream pool saturated")
        run_dir = config.run_root / "runs" / run_config.run_id
        run_dir.mkdir(parents=True)
        report = run_dir / "report.md"
        report.write_text("safe")
        return MinimalRunResult(run_config.run_id, report, True, True)

    monkeypatch.setattr("powercontext_eval.web.worker.load_report", lambda *args: object())
    worker = _capacity_worker(config, store, instance_ids, runner)

    assert worker.run_once() is True

    current = store.get_batch(batch.batch_id)
    assert current.control.intent is BatchControlIntent.RUN
    assert current.control.pause_reason is None
    requeued = store.list_batch_tasks(batch.batch_id)[0]
    assert requeued.status is TaskStatus.QUEUED
    assert requeued.attempt_number == 2

    retry_events = [
        event
        for event in store.list_control_events(batch.batch_id)
        if event.event_type is BatchControlEventType.TASK_RETRY_REQUESTED
    ]
    assert [event.actor for event in retry_events] == ["system"]
    assert retry_events[0].details["reason"] == "codex_capacity"

    assert worker.run_once() is True
    children = store.list_batch_tasks(batch.batch_id)
    assert [child.status for child in children] == [TaskStatus.QUEUED, TaskStatus.SUCCEEDED]
    retry_worker = EvaluationWorker(
        config,
        store,
        runner=runner,
        source=FakeSource(),
        catalog=FakeCatalog(instance_ids),
        clock=lambda: NOW + timedelta(seconds=31),
    )
    assert retry_worker.run_once() is True
    assert store.list_batch_tasks(batch.batch_id)[0].status is TaskStatus.SUCCEEDED
    assert calls[2].endswith("-attempt-0002")


def test_capacity_failures_exhaust_budget_without_pausing_or_blocking_siblings(tmp_path: Path) -> None:
    config = _config(tmp_path, max_attempts=1)
    store = _store(config)
    instance_ids = ("instance_owner__repo-a", "instance_owner__repo-b")
    batch = _create_batch(store, instance_ids=instance_ids)

    def runner(run_config: Any, *, instance: object, on_phase: Any) -> MinimalRunResult:
        on_phase(RunPhase.RUNNING_OFF)
        raise CodexCapacityError("upstream pool saturated")

    worker = _capacity_worker(config, store, instance_ids, runner)

    assert worker.run_once() is True
    assert store.get_batch(batch.batch_id).control.intent is BatchControlIntent.RUN

    assert worker.run_once() is True

    failed = store.list_batch_tasks(batch.batch_id)
    assert [task.status for task in failed] == [TaskStatus.FAILED, TaskStatus.FAILED]
    assert all(task.failure_category is FailureCategory.CODEX_CAPACITY for task in failed)
    assert all(task.retryable is False for task in failed)
    current = store.get_batch(batch.batch_id)
    assert current.status is BatchStatus.COMPLETED
    assert current.control.intent is BatchControlIntent.RUN
    assert current.control.pause_reason is None

    for task in failed:
        attempts = store.list_task_attempts(batch.batch_id, task.task_id)
        assert [attempt.attempt_number for attempt in attempts] == [1]
        assert attempts[0].failure_category is FailureCategory.CODEX_CAPACITY


def test_capacity_failure_never_resumes_a_batch_the_user_paused(tmp_path: Path) -> None:
    config = _config(tmp_path)
    store = _store(config)
    instance_ids = ("instance_owner__repo-a",)
    batch = _create_batch(store, instance_ids=instance_ids)

    def runner(run_config: Any, *, instance: object, on_phase: Any) -> MinimalRunResult:
        store.request_pause(batch.batch_id, reason=BatchPauseReason.USER, now=NOW)
        raise CodexCapacityError("upstream pool saturated")

    assert _capacity_worker(config, store, instance_ids, runner).run_once() is True

    current = store.get_batch(batch.batch_id)
    assert current.control.intent is BatchControlIntent.PAUSE
    assert current.control.pause_reason is BatchPauseReason.USER
    assert store.list_batch_tasks(batch.batch_id)[0].status is TaskStatus.QUEUED


def test_capacity_failure_never_queues_another_attempt_for_a_cancelling_batch(tmp_path: Path) -> None:
    config = _config(tmp_path)
    store = _store(config)
    instance_ids = ("instance_owner__repo-a",)
    batch = _create_batch(store, instance_ids=instance_ids)

    def runner(run_config: Any, *, instance: object, on_phase: Any) -> MinimalRunResult:
        store.request_cancel(batch.batch_id, now=NOW)
        raise CodexCapacityError("upstream pool saturated")

    assert _capacity_worker(config, store, instance_ids, runner).run_once() is True

    child = store.list_batch_tasks(batch.batch_id)[0]
    assert child.status is TaskStatus.FAILED
    assert child.attempt_number == 1
    assert store.list_task_attempts(batch.batch_id, child.task_id)[-1].attempt_number == 1


def test_tokensflow_drain_failure_is_recorded_without_pausing_batch(tmp_path: Path) -> None:
    config = _config(tmp_path)
    store = _store(config)
    instance_ids = ("instance_owner__repo-a", "instance_owner__repo-b")
    batch = _create_batch(store, instance_ids=instance_ids)

    def runner(run_config: Any, *, instance: object, on_phase: Any) -> MinimalRunResult:
        on_phase(RunPhase.RUNNING_OFF)
        raise TokensFlowInfrastructureError("private drain detail")

    worker = EvaluationWorker(
        config,
        store,
        runner=runner,
        source=FakeSource(),
        catalog=FakeCatalog(instance_ids),
        clock=lambda: NOW,
    )

    assert worker.run_once() is True

    retried, queued = store.list_batch_tasks(batch.batch_id)
    assert retried.status is TaskStatus.QUEUED
    attempts = store.list_task_attempts(batch.batch_id, retried.task_id)
    assert [attempt.status for attempt in attempts] == [TaskStatus.FAILED, TaskStatus.QUEUED]
    assert attempts[0].failure_category is FailureCategory.CODEX_EXECUTION
    assert attempts[0].failure_summary == "Codex execution failed."
    assert queued.status is TaskStatus.QUEUED
    current = store.get_batch(batch.batch_id)
    assert current.status is BatchStatus.QUEUED
    assert current.control.intent is BatchControlIntent.RUN
    assert current.control.pause_reason is None


def test_restart_reuses_persisted_batch_sha_and_completed_children(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = _config(tmp_path)
    store = _store(config)
    instance_ids = ("instance_owner__repo-a", "instance_owner__repo-b")
    batch = _create_batch(store, instance_ids=instance_ids)
    catalog = FakeCatalog(instance_ids)
    calls: list[tuple[RunConfig, SweBenchProInstance]] = []
    monkeypatch.setattr("powercontext_eval.web.worker.load_report", lambda *args: object())
    first_source = FakeSource()
    first = EvaluationWorker(
        config,
        store,
        runner=_successful_batch_runner(config, calls),
        source=first_source,
        catalog=catalog,
        clock=lambda: NOW,
    )
    assert first.run_once() is True

    class UnexpectedSource(FakeSource):
        def resolve(self, source: object, requested: object) -> object:
            pytest.fail("persisted batch SHA should avoid resolving latest after restart")

    restarted = EvaluationWorker(
        config,
        store,
        runner=_successful_batch_runner(config, calls),
        source=UnexpectedSource(),
        catalog=catalog,
        clock=lambda: NOW,
    )
    assert restarted.run_once() is True

    children = store.list_batch_tasks(batch.batch_id)
    assert [child.status for child in children] == [TaskStatus.SUCCEEDED, TaskStatus.SUCCEEDED]
    assert len(first_source.resolve_calls) == 1


def test_run_once_without_work_returns_false(tmp_path: Path) -> None:
    config = _config(tmp_path)

    def runner(config: Any, *, on_phase: Any) -> MinimalRunResult:
        pytest.fail("runner should not run")

    worker = EvaluationWorker(config, _store(config), runner=runner, clock=lambda: NOW)

    assert worker.run_once() is False


def test_run_once_maps_config_phases_and_success(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    config = _config(tmp_path)
    store = _store(config)
    task = _create(store)
    calls = []
    observed = []

    def runner(run_config: Any, *, on_phase: Any) -> MinimalRunResult:
        calls.append(run_config)
        for phase in RunPhase:
            before = store.get(task.task_id).version
            on_phase(phase)
            current = store.get(task.task_id)
            observed.append((current.phase, current.version > before))
        run_dir = config.run_root / "runs" / task.task_id
        run_dir.mkdir(parents=True)
        report_path = run_dir / "report.md"
        report_path.write_text("safe")
        return MinimalRunResult(task.task_id, report_path, True, False)

    loaded = []
    monkeypatch.setattr(
        "powercontext_eval.web.worker.load_report",
        lambda run_dir, run_root: loaded.append((run_dir, run_root)) or object(),
    )
    worker = EvaluationWorker(config, store, runner=runner, clock=lambda: NOW)

    assert worker.run_once() is True
    mapped = calls[0]
    assert mapped.run_id == task.task_id
    assert mapped.root == config.run_root
    assert mapped.powercontext_source == config.powercontext_source
    assert mapped.powercontext_ref == task.request.powercontext_ref
    assert mapped.harness_root == config.harness_root
    assert mapped.harness_python == config.harness_python
    assert mapped.raw_sample_path == config.raw_sample_path
    assert mapped.codex_binary == config.codex_binary
    assert mapped.tokensflow_binary == config.tokensflow_binary
    assert mapped.tokensflow_user_home == config.tokensflow_user_home
    assert mapped.uv_binary == config.uv_binary
    assert mapped.auth_json == config.auth_json
    assert mapped.codex_config == config.codex_config
    assert mapped.proxy_url == config.proxy_url
    assert observed == [(TaskPhase(phase.value), True) for phase in RunPhase]
    assert loaded == [(config.run_root / "runs" / task.task_id, config.run_root / "runs")]
    completed = store.get(task.task_id)
    assert completed.status is TaskStatus.SUCCEEDED
    assert completed.result is not None
    assert completed.result.artifact_dir == f"runs/{task.task_id}"
    assert completed.result.report_path == f"runs/{task.task_id}/report.md"
    assert (completed.result.off_resolved, completed.result.on_resolved) == (True, False)


def test_only_one_worker_can_claim_a_task(tmp_path: Path) -> None:
    config = _config(tmp_path)
    store = _store(config)
    task = _create(store, now=datetime.now(UTC))
    entered = threading.Event()
    release = threading.Event()
    calls = []

    def runner(run_config: Any, *, on_phase: Any) -> MinimalRunResult:
        calls.append(run_config.run_id)
        entered.set()
        release.wait(timeout=2)
        raise RuntimeError("stop")

    first = EvaluationWorker(config, store, runner=runner, worker_id="first")
    second = EvaluationWorker(config, store, runner=runner, worker_id="second")
    thread = threading.Thread(target=first.run_once)
    thread.start()
    assert entered.wait(timeout=2)
    assert second.run_once() is False
    release.set()
    thread.join(timeout=2)
    assert calls == [task.task_id]


def test_worker_tightens_an_existing_regular_lock_file_before_use(tmp_path: Path) -> None:
    config = _config(tmp_path)
    store = _store(config)
    lock_path = config.database_path.with_name(f"{config.database_path.name}.worker.lock")
    lock_path.write_text("")
    lock_path.chmod(0o755)
    worker = EvaluationWorker(config, store, worker_id="permission-recovery")

    assert worker.run_once() is False
    assert lock_path.stat().st_mode & 0o777 == 0o600


def test_worker_never_repairs_a_symlink_lock_target(tmp_path: Path) -> None:
    config = _config(tmp_path)
    store = _store(config)
    target = tmp_path / "outside-lock"
    target.write_text("")
    target.chmod(0o755)
    lock_path = config.database_path.with_name(f"{config.database_path.name}.worker.lock")
    lock_path.symlink_to(target)
    worker = EvaluationWorker(config, store, worker_id="symlink-rejection")

    with pytest.raises((OSError, RuntimeError)):
        worker.run_once()
    assert lock_path.is_symlink()
    assert target.stat().st_mode & 0o777 == 0o755


@pytest.mark.parametrize(
    ("error", "category", "summary"),
    [
        (GitSourceError("secret"), FailureCategory.SOURCE_RESOLUTION, "PowerContext source resolution failed."),
        (
            UnsafeSutConfiguration("secret"),
            FailureCategory.ENVIRONMENT_PREPARATION,
            "Evaluation SUT configuration is unsafe.",
        ),
        (
            DatasetSchemaError("secret"),
            FailureCategory.ENVIRONMENT_PREPARATION,
            "Evaluation dataset schema is invalid.",
        ),
        (GoldCheckFailed("secret"), FailureCategory.GOLD_VALIDATION, "Gold patch validation failed."),
        (
            CodexInfrastructureError("secret"),
            FailureCategory.CODEX_EXECUTION,
            "Codex execution infrastructure failed.",
        ),
        (
            ReadinessFailure(ReadinessFailureReason.COMMAND_TIMED_OUT),
            FailureCategory.TREATMENT_VALIDATION,
            "PowerContext readiness probe timed out.",
        ),
        (
            PluginInspectionFailure(PluginInspectionFailureReason.TIMED_OUT),
            FailureCategory.TREATMENT_VALIDATION,
            "Isolated Codex plugin inspection timed out.",
        ),
        (InvalidTreatment("secret"), FailureCategory.TREATMENT_VALIDATION, "Treatment validation failed."),
        (OfficialResultError("secret"), FailureCategory.OFFICIAL_EVALUATOR, "Official evaluation failed."),
    ],
)
def test_known_failures_have_fixed_safe_mapping(
    tmp_path: Path,
    error: Exception,
    category: FailureCategory,
    summary: str,
) -> None:
    config = _config(tmp_path)
    store = _store(config)
    task = _create(store)

    def runner(config: Any, *, on_phase: Any) -> MinimalRunResult:
        raise error

    assert EvaluationWorker(config, store, runner=runner, clock=lambda: NOW).run_once() is True
    failed = store.get(task.task_id)
    assert failed.failure_category is category
    assert failed.failure_summary == summary


@pytest.mark.parametrize(
    "reason",
    [PluginInspectionFailureReason.TIMED_OUT, PluginInspectionFailureReason.INVALID_PLUGIN_SET],
)
def test_plugin_inspection_failures_are_retryable_with_a_stable_subcode(
    tmp_path: Path,
    reason: PluginInspectionFailureReason,
) -> None:
    config = _config(tmp_path)
    store = _store(config)
    task = _create(store)

    def runner(config: Any, *, on_phase: Any) -> MinimalRunResult:
        raise PluginInspectionFailure(reason)

    assert EvaluationWorker(config, store, runner=runner, clock=lambda: NOW).run_once() is True
    failed = store.get(task.task_id)
    assert failed.failure_code is FailureCode.PLUGIN_INSPECTION
    assert failed.retry_disposition is RetryDisposition.RETRY
    assert failed.retryable is True


def test_unknown_failure_never_persists_exception_text(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    config = _config(tmp_path)
    store = _store(config)
    task = _create(store)
    credential = "credential-value-not-for-retention"

    def runner(config: Any, *, on_phase: Any) -> MinimalRunResult:
        on_phase(RunPhase.RUNNING_ON)
        raise RuntimeError(f"proxy had {credential}")

    EvaluationWorker(config, store, runner=runner, clock=lambda: NOW).run_once()
    failed = store.get(task.task_id)
    assert failed.failure_category is FailureCategory.INTERNAL
    assert failed.failure_phase is TaskPhase.RUNNING_ON
    assert failed.failure_summary == "The evaluation worker failed unexpectedly. Inspect the retained worker logs."
    assert credential not in config.database_path.read_bytes().decode(errors="ignore")
    assert "error_type=RuntimeError" in caplog.text
    assert credential not in caplog.text


def test_command_failure_logs_only_safe_command_shape_and_return_code(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    config = _config(tmp_path)
    store = _store(config)
    _create(store)
    credential = "credential-value-not-for-retention"

    def runner(config: Any, *, on_phase: Any) -> MinimalRunResult:
        on_phase(RunPhase.RUNNING_OFF)
        result = CommandResult(
            argv=("docker", "cp", f"container:{credential}", "/private/path"),
            cwd=f"/private/{credential}",
            returncode=70,
            stdout=credential,
            stderr=credential,
        )
        raise CommandFailed("secret command failure", result)

    EvaluationWorker(config, store, runner=runner, clock=lambda: NOW).run_once()

    assert "error_type=CommandFailed" in caplog.text
    assert "command_kind=docker.cp" in caplog.text
    assert "returncode=70" in caplog.text
    assert credential not in caplog.text


def test_existing_artifacts_fail_before_runner_without_overwrite(tmp_path: Path) -> None:
    config = _config(tmp_path)
    store = _store(config)
    task = _create(store)
    run_dir = config.run_root / "runs" / task.task_id
    run_dir.mkdir(parents=True)
    marker = run_dir / "keep.txt"
    marker.write_text("original")
    calls = []

    def runner(config: Any, *, on_phase: Any) -> MinimalRunResult:
        calls.append(config)
        pytest.fail("runner should not run")

    worker = EvaluationWorker(config, store, runner=runner, clock=lambda: NOW)

    assert worker.run_once() is True
    failed = store.get(task.task_id)
    assert failed.failure_category is FailureCategory.REPORT_GENERATION
    assert failed.failure_summary == "Evaluation artifacts already exist; refusing to overwrite them."
    assert marker.read_text() == "original"
    assert calls == []


def test_heartbeat_keeps_lease_alive_during_blocking_runner(tmp_path: Path) -> None:
    config = _config(tmp_path, lease_seconds=1)
    store = _store(config)
    task = _create(store, now=datetime.now(UTC))
    entered = threading.Event()
    release = threading.Event()

    def runner(run_config: Any, *, on_phase: Any) -> MinimalRunResult:
        entered.set()
        assert release.wait(timeout=3)
        raise RuntimeError("done")

    worker = EvaluationWorker(config, store, runner=runner)
    thread = threading.Thread(target=worker.run_once)
    thread.start()
    assert entered.wait(timeout=2)
    time.sleep(1.2)
    assert store.recover_expired(now=datetime.now(UTC)) == []
    assert store.get(task.task_id).status is TaskStatus.RUNNING
    release.set()
    thread.join(timeout=2)
    assert not thread.is_alive()


def test_heartbeat_retries_transient_sqlite_failure_without_losing_ownership(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
) -> None:
    config = _config(tmp_path, lease_seconds=1)
    store = _store(config)
    task = _create(store, now=datetime.now(UTC))
    entered = threading.Event()
    release = threading.Event()
    heartbeat_calls = 0
    heartbeat = store.heartbeat

    def transient_heartbeat(task_id: str, worker_id: str, *, now: datetime) -> Any:
        nonlocal heartbeat_calls
        heartbeat_calls += 1
        if heartbeat_calls == 1:
            raise sqlite3.OperationalError("database is locked leaked-secret")
        return heartbeat(task_id, worker_id, now=now)

    def runner(run_config: Any, *, on_phase: Any) -> MinimalRunResult:
        entered.set()
        assert release.wait(timeout=3)
        raise RuntimeError("done")

    monkeypatch.setattr(store, "heartbeat", transient_heartbeat)
    worker = EvaluationWorker(config, store, runner=runner)
    thread = threading.Thread(target=worker.run_once)
    thread.start()
    assert entered.wait(timeout=2)
    time.sleep(1.2)

    assert store.recover_expired(now=datetime.now(UTC)) == []
    assert store.get(task.task_id).status is TaskStatus.RUNNING
    assert heartbeat_calls >= 2
    assert "OperationalError" in caplog.text
    assert "leaked-secret" not in caplog.text

    release.set()
    thread.join(timeout=2)
    assert not thread.is_alive()


class RecordingThread:
    def __init__(self, *, target: Any, daemon: bool, name: str, args: tuple[Any, ...]) -> None:
        self.target = target
        self.daemon = daemon
        self.name = name
        self.args = args
        self.started = False
        self.joined = False

    def start(self) -> None:
        self.started = True

    def join(self) -> None:
        self.joined = True


class StartFailingThread(RecordingThread):
    def start(self) -> None:
        raise RuntimeError("thread start leaked-secret")


@pytest.mark.parametrize("succeeds", [True, False])
def test_heartbeat_thread_stops_and_joins(succeeds: bool, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    config = _config(tmp_path)
    store = _store(config)
    task = _create(store)
    threads = []

    def runner(run_config: Any, *, on_phase: Any) -> MinimalRunResult:
        if not succeeds:
            raise RuntimeError("failure")
        run_dir = config.run_root / "runs" / task.task_id
        run_dir.mkdir(parents=True)
        path = run_dir / "report.md"
        path.write_text("report")
        return MinimalRunResult(task.task_id, path, True, True)

    monkeypatch.setattr("powercontext_eval.web.worker.load_report", lambda *args: object())

    def thread_factory(**kwargs: Any) -> RecordingThread:
        thread = RecordingThread(**kwargs)
        threads.append(thread)
        return thread

    EvaluationWorker(config, store, runner=runner, thread_factory=thread_factory, clock=lambda: NOW).run_once()
    assert len(threads) == 1
    assert threads[0].started
    assert threads[0].joined
    assert threads[0].args[1].is_set()


def test_heartbeat_start_failure_is_safely_persisted_and_run_forever_continues(tmp_path: Path) -> None:
    config = _config(tmp_path)
    store = _store(config)
    task = _create(store)
    waits = []
    runner_calls = []
    worker: EvaluationWorker

    def runner(config: Any, *, on_phase: Any) -> MinimalRunResult:
        runner_calls.append(config)
        pytest.fail("runner should not run")

    def thread_factory(**kwargs: Any) -> StartFailingThread:
        return StartFailingThread(**kwargs)

    def wait(seconds: float) -> None:
        waits.append(seconds)
        worker.stop()

    worker = EvaluationWorker(
        config,
        store,
        runner=runner,
        thread_factory=thread_factory,
        clock=lambda: NOW,
        sleep=wait,
    )
    worker.run_forever()

    failed = store.get(task.task_id)
    assert failed.status is TaskStatus.FAILED
    assert failed.failure_category is FailureCategory.INTERNAL
    assert failed.failure_summary == "The evaluation worker failed unexpectedly. Inspect the retained worker logs."
    assert runner_calls == []
    assert waits == [config.poll_seconds]


def test_ownership_loss_cancels_runner_and_startup_recovery_fences_attempt(tmp_path: Path) -> None:
    config = _config(tmp_path, lease_seconds=1)
    store = _store(config)
    task = _create(store)
    later = NOW + timedelta(seconds=2)

    def runner(run_config: Any, *, on_phase: Any) -> MinimalRunResult:
        assert run_config.cancel_event is not None
        original = store.set_phase

        def lose_ownership(*args: Any, **kwargs: Any) -> Any:
            del args, kwargs
            raise TaskOwnershipError("ownership changed")

        store.set_phase = lose_ownership  # type: ignore[method-assign]
        on_phase(RunPhase.RUNNING_ON)
        store.set_phase = original  # type: ignore[method-assign]
        assert run_config.cancel_event.is_set()
        return MinimalRunResult(task.task_id, config.run_root / "runs" / task.task_id / "report.md", True, True)

    assert EvaluationWorker(config, store, runner=runner, clock=lambda: NOW).run_once() is True
    assert store.get(task.task_id).status is TaskStatus.RUNNING
    assert store.begin_startup_recovery(now=later) == [task.task_id]
    record = store.get(task.task_id)
    assert record.status is TaskStatus.INTERRUPTED
    assert record.phase is None
    assert record.result is None


def test_startup_recovery_fences_unexpired_predecessor_before_claiming_new_work(tmp_path: Path) -> None:
    config = _config(tmp_path, lease_seconds=1)
    store = _store(config)
    first_task = _create(store, key="first-task")
    second_task = _create(store, key="second-task")
    claimed = store.claim_next("predecessor", now=NOW)
    assert claimed is not None and claimed.task_id == first_task.task_id
    calls: list[str] = []

    def successor_runner(run_config: Any, *, on_phase: Any) -> MinimalRunResult:
        del on_phase
        calls.append(run_config.run_id)
        raise RuntimeError("successor ran")

    successor = EvaluationWorker(
        config,
        store,
        runner=successor_runner,
        worker_id="successor",
        clock=lambda: NOW,
    )
    assert successor.run_once() is True
    assert store.get(first_task.task_id).status is TaskStatus.INTERRUPTED
    assert calls == [second_task.task_id]


@pytest.mark.parametrize(
    "result",
    [
        MinimalRunResult("wrong-run", Path("/tmp/report.md"), True, True),
        MinimalRunResult("placeholder", Path("/tmp/outside.md"), True, True),
    ],
)
def test_invalid_runner_result_fails_safely(
    result: MinimalRunResult,
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    store = _store(config)
    task = _create(store)
    actual = (
        result
        if result.run_id == "wrong-run"
        else MinimalRunResult(task.task_id, config.run_root / "outside.md", True, True)
    )

    assert EvaluationWorker(config, store, runner=lambda *args, **kwargs: actual, clock=lambda: NOW).run_once() is True
    failed = store.get(task.task_id)
    assert failed.failure_category is FailureCategory.REPORT_GENERATION
    assert failed.failure_summary == "Evaluation report validation failed."


@pytest.mark.parametrize("invalid_kind", ["directory", "unrelated", "symlink"])
def test_runner_report_must_be_exact_regular_canonical_report(
    invalid_kind: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    store = _store(config)
    task = _create(store)

    def runner(run_config: Any, *, on_phase: Any) -> MinimalRunResult:
        run_dir = config.run_root / "runs" / task.task_id
        run_dir.mkdir(parents=True)
        expected = run_dir / "report.md"
        if invalid_kind == "directory":
            expected.mkdir()
            returned = expected
        elif invalid_kind == "unrelated":
            returned = run_dir / "other.md"
            returned.write_text("other")
        else:
            target = run_dir / "actual.md"
            target.write_text("actual")
            expected.symlink_to(target)
            returned = expected
        return MinimalRunResult(task.task_id, returned, True, True)

    monkeypatch.setattr("powercontext_eval.web.worker.load_report", lambda *args: object())
    assert EvaluationWorker(config, store, runner=runner, clock=lambda: NOW).run_once() is True
    failed = store.get(task.task_id)
    assert failed.status is TaskStatus.FAILED
    assert failed.failure_category is FailureCategory.REPORT_GENERATION
    assert failed.failure_summary == "Evaluation report validation failed."


def test_run_forever_recovers_once_and_waits_only_when_idle(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    store = _store(config)
    waits = []
    worker: EvaluationWorker

    def wait(seconds: float) -> None:
        waits.append(seconds)
        worker.stop()

    worker = EvaluationWorker(
        config,
        store,
        runner=lambda *args, **kwargs: pytest.fail("runner should not run"),
        clock=lambda: NOW,
        sleep=wait,
    )
    recoveries = []
    original = store.begin_startup_recovery

    def recover(*, now: datetime) -> list[str]:
        recoveries.append(now)
        return original(now=now)

    monkeypatch.setattr(store, "begin_startup_recovery", recover)
    worker.run_forever()

    assert recoveries == [NOW]
    assert waits == [config.poll_seconds]


def test_finalizer_supervisor_is_independent_from_task_pair_slots_and_stops_promptly(tmp_path: Path) -> None:
    config = _config(tmp_path, task_parallelism=4)
    store = _store(config)
    entered = threading.Event()
    observed_stop: list[threading.Event] = []

    class FakeFinalizer:
        def run_forever(self, stop: threading.Event, poll_seconds: float) -> None:
            assert poll_seconds == config.tokensflow_finalizer_poll_seconds
            observed_stop.append(stop)
            entered.set()
            stop.wait(timeout=2)

    worker = EvaluationWorker(config, store, finalizer=FakeFinalizer(), clock=lambda: NOW)
    supervisor = threading.Thread(target=worker.run_forever)
    supervisor.start()
    assert entered.wait(timeout=2)

    worker.stop()
    supervisor.join(timeout=2)

    assert not supervisor.is_alive()
    assert len(worker._slots) == 4
    assert observed_stop == [worker._stop]
    assert worker._stop.is_set()


def test_usage_refresher_supervisor_runs_independently_and_stops_promptly(tmp_path: Path) -> None:
    config = _config(tmp_path, task_parallelism=4, usage_probe_seconds=60)
    store = _store(config)
    entered = threading.Event()
    observed: list[tuple[threading.Event, float]] = []

    class FakeUsageRefresher:
        def run_forever(self, stop: threading.Event, poll_seconds: float) -> None:
            observed.append((stop, poll_seconds))
            entered.set()
            stop.wait(timeout=2)

    worker = EvaluationWorker(config, store, usage_refresher=FakeUsageRefresher(), clock=lambda: NOW)
    supervisor = threading.Thread(target=worker.run_forever)
    supervisor.start()
    assert entered.wait(timeout=2)

    worker.stop()
    supervisor.join(timeout=2)

    assert not supervisor.is_alive()
    assert observed == [(worker._stop, config.usage_probe_seconds)]


def test_usage_refresher_supervisor_failure_stops_worker_without_mutating_user_intent(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config = _config(tmp_path, task_parallelism=2, usage_probe_seconds=60)
    store = _store(config)
    batch = _create_batch(store, key="usage-refresher-failure", instance_ids=("instance_owner__repo-a",))
    failed = threading.Event()

    class FailingUsageRefresher:
        def run_forever(self, stop: threading.Event, poll_seconds: float) -> None:
            del stop, poll_seconds
            failed.set()
            raise RuntimeError("private usage refresh detail")

    worker = EvaluationWorker(config, store, usage_refresher=FailingUsageRefresher(), clock=lambda: NOW)
    for slot in worker._slots:
        monkeypatch.setattr(slot, "run_forever", lambda stop: stop.wait(timeout=2))
    supervisor = threading.Thread(target=worker.run_forever)
    supervisor.start()
    assert failed.wait(timeout=2)
    supervisor.join(timeout=2)

    assert not supervisor.is_alive()
    current = store.get_batch(batch.batch_id)
    assert current.status is BatchStatus.QUEUED
    assert current.control.intent is BatchControlIntent.RUN
    assert current.control.pause_reason is None


@pytest.mark.parametrize("parallelism", [4, 10])
def test_supervisor_respects_configured_isolated_task_pair_capacity_and_stop_prevents_replacement(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, parallelism: int
) -> None:
    config = _config(tmp_path, task_parallelism=parallelism)
    store = _store(config)
    batch = _create_batch(
        store,
        key="parallel-supervisor",
        instance_ids=tuple(f"instance_owner__repo-{index}" for index in range(parallelism + 1)),
    )
    catalog = FakeCatalog(tuple(f"instance_owner__repo-{index}" for index in range(parallelism + 1)))
    entered = threading.Event()
    release = threading.Event()
    calls_lock = threading.Lock()
    run_ids: list[str] = []

    def runner(run_config: Any, *, instance: object, on_phase: Any) -> MinimalRunResult:
        del instance, on_phase
        with calls_lock:
            run_ids.append(run_config.run_id)
            if len(run_ids) == parallelism:
                entered.set()
        release.wait(timeout=5)
        report = config.run_root / "runs" / run_config.run_id / "report.md"
        report.parent.mkdir(parents=True)
        report.write_text("safe")
        return MinimalRunResult(run_config.run_id, report, True, True)

    monkeypatch.setattr("powercontext_eval.web.worker.load_report", lambda *args: object())
    worker = EvaluationWorker(
        config,
        store,
        runner=runner,
        source=FakeSource(),
        catalog=catalog,
        clock=lambda: NOW,
    )
    supervisor = threading.Thread(target=worker.run_forever)
    supervisor.start()
    try:
        assert entered.wait(timeout=3)
        health = store.health_snapshot(now=NOW)
        assert health["task_parallelism"] == parallelism
        assert health["active_task_pairs"] == parallelism
        assert len(store.list_tasks(status=TaskStatus.QUEUED, limit=10, offset=0)) == 1
        assert len(set(run_ids)) == parallelism
    finally:
        worker.stop()
        release.set()
        supervisor.join(timeout=5)

    assert not supervisor.is_alive()
    children = store.list_batch_tasks(batch.batch_id)
    assert [child.status for child in children].count(TaskStatus.SUCCEEDED) == parallelism
    assert [child.status for child in children].count(TaskStatus.QUEUED) == 1


def test_task_pair_worker_directly_preserves_phase_order_and_report_boundary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    store = _store(config)
    task = _create(store, key="direct-task-pair")
    phases: list[RunPhase] = []

    def runner(run_config: Any, *, on_phase: Any) -> MinimalRunResult:
        for phase in (RunPhase.RUNNING_OFF, RunPhase.RUNNING_ON, RunPhase.OFFICIAL_EVALUATION):
            phases.append(phase)
            on_phase(phase)
        report = config.run_root / "runs" / run_config.run_id / "report.md"
        report.parent.mkdir(parents=True)
        report.write_text("safe")
        return MinimalRunResult(run_config.run_id, report, True, True)

    monkeypatch.setattr("powercontext_eval.web.worker.load_report", lambda *args: object())
    slot = TaskPairWorker(config, store, runner=runner, worker_id="direct-slot", clock=lambda: NOW)

    assert slot.run_once() is True
    assert phases == [RunPhase.RUNNING_OFF, RunPhase.RUNNING_ON, RunPhase.OFFICIAL_EVALUATION]
    completed = store.get(task.task_id)
    assert completed.status is TaskStatus.SUCCEEDED
    assert completed.phase is TaskPhase.OFFICIAL_EVALUATION


def test_worker_run_config_registers_arm_handoff_in_store(tmp_path: Path) -> None:
    config = _config(tmp_path)
    store = _store(config)
    batch = _create_batch(store, key="handoff-config", instance_ids=("instance_owner__repo-a",))
    task = store.list_batch_tasks(batch.batch_id)[0]
    assert task.instance_id is not None
    worker = TaskPairWorker(
        config, store, source=FakeSource(), catalog=FakeCatalog((task.instance_id,)), clock=lambda: NOW
    )

    run_config = worker._batch_run_config(task)
    assert run_config.finalization_registrar is not None
    runtime = config.run_root / "work" / task.task_id / "off" / "runtime"
    run_config.finalization_registrar(
        TokensFlowFinalizationDescriptor(
            arm=Arm.OFF,
            run_id=task.task_id,
            container_name=f"powercontext-eval-{task.task_id}-off",
            runtime=runtime,
            wrapper=runtime.parent / "evaluation-control/tokensflow-wrapper",
            egress_network="bridge",
            daemon_pid_file="/runtime/tokensflow-home/.local/share/tokensflow/evaluation-daemon.pid",
            evidence_sha256="c" * 64,
            evidence_bytes=78,
        )
    )

    assert task.attempt_id is not None
    jobs = store.tokensflow_finalizations_for_attempt(task.attempt_id)
    assert len(jobs) == 1
    assert jobs[0].task_id == task.task_id
    assert jobs[0].arm == "off"
    assert jobs[0].deadline_at == NOW + timedelta(seconds=config.tokensflow_finalizer_timeout_seconds)


def test_default_worker_run_config_has_no_tokensflow_finalization_handoff(tmp_path: Path) -> None:
    config = WebConfig.for_root(tmp_path, run_root=tmp_path / "artifacts")
    store = _store(config)
    batch = _create_batch(store, key="no-tokensflow-handoff", instance_ids=("instance_owner__repo-a",))
    task = store.list_batch_tasks(batch.batch_id)[0]
    worker = TaskPairWorker(
        config,
        store,
        source=FakeSource(),
        catalog=FakeCatalog(("instance_owner__repo-a",)),
        clock=lambda: NOW,
    )

    run_config = worker._batch_run_config(task)

    assert run_config.tokensflow_enabled is False
    assert run_config.tokensflow_binary is None
    assert run_config.tokensflow_user_home is None
    assert run_config.tokensflow_egress_network is None
    assert run_config.finalization_registrar is None
    assert run_config.proxy_url is None


def test_second_full_supervisor_cannot_start_slots_while_process_lock_is_owned(tmp_path: Path) -> None:
    config = _config(tmp_path)
    store = _store(config)
    first_task = _create(store, key="full-supervisor-first")
    _create(store, key="full-supervisor-second")
    entered = threading.Event()
    release = threading.Event()
    calls: list[tuple[str, str]] = []

    def first_runner(run_config: Any, *, on_phase: Any) -> MinimalRunResult:
        del on_phase
        calls.append(("first", run_config.run_id))
        entered.set()
        release.wait(timeout=3)
        raise RuntimeError("finish first supervisor")

    def second_runner(run_config: Any, *, on_phase: Any) -> MinimalRunResult:
        del on_phase
        calls.append(("second", run_config.run_id))
        raise RuntimeError("second supervisor should not start")

    first = EvaluationWorker(config, store, runner=first_runner, worker_id="full-first", clock=lambda: NOW)
    second = EvaluationWorker(config, store, runner=second_runner, worker_id="full-second", clock=lambda: NOW)
    first_thread = threading.Thread(target=first.run_forever)
    first_thread.start()
    try:
        assert entered.wait(timeout=2)
        second.run_forever()
        assert calls == [("first", first_task.task_id)]
    finally:
        first.stop()
        release.set()
        first_thread.join(timeout=3)
    assert not first_thread.is_alive()


def test_supervisor_surfaces_slot_failure_without_mutating_user_intent(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    store = _store(config)
    batch = _create_batch(store, key="slot-failure", instance_ids=("instance_owner__repo-a",))

    def fail_slot(_stop: threading.Event | None = None) -> None:
        raise RuntimeError("private slot failure")

    worker = EvaluationWorker(config, store, clock=lambda: NOW)
    monkeypatch.setattr(worker._slots[0], "run_forever", fail_slot)

    with pytest.raises(RuntimeError, match="Evaluation worker slot failed"):
        worker.run_forever()

    current = store.get_batch(batch.batch_id)
    assert current.status is BatchStatus.QUEUED
    assert current.control.intent is BatchControlIntent.RUN
    assert current.control.pause_reason is None


def test_supervisor_slot_failure_stops_replacements_joins_active_slots_and_raises_safely(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config = _config(tmp_path, task_parallelism=4)
    store = _store(config)
    for index in range(5):
        _create(store, key=f"slot-failure-cleanup-{index}")
    entered = threading.Event()
    release = threading.Event()
    injected_failure = threading.Event()
    supervisor_done = threading.Event()
    calls_lock = threading.Lock()
    run_ids: list[str] = []
    errors: list[BaseException] = []

    def runner(run_config: Any, *, on_phase: Any) -> MinimalRunResult:
        del on_phase
        with calls_lock:
            run_ids.append(run_config.run_id)
            if len(run_ids) == 3:
                entered.set()
        assert release.wait(timeout=5)
        report = config.run_root / "runs" / run_config.run_id / "report.md"
        report.parent.mkdir(parents=True)
        report.write_text("safe")
        return MinimalRunResult(run_config.run_id, report, True, True)

    monkeypatch.setattr("powercontext_eval.web.worker.load_report", lambda *args: object())
    worker = EvaluationWorker(config, store, runner=runner, worker_id="failing-supervisor", clock=lambda: NOW)
    failing_slot = worker._slots[0]

    def fail_after_other_slots_enter(_stop: threading.Event | None = None) -> None:
        assert entered.wait(timeout=3)
        injected_failure.set()
        raise RuntimeError("private slot detail")

    monkeypatch.setattr(failing_slot, "run_forever", fail_after_other_slots_enter)

    def supervise() -> None:
        try:
            worker.run_forever()
        except BaseException as error:  # noqa: BLE001 - the test asserts the supervisor boundary
            errors.append(error)
        finally:
            supervisor_done.set()

    supervisor = threading.Thread(target=supervise)
    supervisor.start()
    try:
        assert injected_failure.wait(timeout=3)
        assert worker._stop.is_set()
        assert not supervisor_done.wait(timeout=0.1)
        assert len(run_ids) == 3
    finally:
        worker.stop()
        release.set()
        supervisor.join(timeout=5)

    assert not supervisor.is_alive()
    assert supervisor_done.is_set()
    assert len(errors) == 1
    assert type(errors[0]) is RuntimeError
    assert str(errors[0]) == "Evaluation worker slot failed"
    assert "private slot detail" not in str(errors[0])
    assert len(run_ids) == 3
    assert len(store.list_tasks(status=TaskStatus.SUCCEEDED, limit=10, offset=0)) == 3
    assert len(store.list_tasks(status=TaskStatus.QUEUED, limit=10, offset=0)) == 2


def test_supervisor_partial_thread_start_failure_stops_and_joins_started_slots(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config = _config(tmp_path, task_parallelism=4)
    store = _store(config)
    worker = EvaluationWorker(config, store, worker_id="partial-start", clock=lambda: NOW)
    real_thread = threading.Thread
    started: list[int] = []
    joined: list[int] = []
    wrappers: list[ControlledThread] = []

    class ControlledThread:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self.index = len(wrappers) + 1
            self.inner = real_thread(*args, **kwargs)
            wrappers.append(self)

        def start(self) -> None:
            started.append(self.index)
            if self.index == 2:
                raise OSError("private thread-start detail")
            self.inner.start()

        def join(self) -> None:
            joined.append(self.index)
            self.inner.join()

    monkeypatch.setattr("powercontext_eval.web.worker.threading.Thread", ControlledThread)

    with pytest.raises(RuntimeError, match="^Evaluation worker slot failed$") as raised:
        worker.run_forever()

    assert "private thread-start detail" not in str(raised.value)
    assert worker._stop.is_set()
    assert len(wrappers) == 5
    assert started == [5, 1, 2]
    assert joined == [1, 5]
    assert not wrappers[0].inner.is_alive()
    assert not wrappers[4].inner.is_alive()
