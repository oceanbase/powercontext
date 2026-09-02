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

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest

from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.client import PowerContextClient, ServerResponseError
from powercontext.http import (
    CreateArtifactRequest,
    CreateSourceRequest,
    DeleteArtifactRequest,
    GetArtifactRequest,
    GetArtifactRevisionRequest,
    GetSourceRequest,
    ListArtifactsRequest,
    ListScopesRequest,
    ReplaceArtifactRequest,
    SearchArtifactsRequest,
    SearchSourcesRequest,
    SourceQueryType,
)
from powercontext.server.factory import create_server_app
from powercontext.server.settings import BearerAuthConfig, McpConfig, ServerSettings


def test_source_artifact_and_scope_api_round_trip(tmp_path: Path) -> None:
    app = create_server_app(
        settings=ServerSettings(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'base-access.db'}"),
            auth=BearerAuthConfig(enabled=False),
            mcp=McpConfig(enabled=False),
        )
    )

    async def scenario() -> None:
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://testserver",
            ) as transport,
        ):
            client = PowerContextClient("http://testserver", http_client=transport, trust_transport_security=True)
            source = await client.create_source(
                CreateSourceRequest(
                    scope_id="scope-a",
                    source_type="content",
                    source_id="source-1",
                    content="Keep Source writes separate from Memory generation.",
                    metadata={"channel": "e2e"},
                )
            )
            loaded_source = await client.get_source(
                "source-1",
                GetSourceRequest(scope_id="scope-a", source_type="content"),
            )
            listed_sources = await client.search_sources(
                SearchSourcesRequest(scope_id="scope-a", source_type="content")
            )
            found_sources = await client.search_sources(
                SearchSourcesRequest(
                    scope_id="scope-a",
                    source_type="content",
                    type=SourceQueryType.SEARCH,
                    q="Memory generation",
                )
            )

            first = await client.create_artifact(
                CreateArtifactRequest(
                    scope_id="scope-a",
                    family="document",
                    artifact_id="guide-1",
                    schema_version=1,
                    metadata={"title": "Base API"},
                    content={"body": "Use full replacement updates."},
                    source_refs=[source.source_ref],
                )
            )
            selector = GetArtifactRequest(scope_id="scope-a", family="document")
            listed_artifacts = await client.list_artifacts(ListArtifactsRequest(scope_id="scope-a", family="document"))
            found_artifacts = await client.search_artifacts(
                SearchArtifactsRequest(scope_id="scope-a", family="document", q="replacement updates")
            )
            second = await client.replace_artifact(
                "guide-1",
                selector,
                ReplaceArtifactRequest(
                    schema_version=2,
                    metadata={"title": "Base API"},
                    content={"body": "Use If-Match with full replacement updates."},
                    source_refs=[source.source_ref],
                    artifact_refs=[first.artifact_ref],
                ),
                expected_revision=1,
            )
            exact_first = await client.get_artifact_revision(
                "guide-1",
                1,
                GetArtifactRevisionRequest(scope_id="scope-a", family="document"),
            )
            scopes = await client.list_scopes(ListScopesRequest())

            with pytest.raises(ServerResponseError) as stale:
                await client.replace_artifact(
                    "guide-1",
                    selector,
                    ReplaceArtifactRequest(schema_version=2, content={"body": "stale"}),
                    expected_revision=1,
                )
            assert stale.value.status_code == 412
            assert stale.value.code == "revision_conflict"

            deleted = await client.delete_artifact(
                "guide-1",
                DeleteArtifactRequest(scope_id="scope-a", family="document"),
                expected_revision=2,
            )
            with pytest.raises(ServerResponseError) as missing:
                await client.get_artifact("guide-1", selector)
            assert missing.value.status_code == 404

        assert source == loaded_source
        assert [item.source_ref.source_id for item in listed_sources.items] == ["source-1"]
        assert [item.source_ref.source_id for item in found_sources.items] == ["source-1"]
        assert listed_sources.query is None
        assert found_sources.query == "Memory generation"
        assert found_sources.mode == "keyword"
        assert [item.artifact_ref.artifact_id for item in listed_artifacts.items] == ["guide-1"]
        assert [hit.artifact.artifact_ref.artifact_id for hit in found_artifacts.hits] == ["guide-1"]
        assert second.artifact_ref.revision == 2
        assert exact_first == first
        assert [(item.scope_id, item.source_count, item.artifact_count) for item in scopes.items] == [("scope-a", 1, 1)]
        assert deleted.status == "deleted"
        assert deleted.artifact_ref == second.artifact_ref

    asyncio.run(scenario())
