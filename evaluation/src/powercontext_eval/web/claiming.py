"""Synchronized account-wide usage gating for evaluation task claims."""

from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Protocol

from powercontext_eval.web.config import WebConfig
from powercontext_eval.web.models import TaskRecord
from powercontext_eval.web.resources import (
    DependencyProbe,
    DockerDependencyProbe,
    FilesystemCapacity,
    FilesystemResourceProbe,
    ResourceProbe,
    ResourceUnavailable,
)
from powercontext_eval.web.store import TaskStore
from powercontext_eval.web.usage import UsageSnapshot, UsageUnavailable, is_fresh


class UsageProbe(Protocol):
    def read(self, *, now: datetime) -> UsageSnapshot: ...


class ClaimCoordinator:
    """Serialize account-wide usage decisions and capacity-aware claims."""

    def __init__(
        self,
        config: WebConfig,
        store: TaskStore,
        *,
        usage_probe: UsageProbe,
        clock: Callable[[], datetime],
        resource_probe: ResourceProbe | None = None,
        dependency_probe: DependencyProbe | None = None,
        deployment_gate: Callable[[], bool] | None = None,
    ) -> None:
        self._config = config
        self._store = store
        self._usage_probe = usage_probe
        self._resource_probe = resource_probe or FilesystemResourceProbe(config.run_root)
        self._dependency_probe = dependency_probe or DockerDependencyProbe(config.run_root)
        self._deployment_gate = deployment_gate or (lambda: True)
        self._clock = clock
        self._lock = threading.Lock()
        self._claim_commit_lock = threading.Lock()
        self._stopped = threading.Event()
        self._resource_admission_open = True
        self._dependency_admission_open = True
        self._dependency_checked_at: datetime | None = None

    def stop(self) -> None:
        """Close the synchronized claim gate for all sharing slots."""

        with self._claim_commit_lock:
            self._stopped.set()

    def claim(self, worker_id: str) -> TaskRecord | None:
        """Claim one task after one synchronized, fail-closed usage decision."""

        if self._stopped.is_set():
            return None
        with self._lock:
            if self._stopped.is_set():
                return None
            now = self._clock()
            if not self._deployment_gate():
                return None
            if not self._dependency_admitted(now):
                return None
            try:
                capacity = self._resource_probe.read()
            except ResourceUnavailable:
                capacity = None
            if capacity is None or not self._resource_admitted(capacity):
                return None
            snapshot: UsageSnapshot | None = None
            if self._config.usage_mode == "subscription":
                try:
                    snapshot = self._usage_before_claim(now)
                except UsageUnavailable:
                    return None
            if self._stopped.is_set():
                return None
            with self._claim_commit_lock:
                if self._stopped.is_set():
                    return None
                if snapshot is None:
                    return self._store.claim_next(
                        worker_id,
                        max_concurrency=self._config.task_parallelism,
                        now=now,
                    )
                return self._store.claim_next_with_usage(
                    worker_id,
                    snapshot=snapshot,
                    default_threshold=self._config.usage_pause_percent,
                    max_concurrency=self._config.task_parallelism,
                    now=now,
                )

    def refresh_after_attempt(self, batch_id: str) -> None:
        """Refresh account usage and finalize batch control after one attempt."""

        with self._lock:
            now = self._clock()
            self._refresh_usage_locked(now)
            self._store.finalize_batch_intent_after_attempt(batch_id, now=now)

    def refresh_usage(self) -> bool:
        """Refresh account usage independently of task claim and completion traffic."""

        with self._lock:
            return self._refresh_usage_locked(self._clock())

    def _refresh_usage_locked(self, now: datetime) -> bool:
        if self._config.usage_mode == "api_key":
            return True
        try:
            snapshot = self._usage_probe.read(now=now)
            self._store.apply_usage_snapshot(snapshot, now=now)
        except UsageUnavailable:
            return False
        return True

    def _resource_admitted(self, capacity: FilesystemCapacity) -> bool:
        """Apply a small in-memory hysteresis band without mutating batch intent."""

        min_bytes = self._config.filesystem_claim_min_free_bytes
        min_inodes = self._config.filesystem_claim_min_free_inodes
        if self._resource_admission_open:
            admitted = capacity.admission_open(min_free_bytes=min_bytes, min_free_inodes=min_inodes)
        else:
            admitted = capacity.admission_open(
                min_free_bytes=min_bytes + max(min_bytes // 10, 1),
                min_free_inodes=min_inodes + max(min_inodes // 10, 1),
            )
        self._resource_admission_open = admitted
        return admitted

    def _dependency_admitted(self, now: datetime) -> bool:
        """Cache a direct daemon probe briefly and reopen automatically after recovery."""

        if self._dependency_checked_at is not None:
            age = now - self._dependency_checked_at
            if timedelta(0) <= age < timedelta(seconds=10):
                return self._dependency_admission_open
        try:
            self._dependency_probe.check()
        except ResourceUnavailable:
            admitted = False
        else:
            admitted = True
        self._dependency_checked_at = now
        self._dependency_admission_open = admitted
        return admitted

    def _usage_before_claim(self, now: datetime) -> UsageSnapshot:
        snapshot = self._store.latest_usage_snapshot()
        if snapshot is not None and self._usage_snapshot_is_fresh(snapshot, now=now):
            return snapshot
        return self._usage_probe.read(now=now)

    def _usage_snapshot_is_fresh(self, snapshot: UsageSnapshot, *, now: datetime) -> bool:
        return is_fresh(
            snapshot,
            now=now,
            max_age=timedelta(seconds=self._config.usage_snapshot_max_age_seconds),
        )


class PeriodicUsageRefresher:
    """Keep the account snapshot fresh while every task-pair slot is busy."""

    def __init__(self, coordinator: ClaimCoordinator) -> None:
        self._coordinator = coordinator

    def run_forever(self, stop: threading.Event, poll_seconds: float) -> None:
        """Refresh immediately and then at the configured interruptible interval."""

        if poll_seconds <= 0:
            raise ValueError("Usage refresh interval must be positive")
        while not stop.is_set():
            self._coordinator.refresh_usage()
            stop.wait(poll_seconds)
