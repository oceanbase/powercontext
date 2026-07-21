import asyncio

import httpx
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from pydantic import JsonValue

from powercontext.api import Capabilities
from powercontext.mcp import create_mcp_app


def test_mcp_streamable_http_is_mounted_on_the_server() -> None:
    capabilities = Capabilities(
        source_types=["local-worktree"],
        artifact_families=[],
        search_modes=[],
        limits=[],
    )
    app = create_mcp_app(capability_provider=lambda: capabilities)

    def create_http_client(
        headers: dict[str, str] | None = None,
        timeout: httpx.Timeout | None = None,
        auth: httpx.Auth | None = None,
        **_: object,
    ) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
            headers=headers,
            timeout=timeout,
            auth=auth,
            follow_redirects=True,
        )

    async def call_capabilities() -> dict[str, JsonValue] | None:
        transport = StreamableHttpTransport(
            "http://testserver/mcp/",
            httpx_client_factory=create_http_client,
        )
        async with app.router.lifespan_context(app), Client(transport) as client:
            result = await client.call_tool("get_capabilities")
        return result.structured_content

    assert asyncio.run(call_capabilities()) == capabilities.model_dump(mode="json")
