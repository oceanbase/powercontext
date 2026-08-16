from __future__ import annotations

import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

import pytest

from powercontext_eval.web.batches import BatchControlEventType, BatchCreate, BatchStatus
from powercontext_eval.web.claiming import ClaimCoordinator, PeriodicUsageRefresher
from powercontext_eval.web.config import WebConfig
from powercontext_eval.web.controls import BatchPauseReason
from powercontext_eval.web.models import TaskRecord
from powercontext_eval.web.resources import FilesystemCapacity, ResourceUnavailable
from powercontext_eval.web.store import TaskStore
from powercontext_eval.web.usage import UsageSnapshot, UsageUnavailable

NOW = datetime(2026, 8, 1, 1, 2, 3, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _default_dependency_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("powercontext_eval.web.resources.DockerDependencyProbe.check", lambda _self: None)


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


class CountingProbe:
    def __init__(self, observations: list[UsageSnapshot | Exception], *, delay_seconds: float = 0.0) -> None:
        self._observations = observations
        self._delay_seconds = delay_seconds
        self._lock = threading.Lock()
        self.calls: list[datetime] = []
        self.active = 0
        self.maximum_active = 0

    def read(self, *, now: datetime) -> UsageSnapshot:
        with self._lock:
            self.calls.append(now)
            self.active += 1
            self.maximum_active = max(self.maximum_active, self.active)
            observation = self._observations.pop(0)
        try:
            if self._delay_seconds:
                time.sleep(self._delay_seconds)
            if isinstance(observation, Exception):
                raise observation
            return observation.model_copy(update={"observed_at": now})
        finally:
            with self._lock:
                self.active -= 1


class AdvancingClock:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._count = 0

    def __call__(self) -> datetime:
        with self._lock:
            now = NOW + timedelta(seconds=self._count)
            self._count += 1
            return now


def _config(
    root: Path,
    *,
    task_parallelism: int = 4,
    usage_mode: Literal["subscription", "api_key"] = "subscription",
) -> WebConfig:
    return WebConfig.for_root(
        root,
        tokensflow_egress_network="bridge",
        proxy_url="http://127.0.0.1:8081",
        task_parallelism=task_parallelism,
        usage_mode=usage_mode,
        usage_probe_seconds=60,
        usage_snapshot_max_age_seconds=120,
    )


def _store(config: WebConfig) -> TaskStore:
    store = TaskStore(config.database_path, lease_duration=timedelta(seconds=config.lease_seconds))
    store.initialize()
    return store


def _batch(store: TaskStore, *, key: str = "claim-coordinator", count: int = 4) -> str:
    batch = store.create_batch(
        BatchCreate(
            powercontext_ref="latest",
            benchmark="swebench-pro",
            task_set="swebench-pro-public-v2",
            model="gpt-5.6-sol",
            reasoning_effort="medium",
            treatment_mode="off_on",
            idempotency_key=key,
        ),
        tuple(f"instance_owner__repo-{index}" for index in range(count)),
        now=NOW,
    )[0]
    return batch.batch_id


def test_concurrent_claims_share_one_fresh_account_usage_probe(tmp_path: Path) -> None:
    config = _config(tmp_path)
    store = _store(config)
    _batch(store)
    probe = CountingProbe([_usage(9)], delay_seconds=0.02)
    coordinator = ClaimCoordinator(config, store, usage_probe=probe, clock=lambda: NOW)
    claimed: list[TaskRecord | None] = []
    result_lock = threading.Lock()

    def claim(index: int) -> None:
        task = coordinator.claim(f"slot-{index}")
        with result_lock:
            claimed.append(task)

    threads = [threading.Thread(target=claim, args=(index,)) for index in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    tasks = [task for task in claimed if task is not None]
    assert len(probe.calls) == 1
    assert probe.maximum_active == 1
    assert len(tasks) == 4
    assert len({task.task_id for task in tasks}) == 4


def test_api_key_mode_claims_without_subscription_usage_probe(tmp_path: Path) -> None:
    config = _config(tmp_path, task_parallelism=1, usage_mode="api_key")
    store = _store(config)
    _batch(store, count=1)
    probe = CountingProbe([UsageUnavailable("must not be called")])
    coordinator = ClaimCoordinator(config, store, usage_probe=probe, clock=lambda: NOW)

    claimed = coordinator.claim("slot-api-key")

    assert claimed is not None
    assert probe.calls == []
    assert store.latest_usage_snapshot() is None


def test_periodic_usage_refresh_keeps_snapshot_current_without_new_claims(tmp_path: Path) -> None:
    config = _config(tmp_path)
    store = _store(config)
    stop = threading.Event()
    observations = iter((NOW, NOW + timedelta(seconds=60)))

    class StopAfterSecondProbe(CountingProbe):
        def read(self, *, now: datetime) -> UsageSnapshot:
            snapshot = super().read(now=now)
            if len(self.calls) == 2:
                stop.set()
            return snapshot

    probe = StopAfterSecondProbe([_usage(9), _usage(10)])
    coordinator = ClaimCoordinator(config, store, usage_probe=probe, clock=lambda: next(observations))
    refresher = PeriodicUsageRefresher(coordinator)
    thread = threading.Thread(target=refresher.run_forever, args=(stop, 0.01))

    thread.start()
    try:
        deadline = time.monotonic() + 2
        while len(probe.calls) < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
    finally:
        stop.set()
        thread.join(timeout=2)

    assert not thread.is_alive()
    assert probe.calls == [NOW, NOW + timedelta(seconds=60)]
    assert store.latest_usage_snapshot() == _usage(10, observed_at=NOW + timedelta(seconds=60))
    assert store.list_batches() == []


def test_periodic_usage_refresh_tolerates_one_failure_while_cached_snapshot_is_fresh(tmp_path: Path) -> None:
    config = _config(tmp_path)
    store = _store(config)
    batch_id = _batch(store, key="periodic-usage-transient", count=1)
    observations = iter((NOW, NOW + timedelta(seconds=60)))
    coordinator = ClaimCoordinator(
        config,
        store,
        usage_probe=CountingProbe([_usage(9), UsageUnavailable("unavailable")]),
        clock=lambda: next(observations),
    )

    assert coordinator.refresh_usage() is True
    assert coordinator.refresh_usage() is False
    batch = store.get_batch(batch_id)
    assert batch.status is BatchStatus.QUEUED
    assert batch.control.pause_reason is None


def test_periodic_usage_refresh_expiry_closes_admission_without_mutating_batch(tmp_path: Path) -> None:
    config = _config(tmp_path)
    store = _store(config)
    batch_id = _batch(store, key="periodic-usage-expired", count=1)
    observations = iter((NOW, NOW + timedelta(seconds=121)))
    coordinator = ClaimCoordinator(
        config,
        store,
        usage_probe=CountingProbe([_usage(9), UsageUnavailable("unavailable")]),
        clock=lambda: next(observations),
    )

    assert coordinator.refresh_usage() is True
    assert coordinator.refresh_usage() is False
    batch = store.get_batch(batch_id)
    assert batch.status is BatchStatus.QUEUED
    assert batch.control.pause_reason is None


def test_claim_uses_cached_snapshot_for_the_configured_grace_window(tmp_path: Path) -> None:
    config = _config(tmp_path)
    store = _store(config)
    _batch(store, key="claim-usage-grace", count=1)
    probe = CountingProbe([UsageUnavailable("must not be called")])
    store.apply_usage_snapshot(_usage(9), now=NOW)
    coordinator = ClaimCoordinator(
        config,
        store,
        usage_probe=probe,
        clock=lambda: NOW + timedelta(seconds=61),
    )

    claimed = coordinator.claim("slot-0")

    assert claimed is not None
    assert probe.calls == []


def test_threshold_snapshot_closes_admission_without_pausing_batch(tmp_path: Path) -> None:
    config = _config(tmp_path)
    store = _store(config)
    batch_id = _batch(store, key="claim-threshold", count=1)
    coordinator = ClaimCoordinator(config, store, usage_probe=CountingProbe([_usage(80)]), clock=lambda: NOW)

    assert coordinator.claim("slot-0") is None
    batch = store.get_batch(batch_id)
    assert batch.status is BatchStatus.QUEUED
    assert batch.control.pause_reason is None


def test_unavailable_usage_closes_admission_without_pausing_batch(tmp_path: Path) -> None:
    config = _config(tmp_path)
    store = _store(config)
    batch_id = _batch(store, key="claim-unavailable", count=1)
    coordinator = ClaimCoordinator(
        config,
        store,
        usage_probe=CountingProbe([UsageUnavailable("unavailable")]),
        clock=lambda: NOW,
    )

    assert coordinator.claim("slot-0") is None
    batch = store.get_batch(batch_id)
    assert batch.status is BatchStatus.QUEUED
    assert batch.control.pause_reason is None


def test_usage_gate_reopens_automatically_after_probe_recovers(tmp_path: Path) -> None:
    config = _config(tmp_path)
    store = _store(config)
    batch_id = _batch(store, key="claim-unavailable-recovery", count=1)
    observations = iter((NOW, NOW + timedelta(seconds=1)))
    coordinator = ClaimCoordinator(
        config,
        store,
        usage_probe=CountingProbe([UsageUnavailable("unavailable"), _usage(9)]),
        clock=lambda: next(observations),
    )

    assert coordinator.claim("slot-0") is None
    claimed = coordinator.claim("slot-0")

    assert claimed is not None
    assert store.get_batch(batch_id).control.pause_reason is None


class FixedResourceProbe:
    def __init__(self, observation: FilesystemCapacity | Exception) -> None:
        self.observation = observation
        self.calls = 0

    def read(self) -> FilesystemCapacity:
        self.calls += 1
        if isinstance(self.observation, Exception):
            raise self.observation
        return self.observation


class FixedDependencyProbe:
    def __init__(self, observation: Exception | None) -> None:
        self.observation = observation
        self.calls = 0

    def check(self) -> None:
        self.calls += 1
        if self.observation is not None:
            raise self.observation


def test_dependency_gate_rechecks_and_reopens_without_consuming_an_attempt(tmp_path: Path) -> None:
    config = _config(tmp_path, task_parallelism=1)
    store = _store(config)
    batch_id = _batch(store, key="claim-dependency-recovery", count=1)
    dependency = FixedDependencyProbe(ResourceUnavailable("docker unavailable"))
    observations = iter((NOW, NOW + timedelta(seconds=9), NOW + timedelta(seconds=10)))
    coordinator = ClaimCoordinator(
        config,
        store,
        usage_probe=CountingProbe([_usage(9)]),
        dependency_probe=dependency,
        clock=lambda: next(observations),
    )

    assert coordinator.claim("slot-0") is None
    assert coordinator.claim("slot-0") is None
    assert dependency.calls == 1
    dependency.observation = None
    claimed = coordinator.claim("slot-0")

    assert claimed is not None
    assert dependency.calls == 2
    assert claimed.attempt_number == 1
    batch = store.get_batch(batch_id)
    assert batch.control.intent.value == "run"
    assert batch.control.pause_reason is None


def test_deployment_mismatch_blocks_claims_without_mutating_or_consuming_work(tmp_path: Path) -> None:
    config = _config(tmp_path, task_parallelism=1)
    store = _store(config)
    batch_id = _batch(store, key="claim-deployment-recovery", count=1)
    usage = CountingProbe([_usage(9)])
    store.record_runtime_revision("web", build_revision="a" * 40, schema_version=2, now=NOW)
    store.record_runtime_revision("worker", build_revision="b" * 40, schema_version=2, now=NOW)
    coordinator = ClaimCoordinator(
        config,
        store,
        usage_probe=usage,
        clock=lambda: NOW,
        deployment_gate=store.deployment_admission_open,
    )

    assert coordinator.claim("slot-0") is None
    assert usage.calls == []
    assert store.get_batch(batch_id).control.intent.value == "run"
    assert store.list_batch_tasks(batch_id)[0].attempt_number == 1

    store.record_runtime_revision("worker", build_revision="a" * 40, schema_version=2, now=NOW)
    claimed = coordinator.claim("slot-0")

    assert claimed is not None
    assert claimed.attempt_number == 1


@pytest.mark.parametrize(
    "capacity",
    [
        FilesystemCapacity(free_bytes=1, total_bytes=100, free_inodes=10_000_000, total_inodes=20_000_000),
        FilesystemCapacity(
            free_bytes=200 * 1024**3,
            total_bytes=300 * 1024**3,
            free_inodes=1,
            total_inodes=20_000_000,
        ),
    ],
)
def test_resource_pressure_closes_admission_before_usage_without_pause(
    tmp_path: Path, capacity: FilesystemCapacity
) -> None:
    config = _config(tmp_path)
    store = _store(config)
    batch_id = _batch(store, key=f"claim-resource-{capacity.free_bytes}-{capacity.free_inodes}", count=1)
    usage = CountingProbe([_usage(9)])
    resources = FixedResourceProbe(capacity)
    coordinator = ClaimCoordinator(
        config,
        store,
        usage_probe=usage,
        resource_probe=resources,
        clock=lambda: NOW,
    )

    assert coordinator.claim("slot-0") is None
    batch = store.get_batch(batch_id)
    assert batch.status is BatchStatus.QUEUED
    assert batch.control.pause_reason is None
    assert resources.calls == 1
    assert usage.calls == []


def test_unavailable_resource_probe_fails_closed_before_usage(tmp_path: Path) -> None:
    config = _config(tmp_path)
    store = _store(config)
    batch_id = _batch(store, key="claim-resource-unavailable", count=1)
    usage = CountingProbe([_usage(9)])
    coordinator = ClaimCoordinator(
        config,
        store,
        usage_probe=usage,
        resource_probe=FixedResourceProbe(ResourceUnavailable("unavailable")),
        clock=lambda: NOW,
    )

    assert coordinator.claim("slot-0") is None
    assert store.get_batch(batch_id).control.pause_reason is None
    assert usage.calls == []


def test_resource_gate_reopens_only_after_hysteresis_high_water(tmp_path: Path) -> None:
    config = _config(tmp_path, task_parallelism=1)
    store = _store(config)
    _batch(store, key="claim-resource-hysteresis", count=1)
    low_bytes = config.filesystem_claim_min_free_bytes
    low_inodes = config.filesystem_claim_min_free_inodes
    resources = FixedResourceProbe(FilesystemCapacity(low_bytes - 1, low_bytes * 3, low_inodes * 3, low_inodes * 4))
    coordinator = ClaimCoordinator(
        config,
        store,
        usage_probe=CountingProbe([_usage(9)]),
        resource_probe=resources,
        clock=lambda: NOW,
    )

    assert coordinator.claim("slot-0") is None
    resources.observation = FilesystemCapacity(low_bytes, low_bytes * 3, low_inodes * 3, low_inodes * 4)
    assert coordinator.claim("slot-0") is None
    resources.observation = FilesystemCapacity(
        low_bytes + max(low_bytes // 10, 1),
        low_bytes * 3,
        low_inodes + max(low_inodes // 10, 1),
        low_inodes * 4,
    )
    assert coordinator.claim("slot-0") is not None


def test_stop_closes_the_shared_claim_gate_before_any_replacement(tmp_path: Path) -> None:
    config = _config(tmp_path)
    store = _store(config)
    batch_id = _batch(store, key="claim-stop", count=1)
    probe = CountingProbe([_usage(9)])
    coordinator = ClaimCoordinator(config, store, usage_probe=probe, clock=lambda: NOW)

    coordinator.stop()

    assert coordinator.claim("slot-0") is None
    assert probe.calls == []
    assert store.get_batch(batch_id).status is BatchStatus.QUEUED


def test_stop_does_not_wait_for_inflight_probe_or_allow_its_claim(tmp_path: Path) -> None:
    config = _config(tmp_path)
    store = _store(config)
    _batch(store, key="claim-stop-order", count=2)
    probe_entered = threading.Event()
    release_probe = threading.Event()

    class BlockingProbe:
        def __init__(self) -> None:
            self.calls = 0

        def read(self, *, now: datetime) -> UsageSnapshot:
            self.calls += 1
            probe_entered.set()
            assert release_probe.wait(timeout=2)
            return _usage(9, observed_at=now)

    probe = BlockingProbe()
    coordinator = ClaimCoordinator(
        config,
        store,
        usage_probe=probe,
        resource_probe=FixedResourceProbe(
            FilesystemCapacity(
                free_bytes=1024**5,
                total_bytes=2 * 1024**5,
                free_inodes=100_000_000,
                total_inodes=200_000_000,
            )
        ),
        clock=lambda: NOW,
    )
    first_claim: list[object] = []
    stop_started = threading.Event()
    stop_finished = threading.Event()

    def claim_inside_gate() -> None:
        first_claim.append(coordinator.claim("slot-inside-gate"))

    def stop_at_boundary() -> None:
        stop_started.set()
        coordinator.stop()
        stop_finished.set()

    claim_thread = threading.Thread(target=claim_inside_gate)
    stop_thread = threading.Thread(target=stop_at_boundary)
    claim_thread.start()
    assert probe_entered.wait(timeout=2)

    stop_thread.start()
    assert stop_started.wait(timeout=2)
    stop_returned_promptly = stop_finished.wait(timeout=0.5)

    release_probe.set()
    claim_thread.join(timeout=2)
    stop_thread.join(timeout=2)

    assert not claim_thread.is_alive()
    assert not stop_thread.is_alive()
    assert stop_returned_promptly
    assert first_claim == [None]
    assert stop_finished.is_set()
    assert coordinator.claim("slot-after-stop") is None
    assert probe.calls == 1


def test_stop_rejects_waiting_parallel_claims_without_starting_more_probes(tmp_path: Path) -> None:
    parallelism = 10
    config = _config(tmp_path, task_parallelism=parallelism)
    store = _store(config)
    _batch(store, key="claim-stop-parallel", count=parallelism)
    probe_entered = threading.Event()
    release_probe = threading.Event()

    class BlockingProbe:
        def __init__(self) -> None:
            self.calls = 0

        def read(self, *, now: datetime) -> UsageSnapshot:
            self.calls += 1
            probe_entered.set()
            assert release_probe.wait(timeout=2)
            return _usage(9, observed_at=now)

    probe = BlockingProbe()
    coordinator = ClaimCoordinator(
        config,
        store,
        usage_probe=probe,
        resource_probe=FixedResourceProbe(
            FilesystemCapacity(
                free_bytes=1024**5,
                total_bytes=2 * 1024**5,
                free_inodes=100_000_000,
                total_inodes=200_000_000,
            )
        ),
        clock=lambda: NOW,
    )
    claims: list[object] = []
    claims_lock = threading.Lock()

    def claim(slot: int) -> None:
        result = coordinator.claim(f"slot-{slot}")
        with claims_lock:
            claims.append(result)

    threads = [threading.Thread(target=claim, args=(slot,)) for slot in range(parallelism)]
    threads[0].start()
    assert probe_entered.wait(timeout=2)
    for thread in threads[1:]:
        thread.start()

    coordinator.stop()
    release_probe.set()
    for thread in threads:
        thread.join(timeout=2)

    assert all(not thread.is_alive() for thread in threads)
    assert claims == [None] * parallelism
    assert probe.calls == 1


def test_stop_waits_only_for_a_claim_that_entered_the_database_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(tmp_path)
    store = _store(config)
    _batch(store, key="claim-stop-commit", count=1)
    coordinator = ClaimCoordinator(config, store, usage_probe=CountingProbe([_usage(9)]), clock=lambda: NOW)
    commit_entered = threading.Event()
    release_commit = threading.Event()
    original_claim = store.claim_next_with_usage

    def blocking_claim(*args: Any, **kwargs: Any) -> TaskRecord | None:
        commit_entered.set()
        assert release_commit.wait(timeout=2)
        return original_claim(*args, **kwargs)

    monkeypatch.setattr(store, "claim_next_with_usage", blocking_claim)
    claims: list[TaskRecord | None] = []
    claim_thread = threading.Thread(target=lambda: claims.append(coordinator.claim("slot-commit")))
    stop_finished = threading.Event()
    stop_thread = threading.Thread(target=lambda: (coordinator.stop(), stop_finished.set()))
    claim_thread.start()
    assert commit_entered.wait(timeout=2)

    stop_thread.start()
    stop_returned_before_commit = stop_finished.wait(timeout=0.5)
    release_commit.set()
    claim_thread.join(timeout=2)
    stop_thread.join(timeout=2)

    assert not claim_thread.is_alive()
    assert not stop_thread.is_alive()
    assert not stop_returned_before_commit
    assert claims[0] is not None
    assert stop_finished.is_set()
    assert coordinator.claim("slot-after-stop") is None


def test_refresh_after_attempt_serializes_probes_and_finalizes_idempotently(tmp_path: Path) -> None:
    config = _config(tmp_path)
    store = _store(config)
    batch_id = _batch(store, key="claim-refresh", count=1)
    store.request_pause(batch_id, reason=BatchPauseReason.USER, now=NOW)
    probe = CountingProbe([_usage(9) for _ in range(4)], delay_seconds=0.01)
    coordinator = ClaimCoordinator(config, store, usage_probe=probe, clock=AdvancingClock())

    threads = [threading.Thread(target=coordinator.refresh_after_attempt, args=(batch_id,)) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(probe.calls) == 4
    assert probe.maximum_active == 1
    snapshot = store.latest_usage_snapshot()
    assert snapshot is not None
    assert snapshot.observed_at == probe.calls[-1]
    assert [event.event_type for event in store.list_control_events(batch_id)].count(BatchControlEventType.PAUSED) == 1
