import asyncio
from collections.abc import Callable, Coroutine
from typing import Any, TypeVar

from fastmcp import Client
from pydantic import JsonValue

from powercontext.api import Capabilities, CapabilityLimit
from powercontext.mcp import create_mcp_server
from powercontext.server.app import create_app

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

    assert tool_names == ["get_capabilities"]
    assert resource_count == 0
    assert prompt_count == 0


def test_mcp_tool_reuses_the_server_capability_binding() -> None:
    capabilities = Capabilities(
        source_types=["git-commit"],
        artifact_families=["memory"],
        search_modes=["text"],
        limits=[CapabilityLimit(name="max_results", value=20)],
    )

    async def call_capabilities() -> dict[str, JsonValue] | None:
        server_app = create_app(capability_provider=lambda: capabilities)
        async with Client(create_mcp_server(server_app)) as client:
            result = await client.call_tool("get_capabilities")
        return result.structured_content

    assert run_async(call_capabilities) == capabilities.model_dump(mode="json")
