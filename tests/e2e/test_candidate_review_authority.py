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

"""Submission and review authority through the Server's HTTP and MCP transports."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

from powercontext.builtin.authz import PrincipalRef
from powercontext.builtin.authz.composition import open_builtin_access_control
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import RuntimeConfig
from powercontext.server.authentication import (
    AuthenticationRejectedError,
    AuthenticationRequest,
    AuthenticationResult,
    ProviderReadiness,
)
from powercontext.server.factory import create_server_app
from powercontext.server.settings import AccessControlConfig, McpConfig, MetricsConfig, ServerSettings
from tests.e2e.server_storage import prepare_server_database

ADMIN = PrincipalRef(type="service", id="admin")
AGENT = PrincipalRef(type="service", id="coding-agent")
REVIEWER = PrincipalRef(type="user", id="reviewer")


class _Authentication:
    """Test credentials for three distinct identities; no external identity service."""

    async def authenticate(self, request: AuthenticationRequest, /) -> AuthenticationResult:
        principals = {"Bearer admin": ADMIN, "Bearer agent": AGENT, "Bearer reviewer": REVIEWER}
        principal = principals.get(request.headers.get("authorization", ""))
        if principal is None:
            raise AuthenticationRejectedError
        return AuthenticationResult(subject=principal)

    async def readiness(self) -> ProviderReadiness:
        return ProviderReadiness(ready=True)


def _http_client(app: FastAPI, token: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
        headers={"Authorization": f"Bearer {token}"},
        trust_env=False,
    )


def _mcp_client(app: FastAPI, token: str) -> Client[StreamableHttpTransport]:
    def create_http_client(
        headers: dict[str, str] | None = None,
        timeout: httpx.Timeout | None = None,
        auth: httpx.Auth | None = None,
        **_: object,
    ) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
            headers={"Authorization": f"Bearer {token}", **(headers or {})},
            timeout=timeout,
            auth=auth,
            follow_redirects=True,
            trust_env=False,
        )

    return Client(StreamableHttpTransport("http://testserver/mcp/", httpx_client_factory=create_http_client))


@asynccontextmanager
async def _review_server(tmp_path: Path) -> AsyncIterator[tuple[FastAPI, str]]:
    database = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'review.db'}")
    settings = ServerSettings(
        database=database,
        runtime=RuntimeConfig(artifact_processing_families=()),
        access=AccessControlConfig(mode="enforced"),
        mcp=McpConfig(enabled=True),
        metrics=MetricsConfig(enabled=False),
    )
    await asyncio.to_thread(prepare_server_database, settings)
    async with open_builtin_access_control(database, bootstrap_administrators=(ADMIN,)) as access:
        app = create_server_app(
            settings=settings,
            authentication_provider=_Authentication(),
            access_control=access,
        )
        async with app.router.lifespan_context(app), _http_client(app, "admin") as admin:
            scope = await admin.post(
                "/v1/scopes",
                json={
                    "title": "Review authority",
                    "summary": "Separate submission and review",
                    "idempotency_key": "scope",
                },
            )
            assert scope.status_code == 201, scope.text
            scope_id = scope.json()["scope_id"]
            for principal, role in ((AGENT, "scope.contributor"), (REVIEWER, "scope.reviewer")):
                binding = await admin.post(
                    "/v1/access/bindings/create",
                    json={
                        "subject": {"type": principal.type, "id": principal.id},
                        "resource": {"type": "scope", "scope_id": scope_id},
                        "role": role,
                        "idempotency_key": f"{principal.id}-{role}",
                    },
                )
                assert binding.is_success, binding.text
            yield app, scope_id


async def _submit_candidate(client: httpx.AsyncClient, scope_id: str, family: str) -> dict[str, Any]:
    captured = await client.post(
        "/v1/sources/content",
        json={"scope_id": scope_id, "source_id": "review-evidence", "content": "API contract checks passed."},
    )
    assert captured.status_code == 202, captured.text
    proposal = (
        {
            "situation": "The public API contract changes.",
            "action": "Regenerate the client and run contract tests.",
            "outcome": "API contract checks passed.",
            "lesson": "Regenerate the client before running contract tests.",
        }
        if family == "experience"
        else {
            "name": "review-api-contract",
            "description": "Use when changing the public API contract.",
            "instructions": "Regenerate the client and run contract tests.",
            "validation": ["API contract checks pass."],
        }
    )
    response = await client.post(
        f"/v1/{family}/propose",
        json={
            "scope_id": scope_id,
            "proposal": proposal,
            "source_refs": [captured.json()["source"]],
            "artifact_refs": [],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.mark.parametrize("family", ["experience", "skill"])
@pytest.mark.parametrize("review_transport", ["http", "mcp"])
def test_contributor_submits_but_only_reviewer_can_approve(tmp_path: Path, family: str, review_transport: str) -> None:
    async def scenario() -> None:
        async with (
            _review_server(tmp_path) as (app, scope_id),
            _http_client(app, "agent") as agent,
            _http_client(app, "reviewer") as reviewer,
        ):
            candidate = await _submit_candidate(agent, scope_id, family)
            selection = {"scope_id": scope_id, "candidate_id": candidate["candidate_id"]}
            decision = {**selection, "expected_version": candidate["version"]}
            denied = await agent.post("/v1/candidates/approve", json=decision)
            assert denied.status_code == 403, denied.text
            async with _mcp_client(app, "agent") as mcp:
                denied_tool = await mcp.call_tool("approve_candidate", decision, raise_on_error=False)
                assert denied_tool.is_error
                assert "403" in denied_tool.content[0].text

            pending = await agent.post("/v1/candidates/get", json=selection)
            assert pending.status_code == 200, pending.text
            assert pending.json()["status"] == "pending"
            assert pending.json()["result_artifact"] is None
            assert pending.json()["permissions"] == {"can_revise": False, "can_approve": False, "can_reject": False}
            visible = await reviewer.post("/v1/candidates/get", json=selection)
            assert visible.status_code == 200, visible.text
            assert visible.json()["permissions"] == {"can_revise": False, "can_approve": True, "can_reject": True}
            context_request = {"scope_id": scope_id, "query": "Regenerate the client before running contract tests."}
            before = await agent.post("/v1/context/prepare", json=context_request)
            assert before.status_code == 200, before.text
            assert before.json()["status"] == "empty"

            if review_transport == "http":
                response = await reviewer.post("/v1/candidates/approve", json=decision)
                assert response.status_code == 200, response.text
                approved = response.json()
            else:
                async with _mcp_client(app, "reviewer") as mcp:
                    result = await mcp.call_tool("approve_candidate", decision)
                    assert not result.is_error
                    approved = result.structured_content
            assert approved is not None
            assert approved["status"] == "approved"
            artifact = approved["result_artifact"]
            assert artifact["family"] == family
            readback = await agent.post(f"/v1/{family}/get", json={"scope_id": scope_id, "artifact": artifact})
            assert readback.status_code == 200, readback.text
            after = await agent.post("/v1/context/prepare", json=context_request)
            assert after.status_code == 200, after.text
            if family == "experience":
                assert after.json()["status"] == "ready"
                assert candidate["proposal"]["lesson"] in after.json()["content"]
            else:
                assert after.json()["status"] == "empty"

    asyncio.run(scenario())


def test_reviewer_rejects_and_contributor_resubmits_without_review_authority(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with (
            _review_server(tmp_path) as (app, scope_id),
            _http_client(app, "agent") as agent,
            _http_client(app, "reviewer") as reviewer,
        ):
            candidate = await _submit_candidate(agent, scope_id, "experience")
            selection = {"scope_id": scope_id, "candidate_id": candidate["candidate_id"]}
            decision = {**selection, "expected_version": candidate["version"]}
            replacement = {**candidate["proposal"], "lesson": "Inspect the generated client before contract tests."}
            for client in (agent, reviewer):
                denied = await client.post("/v1/candidates/revise", json={**decision, "proposal": replacement})
                assert denied.status_code == 403, denied.text

            rejected = await reviewer.post(
                "/v1/candidates/reject", json={**decision, "reason": "Include inspection of the generated client."}
            )
            assert rejected.status_code == 200, rejected.text
            assert rejected.json()["status"] == "rejected"
            assert rejected.json()["result_artifact"] is None
            terminal = await reviewer.post("/v1/candidates/approve", json=decision)
            assert terminal.status_code == 409, terminal.text

            resubmitted = await agent.post(
                "/v1/experience/propose",
                json={
                    "scope_id": scope_id,
                    "proposal": replacement,
                    "source_refs": candidate["source_refs"],
                    "artifact_refs": candidate["artifact_refs"],
                },
            )
            assert resubmitted.status_code == 201, resubmitted.text
            corrected = resubmitted.json()
            assert corrected["candidate_id"] != candidate["candidate_id"]
            assert corrected["status"] == "pending"
            assert corrected["permissions"]["can_approve"] is False
            approved = await reviewer.post(
                "/v1/candidates/approve",
                json={
                    "scope_id": scope_id,
                    "candidate_id": corrected["candidate_id"],
                    "expected_version": corrected["version"],
                },
            )
            assert approved.status_code == 200, approved.text
            exact = await agent.post(
                "/v1/experience/get", json={"scope_id": scope_id, "artifact": approved.json()["result_artifact"]}
            )
            assert exact.status_code == 200, exact.text
            assert exact.json()["content"]["lesson"] == replacement["lesson"]

    asyncio.run(scenario())


def test_proposer_with_review_permission_can_approve_its_own_candidate(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with (
            _review_server(tmp_path) as (app, scope_id),
            _http_client(app, "agent") as agent,
            _http_client(app, "admin") as admin,
        ):
            candidate = await _submit_candidate(agent, scope_id, "experience")
            granted = await admin.post(
                "/v1/access/bindings/create",
                json={
                    "subject": {"type": AGENT.type, "id": AGENT.id},
                    "resource": {"type": "scope", "scope_id": scope_id},
                    "role": "scope.reviewer",
                    "idempotency_key": "agent-reviewer",
                },
            )
            assert granted.is_success, granted.text
            async with _mcp_client(app, "agent") as mcp:
                approved = await mcp.call_tool(
                    "approve_candidate",
                    {
                        "scope_id": scope_id,
                        "candidate_id": candidate["candidate_id"],
                        "expected_version": candidate["version"],
                    },
                )
                assert not approved.is_error
                assert approved.structured_content is not None
                assert approved.structured_content["status"] == "approved"

    asyncio.run(scenario())
