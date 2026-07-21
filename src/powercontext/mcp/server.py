"""Experimental MCP projection assembled from the FastAPI Server contract."""

from __future__ import annotations

from re import escape

from fastapi import FastAPI
from fastmcp import FastMCP
from fastmcp.server.providers.openapi import MCPType, RouteMap
from fastmcp.utilities.lifespan import combine_lifespans
from fastmcp.utilities.openapi import HttpMethod
from pydantic import TypeAdapter

from powercontext.api.generated.operations import GET_CAPABILITIES
from powercontext.server.app import CapabilityProvider, ReadinessProbe
from powercontext.server.app import create_app as create_server_app
from powercontext.server.settings import ServerSettings

MCP_PATH = "/mcp"
MCP_SERVER_NAME = "PowerContext Server"
_HTTP_METHOD_ADAPTER = TypeAdapter(HttpMethod)


def create_mcp_server(server_app: FastAPI) -> FastMCP:
    """Project the Agent-facing subset of a Server app into MCP components."""

    return FastMCP.from_fastapi(
        app=server_app,
        name=MCP_SERVER_NAME,
        route_maps=[
            RouteMap(
                methods=[_HTTP_METHOD_ADAPTER.validate_python(GET_CAPABILITIES.method)],
                pattern=rf"^{escape(GET_CAPABILITIES.path)}$",
                mcp_type=MCPType.TOOL,
                mcp_tags={"server"},
            ),
            RouteMap(mcp_type=MCPType.EXCLUDE),
        ],
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


def create_mcp_app(
    *,
    settings: ServerSettings | None = None,
    capability_provider: CapabilityProvider | None = None,
    readiness_probe: ReadinessProbe | None = None,
    path: str = MCP_PATH,
) -> FastAPI:
    """Build the standard Server app with the experimental MCP transport mounted."""

    server_app = create_server_app(
        settings=settings,
        capability_provider=capability_provider,
        readiness_probe=readiness_probe,
    )
    return mount_mcp(server_app, path=path)
