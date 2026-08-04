"""Minimal transport access logging for the ready-to-run Server."""

from __future__ import annotations

import asyncio
import logging
from time import perf_counter
from typing import Any

from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from powercontext._logging import log_safely
from powercontext.server.context import current_request_id, is_internal_bridge

logger = logging.getLogger("powercontext.server.access")


class HttpAccessLogMiddleware:
    """Log one completion record for each external HTTP request."""

    def __init__(self, app: ASGIApp, *, skip_paths: tuple[str, ...] = ()) -> None:
        self.app = app
        self.skip_paths = skip_paths

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or is_internal_bridge() or scope["path"].startswith(self.skip_paths):
            await self.app(scope, receive, send)
            return

        started_at = perf_counter()
        status_code = 500

        async def send_with_access_log(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
            await send(message)
            if message["type"] == "http.response.body" and not message.get("more_body", False):
                _log_transport_completion(
                    transport="http",
                    operation=_http_operation(scope),
                    outcome="success" if status_code < 400 else "failure",
                    started_at=started_at,
                    status_code=status_code,
                    request_id=_scope_request_id(scope),
                )

        try:
            await self.app(scope, receive, send_with_access_log)
        except asyncio.CancelledError:
            _log_transport_completion(
                transport="http",
                operation=_http_operation(scope),
                outcome="cancelled",
                started_at=started_at,
                request_id=_scope_request_id(scope),
            )
            raise


class McpAccessLogMiddleware(Middleware):
    """Log logical MCP protocol requests instead of Streamable HTTP frames."""

    async def on_request(
        self,
        context: MiddlewareContext[Any],
        call_next: CallNext[Any, Any],
    ) -> Any:
        started_at = perf_counter()
        try:
            result = await call_next(context)
        except asyncio.CancelledError:
            _log_transport_completion(
                transport="mcp",
                operation=_mcp_operation(context),
                outcome="cancelled",
                started_at=started_at,
                request_id=current_request_id(),
            )
            raise
        except Exception:
            _log_transport_completion(
                transport="mcp",
                operation=_mcp_operation(context),
                outcome="failure",
                started_at=started_at,
                request_id=current_request_id(),
            )
            raise
        _log_transport_completion(
            transport="mcp",
            operation=_mcp_operation(context),
            outcome="success",
            started_at=started_at,
            request_id=current_request_id(),
        )
        return result


def _http_operation(scope: Scope) -> str:
    route = scope.get("route")
    operation_id = getattr(route, "operation_id", None)
    return operation_id if isinstance(operation_id, str) else "unmatched"


def _mcp_operation(context: MiddlewareContext[Any]) -> str:
    return f"mcp.{(context.method or 'unknown').replace('/', '.')}"


def _scope_request_id(scope: Scope) -> str | None:
    state = scope.get("state")
    if not isinstance(state, dict):
        return None
    request_id = state.get("request_id")
    return request_id if isinstance(request_id, str) else None


def _log_transport_completion(
    *,
    transport: str,
    operation: str,
    outcome: str,
    started_at: float,
    request_id: str | None,
    status_code: int | None = None,
) -> None:
    extra = {
        "event": "transport.request.completed",
        "operation": operation,
        "outcome": outcome,
        "request_id": request_id,
        "transport": transport,
        "unit": "transport",
        "duration_ms": max(perf_counter() - started_at, 0) * 1_000,
    }
    if status_code is not None:
        extra["status_code"] = status_code
    level = logging.ERROR if status_code is not None and status_code >= 500 else logging.INFO
    log_safely(logger, level, "PowerContext transport request completed", extra=extra)


__all__ = ["HttpAccessLogMiddleware", "McpAccessLogMiddleware"]
