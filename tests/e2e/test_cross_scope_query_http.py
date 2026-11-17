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

"""The published cross-Scope read boundary through a real ASGI app and the SDK client."""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx

from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.client import PowerContextClient
from powercontext.http import CreateScopeRequest, CrossScopeArtifactQueryRequest
from powercontext.http._generated.models import Coverage, Scope
from powercontext.server.factory import create_server_app
from powercontext.server.settings import McpConfig, ServerSettings
from tests.e2e.server_storage import prepare_server_database


def test_cross_scope_query_reaches_the_published_boundary_with_honest_coverage(tmp_path: Path) -> None:
    settings = ServerSettings(
        database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'cross-scope.db'}"),
        mcp=McpConfig(enabled=False),
    )
    prepare_server_database(settings, evidence_dir=tmp_path / "migration-evidence")

    async def scenario() -> None:
        app = create_server_app(settings=settings)
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as transport,
            PowerContextClient("http://testserver", http_client=transport, trust_transport_security=True) as client,
        ):
            home = (await client.get_default_scope()).scope_id
            library = (
                await client.create_scope(
                    CreateScopeRequest(
                        title="Library", summary="Origin", parent_scope_id=home, idempotency_key="library"
                    )
                )
            ).scope_id
            # A declared origin that is not searchable is reported, not silently dropped.
            partial = await client.search_cross_scope_artifacts(
                home,
                CrossScopeArtifactQueryRequest(
                    family="experience",
                    payload={"query": "crossscopemarker"},
                    scopes=[Scope(root=library), Scope(root="scp_missing")],
                    coverage=Coverage.PARTIAL,
                ),
            )
            assert partial.hits == []
            assert partial.coverage.complete is False
            assert partial.coverage.reason == "origin_failure"
            coverage = partial.coverage
            assert [origin.root for origin in coverage.failed_origins or []] == ["scp_missing"]
            assert [origin.root for origin in coverage.searched_origins or []] == [library]

            # An impossible budget is refused through the wire rather than truncated.
            over_budget = await transport.post(
                f"/v1/scopes/{home}/artifacts/search:cross-scope",
                json={
                    "family": "experience",
                    "payload": {"query": "crossscopemarker", "limit": 200},
                    "scopes": [library],
                    "max_candidates": 8,
                },
            )
            assert over_budget.status_code == 422, over_budget.text
            assert over_budget.json()["error"]["code"] == "cross_scope_query_bounded_out"

            # An unknown Family is the Family's own error, not a cross-Scope invention.
            unknown = await transport.post(
                f"/v1/scopes/{home}/artifacts/search:cross-scope",
                json={"family": "unknown-family", "payload": {"query": "x"}, "scopes": [library]},
            )
            assert unknown.status_code in {404, 422}, unknown.text

    asyncio.run(scenario())
