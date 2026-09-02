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
from urllib.parse import quote

import httpx
import pytest

from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.client import PowerContextClient, ServerResponseError
from powercontext.http import (
    CreateArtifactRequest,
    CreateSourceRequest,
    ListArtifactsRequest,
    ListSourcesRequest,
    ReplaceArtifactRequest,
    SourceTypeReference,
    TextSearchMode,
)
from powercontext.server.factory import create_server_app
from powercontext.server.settings import BearerAuthConfig, McpConfig, ServerSettings


def test_source_and_artifact_api_round_trip(tmp_path: Path) -> None:
    app = create_server_app(
        settings=ServerSettings(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'base-access.db'}"),
            auth=BearerAuthConfig(enabled=False),
            mcp=McpConfig(enabled=False),
        )
    )

    async def scenario() -> None:
        scope_id = "git:github.com/oceanbase/powercontext"
        family = "company.example/decision"
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://testserver",
            ) as transport,
        ):
            client = PowerContextClient("http://testserver", http_client=transport, trust_transport_security=True)
            unsupported = await transport.post(
                f"/v1/scopes/{quote(scope_id, safe='')}/artifacts",
                json={"family": "memory", "content": {}},
            )
            assert unsupported.status_code == 405
            assert unsupported.headers["Allow"] == "GET"
            source = await client.create_source(
                scope_id,
                CreateSourceRequest(
                    content="Keep Source writes separate from Memory generation.",
                    metadata={"channel": "e2e"},
                ),
            )
            loaded_source = await client.get_source(scope_id, "content", source.source_id)
            listed_sources = await client.list_sources(
                scope_id,
                "content",
                ListSourcesRequest(),
            )
            found_sources = await client.list_sources(
                scope_id,
                "content",
                ListSourcesRequest(query="Memory generation"),
            )
            with pytest.raises(ServerResponseError) as invalid_query:
                await client.list_sources(
                    scope_id,
                    "content",
                    ListSourcesRequest(mode=TextSearchMode.AUTO),
                )
            assert invalid_query.value.status_code == 400
            assert invalid_query.value.code == "invalid_request"
            with pytest.raises(ServerResponseError) as invalid_cursor:
                await client.list_sources(
                    scope_id,
                    "content",
                    ListSourcesRequest(cursor="not-a-valid-cursor"),
                )
            assert invalid_cursor.value.status_code == 400
            assert invalid_cursor.value.code == "invalid_cursor"

            first = await client.create_artifact(
                scope_id,
                CreateArtifactRequest(
                    family=family,
                    content={"body": "Use full replacement updates."},
                    source_refs=[SourceTypeReference(source_type=source.source_type, source_id=source.source_id)],
                ),
            )
            artifact_id = first.artifact_ref.artifact_id
            loaded_first = await client.get_artifact(scope_id, family, artifact_id)
            not_modified = await client.get_artifact(
                scope_id,
                family,
                artifact_id,
                if_none_match='"revision:1"',
            )
            listed_artifacts = await client.list_artifacts(
                scope_id,
                family,
                ListArtifactsRequest(),
            )
            found_artifacts = await client.list_artifacts(
                scope_id,
                family,
                ListArtifactsRequest(query="replacement updates"),
            )
            second = await client.replace_artifact(
                scope_id,
                family,
                artifact_id,
                ReplaceArtifactRequest(
                    content={"body": "Use If-Match with full replacement updates."},
                    source_refs=[SourceTypeReference(source_type=source.source_type, source_id=source.source_id)],
                    artifact_refs=[first.artifact_ref],
                ),
                expected_revision=1,
            )
            exact_first = await client.get_artifact_revision(
                scope_id,
                family,
                artifact_id,
                1,
            )

            with pytest.raises(ServerResponseError) as stale:
                await client.replace_artifact(
                    scope_id,
                    family,
                    artifact_id,
                    ReplaceArtifactRequest(content={"body": "stale"}),
                    expected_revision=1,
                )
            assert stale.value.status_code == 412
            assert stale.value.code == "revision_conflict"

            deleted = await client.delete_artifact(
                scope_id,
                family,
                artifact_id,
                expected_revision=2,
            )
            with pytest.raises(ServerResponseError) as missing:
                await client.get_artifact(scope_id, family, artifact_id)
            assert missing.value.status_code == 404
            exact_second = await client.get_artifact_revision(
                scope_id,
                family,
                artifact_id,
                2,
            )

        assert source == loaded_source
        assert source.scope_id == scope_id
        assert source.source_type == "content"
        assert source.source_id
        assert source.created_at is not None
        assert [item.source_id for item in listed_sources.items] == [source.source_id]
        assert [item.source_id for item in found_sources.items] == [source.source_id]
        assert "content" not in listed_sources.items[0].model_dump()
        assert listed_sources.query is None
        assert found_sources.query == "Memory generation"
        assert found_sources.mode == "keyword"
        assert loaded_first == first
        assert not_modified is None
        assert first.created_at is not None
        assert [item.artifact_ref.artifact_id for item in listed_artifacts.items] == [artifact_id]
        assert [item.artifact_ref.artifact_id for item in found_artifacts.items] == [artifact_id]
        assert "content" not in listed_artifacts.items[0].model_dump()
        assert found_artifacts.query == "replacement updates"
        assert found_artifacts.mode == "keyword"
        assert second.artifact_ref.revision == 2
        assert second.created_at is not None
        assert exact_first == first
        assert exact_second == second
        assert deleted is None

    asyncio.run(scenario())
