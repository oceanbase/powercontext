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
    BaseArtifactFamily,
    CreateArtifactRequest,
    CreateSourceRequest,
    ListArtifactsRequest,
    ReplaceArtifactRequest,
)
from powercontext.server.factory import create_server_app
from powercontext.server.settings import BearerAuthConfig, McpConfig, ServerSettings


def _memory_content() -> dict[str, object]:
    return {
        "manifest": {"entries": [], "format": "flat-v1"},
        "changes": [],
        "schema": "powercontext.memory.v1",
    }


def _handoff_content(objective: str = "Transfer the API test result.") -> dict[str, object]:
    return {
        "schema": "powercontext.handoff.v1",
        "objective": objective,
        "state": [
            {
                "text": "The Source and Artifact API passed live HTTP tests.",
                "citations": [
                    {
                        "kind": "source",
                        "source_ref": {"source_type": "content", "source_id": "source-evidence"},
                    }
                ],
            }
        ],
        "disposition": "complete",
        "next_action": None,
        "omissions": [],
    }


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
        encoded_scope = quote(scope_id, safe="")
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as transport,
        ):
            client = PowerContextClient("http://testserver", http_client=transport, trust_transport_security=True)
            source = await client.create_source(
                scope_id,
                CreateSourceRequest(content={"statement": "Keep the public Source immutable."}),
            )
            assert await client.get_source(scope_id, "content", source.source_id) == source
            assert source.content == {"statement": "Keep the public Source immutable."}
            assert set(source.model_dump()) == {
                "scope_id",
                "source_type",
                "source_id",
                "content",
                "position",
                "content_digest",
            }
            null_source = await client.create_source(scope_id, CreateSourceRequest(content=None))
            assert null_source.content is None
            assert (await client.get_source(scope_id, "content", null_source.source_id)).content is None

            invalid_source_type = await transport.get(
                f"/v1/scopes/{encoded_scope}/sources/private/{quote(source.source_id, safe='')}"
            )
            assert invalid_source_type.status_code == 422
            invalid_family = await transport.post(
                f"/v1/scopes/{encoded_scope}/artifacts",
                json={"family": "document", "content": {}},
            )
            assert invalid_family.status_code == 422

            created = await client.create_artifact(
                scope_id,
                CreateArtifactRequest(family=BaseArtifactFamily.MEMORY, content=_memory_content()),
            )
            assert created.revision == 1
            assert len(created.sources) == 1
            assert created.artifacts == []
            assert "content" not in created.model_dump()

            system_source = await client.get_source(
                scope_id,
                created.sources[0].source_type.value,
                created.sources[0].source_id,
            )
            assert system_source.content == _memory_content()
            assert "internal" not in system_source.model_dump()

            head_path = f"/v1/scopes/{encoded_scope}/artifacts/memory/{quote(created.artifact_id, safe='')}"
            raw_head = await transport.get(head_path)
            assert raw_head.status_code == 200
            etag = raw_head.headers["ETag"]
            loaded = await client.get_artifact(scope_id, "memory", created.artifact_id)
            assert loaded is not None
            assert loaded.sources == created.sources
            not_modified = await client.get_artifact(
                scope_id,
                "memory",
                created.artifact_id,
                if_none_match=etag,
            )
            assert not_modified is None

            listed = await client.list_artifacts(scope_id, "memory", ListArtifactsRequest())
            assert [item.artifact_id for item in listed.items] == [created.artifact_id]
            assert "content" not in listed.items[0].model_dump()
            assert listed.items[0].sources == created.sources

            replaced = await client.replace_artifact(
                scope_id,
                "memory",
                created.artifact_id,
                ReplaceArtifactRequest(content=_memory_content()),
                expected_etag=etag,
            )
            assert replaced.revision == 2
            assert len(replaced.sources) == 1
            assert replaced.sources != created.sources
            replacement_source = await client.get_source(
                scope_id,
                replaced.sources[0].source_type.value,
                replaced.sources[0].source_id,
            )
            assert replacement_source.content == _memory_content()
            exact_first = await client.get_artifact_revision(scope_id, "memory", created.artifact_id, 1)
            assert exact_first.revision == 1
            assert exact_first.sources == created.sources

            with pytest.raises(ServerResponseError) as stale:
                await client.replace_artifact(
                    scope_id,
                    "memory",
                    created.artifact_id,
                    ReplaceArtifactRequest(content=_memory_content()),
                    expected_etag=etag,
                )
            assert stale.value.status_code == 412
            assert stale.value.code == "revision_conflict"

    asyncio.run(scenario())


def test_handoff_artifact_round_trip_accepts_json_arrays(tmp_path: Path) -> None:
    app = create_server_app(
        settings=ServerSettings(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'handoff-base-access.db'}"),
            auth=BearerAuthConfig(enabled=False),
            mcp=McpConfig(enabled=False),
        )
    )

    async def scenario() -> None:
        scope_id = "git:github.com/oceanbase/powercontext"
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as transport,
        ):
            client = PowerContextClient("http://testserver", http_client=transport, trust_transport_security=True)
            created = await client.create_artifact(
                scope_id,
                CreateArtifactRequest(family=BaseArtifactFamily.HANDOFF, content=_handoff_content()),
            )
            loaded = await client.get_artifact(scope_id, "handoff", created.artifact_id)
            assert loaded is not None
            assert loaded.content == _handoff_content()

            replaced = await client.replace_artifact(
                scope_id,
                "handoff",
                created.artifact_id,
                ReplaceArtifactRequest(content=_handoff_content("Transfer the verified API test result.")),
                expected_etag='"revision:1"',
            )
            assert replaced.revision == 2
            assert replaced.content == _handoff_content("Transfer the verified API test result.")

    asyncio.run(scenario())
