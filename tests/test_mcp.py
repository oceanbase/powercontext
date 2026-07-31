import asyncio
from collections.abc import Callable, Coroutine
from types import SimpleNamespace
from typing import Any, Self, TypeVar

import httpx
from fastapi import Request
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

from powercontext.builtin.runtime import MemoryEntriesPage
from powercontext.server.app import create_app
from powercontext.server.context import is_internal_bridge
from powercontext.server.mcp import create_mcp_server, mount_mcp

ResultT = TypeVar("ResultT")


def run_async(operation: Callable[[], Coroutine[Any, Any, ResultT]]) -> ResultT:
    return asyncio.run(operation())


def test_mcp_exposes_only_the_agent_facing_server_operations() -> None:
    async def inspect_components() -> tuple[list[str], int, int]:
        async with Client(create_mcp_server(create_app())) as client:
            tools = await client.list_tools()
            resources = await client.list_resources()
            prompts = await client.list_prompts()
        return [tool.name for tool in tools], len(resources), len(prompts)

    tool_names, resource_count, prompt_count = run_async(inspect_components)

    assert set(tool_names) == {
        "approve_artifact_candidate",
        "get_artifact_candidate",
        "get_memory_entry",
        "list_artifact_candidates",
        "list_memory_entries",
        "reject_artifact_candidate",
        "remember_memory",
        "retire_memory_entry",
        "revise_artifact_candidate",
        "revise_memory_entry",
        "search_memory",
    }
    assert resource_count == 0
    assert prompt_count == 0


def test_mcp_exact_entry_tools_use_nested_citations() -> None:
    async def exact_entry_tool_schemas() -> dict[str, dict[str, Any]]:
        server = create_mcp_server(create_app())
        async with Client(server) as client:
            return {
                tool.name: tool.inputSchema
                for tool in await client.list_tools()
                if tool.name in {"get_memory_entry", "revise_memory_entry", "retire_memory_entry"}
            }

    schemas = run_async(exact_entry_tool_schemas)

    for schema in schemas.values():
        properties = schema["properties"]
        assert "citation" in properties
        assert "memory_id" not in properties
        assert set(properties["citation"]["properties"]) == {"memory_ref", "entry_id", "entry_version_id"}


def test_mcp_bridge_reuses_request_id_and_is_marked_internal() -> None:
    class MemoryApplication:
        def for_scope(self, scope_id: str) -> Self:
            del scope_id
            return self

        async def list(self, *, include_inactive: bool = False) -> MemoryEntriesPage:
            del include_inactive
            return MemoryEntriesPage(memory_ref=None)

    app = create_app(application=SimpleNamespace(memory=MemoryApplication(), sources=object()))
    requests: list[tuple[str, str, bool]] = []

    @app.middleware("http")
    async def record_request(request: Request, call_next):
        response = await call_next(request)
        requests.append((request.url.path, request.state.request_id, is_internal_bridge()))
        return response

    mount_mcp(app)

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

    async def call_tool() -> None:
        transport = StreamableHttpTransport(
            "http://testserver/mcp/",
            httpx_client_factory=create_http_client,
        )
        async with app.router.lifespan_context(app), Client(transport) as client:
            await client.call_tool("list_memory_entries", {"scope_id": "project"})

    run_async(call_tool)

    bridge_request = next(request for request in requests if request[0] == "/v1/memory/entries/list")
    external_request_ids = {request_id for path, request_id, internal in requests if path == "/mcp/" and not internal}
    assert bridge_request[1] in external_request_ids
    assert bridge_request[2] is True
