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

"""Bounded, passive adapters for domain-owned operational observations."""

from __future__ import annotations

import math
import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from time import time
from typing import TYPE_CHECKING

from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily, HistogramMetricFamily, Metric

from powercontext.limits import MAX_ARTIFACT_FAMILY_LENGTH
from powercontext.server.logging import log_record_failures

if TYPE_CHECKING:
    from powercontext.builtin.runtime.application import BuiltinRuntime
    from powercontext.builtin.runtime.artifact_processing import ArtifactProcessingSupervisors
    from powercontext.builtin.runtime.observations import DurationObservationSnapshot
    from powercontext.builtin.runtime.readiness import RuntimeReadinessSnapshot

_PREFIX = "powercontext_server_"
_DEPENDENCIES = ("database", "inference.generation", "inference.rerank", "inference.decision", "inference.embedding")
_READINESS = ("unobserved", "ready", "degraded", "not_ready")
_DEPENDENCY_STATES = ("unobserved", "disabled", "ready", "unavailable", "timeout", "misconfigured")
_OUTCOMES = (
    "success",
    "hold",
    "noop",
    "failure",
    "cancelled",
    "blocked",
    "cursor_conflict",
    "head_conflict",
    "leadership_lost",
    "ready",
    "unavailable",
    "timeout",
    "misconfigured",
)
_ERROR_CATEGORIES = (
    "authentication",
    "authorization",
    "configuration",
    "rate_limit",
    "dependency",
    "timeout",
    "not_found",
    "conflict",
    "invalid_request",
    "internal",
    "cancelled",
    "blocked",
)


def _gauge(name: str, description: str, labels: Mapping[str, str], value: float) -> GaugeMetricFamily:
    metric = GaugeMetricFamily(_PREFIX + name, description, labels=list(labels))
    metric.add_metric(list(labels.values()), value)
    return metric


def _state(name: str, labels: Mapping[str, str], current: str, choices: tuple[str, ...]) -> GaugeMetricFamily:
    metric = GaugeMetricFamily(
        _PREFIX + name, "One-hot observed state; freshness is reported separately.", labels=[*labels, "state"]
    )
    for state in choices:
        metric.add_metric([*labels.values(), state], float(current == state))
    return metric


def _histogram(
    name: str, description: str, labels: Mapping[str, str], observations: tuple[DurationObservationSnapshot, ...]
) -> HistogramMetricFamily:
    metric = HistogramMetricFamily(_PREFIX + name, description, labels=[*labels, "outcome"])
    for observation in observations:
        if observation.outcome not in _OUTCOMES:
            continue
        metric.add_metric(
            [*labels.values(), observation.outcome],
            buckets=[("+Inf" if math.isinf(bound) else str(bound), count) for bound, count in observation.buckets],
            sum_value=observation.total_seconds,
        )
    return metric


@dataclass(frozen=True)
class OperationalMetricsSnapshot:
    metrics: tuple[Metric, ...]
    collected_at: float


class OperationalMetricsCollector:
    """Copy bounded in-memory snapshots, never query a dependency during collection."""

    def __init__(self, *, role: str) -> None:
        self._role = role if role in {"server", "all", "api", "background"} else "server"
        self._snapshot = OperationalMetricsSnapshot((), 0.0)
        self._failures = 0

    def replace(self, runtime: BuiltinRuntime | None) -> None:
        """Publish an entire snapshot atomically; a failure preserves the last observation."""
        self.publish(self.prepare(runtime))

    def prepare(
        self,
        runtime: BuiltinRuntime | None,
        *,
        families: Mapping[str, Mapping[str, int | float | str]] | None = None,
    ) -> OperationalMetricsSnapshot:
        """Build an observation without changing the published snapshot."""
        readiness = None if runtime is None else runtime.readiness_snapshot()
        supervisor = None if runtime is None else runtime.artifact_processing_supervisor
        snapshot = [*_readiness_metrics(readiness), *_processing_metrics(supervisor, families)]
        merged: dict[str, Metric] = {}
        for metric in snapshot:
            if metric.name in merged:
                merged[metric.name].samples.extend(metric.samples)
            else:
                merged[metric.name] = metric
        return OperationalMetricsSnapshot(tuple(merged.values()), time())

    def publish(self, snapshot: OperationalMetricsSnapshot) -> None:
        """Commit an already prepared observation without further collection work."""
        self._snapshot = snapshot

    def collection_failed(self) -> None:
        """Record collection failure without logging recursively or dropping business work."""
        self._failures += 1

    def collect(self) -> Iterator[Metric]:
        snapshot = self._snapshot
        yield _gauge(
            "service_role_info",
            "Configured process role; identify instances using scrape target labels.",
            {"role": self._role},
            1,
        )
        yield _gauge(
            "telemetry_collection_timestamp_seconds",
            "UNIX time of the latest successful passive snapshot collection; zero before collection.",
            {},
            snapshot.collected_at,
        )
        for name, description, value in (
            (
                "telemetry_collection_failures",
                "Failed passive collection attempts; resets on process restart.",
                self._failures,
            ),
            (
                "telemetry_log_records_dropped",
                "Records the process-owned log handler failed to deliver; resets on process restart.",
                log_record_failures(),
            ),
        ):
            yield CounterMetricFamily(_PREFIX + name, description, value=value)
        yield from snapshot.metrics


def _valid_family(family: str) -> bool:
    return len(family) <= MAX_ARTIFACT_FAMILY_LENGTH and re.fullmatch(r"[a-z][a-z0-9-]*", family) is not None


def _readiness_metrics(readiness: RuntimeReadinessSnapshot | None) -> list[Metric]:
    snapshot: list[Metric] = []
    snapshot.append(
        _state("runtime_readiness_state", {}, "unobserved" if readiness is None else readiness.status, _READINESS)
    )
    if readiness is not None:
        if readiness.observed_at is not None:
            snapshot.append(
                _gauge(
                    "runtime_readiness_observed_timestamp_seconds",
                    "UNIX time of the last readiness observation.",
                    {},
                    readiness.observed_at,
                )
            )
        for dependency in readiness.dependencies:
            if dependency.name not in _DEPENDENCIES:
                continue
            labels = {"dependency": dependency.name}
            snapshot.append(_state("dependency_state", labels, dependency.status, _DEPENDENCY_STATES))
            snapshot.append(
                _gauge(
                    "dependency_blocking",
                    "Whether this dependency blocks readiness.",
                    labels,
                    float(dependency.blocking),
                )
            )
            for suffix, value in (
                ("observed", dependency.last_checked_at),
                ("last_success", dependency.last_success_at),
            ):
                if value is not None:
                    snapshot.append(
                        _gauge(
                            f"dependency_{suffix}_timestamp_seconds",
                            "UNIX time of an actual dependency probe, excluding cache hits.",
                            labels,
                            value,
                        )
                    )
            snapshot.append(
                _histogram(
                    "dependency_probe_duration_seconds",
                    "Actual dependency probe duration; cache hits are excluded.",
                    labels,
                    dependency.durations,
                )
            )
    return snapshot


def _processing_metrics(  # noqa: C901
    supervisor: ArtifactProcessingSupervisors | None,
    families: Mapping[str, Mapping[str, int | float | str]] | None,
) -> list[Metric]:
    snapshot: list[Metric] = []
    snapshot.append(
        _gauge(
            "artifact_processing_supervisor_present",
            "Whether this instance owns a configured Supervisor.",
            {},
            float(supervisor is not None),
        )
    )
    if supervisor is not None:
        for family, values in (supervisor.family_status if families is None else families).items():
            if not _valid_family(family):
                continue
            labels = {"family": family}
            for field in ("supervisor_running", "automatic_processing_enabled"):
                if field in values:
                    snapshot.append(
                        _gauge(
                            f"artifact_processing_{field}",
                            "Current Supervisor configuration or lifecycle observation.",
                            labels,
                            float(bool(values[field])),
                        )
                    )
            for field, choices in (
                ("supervisor_role", ("leader", "standby")),
                ("status", ("disabled", "leader", "standby", "degraded")),
            ):
                if values.get(field) in choices:
                    snapshot.append(_state(f"artifact_processing_{field}", labels, str(values[field]), choices))
            for field in (
                "last_acknowledgement_timestamp_seconds",
                "requested_snapshot_timestamp_seconds",
                "oldest_observed_pending_age_seconds",
                "oldest_pending_timestamp_seconds",
                "oldest_pending_age_seconds",
                "pending_age_unknown_scopes",
                "blocked_wait",
                "observed_pending_scopes",
            ):
                value = values.get(field)
                if (
                    isinstance(value, (int, float))
                    and not isinstance(value, bool)
                    and math.isfinite(value)
                    and value >= 0
                ):
                    snapshot.append(
                        _gauge(
                            f"artifact_processing_{field}",
                            "Domain-owned processing observation; see operational freshness and reset semantics.",
                            labels,
                            value,
                        )
                    )
            failures = values.get("pending_observation_failures", 0)
            if isinstance(failures, int) and not isinstance(failures, bool) and failures >= 0:
                metric = CounterMetricFamily(
                    _PREFIX + "artifact_processing_pending_observation_failures",
                    "Failed background pending observations; last successful snapshot is preserved.",
                    labels=["family"],
                )
                metric.add_metric([family], failures)
                snapshot.append(metric)
        for family, observation in getattr(supervisor, "processing_observations", {}).items():
            if not _valid_family(family):
                continue
            labels = {"family": family}
            snapshot.append(
                _histogram(
                    "artifact_processing_attempt_duration_seconds",
                    "Worker attempt duration, including retry attempts; not logical operations.",
                    labels,
                    observation.attempt_durations,
                )
            )
            snapshot.append(
                _histogram(
                    "artifact_processing_discovery_duration_seconds",
                    "Bounded discovery pass duration.",
                    labels,
                    observation.discovery_durations,
                )
            )
            for suffix, label, choices, counts in (
                ("errors", "category", _ERROR_CATEGORIES, observation.error_counts),
                ("retries", "reason", ("failure", "conflict", "blocked"), observation.retry_counts),
            ):
                metric = CounterMetricFamily(
                    _PREFIX + "artifact_processing_" + suffix,
                    "Domain-owned bounded processing observations.",
                    labels=["family", label],
                )
                for category, count in counts:
                    if category in choices:
                        metric.add_metric([family, category], count)
                snapshot.append(metric)
            metric = CounterMetricFamily(
                _PREFIX + "artifact_processing_admission_blocks",
                "Observed admission blocks; resets with the Supervisor.",
                labels=["family"],
            )
            metric.add_metric([family], observation.admission_blocks)
            snapshot.append(metric)
    return snapshot
