"""Synchronized account-wide usage gating for evaluation task claims."""

from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Protocol

from powercontext_eval.web.config import WebConfig
from powercontext_eval.web.controls import BatchPauseReason
from powercontext_eval.web.models import TaskRecord
from powercontext_eval.web.resources import FilesystemResourceProbe, ResourceProbe, ResourceUnavailable
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
    ) -> None:
        self._config = config
        self._store = store
        self._usage_probe = usage_probe
        self._resource_probe = resource_probe or FilesystemResourceProbe(config.run_root)
        self._clock = clock
        self._lock = threading.Lock()
        self._claim_commit_lock = threading.Lock()
        self._stopped = threading.Event()

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
            self._store.recover_expired(now=now)
            try:
                capacity = self._resource_probe.read()
            except ResourceUnavailable:
                capacity = None
            if capacity is None or not capacity.admission_open(
                min_free_bytes=self._config.filesystem_claim_min_free_bytes,
                min_free_inodes=self._config.filesystem_claim_min_free_inodes,
            ):
                if self._stopped.is_set():
                    return None
                self._store.pause_runnable_batches(
                    reason=BatchPauseReason.RESOURCE_PRESSURE,
                    now=now,
                )
                return None
            try:
                snapshot = self._usage_before_claim(now)
            except UsageUnavailable:
                if self._stopped.is_set():
                    return None
                self._store.pause_runnable_batches(
                    reason=BatchPauseReason.USAGE_UNAVAILABLE,
                    now=now,
                )
                return None
            if self._stopped.is_set():
                return None
            with self._claim_commit_lock:
                if self._stopped.is_set():
                    return None
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
        try:
            snapshot = self._usage_probe.read(now=now)
            self._store.apply_usage_snapshot(snapshot, now=now)
        except UsageUnavailable:
            if (
                self._fresh_usage_snapshot(
                    now,
                    max_age_seconds=self._config.usage_snapshot_max_age_seconds,
                )
                is not None
            ):
                return False
            self._store.pause_runnable_batches(
                reason=BatchPauseReason.USAGE_UNAVAILABLE,
                now=now,
            )
            return False
        return True

    def _usage_before_claim(self, now: datetime) -> UsageSnapshot:
        snapshot = self._fresh_usage_snapshot(
            now,
            max_age_seconds=self._config.usage_probe_seconds,
        )
        if snapshot is not None:
            return snapshot
        try:
            return self._usage_probe.read(now=now)
        except UsageUnavailable:
            snapshot = self._fresh_usage_snapshot(
                now,
                max_age_seconds=self._config.usage_snapshot_max_age_seconds,
            )
            if snapshot is not None:
                return snapshot
            raise

    def _fresh_usage_snapshot(self, now: datetime, *, max_age_seconds: int) -> UsageSnapshot | None:
        snapshot = self._store.latest_usage_snapshot()
        if snapshot is None or not is_fresh(
            snapshot,
            now=now,
            max_age=timedelta(seconds=max_age_seconds),
        ):
            return None
        return snapshot


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
