"""Render received provider observations without replaying individual calls."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import suppress
from time import time

from opentelemetry import trace
from prometheus_client import CollectorRegistry, Counter, Gauge
from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily, HistogramMetricFamily, Metric

from powercontext.builtin.inference.observability import (
    PROVIDER_DURATION_BUCKETS,
    ProviderObservation,
    ProviderObservationAggregate,
    ProviderObservationCollector,
    ProviderObservationReport,
)

_PREFIX = "powercontext_server_provider_"


def record_provider_span(observation: ProviderObservation) -> None:
    with suppress(Exception):
        if observation.kind == "coverage":
            return
        attributes: dict[str, str | int | float] = {
            "powercontext.provider.name": observation.provider,
            "powercontext.provider.capability": observation.capability,
            "powercontext.provider.outcome": observation.outcome,
            "powercontext.provider.duration_ms": observation.seconds * 1000,
        }
        if observation.status_code is not None:
            attributes["http.response.status_code"] = observation.status_code
        if (
            observation.kind == "business"
            and type(observation.business_code) is int
            and 0 <= observation.business_code <= 2**31 - 1
        ):
            attributes["powercontext.provider.error_code"] = observation.business_code
        trace.get_current_span().add_event("provider." + observation.kind + ".completed", attributes)


class ProviderMetrics:
    """Keep parent observations separate from completed-worker aggregate reports."""

    def __init__(self, registry: CollectorRegistry) -> None:
        self._parent = ProviderObservationCollector(max_keys=2048)
        self._worker: ProviderObservationCollector | None = None
        self._reports = Counter(
            _PREFIX + "worker_reports_total",
            "Worker attempts with a received or missing provider observation report.",
            ("outcome",),
            registry=registry,
        )
        for outcome in ("received", "missing"):
            self._reports.labels(outcome)
        self._reported_at = Gauge(
            _PREFIX + "worker_report_timestamp_seconds",
            "Unix time of the latest received worker provider report; zero is unobserved.",
            registry=registry,
        )
        registry.register(self)

    def observe(self, observation: ProviderObservation) -> None:
        with suppress(Exception):
            self._parent.observe(observation)
        record_provider_span(observation)

    def observe_worker(self, report: ProviderObservationReport | None) -> None:
        with suppress(Exception):
            self._reports.labels("missing" if report is None else "received").inc()
            if report is not None:
                if self._worker is None:
                    self._worker = ProviderObservationCollector(max_keys=2048)
                self._worker.merge(report)
                self._reported_at.set(time())

    def collect(self) -> Iterator[Metric]:  # noqa: C901 - fixed signal projection shares one immutable snapshot.
        snapshots = [("parent", self._parent.snapshot())]
        if self._worker is not None:
            snapshots.append(("worker", self._worker.snapshot()))
        rows = [(execution, row) for execution, snapshot in snapshots for row in snapshot.rows]
        base = ["provider", "capability", "execution"]
        for suffix, description, kind, extra in (
            (
                "http_attempts",
                "HTTP exchanges completed at headers or failed before headers; retry is SDK-marked.",
                "http",
                ["outcome", "retry"],
            ),
            (
                "http_responses",
                "Actual HTTP response headers received from providers, including redirects.",
                "http",
                ["status_code"],
            ),
            (
                "operations",
                "Final generation or embedding adapter outcomes; one operation can use several HTTP exchanges.",
                "operation",
                ["outcome"],
            ),
            (
                "business_errors",
                "Provider business rejections independently of HTTP status; numeric codes stay in logs and traces.",
                "business",
                [],
            ),
        ):
            instrument = CounterMetricFamily(_PREFIX + suffix, description, labels=base + extra)
            grouped: dict[tuple[str, ...], int] = {}
            for execution, row in rows:
                key = row.key
                if key.kind != kind or (suffix == "http_responses" and not key.status_code):
                    continue
                labels = (key.provider, key.capability, execution, *(getattr(key, field) for field in extra))
                grouped[labels] = grouped.get(labels, 0) + row.count
            for labels, count in grouped.items():
                instrument.add_metric(list(labels), count)
            yield instrument
        for kind, name, description in (
            (
                "http",
                "http_header_duration_seconds",
                "Time to provider HTTP response headers or pre-header transport failure; excludes response-body reading.",
            ),
            (
                "operation",
                "operation_duration_seconds",
                "Final model adapter duration including retries, body reading and validation.",
            ),
        ):
            histogram = HistogramMetricFamily(_PREFIX + name, description, labels=[*base, "outcome"])
            groups: dict[tuple[str, ...], list[ProviderObservationAggregate]] = {}
            for execution, row in rows:
                if row.key.kind == kind:
                    labels = (row.key.provider, row.key.capability, execution, row.key.outcome)
                    groups.setdefault(labels, []).append(row)
            for labels, values in groups.items():
                buckets = [
                    ("+Inf" if bound == float("inf") else str(bound), sum(value.buckets[index] for value in values))
                    for index, bound in enumerate(PROVIDER_DURATION_BUCKETS)
                ]
                histogram.add_metric(list(labels), buckets=buckets, sum_value=sum(value.seconds for value in values))
            yield histogram
        coverage = GaugeMetricFamily(
            _PREFIX + "http_observation_supported",
            "Whether observed composition exposes its HTTP transport; zero means unavailable, not healthy.",
            labels=base,
        )
        support: dict[tuple[str, ...], int] = {}
        for execution, row in rows:
            if row.key.kind == "coverage":
                labels = (row.key.provider, row.key.capability, execution)
                support[labels] = min(support.get(labels, 1), int(row.key.outcome == "available"))
        for labels, supported in support.items():
            coverage.add_metric(list(labels), supported)
        yield coverage
        dropped = CounterMetricFamily(
            _PREFIX + "observations_dropped",
            "Observations omitted by bounded provider collectors; not recovered on scrape.",
            labels=["execution"],
        )
        for execution, snapshot in snapshots:
            dropped.add_metric([execution], snapshot.dropped)
        yield dropped
