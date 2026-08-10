"""MCP transport owned and configured by the PowerContext Server."""

from __future__ import annotations

import httpx
from fastapi import FastAPI
from fastmcp import FastMCP
from fastmcp.server.providers.openapi import MCPType, OpenAPIProvider
from fastmcp.utilities.lifespan import combine_lifespans
from fastmcp.utilities.openapi import HTTPRoute

from powercontext.http._generated.operations import (
    ACTIVATE_HANDOFF,
    APPROVE_ARTIFACT_CANDIDATE,
    CAPTURE_CONTENT_SOURCE,
    COMMIT_HANDOFF,
    CONTINUE_HANDOFF,
    FINALIZE_HANDOFF,
    GET_ARTIFACT_CANDIDATE,
    GET_HANDOFF_REPORT,
    GET_HANDOFF_REPORT_WORKSPACE,
    GET_MEMORY_ENTRY,
    LIST_ARTIFACT_CANDIDATES,
    LIST_MEMORY_ENTRIES,
    REJECT_ARTIFACT_CANDIDATE,
    REMEMBER_MEMORY,
    RETIRE_MEMORY_ENTRY,
    REVISE_ARTIFACT_CANDIDATE,
    REVISE_MEMORY_ENTRY,
    SEARCH_MEMORY,
)
from powercontext.server.access import McpAccessLogMiddleware
from powercontext.server.app import REQUEST_ID_HEADER
from powercontext.server.context import (
    bind_internal_bridge,
    current_request_id,
    reset_internal_bridge,
)
from powercontext.server.metrics import McpMetricsMiddleware, ServerMetrics
from powercontext.server.tracing import McpTracingMiddleware, ServerTracing

MCP_PATH = "/mcp"
MCP_SERVER_NAME = "PowerContext Server"
_MCP_OPERATION_IDS = frozenset({
    CAPTURE_CONTENT_SOURCE.operation_id,
    ACTIVATE_HANDOFF.operation_id,
    FINALIZE_HANDOFF.operation_id,
    COMMIT_HANDOFF.operation_id,
    CONTINUE_HANDOFF.operation_id,
    SEARCH_MEMORY.operation_id,
    LIST_MEMORY_ENTRIES.operation_id,
    GET_MEMORY_ENTRY.operation_id,
    REMEMBER_MEMORY.operation_id,
    REVISE_MEMORY_ENTRY.operation_id,
    GET_HANDOFF_REPORT.operation_id,
    GET_HANDOFF_REPORT_WORKSPACE.operation_id,
    RETIRE_MEMORY_ENTRY.operation_id,
    LIST_ARTIFACT_CANDIDATES.operation_id,
    GET_ARTIFACT_CANDIDATE.operation_id,
    APPROVE_ARTIFACT_CANDIDATE.operation_id,
    REJECT_ARTIFACT_CANDIDATE.operation_id,
    REVISE_ARTIFACT_CANDIDATE.operation_id,
})


def _select_mcp_type(route: HTTPRoute, _: MCPType) -> MCPType:
    if route.operation_id in _MCP_OPERATION_IDS:
        return MCPType.TOOL
    return MCPType.EXCLUDE


def create_mcp_server(
    server_app: FastAPI,
    *,
    access_log: bool = False,
    metrics: ServerMetrics | None = None,
    tracing: ServerTracing | None = None,
) -> FastMCP:
    """Project the Agent-facing subset of a Server app into MCP components."""

    resolved_tracing = ServerTracing.context_only() if tracing is None else tracing
    client = httpx.AsyncClient(
        transport=_InternalBridgeTransport(app=server_app),
        base_url="http://fastapi",
    )
    provider = OpenAPIProvider(
        openapi_spec=server_app.openapi(),
        client=client,
        route_map_fn=_select_mcp_type,
        # FastAPI has already validated the response model. A second JSON Schema
        # pass rejects valid OpenAPI 3.0 nullable references in empty results.
        validate_output=False,
    )
    server = FastMCP(name=MCP_SERVER_NAME, providers=[provider])
    server.add_middleware(McpTracingMiddleware(resolved_tracing))
    if access_log:
        server.add_middleware(McpAccessLogMiddleware())
    if metrics is not None:
        server.add_middleware(McpMetricsMiddleware(metrics))
    return server


class _InternalBridgeTransport(httpx.ASGITransport):
    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        request_id = current_request_id()
        if request_id is not None:
            request.headers[REQUEST_ID_HEADER] = request_id
        token = bind_internal_bridge()
        try:
            return await super().handle_async_request(request)
        finally:
            reset_internal_bridge(token)


def mount_mcp(
    server_app: FastAPI,
    *,
    path: str = MCP_PATH,
    access_log: bool = False,
    metrics: ServerMetrics | None = None,
    tracing: ServerTracing | None = None,
) -> FastAPI:
    """Mount the MCP transport while preserving the Server HTTP contract."""

    mcp_server = create_mcp_server(
        server_app,
        access_log=access_log,
        metrics=metrics,
        tracing=tracing,
    )
    mcp_app = mcp_server.http_app(path="/")

    server_app.router.lifespan_context = combine_lifespans(
        server_app.router.lifespan_context,
        mcp_app.lifespan,
    )
    server_app.mount(path, mcp_app, name="mcp")
    return server_app
