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
from pathlib import Path

import httpx
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

from powercontext.builtin.artifacts.handoff import (
    HandoffDraft,
    HandoffGenerationRequest,
    HandoffStatement,
)
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.server.factory import create_server_app
from powercontext.server.settings import McpConfig, ServerSettings


class DeterministicHandoffPipeline:
    async def generate(self, request: HandoffGenerationRequest, /) -> HandoffDraft:
        citations = tuple(item.citation for item in request.evidence)
        return HandoffDraft(
            objective=request.objective,
            state=(HandoffStatement(text="The MCP Handoff path is connected.", citations=citations),),
            disposition="continuable",
            next_action=HandoffStatement(text="Pass the inspected Handoff to the next task.", citations=citations),
        )


def test_mcp_projects_curated_tools_at_the_configured_server_path(tmp_path: Path) -> None:
    app = create_server_app(
        settings=ServerSettings(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'runtime.db'}"),
            mcp=McpConfig(enabled=True, path="/agent"),
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

    async def exercise_tools() -> set[str]:
        transport = StreamableHttpTransport(
            "http://testserver/agent/",
            httpx_client_factory=create_http_client,
        )
        async with (
            app.router.lifespan_context(app),
            create_http_client() as http_client,
            Client(transport) as client,
        ):
            projected_tools = {tool.name: tool for tool in await client.list_tools()}
            include_inactive = projected_tools["list_memory_entries"].inputSchema["properties"]["include_inactive"]
            assert include_inactive["type"] == "boolean"
            assert include_inactive["default"] is False
            empty_list = await client.call_tool(
                "list_memory_entries",
                {
                    "scope_id": "project:empty",
                    "include_inactive": True,
                },
            )
            assert empty_list.structured_content == {"memory": None, "entries": []}

            captured_response = await http_client.post(
                "/v1/sources/content",
                json={
                    "scope_id": "project:review",
                    "source_id": "task-1",
                    "content": "api-generate and contract-test passed",
                },
            )
            captured_response.raise_for_status()
            candidate_response = await http_client.post(
                "/v1/experience/propose",
                json={
                    "scope_id": "project:review",
                    "proposal": {
                        "situation": "The public OpenAPI contract changes.",
                        "action": "Regenerate the Client and run contract tests.",
                        "outcome": "The generated transport matches the contract.",
                        "lesson": "Regenerate the Client before contract tests.",
                    },
                    "source_refs": [captured_response.json()["source"]],
                    "artifact_refs": [],
                },
            )
            candidate_response.raise_for_status()
            candidate = candidate_response.json()

            approved = await client.call_tool(
                "approve_artifact_candidate",
                {
                    "scope_id": "project:review",
                    "candidate_id": candidate["candidate_id"],
                    "expected_version": candidate["version"],
                },
            )
            assert approved.structured_content["status"] == "approved"
            assert approved.structured_content["result_artifact"] is not None
            return set(projected_tools)

    tools = asyncio.run(exercise_tools())

    assert tools == {
        "acknowledge_handoff",
        "activate_handoff",
        "approve_artifact_candidate",
        "capture_content_source",
        "commit_handoff",
        "continue_handoff",
        "create_work_contract",
        "finalize_handoff",
        "get_artifact_candidate",
        "get_handoff_report",
        "get_handoff_report_workspace",
        "get_memory_entry",
        "handoff_current_work",
        "list_artifact_candidates",
        "list_memory_entries",
        "record_task_outcome",
        "reject_artifact_candidate",
        "remember_memory",
        "retire_memory_entry",
        "revise_artifact_candidate",
        "revise_memory_entry",
        "search_memory",
        "select_handoff_workstream",
    }


def test_mcp_handoff_tools_share_one_source_to_artifact_lifecycle(tmp_path: Path) -> None:
    app = create_server_app(
        settings=ServerSettings(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'handoff.db'}"),
            mcp=McpConfig(enabled=True),
        ),
        handoff_pipeline=DeterministicHandoffPipeline(),
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

    async def exercise_handoff() -> None:
        transport = StreamableHttpTransport(
            "http://testserver/mcp/",
            httpx_client_factory=create_http_client,
        )
        async with app.router.lifespan_context(app), Client(transport) as client:
            captured_result = await client.call_tool(
                "capture_content_source",
                {
                    "scope_id": "project:handoff",
                    "source_id": "turn-1",
                    "content": "MCP must expose the same explicit lifecycle as the SDK.",
                },
            )
            captured = captured_result.structured_content or {}
            activation_result = await client.call_tool(
                "activate_handoff",
                {
                    "scope_id": "project:handoff",
                    "boundary_source": captured["source"],
                    "objective": "Transfer the MCP integration state.",
                },
            )
            activation = activation_result.structured_content or {}
            draft = activation["draft"]
            draft["state"][0]["text"] = "The complete MCP Handoff path is connected."
            prepared_result = await client.call_tool(
                "finalize_handoff",
                {
                    "scope_id": "project:handoff",
                    "draft": draft,
                },
            )
            prepared = prepared_result.structured_content or {}
            temporary_result = await client.call_tool(
                "continue_handoff",
                {
                    "scope_id": "project:handoff",
                    "selection": "prepared",
                    "prepared": prepared,
                },
            )
            committed_result = await client.call_tool(
                "commit_handoff",
                {
                    "scope_id": "project:handoff",
                    "handoff": prepared,
                },
            )
            latest_result = await client.call_tool(
                "continue_handoff",
                {
                    "scope_id": "project:handoff",
                    "selection": "latest",
                },
            )

        temporary = temporary_result.structured_content or {}
        committed = committed_result.structured_content or {}
        latest = latest_result.structured_content or {}
        assert activation["status"] == "generated"
        assert temporary["selection"] == "prepared"
        assert temporary["content"]["state"][0]["text"] == "The complete MCP Handoff path is connected."
        assert committed["reference"]["family"] == "handoff"
        assert committed["source_refs"] == [captured["source"]]
        assert latest["selected_revision"] == committed["reference"]

    asyncio.run(exercise_handoff())


def test_mcp_endpoint_is_absent_when_disabled_by_server_settings() -> None:
    app = create_server_app(settings=ServerSettings(mcp=McpConfig(enabled=False)))

    async def request_mcp() -> httpx.Response:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            return await client.get("/mcp")

    response = asyncio.run(request_mcp())

    assert response.status_code == 404
