"""Prometheus-compatible metrics for the ready-to-run Server."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from time import perf_counter
from typing import Any

from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Counter, Gauge, Histogram, generate_latest
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from powercontext.server.context import is_internal_bridge


class ServerMetrics:
    """Own one Server instance's Prometheus registry and instruments."""

    def __init__(self) -> None:
        self.registry = CollectorRegistry()
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
        self.runtime_ready = Gauge(
            "powercontext_server_runtime_ready",
            "Whether the built-in Runtime is ready.",
            registry=self.registry,
        )

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

    def set_ready(self, ready: bool) -> None:
        with suppress(Exception):
            self.runtime_ready.set(1 if ready else 0)

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
