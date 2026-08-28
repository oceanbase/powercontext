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
from fastmcp.client.elicitation import ElicitResult
from fastmcp.client.transports import StreamableHttpTransport
from mcp.types import ElicitRequestFormParams

from powercontext.builtin.handoff_report import CatalogPage
from powercontext.builtin.handoff_report import ProjectDescriptor as ReportProjectDescriptor
from powercontext.builtin.handoff_report import WorkstreamDescriptor as ReportWorkstreamDescriptor
from powercontext.builtin.runtime import MemoryEntriesPage
from powercontext.server.access import HttpAccessLogMiddleware
from powercontext.server.app import create_app
from powercontext.server.context import is_internal_bridge
from powercontext.server.mcp import create_mcp_server, mount_mcp

ResultT = TypeVar("ResultT")


class HandoffReportCatalogStub:
    def __init__(
        self,
        projects: tuple[ReportProjectDescriptor, ...],
        workstreams: dict[str, tuple[ReportWorkstreamDescriptor, ...]],
    ) -> None:
        self.projects = projects
        self.workstreams = workstreams

    async def list_projects(
        self,
        *,
        cursor: str | None = None,
        limit: int = 50,
        include_archived: bool = False,
    ) -> CatalogPage[ReportProjectDescriptor]:
        del cursor, include_archived
        return CatalogPage(self.projects[:limit], None)

    async def list_workstreams(
        self,
        project_id: str,
        *,
        cursor: str | None = None,
        limit: int = 50,
        include_archived: bool = False,
    ) -> CatalogPage[ReportWorkstreamDescriptor]:
        del cursor, include_archived
        return CatalogPage(self.workstreams.get(project_id, ())[:limit], None)


def handoff_report_app(
    projects: tuple[ReportProjectDescriptor, ...],
    workstreams: dict[str, tuple[ReportWorkstreamDescriptor, ...]],
):
    application = SimpleNamespace(handoff_report=HandoffReportCatalogStub(projects, workstreams))
    return create_app(application=application, handoff_report_enabled=True)


def report_project(project_id: str, project_key: str, title: str) -> ReportProjectDescriptor:
    return ReportProjectDescriptor(
        project_id=project_id,
        project_key=project_key,
        title=title,
        timezone="UTC",
        version=1,
    )


def report_workstream(
    project_id: str,
    scope_id: str,
    key: str,
    title: str,
    *,
    labels: tuple[str, ...] = (),
) -> ReportWorkstreamDescriptor:
    return ReportWorkstreamDescriptor(
        project_id=project_id,
        scope_id=scope_id,
        key=key,
        title=title,
        kind="feature",
        labels=labels,
        version=1,
    )


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
        "activate_handoff",
        "acknowledge_handoff",
        "approve_artifact_candidate",
        "capture_content_source",
        "commit_handoff",
        "continue_handoff",
        "create_work_contract",
        "finalize_handoff",
        "get_artifact_candidate",
        "get_memory_entry",
        "handoff_current_work",
        "list_artifact_candidates",
        "list_memory_entries",
        "reject_artifact_candidate",
        "record_task_outcome",
        "remember_memory",
        "retire_memory_entry",
        "revise_artifact_candidate",
        "revise_memory_entry",
        "search_memory",
    }
    assert resource_count == 0
    assert prompt_count == 0


def test_mcp_exposes_read_only_handoff_report_tools_only_when_feature_routes_are_enabled() -> None:
    async def inspect_components() -> dict[str, Any]:
        async with Client(create_mcp_server(create_app(handoff_report_enabled=True))) as client:
            return {tool.name: tool.annotations for tool in await client.list_tools()}

    tools = run_async(inspect_components)

    assert "get_handoff_report" in tools
    assert "get_handoff_report_workspace" in tools
    assert "list_handoff_report_known_scopes" in tools
    assert "record_handoff_report_activity" not in tools
    assert "attach_handoff_report_workspace" not in tools
    picker = tools["select_handoff_workstream"]
    assert picker is not None
    assert picker.readOnlyHint is True
    assert picker.destructiveHint is False
    assert picker.idempotentHint is True
    assert picker.openWorldHint is False


def test_mcp_handoff_picker_returns_structured_choices_without_elicitation() -> None:
    project = report_project("prj-powercontext", "powercontext", "PowerContext")
    workstreams = (
        report_workstream(project.project_id, "scope-claude", "claude-compat", "Claude compatibility"),
        report_workstream(project.project_id, "scope-ui", "handoff-ui", "Handoff workbench"),
    )
    app = handoff_report_app((project,), {project.project_id: workstreams})

    async def select() -> tuple[dict[str, Any], dict[str, Any]]:
        async with Client(create_mcp_server(app)) as client:
            choices = await client.call_tool("select_handoff_workstream", {})
            selected = await client.call_tool(
                "select_handoff_workstream",
                {"project_id": project.project_id, "work_id": "handoff-ui"},
            )
        return choices.structured_content or {}, selected.structured_content or {}

    choices, selected = run_async(select)

    assert choices["status"] == "needs_selection"
    assert choices["stage"] == "workstream"
    assert [item["work_id"] for item in choices["workstream_choices"]] == ["claude-compat", "handoff-ui"]
    assert selected["status"] == "selected"
    assert selected["selected"] == {
        "work_id": "handoff-ui",
        "scope_id": "scope-ui",
        "project_id": project.project_id,
        "project_key": "powercontext",
        "title": "Handoff workbench",
        "kind": "feature",
        "catalog_version": 1,
    }


def test_mcp_handoff_picker_does_not_silently_resolve_an_ambiguous_work_id() -> None:
    project = report_project("prj-powercontext", "powercontext", "PowerContext")
    workstreams = (
        report_workstream(project.project_id, "scope-claude", "shared-id", "Claude compatibility"),
        report_workstream(project.project_id, "shared-id", "handoff-ui", "Handoff workbench"),
    )
    app = handoff_report_app((project,), {project.project_id: workstreams})

    async def select() -> dict[str, Any]:
        async with Client(create_mcp_server(app)) as client:
            result = await client.call_tool(
                "select_handoff_workstream",
                {"project_id": project.project_id, "work_id": "shared-id"},
            )
        return result.structured_content or {}

    result = run_async(select)

    assert result["status"] == "needs_selection"
    assert result["stage"] == "workstream"
    assert [item["work_id"] for item in result["workstream_choices"]] == ["shared-id", "handoff-ui"]


def test_mcp_handoff_picker_uses_native_form_elicitation() -> None:
    project = report_project("prj-powercontext", "powercontext", "PowerContext")
    workstreams = (
        report_workstream(project.project_id, "scope-claude", "claude-compat", "Claude compatibility"),
        report_workstream(project.project_id, "scope-ui", "handoff-ui", "Handoff workbench"),
    )
    app = handoff_report_app((project,), {project.project_id: workstreams})
    requests: list[ElicitRequestFormParams] = []

    async def choose_second(_message, _response_type, params, _context) -> str:
        assert isinstance(params, ElicitRequestFormParams)
        requests.append(params)
        return "option-2"

    async def select() -> dict[str, Any]:
        async with Client(create_mcp_server(app), elicitation_handler=choose_second) as client:
            result = await client.call_tool("select_handoff_workstream", {"locale": "en"})
        return result.structured_content or {}

    selected = run_async(select)

    assert selected["status"] == "selected"
    assert selected["selected"]["work_id"] == "handoff-ui"
    assert len(requests) == 1
    assert requests[0].message == "Choose the work to hand off or continue."
    options = requests[0].requestedSchema["properties"]["value"]["oneOf"]
    assert options == [
        {"const": "option-1", "title": "Claude compatibility · claude-compat · feature"},
        {"const": "option-2", "title": "Handoff workbench · handoff-ui · feature"},
    ]


def test_mcp_handoff_picker_preserves_cancelled_selection() -> None:
    project = report_project("prj-powercontext", "powercontext", "PowerContext")
    workstreams = (
        report_workstream(project.project_id, "scope-claude", "claude-compat", "Claude compatibility"),
        report_workstream(project.project_id, "scope-ui", "handoff-ui", "Handoff workbench"),
    )
    app = handoff_report_app((project,), {project.project_id: workstreams})

    async def cancel(_message, _response_type, _params, _context) -> ElicitResult:
        return ElicitResult(action="cancel")

    async def select() -> dict[str, Any]:
        async with Client(create_mcp_server(app), elicitation_handler=cancel) as client:
            result = await client.call_tool("select_handoff_workstream", {})
        return result.structured_content or {}

    result = run_async(select)

    assert result["status"] == "cancelled"
    assert result["stage"] == "workstream"
    assert result["selected"] is None


def test_mcp_handoff_picker_selects_project_then_workstream() -> None:
    memory = report_project("prj-memory", "memory", "Memory")
    handoff = report_project("prj-handoff", "handoff", "Handoff")
    app = handoff_report_app(
        (memory, handoff),
        {
            memory.project_id: (report_workstream(memory.project_id, "scope-memory", "memory-core", "Memory Core"),),
            handoff.project_id: (
                report_workstream(handoff.project_id, "scope-cli", "handoff-cli", "Handoff CLI"),
                report_workstream(handoff.project_id, "scope-web", "handoff-web", "Handoff Web"),
            ),
        },
    )
    replies = iter(("option-2", "option-1"))
    messages: list[str] = []

    async def choose(_message, _response_type, _params, _context) -> str:
        messages.append(_message)
        return next(replies)

    async def select() -> dict[str, Any]:
        async with Client(create_mcp_server(app), elicitation_handler=choose) as client:
            result = await client.call_tool("select_handoff_workstream", {"locale": "en"})
        return result.structured_content or {}

    result = run_async(select)

    assert messages == [
        "Choose the Project that owns this Handoff.",
        "Choose the work to hand off or continue.",
    ]
    assert result["status"] == "selected"
    assert result["selected"]["project_id"] == handoff.project_id
    assert result["selected"]["work_id"] == "handoff-cli"


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
