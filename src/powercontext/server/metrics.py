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
from collections.abc import Iterator, Mapping, Sequence
from contextlib import suppress
from time import perf_counter
from typing import Any

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

from powercontext.builtin.persistence.work import WorkQueueStatistic
from powercontext.limits import MAX_ARTIFACT_FAMILY_LENGTH
from powercontext.server.context import is_internal_bridge

_PROCESSING_INSTRUMENTS = (
    ("max_workers", "Configured Worker capacity for this Artifact Family.", False),
    ("available_workers", "Available Worker slots for this Artifact Family.", False),
    ("used_workers", "Worker slots currently in use for this Artifact Family.", False),
    ("ready", "Scope invocations in this Artifact Family's bounded ready queue.", False),
    ("retry_wait", "Scope invocations retained in this Artifact Family's bounded retry cache.", False),
    (
        "unacknowledged_requests",
        "Snapshot of Scopes with unacknowledged requests at the latest requested-work discovery pass.",
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


class ServerMetrics:
    """Own one Server instance's Prometheus registry and instruments."""

    def __init__(self) -> None:
        self.registry = CollectorRegistry()
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
        self.work_enqueues = Counter(
            "powercontext_work_enqueues_total",
            "Durable logical work enqueue decisions.",
            ("kind", "outcome"),
            registry=self.registry,
        )
        self.work_claim_latency = Histogram(
            "powercontext_work_claim_latency_seconds",
            "Time from durable work creation to a Worker claim.",
            ("kind",),
            registry=self.registry,
        )
        self.work_attempts = Counter(
            "powercontext_work_attempts_total",
            "Completed durable work attempts.",
            ("kind", "outcome", "error_category"),
            registry=self.registry,
        )
        self.work_attempt_duration = Histogram(
            "powercontext_work_attempt_duration_seconds",
            "Durable work attempt execution duration.",
            ("kind", "outcome"),
            registry=self.registry,
        )
        self.work_lease_expirations = Counter(
            "powercontext_work_lease_expirations_total",
            "Worker leases recovered after database-time expiry.",
            ("kind", "outcome"),
            registry=self.registry,
        )
        self.scheduler_leadership_changes = Counter(
            "powercontext_scheduler_leadership_changes_total",
            "Scheduler leadership transitions observed by this process.",
            ("outcome",),
            registry=self.registry,
        )
        self.work_queue_depth = Gauge(
            "powercontext_work_queue_depth",
            "Durable non-terminal work grouped by bounded kind and state.",
            ("kind", "status"),
            registry=self.registry,
        )
        self.work_queue_oldest_age = Gauge(
            "powercontext_work_queue_oldest_age_seconds",
            "Age of the oldest durable work item in each bounded queue group.",
            ("kind", "status"),
            registry=self.registry,
        )
        self.runtime_role_members = Gauge(
            "powercontext_runtime_role_members",
            "Compatible live runtime members grouped by role.",
            ("role",),
            registry=self.registry,
        )
        self._work_queue_labels: set[tuple[str, str]] = set()
        self._processing = _ProcessingMetricsCollector()
        self.registry.register(self._processing)
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

    def set_runtime_scopes(self, cached: int, active: int) -> None:
        for state, value in (("active", active), ("cached", cached)):
            with suppress(Exception):
                self.runtime_scopes.labels(state=state).set(value)

    def observe_work_enqueue(self, kind: str, *, created: bool) -> None:
        self.work_enqueues.labels(kind=kind, outcome="created" if created else "joined").inc()

    def observe_work_claim(self, kind: str, *, latency_seconds: float) -> None:
        self.work_claim_latency.labels(kind=kind).observe(max(0.0, latency_seconds))

    def observe_work_attempt(
        self,
        kind: str,
        *,
        outcome: str,
        error_category: str,
        duration_seconds: float,
    ) -> None:
        self.work_attempts.labels(
            kind=kind,
            outcome=outcome,
            error_category=error_category,
        ).inc()
        self.work_attempt_duration.labels(kind=kind, outcome=outcome).observe(max(0.0, duration_seconds))

    def observe_work_lease_expiry(self, kind: str, *, outcome: str) -> None:
        self.work_lease_expirations.labels(kind=kind, outcome=outcome).inc()

    def observe_scheduler_leadership(self, *, outcome: str) -> None:
        self.scheduler_leadership_changes.labels(outcome=outcome).inc()

    def set_work_queue(self, samples: Sequence[WorkQueueStatistic]) -> None:
        current = {(sample.kind, str(sample.status)) for sample in samples}
        for kind, status in self._work_queue_labels - current:
            self.work_queue_depth.labels(kind=kind, status=status).set(0)
            self.work_queue_oldest_age.labels(kind=kind, status=status).set(0)
        for sample in samples:
            status = str(sample.status)
            self.work_queue_depth.labels(kind=sample.kind, status=status).set(sample.depth)
            self.work_queue_oldest_age.labels(kind=sample.kind, status=status).set(sample.oldest_age_seconds)
        self._work_queue_labels = current

    def set_runtime_members(self, counts: Mapping[str, int]) -> None:
        for role in ("all", "api", "scheduler", "worker"):
            self.runtime_role_members.labels(role=role).set(counts.get(role, 0))

    def set_processing_families(self, families: Mapping[str, Mapping[str, int | float | str]]) -> None:
        """Refresh bounded scheduler observations using only registered Family labels."""

        with suppress(Exception):
            self._processing.replace(families)

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
