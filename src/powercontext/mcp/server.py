"""Experimental MCP projection assembled from the FastAPI Server contract."""

from __future__ import annotations

from fastapi import FastAPI
from fastmcp import FastMCP
from fastmcp.server.providers.openapi import MCPType
from fastmcp.utilities.lifespan import combine_lifespans
from fastmcp.utilities.openapi import HTTPRoute

from powercontext.api.generated.operations import (
    GET_MEMORY_ENTRY,
    LIST_MEMORY_ENTRIES,
    REMEMBER_MEMORY,
    RETIRE_MEMORY_ENTRY,
    REVISE_MEMORY_ENTRY,
    SEARCH_MEMORY,
)

MCP_PATH = "/mcp"
MCP_SERVER_NAME = "PowerContext Server"
_MCP_OPERATION_IDS = frozenset({
    SEARCH_MEMORY.operation_id,
    LIST_MEMORY_ENTRIES.operation_id,
    GET_MEMORY_ENTRY.operation_id,
    REMEMBER_MEMORY.operation_id,
    REVISE_MEMORY_ENTRY.operation_id,
    RETIRE_MEMORY_ENTRY.operation_id,
})


def _select_mcp_type(route: HTTPRoute, _: MCPType) -> MCPType:
    if route.operation_id in _MCP_OPERATION_IDS:
        return MCPType.TOOL
    return MCPType.EXCLUDE


def create_mcp_server(server_app: FastAPI) -> FastMCP:
    """Project the Agent-facing subset of a Server app into MCP components."""

    return FastMCP.from_fastapi(
        app=server_app,
        name=MCP_SERVER_NAME,
        route_map_fn=_select_mcp_type,
    )


def mount_mcp(server_app: FastAPI, *, path: str = MCP_PATH) -> FastAPI:
    """Mount the MCP transport while preserving the Server HTTP contract."""

    mcp_server = create_mcp_server(server_app)
    mcp_app = mcp_server.http_app(path="/")

    server_app.router.lifespan_context = combine_lifespans(
        server_app.router.lifespan_context,
        mcp_app.lifespan,
    )
    server_app.mount(path, mcp_app, name="mcp")
    return server_app
