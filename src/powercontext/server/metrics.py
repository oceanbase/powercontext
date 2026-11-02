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

"""Prometheus-compatible metrics for the ready-to-run Server."""

from __future__ import annotations

import asyncio
import math
import re
from collections.abc import Iterator, Mapping
from contextlib import suppress
from threading import RLock
from time import perf_counter, time
from typing import TYPE_CHECKING, Any, cast

from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    GCCollector,
    Histogram,
    PlatformCollector,
    ProcessCollector,
    generate_latest,
)
from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily, Metric
from starlette.types import ASGIApp, Message, Receive, Scope, Send
from typing_extensions import override

from powercontext.builtin.runtime.processing_contracts import ArtifactProcessingModelUsageDelta
from powercontext.limits import MAX_ARTIFACT_FAMILY_LENGTH
from powercontext.server.context import is_internal_bridge
from powercontext.server.operational_metrics import OperationalMetricsCollector

if TYPE_CHECKING:
    from powercontext.builtin.runtime.application import BuiltinRuntime

if TYPE_CHECKING:
    from typing import Protocol

    class ArtifactProcessingSnapshotSource(Protocol):
        """Structural source of one bounded processing family snapshot."""

        @property
        def family_status(self) -> Mapping[str, Mapping[str, int | float | str]]: ...


_DEPENDENCY_NAMES = frozenset({
    "database",
    "runtime",
    "inference.generation",
    "inference.embedding",
    "inference.rerank",
    "inference.decision",
    "artifact_processing_supervisor",
    "access_provider",
    "authentication_provider",
})
# Access and supervisor checks share the Runtime vocabulary plus their bounded policy states.
_DEPENDENCY_STATES = (
    "unobserved",
    "ready",
    "unavailable",
    "timeout",
    "misconfigured",
    "not_ready",
    "disabled",
    "degraded",
    "leader",
    "standby",
)

_PROCESSING_INSTRUMENTS = (
    (
        "unacknowledged_observed_timestamp_seconds",
        "Unix time of the unacknowledged snapshot; zero means unobserved.",
        False,
    ),
    ("supervisor_alive", "Whether the owning supervisor task is running.", False),
    ("leader", "Whether this process holds the supervisor fence.", False),
    ("degraded", "Whether discovery is degraded.", False),
    ("blocked", "Locally retained invocations blocked by domain policy.", False),
    (
        "discovered_timestamp_seconds",
        "Unix time of the latest successful discovery pass; zero means unobserved.",
        False,
    ),
    (
        "last_success_timestamp_seconds",
        "Unix time of the latest verified acknowledgement; zero means unobserved.",
        False,
    ),
    (
        "oldest_observed_pending_timestamp_seconds",
        "Oldest locally observed unfinished admission; zero means none observed.",
        False,
    ),
    ("max_workers", "Configured Worker capacity for this Artifact Family.", False),
    ("available_workers", "Available Worker slots for this Artifact Family.", False),
    ("used_workers", "Worker slots currently in use for this Artifact Family.", False),
    ("ready", "Scope invocations in this Artifact Family's bounded ready queue.", False),
    ("retry_wait", "Scope invocations retained in this Artifact Family's bounded retry cache.", False),
    (
        "unacknowledged_requests",
        "Snapshot of Scopes with unacknowledged requests at the latest background pending observation.",
        False,
    ),
    ("discovery_seconds", "Duration in seconds of this Artifact Family's most recent discovery pass.", False),
    ("last_invocation_seconds", "Duration in seconds of this Artifact Family's most recent invocation.", False),
    ("completed", "Acknowledged invocations accumulated by the current Supervisor instance; resets on restart.", True),
    ("failed", "Failed invocations accumulated by the current Supervisor instance; resets on restart.", True),
    ("timeouts", "Timed-out invocations accumulated by the current Supervisor instance; resets on restart.", True),
)


class _ProcessingMetricsCollector:
    """Render registered Family snapshots without retaining stale label values."""

    def __init__(self) -> None:
        self._snapshot: dict[str, dict[str, float]] = {}

    def replace(self, families: Mapping[str, Mapping[str, int | float | str]]) -> None:
        snapshot: dict[str, dict[str, float]] = {}
        for family, values in families.items():
            if len(family) > MAX_ARTIFACT_FAMILY_LENGTH or re.fullmatch(r"[a-z][a-z0-9-]*", family) is None:
                continue
            measurements: dict[str, float] = {}
            for field, _, _ in _PROCESSING_INSTRUMENTS:
                value = values.get(field)
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    with suppress(OverflowError):
                        number = float(value)
                        if math.isfinite(number) and number >= 0:
                            measurements[field] = number
            snapshot[family] = measurements
        # A scrape observes one complete snapshot even when another thread
        # replaces the registered Family set during collection.
        self._snapshot = snapshot

    def collect(self) -> Iterator[Metric]:
        snapshot = self._snapshot
        for field, description, counter in _PROCESSING_INSTRUMENTS:
            instrument_type = CounterMetricFamily if counter else GaugeMetricFamily
            instrument = instrument_type(
                f"powercontext_server_artifact_processing_{field}", description, labels=["family"]
            )
            for family, measurements in snapshot.items():
                if field in measurements:
                    instrument.add_metric([family], measurements[field])
            yield instrument


class _ModelUsageMetricsCollector:
    """Render local queue gauges and local plus completed-worker loss counters."""

    def __init__(self) -> None:
        self.snapshot: tuple[int, int, int, int] | None = None
        self.worker_losses: tuple[int, int] | None = None

    def collect(self) -> Iterator[Metric]:
        snapshot = self.snapshot
        worker_losses = self.worker_losses
        if snapshot is None and worker_losses is None:
            return
        if snapshot is not None:
            for name, value, description in (
                ("queue_depth", snapshot[0], "Local queued records, excluding in-flight writes and child queues."),
                ("queue_capacity", snapshot[1], "Local recorder queue capacity, excluding child queues."),
            ):
                yield GaugeMetricFamily("powercontext_server_model_usage_" + name, description, value=value)
        local_losses = (0, 0) if snapshot is None else snapshot[2:]
        losses = tuple(local + worker for local, worker in zip(local_losses, worker_losses or (0, 0), strict=True))
        for name, value, description in zip(
            ("dropped_records", "write_failures"),
            losses,
            (
                "Dropped records from the local recorder and received child reports; resets on restart.",
                "Failed/indeterminate writes from the local recorder and received child reports; resets on restart.",
            ),
            strict=True,
        ):
            yield CounterMetricFamily("powercontext_server_model_usage_" + name, description, value=value)


class ServerMetrics:
    """Own one Server instance's Prometheus registry and instruments."""

    def __init__(self, *, role: str = "server") -> None:
        self.registry = CollectorRegistry()
        self.operational = OperationalMetricsCollector(role=role)
        self._snapshot_lock = RLock()
        ProcessCollector(registry=self.registry)
        PlatformCollector(registry=self.registry)
        GCCollector(registry=self.registry)
        self.transport_requests = Counter(
            "powercontext_server_transport_requests_total",
            "External transport requests completed by the Server.",
            ("transport", "operation", "outcome"),
            registry=self.registry,
        )
        self.transport_duration = Histogram(
            "powercontext_server_transport_request_duration_seconds",
            "External transport request duration.",
            ("transport", "operation", "outcome"),
            registry=self.registry,
        )
        self.transport_in_progress = Gauge(
            "powercontext_server_transport_requests_in_progress",
            "External transport requests currently in progress.",
            ("transport", "operation"),
            registry=self.registry,
        )
        self.application_operations = Counter(
            "powercontext_server_application_operations_total",
            "PowerContext application operations completed by the Server.",
            ("operation", "outcome"),
            registry=self.registry,
        )
        self.application_duration = Histogram(
            "powercontext_server_application_operation_duration_seconds",
            "PowerContext application operation duration.",
            ("operation", "outcome"),
            registry=self.registry,
        )
        self.topic_memory_searches = Counter(
            "powercontext_server_topic_memory_searches_total",
            "Topic Memory searches by actual mode and embedding fallback outcome.",
            ("mode", "fallback"),
            registry=self.registry,
        )
        self.runtime_ready = Gauge(
            "powercontext_server_runtime_ready",
            "Whether the built-in Runtime can accept operations.",
            registry=self.registry,
        )
        self.runtime_scopes = Gauge(
            "powercontext_server_runtime_scopes",
            "Scope compositions currently active or retained by the built-in Runtime.",
            ("state",),
            registry=self.registry,
        )
        self.readiness_states = Gauge(
            "powercontext_server_readiness_state",
            "Latest evaluated aggregate readiness, including access and authentication providers.",
            ("state",),
            registry=self.registry,
        )
        self.readiness_evaluated = Gauge(
            "powercontext_server_readiness_evaluated_timestamp_seconds",
            "Unix time readiness was evaluated (probes may be cached).",
            registry=self.registry,
        )
        self.dependency_states = Gauge(
            "powercontext_server_dependency_state",
            "Latest evaluated state for exposed dependency checks; missing means not configured or not state-shaped.",
            ("dependency", "state"),
            # The passive collector owns the same metric family for dependency
            # probes. Merge both views in collect() instead of registering twice.
            registry=None,
        )
        self.processing_duration = Histogram(
            "powercontext_server_artifact_processing_invocation_duration_seconds",
            "One reaped invocation attempt, including launch, execution and acknowledgement; retries are separate samples.",
            ("family", "outcome"),
            buckets=(0.1, 0.5, 1, 5, 15, 30, 60, 120, 300, 600, 1800),
            registry=self.registry,
        )
        self._usage = _ModelUsageMetricsCollector()
        self.registry.register(self._usage)
        self.worker_usage_reports = Counter(
            "powercontext_server_model_usage_worker_reports_total",
            "Worker attempts with a received recorder summary or missing observations; missing is not zero losses.",
            ("outcome",),
            registry=self.registry,
        )
        self.collection_failures = Counter(
            "powercontext_server_metrics_collection_failures_total",
            "Failed passive Runtime snapshot refreshes.",
            registry=self.registry,
        )
        self._processing = _ProcessingMetricsCollector()
        self.registry.register(self)
        self.set_runtime_scopes(0, 0)

    def start_transport(self, transport: str, operation: str) -> float:
        with suppress(Exception):
            self.transport_in_progress.labels(transport=transport, operation=operation).inc()
        return perf_counter()

    def finish_transport(self, transport: str, operation: str, outcome: str, started_at: float) -> None:
        duration = max(perf_counter() - started_at, 0)
        with suppress(Exception):
            self.transport_requests.labels(
                transport=transport,
                operation=operation,
                outcome=outcome,
            ).inc()
        with suppress(Exception):
            self.transport_duration.labels(
                transport=transport,
                operation=operation,
                outcome=outcome,
            ).observe(duration)
        with suppress(Exception):
            self.transport_in_progress.labels(transport=transport, operation=operation).dec()

    def observe_application(self, operation: str, outcome: str, started_at: float) -> None:
        duration = max(perf_counter() - started_at, 0)
        with suppress(Exception):
            self.application_operations.labels(operation=operation, outcome=outcome).inc()
        with suppress(Exception):
            self.application_duration.labels(operation=operation, outcome=outcome).observe(duration)

    def observe_topic_memory_search(self, mode: str, fallback: bool) -> None:
        with suppress(Exception):
            self.topic_memory_searches.labels(mode=mode, fallback=str(fallback).lower()).inc()

    def set_ready(self, ready: bool) -> None:
        with suppress(Exception):
            self.runtime_ready.set(1 if ready else 0)

    def observe_model_usage(self, queued: int, capacity: int, dropped: int, failed: int) -> None:
        with suppress(Exception):
            self._usage.snapshot = (queued, capacity, dropped, failed)

    def observe_worker_model_usage(self, delta: ArtifactProcessingModelUsageDelta | None) -> None:
        """Add one attempt's child counters once, independently of durable acknowledgement."""
        with suppress(Exception):
            if delta is None:
                self.worker_usage_reports.labels(outcome="missing").inc()
                return
            dropped, failed = self._usage.worker_losses or (0, 0)
            self._usage.worker_losses = (dropped + delta.dropped_records, failed + delta.write_failures)
            self.worker_usage_reports.labels(outcome="received").inc()

    def observe_readiness(self, state: str, checks: Mapping[str, str]) -> None:
        """Publish the last evaluation without executing dependency probes at scrape time."""
        with self._snapshot_lock, suppress(Exception):
            for candidate in ("ready", "degraded", "not_ready"):
                self.readiness_states.labels(state=candidate).set(int(state == candidate))
            self.dependency_states.clear()
            for dependency, result in checks.items():
                if dependency not in _DEPENDENCY_NAMES and not dependency.startswith("artifact_processing."):
                    continue
                result = result.split(":", 1)[0]
                if result not in _DEPENDENCY_STATES:
                    result = "unavailable"
                for candidate in _DEPENDENCY_STATES:
                    self.dependency_states.labels(dependency=dependency, state=candidate).set(int(result == candidate))
            self.readiness_evaluated.set(time())

    def observe_processing(self, family: str, outcome: str, duration: float) -> None:
        with suppress(Exception):
            if len(family) > MAX_ARTIFACT_FAMILY_LENGTH or re.fullmatch(r"[a-z][a-z0-9-]*", family) is None:
                return
            if outcome not in {"success", "failure", "blocked", "cancelled", "conflict", "timeout", "leadership_lost"}:
                return
            if math.isfinite(duration) and duration >= 0:
                self.processing_duration.labels(family=family, outcome=outcome).observe(duration)

    def set_runtime_scopes(self, cached: int, active: int) -> None:
        for state, value in (("active", active), ("cached", cached)):
            with suppress(Exception):
                self.runtime_scopes.labels(state=state).set(value)

    def set_processing_families(
        self,
        supervisor: ArtifactProcessingSnapshotSource | Mapping[str, Mapping[str, int | float | str]] | None,
    ) -> None:
        """Refresh a bounded snapshot or source without changing the last good observation on failure."""

        with self._snapshot_lock:
            try:
                if supervisor is None:
                    families: Mapping[str, Mapping[str, int | float | str]] = {}
                elif isinstance(supervisor, Mapping):
                    families = cast(Mapping[str, Mapping[str, int | float | str]], supervisor)
                else:
                    families = supervisor.family_status
                self._processing.replace(families)
            except Exception:
                with suppress(Exception):
                    self.collection_failures.inc()

    def refresh_runtime(self, runtime: BuiltinRuntime | None) -> None:
        """Stage counts and freshness together; retain both on collection failure."""
        with self._snapshot_lock:
            try:
                supervisor = None if runtime is None else runtime.artifact_processing_supervisor
                families = {} if supervisor is None else supervisor.family_status
                processing = _ProcessingMetricsCollector()
                processing.replace(families)
                operational = self.operational.prepare(runtime, families=families)
            except Exception:
                self.operational.collection_failed()
                with suppress(Exception):
                    self.collection_failures.inc()
                return
            self._processing = processing
            self.operational.publish(operational)

    def collect(self) -> Iterator[Metric]:
        # Capture the pair before yielding: registry readers in other threads must
        # never combine processing counts with a different freshness observation.
        with self._snapshot_lock:
            operational = tuple(self.operational.collect())
            # Actual dependency probes own their passive states and freshness;
            # the Server evaluation adds access/authentication and supervisor
            # checks without emitting duplicate series for shared dependencies.
            dependency_name = "powercontext_server_dependency_state"
            states: dict[str, str] = {}
            for metric in (*self.dependency_states.collect(), *operational):
                if metric.name == dependency_name:
                    for sample in metric.samples:
                        if sample.value == 1:
                            states[sample.labels["dependency"]] = sample.labels["state"]
            dependencies = GaugeMetricFamily(
                dependency_name,
                "Latest observed dependency state; probe freshness is reported separately.",
                labels=["dependency", "state"],
            )
            for dependency, state in states.items():
                for candidate in _DEPENDENCY_STATES:
                    dependencies.add_metric([dependency, candidate], float(state == candidate))
            snapshot = (
                *(metric for metric in operational if metric.name != dependency_name),
                dependencies,
                *self._processing.collect(),
            )
        yield from snapshot

    def render(self) -> bytes:
        return generate_latest(self.registry)


class HttpMetricsMiddleware:
    """Measure external HTTP requests with declared operation identities."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        metrics: ServerMetrics,
        operations: dict[tuple[str, str], str],
        skip_paths: tuple[str, ...] = (),
    ) -> None:
        self.app = app
        self.metrics = metrics
        self.operations = operations
        self.skip_paths = skip_paths

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or is_internal_bridge() or scope["path"].startswith(self.skip_paths):
            await self.app(scope, receive, send)
            return

        operation = self.operations.get((scope["method"], scope["path"]), "unmatched")
        started_at = self.metrics.start_transport("http", operation)
        completed = False
        status_code = 500

        async def send_with_metrics(message: Message) -> None:
            nonlocal completed, status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
            await send(message)
            if message["type"] == "http.response.body" and not message.get("more_body", False):
                self.metrics.finish_transport(
                    "http",
                    operation,
                    "success" if status_code < 400 else "failure",
                    started_at,
                )
                completed = True

        try:
            await self.app(scope, receive, send_with_metrics)
        except asyncio.CancelledError:
            if not completed:
                self.metrics.finish_transport("http", operation, "cancelled", started_at)
            raise
        except Exception:
            if not completed:
                self.metrics.finish_transport("http", operation, "failure", started_at)
            raise


class McpMetricsMiddleware(Middleware):
    """Measure logical MCP requests rather than Streamable HTTP frames."""

    def __init__(self, metrics: ServerMetrics) -> None:
        self.metrics = metrics

    @override
    async def on_request(
        self,
        context: MiddlewareContext[Any],
        call_next: CallNext[Any, Any],
    ) -> Any:
        operation = f"mcp.{(context.method or 'unknown').replace('/', '.')}"
        started_at = self.metrics.start_transport("mcp", operation)
        try:
            result = await call_next(context)
        except asyncio.CancelledError:
            self.metrics.finish_transport("mcp", operation, "cancelled", started_at)
            raise
        except Exception:
            self.metrics.finish_transport("mcp", operation, "failure", started_at)
            raise
        self.metrics.finish_transport("mcp", operation, "success", started_at)
        return result


__all__ = [
    "CONTENT_TYPE_LATEST",
    "HttpMetricsMiddleware",
    "McpMetricsMiddleware",
    "ServerMetrics",
]
