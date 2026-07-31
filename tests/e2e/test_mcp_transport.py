import asyncio
from pathlib import Path

import httpx
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.server.factory import create_server_app
from powercontext.server.settings import McpConfig, ServerSettings


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
