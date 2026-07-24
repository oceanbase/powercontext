import asyncio
from pathlib import Path
from typing import Any

import httpx
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

from powercontext.server.runtime import create_server_app
from powercontext.server.settings import ServerSettings, SQLiteStorageSettings


def test_mcp_streamable_http_is_mounted_on_the_server(tmp_path: Path) -> None:
    app = create_server_app(
        settings=ServerSettings(
            storage=SQLiteStorageSettings(path=tmp_path / "runtime.db"),
        )
    )

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

    async def exercise_tools() -> tuple[set[str], dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
        transport = StreamableHttpTransport(
            "http://testserver/mcp/",
            httpx_client_factory=create_http_client,
        )
        async with app.router.lifespan_context(app), Client(transport) as client:
            tools = {tool.name for tool in await client.list_tools()}
            remembered = await client.call_tool(
                "remember_memory",
                {
                    "scope_id": "project:powercontext",
                    "kind": "decision",
                    "text": "Expose a curated MCP projection.",
                },
            )
            found = await client.call_tool(
                "search_memory",
                {
                    "scope_id": "project:powercontext",
                    "query": "curated MCP projection",
                },
            )
            remembered_content = remembered.structured_content or {}
            listed = await client.call_tool(
                "list_memory_entries",
                {"scope_id": "project:powercontext"},
            )
            entry = remembered_content["entry"]
            exact = await client.call_tool(
                "get_memory_entry",
                {
                    "scope_id": "project:powercontext",
                    "citation": entry["citation"],
                },
            )
            return (
                tools,
                remembered_content,
                found.structured_content or {},
                listed.structured_content or {},
                exact.structured_content or {},
            )

    tools, remembered, found, listed, exact = asyncio.run(exercise_tools())

    assert tools == {
        "get_memory_entry",
        "list_memory_entries",
        "remember_memory",
        "retire_memory_entry",
        "revise_memory_entry",
        "search_memory",
    }
    assert remembered["memory"]["revision"] == 1
    assert found["hits"][0]["text"] == "Expose a curated MCP projection."
    assert found["hits"][0]["citation"] == remembered["entry"]["citation"]
    assert listed["entries"] == [remembered["entry"]]
    assert exact == remembered["entry"]
