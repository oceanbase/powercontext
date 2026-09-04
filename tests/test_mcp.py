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

import asyncio
import logging
from collections.abc import Callable, Coroutine
from types import SimpleNamespace
from typing import Any, Self, TypeVar

import httpx
from fastapi import Request
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

from powercontext.builtin.runtime import MemoryEntriesPage
from powercontext.server.access import HttpAccessLogMiddleware
from powercontext.server.app import create_app
from powercontext.server.context import is_internal_bridge
from powercontext.server.mcp import create_mcp_server, mount_mcp

ResultT = TypeVar("ResultT")


def run_async(operation: Callable[[], Coroutine[Any, Any, ResultT]]) -> ResultT:
    return asyncio.run(operation())


def test_mcp_exposes_only_data_plane_and_integration_control_operations() -> None:
    async def inspect_components() -> tuple[list[str], int, int]:
        async with Client(create_mcp_server(create_app())) as client:
            tools = await client.list_tools()
            resources = await client.list_resources()
            prompts = await client.list_prompts()
        return [tool.name for tool in tools], len(resources), len(prompts)

    tool_names, resource_count, prompt_count = run_async(inspect_components)

    assert set(tool_names) == {
        "activate_handoff",
        "acknowledge_handoff",
        "approve_artifact_candidate",
        "capture_content_source",
        "clear_scope_binding",
        "commit_handoff",
        "continue_handoff",
        "create_scope",
        "create_work_contract",
        "finalize_handoff",
        "get_artifact_candidate",
        "get_memory_entry",
        "get_scope",
        "handoff_current_work",
        "list_artifact_candidates",
        "list_memory_entries",
        "list_scopes",
        "publish_artifact",
        "reject_artifact_candidate",
        "record_task_outcome",
        "resolve_scope_binding",
        "remember_memory",
        "retire_memory_entry",
        "revise_artifact_candidate",
        "revise_memory_entry",
        "search_memory",
        "set_scope_binding",
    }
    assert resource_count == 0
    assert prompt_count == 0


def test_mcp_exposes_read_only_handoff_report_tools_only_when_feature_routes_are_enabled() -> None:
    async def inspect_components() -> dict[str, Any]:
        async with Client(create_mcp_server(create_app(handoff_report_enabled=True))) as client:
            return {tool.name: tool.annotations for tool in await client.list_tools()}

    tools = run_async(inspect_components)

    assert "get_handoff_report" in tools
    report = tools["get_handoff_report"]
    assert report is not None
    assert report.readOnlyHint is True
    assert report.destructiveHint is False
    assert report.idempotentHint is True
    assert report.openWorldHint is False


def test_mcp_describes_handoff_tool_side_effects_for_host_approval() -> None:
    async def inspect_annotations() -> dict[str, Any]:
        async with Client(create_mcp_server(create_app())) as client:
            return {
                tool.name: tool.annotations
                for tool in await client.list_tools()
                if tool.name in {"handoff_current_work", "commit_handoff", "continue_handoff"}
            }

    annotations = run_async(inspect_annotations)

    prepare = annotations["handoff_current_work"]
    assert prepare is not None
    assert prepare.readOnlyHint is False
    assert prepare.destructiveHint is False
    assert prepare.idempotentHint is False
    assert prepare.openWorldHint is False

    commit = annotations["commit_handoff"]
    assert commit is not None
    assert commit.readOnlyHint is False
    assert commit.destructiveHint is False
    assert commit.idempotentHint is True
    assert commit.openWorldHint is False

    resolve = annotations["continue_handoff"]
    assert resolve is not None
    assert resolve.readOnlyHint is True
    assert resolve.destructiveHint is False
    assert resolve.openWorldHint is False


def test_mcp_describes_review_write_side_effects_for_host_approval() -> None:
    review_writes = {
        "approve_artifact_candidate",
        "reject_artifact_candidate",
        "revise_artifact_candidate",
    }

    async def inspect_annotations() -> dict[str, Any]:
        async with Client(create_mcp_server(create_app())) as client:
            return {tool.name: tool.annotations for tool in await client.list_tools() if tool.name in review_writes}

    annotations = run_async(inspect_annotations)

    assert set(annotations) == review_writes
    for name, decision in annotations.items():
        assert decision is not None, f"{name} carries no annotations for an MCP host to prompt on"
        assert decision.readOnlyHint is False
        assert decision.destructiveHint is True
        assert decision.idempotentHint is True
        assert decision.openWorldHint is False


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


def test_mcp_bridge_reuses_logical_request_id_and_is_marked_internal(caplog) -> None:
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

    mount_mcp(app, access_log=True)

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

    with caplog.at_level(logging.INFO, logger="powercontext.server.access"):
        run_async(call_tool)

    bridge_request = next(request for request in requests if request[0] == "/v1/memory/entries/list")
    tool_call = next(
        record
        for record in caplog.records
        if getattr(record, "event", None) == "transport.request.completed" and record.operation == "mcp.tools.call"
    )
    assert bridge_request[1] == tool_call.request_id
    assert bridge_request[2] is True


def test_mcp_access_log_counts_the_logical_tool_call_without_the_bridge(caplog) -> None:
    class MemoryApplication:
        def for_scope(self, scope_id: str) -> Self:
            del scope_id
            return self

        async def list(self, *, include_inactive: bool = False) -> MemoryEntriesPage:
            del include_inactive
            return MemoryEntriesPage(memory_ref=None)

    app = create_app(application=SimpleNamespace(memory=MemoryApplication(), sources=object()))
    app.add_middleware(HttpAccessLogMiddleware, skip_paths=("/mcp",))
    mount_mcp(app, access_log=True)

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

    with caplog.at_level(logging.INFO, logger="powercontext.server.access"):
        run_async(call_tool)

    records = [record for record in caplog.records if getattr(record, "event", None) == "transport.request.completed"]
    tool_calls = [record for record in records if record.operation == "mcp.tools.call"]
    assert len(tool_calls) == 1
    assert tool_calls[0].transport == "mcp"
    assert not any(record.transport == "http" and record.operation == "list_memory_entries" for record in records)
