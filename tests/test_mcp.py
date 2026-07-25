import asyncio
from collections.abc import Callable, Coroutine
from typing import Any, TypeVar

from fastmcp import Client

from powercontext.server.app import create_app
from powercontext.server.mcp import create_mcp_server

ResultT = TypeVar("ResultT")


def run_async(operation: Callable[[], Coroutine[Any, Any, ResultT]]) -> ResultT:
    return asyncio.run(operation())


def test_mcp_exposes_only_the_agent_facing_server_operation() -> None:
    async def inspect_components() -> tuple[list[str], int, int]:
        async with Client(create_mcp_server(create_app())) as client:
            tools = await client.list_tools()
            resources = await client.list_resources()
            prompts = await client.list_prompts()
        return [tool.name for tool in tools], len(resources), len(prompts)

    tool_names, resource_count, prompt_count = run_async(inspect_components)

    assert set(tool_names) == {
        "get_memory_entry",
        "list_memory_entries",
        "remember_memory",
        "retire_memory_entry",
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
