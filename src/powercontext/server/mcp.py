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

"""MCP transport owned and configured by the PowerContext Server."""

from __future__ import annotations

import httpx
from fastapi import FastAPI
from fastmcp import FastMCP
from fastmcp.server.providers.openapi import (
    MCPType,
    OpenAPIProvider,
    OpenAPIResource,
    OpenAPIResourceTemplate,
    OpenAPITool,
)
from fastmcp.utilities.lifespan import combine_lifespans
from fastmcp.utilities.openapi import HTTPRoute
from mcp.types import ToolAnnotations
from typing_extensions import override

from powercontext.http._generated.operations import (
    ACKNOWLEDGE_HANDOFF,
    ACTIVATE_HANDOFF,
    APPROVE_ARTIFACT_CANDIDATE,
    CAPTURE_CONTENT_SOURCE,
    CLEAR_SCOPE_BINDING,
    COMMIT_HANDOFF,
    CONTINUE_HANDOFF,
    CREATE_SCOPE,
    CREATE_WORK_CONTRACT,
    FINALIZE_HANDOFF,
    GET_ARTIFACT_CANDIDATE,
    GET_HANDOFF_REPORT,
    GET_MEMORY_ENTRY,
    GET_SCOPE,
    HANDOFF_CURRENT_WORK,
    LIST_ARTIFACT_CANDIDATES,
    LIST_MEMORY_ENTRIES,
    LIST_SCOPES,
    PUBLISH_ARTIFACT,
    RECORD_TASK_OUTCOME,
    REJECT_ARTIFACT_CANDIDATE,
    REMEMBER_MEMORY,
    RESOLVE_SCOPE_BINDING,
    RETIRE_MEMORY_ENTRY,
    REVISE_ARTIFACT_CANDIDATE,
    REVISE_MEMORY_ENTRY,
    SEARCH_MEMORY,
    SET_SCOPE_BINDING,
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
    CREATE_WORK_CONTRACT.operation_id,
    HANDOFF_CURRENT_WORK.operation_id,
    ACKNOWLEDGE_HANDOFF.operation_id,
    RECORD_TASK_OUTCOME.operation_id,
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
    RETIRE_MEMORY_ENTRY.operation_id,
    LIST_ARTIFACT_CANDIDATES.operation_id,
    GET_ARTIFACT_CANDIDATE.operation_id,
    APPROVE_ARTIFACT_CANDIDATE.operation_id,
    REJECT_ARTIFACT_CANDIDATE.operation_id,
    REVISE_ARTIFACT_CANDIDATE.operation_id,
    CREATE_SCOPE.operation_id,
    LIST_SCOPES.operation_id,
    GET_SCOPE.operation_id,
    RESOLVE_SCOPE_BINDING.operation_id,
    SET_SCOPE_BINDING.operation_id,
    CLEAR_SCOPE_BINDING.operation_id,
    PUBLISH_ARTIFACT.operation_id,
})
_MCP_READ_ONLY_OPERATION_IDS = frozenset({
    CONTINUE_HANDOFF.operation_id,
    SEARCH_MEMORY.operation_id,
    LIST_MEMORY_ENTRIES.operation_id,
    GET_MEMORY_ENTRY.operation_id,
    GET_HANDOFF_REPORT.operation_id,
    LIST_ARTIFACT_CANDIDATES.operation_id,
    GET_ARTIFACT_CANDIDATE.operation_id,
    LIST_SCOPES.operation_id,
    GET_SCOPE.operation_id,
    RESOLVE_SCOPE_BINDING.operation_id,
})
_MCP_REVIEW_WRITE_OPERATION_IDS = frozenset({
    APPROVE_ARTIFACT_CANDIDATE.operation_id,
    REJECT_ARTIFACT_CANDIDATE.operation_id,
    REVISE_ARTIFACT_CANDIDATE.operation_id,
})


def _select_mcp_type(route: HTTPRoute, _: MCPType) -> MCPType:
    if route.operation_id in _MCP_OPERATION_IDS:
        return MCPType.TOOL
    return MCPType.EXCLUDE


def _annotate_mcp_component(
    route: HTTPRoute,
    component: OpenAPITool | OpenAPIResource | OpenAPIResourceTemplate,
) -> None:
    """Describe the side effects that an MCP host should use for approval decisions."""

    if not isinstance(component, OpenAPITool):
        return
    if route.operation_id in _MCP_READ_ONLY_OPERATION_IDS:
        component.annotations = ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        )
    elif route.operation_id == HANDOFF_CURRENT_WORK.operation_id:
        component.annotations = ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=False,
            openWorldHint=False,
        )
    elif route.operation_id == COMMIT_HANDOFF.operation_id:
        component.annotations = ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        )
    elif route.operation_id in _MCP_REVIEW_WRITE_OPERATION_IDS:
        # Approval and rejection are terminal; a revision replaces the proposal a reviewer last
        # inspected. MCP visibility is not an authorization boundary (RFC 0050), so these hints
        # only let a host apply its own confirmation policy. An exact replay is rejected by the
        # pending-head CAS before anything is written, so repeated identical calls have no
        # additional effect and the tools are idempotent in the MCP sense.
        component.annotations = ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=True,
            idempotentHint=True,
            openWorldHint=False,
        )


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
        mcp_component_fn=_annotate_mcp_component,
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
    @override
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
