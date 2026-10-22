"""Optional, content-free observations of provider exchanges and model operations."""

from __future__ import annotations

import asyncio
import logging
import math
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from contextvars import ContextVar
from dataclasses import dataclass
from threading import Lock
from time import perf_counter
from typing import Literal

import httpx
from typing_extensions import override

from powercontext._logging import error_category, log_safely
from powercontext.builtin.inference.errors import InferenceTimeoutError

PROVIDER_DURATION_BUCKETS = (0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 15.0, 30.0, 60.0, 300.0, math.inf)
_EMPTY_BUCKETS = (0,) * len(PROVIDER_DURATION_BUCKETS)
_PROVIDERS = frozenset({
    "openai",
    "anthropic",
    "google",
    "bedrock",
    "azure",
    "groq",
    "mistral",
    "cohere",
    "deepseek",
    "openrouter",
    "ollama",
    "huggingface",
    "minimax",
    "other",
})
Capability = Literal["generation", "embedding"]
ObservationKind = Literal["http", "operation", "business", "coverage"]


def provider_name(value: object) -> str:
    """Keep provider identity finite, without accepting model names or URLs as labels."""
    if not isinstance(value, str) or len(value) > 64:
        return "other"
    value = value.casefold()
    if value in {"openai-chat", "openai-responses"}:
        return "openai"
    if value in {"google-gla", "google-vertex"}:
        return "google"
    return value if value in _PROVIDERS else "other"


@dataclass(frozen=True, slots=True)
class ProviderObservation:
    kind: ObservationKind
    provider: str
    capability: Capability
    outcome: str
    seconds: float = 0.0
    status_code: int | None = None
    retry: str = "unknown"
    business_code: int | None = None

    def __post_init__(self) -> None:
        if (
            self.kind not in {"http", "operation", "business", "coverage"}
            or self.provider not in _PROVIDERS
            or self.capability not in {"generation", "embedding"}
            or self.outcome not in {"success", "failure", "timeout", "cancelled", "available", "unavailable"}
            or self.retry not in {"initial", "retry", "unknown"}
            or not math.isfinite(self.seconds)
            or self.seconds < 0
            or (
                self.status_code is not None
                and (type(self.status_code) is not int or not 200 <= self.status_code <= 599)
            )
        ):
            raise ValueError("invalid provider observation")  # noqa: TRY003


ProviderObserver = Callable[[ProviderObservation], None]
_OBSERVER: ContextVar[ProviderObserver | None] = ContextVar("powercontext_provider_observer", default=None)
_SUPPRESSED: ContextVar[bool] = ContextVar("powercontext_provider_suppressed", default=False)


@contextmanager
def bind_provider_observer(observer: ProviderObserver | None) -> Iterator[None]:
    """Supply an optional observer when composing model adapters in this task."""
    token = _OBSERVER.set(observer)
    try:
        yield
    finally:
        _OBSERVER.reset(token)


@contextmanager
def suppress_provider_observation() -> Iterator[None]:
    """Keep readiness traffic separate from business provider observations."""
    token = _SUPPRESSED.set(True)
    try:
        yield
    finally:
        _SUPPRESSED.reset(token)


def current_provider_observer() -> ProviderObserver | None:
    return _OBSERVER.get()


def _emit(observer: ProviderObserver, observation: ProviderObservation) -> None:
    with suppress(Exception):
        observer(observation)
    if observation.kind == "coverage":
        return
    extra: dict[str, str | int | float | None] = {
        "event": "provider." + observation.kind + ".completed",
        "operation": "provider." + observation.capability,
        "unit": "dependency",
        "provider": observation.provider,
        "model_capability": observation.capability,
        "outcome": observation.outcome,
        "duration_ms": observation.seconds * 1000,
    }
    if observation.kind == "http":
        extra.update(status_code=observation.status_code, provider_retry=observation.retry)
    if observation.kind == "business":
        code = observation.business_code
        extra["provider_error_code"] = code if type(code) is int and 0 <= code <= 2**31 - 1 else None
    if observation.outcome in {"failure", "timeout", "cancelled"}:
        extra["error_category"] = (
            "cancelled"
            if observation.outcome == "cancelled"
            else "timeout"
            if observation.outcome == "timeout"
            else "dependency"
            if observation.kind == "business" or observation.status_code is None or observation.status_code >= 500
            else error_category(None, status_code=observation.status_code, outcome=observation.outcome)
        )
    log_safely(
        logging.getLogger(__name__),
        logging.ERROR if observation.outcome in {"failure", "timeout"} else logging.INFO,
        "Provider observation completed",
        extra=extra,
    )


@dataclass(frozen=True, slots=True)
class ProviderObservationSource:
    provider: str
    capability: Capability
    observer: ProviderObserver | None

    @contextmanager
    def operation(self) -> Iterator[None]:
        active = self if self.observer is not None and not _SUPPRESSED.get() else None
        token = _ACTIVE.set(active)
        started = perf_counter()
        outcome = "success"
        try:
            yield
        except BaseException as error:
            outcome = (
                "cancelled"
                if isinstance(error, asyncio.CancelledError)
                else "timeout"
                if isinstance(error, (InferenceTimeoutError, TimeoutError, httpx.TimeoutException))
                else "failure"
            )
            raise
        finally:
            _ACTIVE.reset(token)
            if active is not None:
                active.emit("operation", outcome=outcome, seconds=max(perf_counter() - started, 0))

    def emit(
        self,
        kind: ObservationKind,
        *,
        outcome: str,
        seconds: float = 0.0,
        status_code: int | None = None,
        retry: str = "unknown",
        business_code: int | None = None,
    ) -> None:
        if self.observer is not None and not _SUPPRESSED.get():
            with suppress(Exception):
                _emit(
                    self.observer,
                    ProviderObservation(
                        kind,
                        self.provider,
                        self.capability,
                        outcome,
                        seconds,
                        status_code,
                        retry,
                        business_code,
                    ),
                )


_ACTIVE: ContextVar[ProviderObservationSource | None] = ContextVar("powercontext_provider_call", default=None)


def observe_business_error(code: object) -> None:
    source = _ACTIVE.get()
    if source is not None:
        source.emit("business", outcome="failure", business_code=code if type(code) is int else None)


class _ObservedTransport(httpx.AsyncBaseTransport):
    def __init__(self, delegate: httpx.AsyncBaseTransport) -> None:
        self.delegate = delegate

    @override
    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        source = _ACTIVE.get()
        if source is None or _SUPPRESSED.get():
            return await self.delegate.handle_async_request(request)
        started = perf_counter()
        status = None
        outcome = "failure"
        marker = request.headers.get("x-stainless-retry-count", "")
        retry = (
            ("initial" if int(marker) == 0 else "retry")
            if marker.isascii() and marker.isdecimal() and len(marker) <= 6
            else "unknown"
        )
        try:
            response = await self.delegate.handle_async_request(request)
            status = response.status_code
            outcome = "success" if status < 400 else "failure"
        except asyncio.CancelledError:
            outcome = "cancelled"
            raise
        except httpx.TimeoutException:
            outcome = "timeout"
            raise
        except (httpx.ConnectError, httpx.NetworkError):
            outcome = "failure"
            raise
        else:
            return response
        finally:
            source.emit(
                "http", outcome=outcome, seconds=max(perf_counter() - started, 0), status_code=status, retry=retry
            )

    @override
    async def aclose(self) -> None:
        await self.delegate.aclose()


def observe_http_client(client: httpx.AsyncClient) -> None:
    """Wrap a newly owned client's transports, preserving its configuration and proxies.

    HTTPX has no transport-observation setter. Keep these two private seams here;
    the original transports retain all connection, TLS, proxy and retry policy.
    """
    from powercontext.builtin.inference.protected_transport import ProtectedAsyncTransport

    transports: dict[int, httpx.AsyncBaseTransport] = {}

    def wrap(transport: httpx.AsyncBaseTransport) -> httpx.AsyncBaseTransport:
        if isinstance(transport, _ObservedTransport):
            return transport
        if id(transport) not in transports:
            if isinstance(transport, ProtectedAsyncTransport):
                # Observe the wire exchange after send admission and before raw
                # response inspection, preserving both privacy boundaries.
                transports[id(transport)] = transport
                transport._transport = wrap(transport._transport)
            else:
                transports[id(transport)] = _ObservedTransport(transport)
        return transports[id(transport)]

    client._transport = wrap(client._transport)
    client._mounts = {
        pattern: None if transport is None else wrap(transport) for pattern, transport in client._mounts.items()
    }


@dataclass(frozen=True, slots=True)
class ProviderObservationKey:
    kind: ObservationKind
    provider: str
    capability: Capability
    outcome: str
    status_code: str
    retry: str


@dataclass(frozen=True, slots=True)
class ProviderObservationAggregate:
    key: ProviderObservationKey
    count: int
    seconds: float
    buckets: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class ProviderObservationReport:
    rows: tuple[ProviderObservationAggregate, ...] = ()
    dropped: int = 0


class ProviderObservationCollector:
    """Accumulate bounded counters and fixed buckets, never content or individual requests."""

    def __init__(self, *, max_keys: int = 128) -> None:
        self._max_keys = max_keys
        self._rows: dict[ProviderObservationKey, ProviderObservationAggregate] = {}
        self._dropped = 0
        self._lock = Lock()

    def observe(self, observation: ProviderObservation) -> None:
        key = ProviderObservationKey(
            observation.kind,
            observation.provider,
            observation.capability,
            observation.outcome,
            "" if observation.status_code is None else str(observation.status_code),
            observation.retry,
        )
        timed = observation.kind in {"http", "operation"}
        buckets = (
            tuple(int(observation.seconds <= bound) for bound in PROVIDER_DURATION_BUCKETS) if timed else _EMPTY_BUCKETS
        )
        self.merge(ProviderObservationReport((ProviderObservationAggregate(key, 1, observation.seconds, buckets),)))

    def merge(self, report: ProviderObservationReport) -> None:
        with self._lock:
            self._dropped += report.dropped
            for row in report.rows:
                old = self._rows.get(row.key)
                if old is None:
                    if len(self._rows) >= self._max_keys:
                        self._dropped += row.count
                        continue
                    self._rows[row.key] = row
                else:
                    self._rows[row.key] = ProviderObservationAggregate(
                        row.key,
                        old.count + row.count,
                        old.seconds + row.seconds,
                        tuple(a + b for a, b in zip(old.buckets, row.buckets, strict=True)),
                    )

    def snapshot(self) -> ProviderObservationReport:
        with self._lock:
            return ProviderObservationReport(tuple(self._rows.values()), self._dropped)
