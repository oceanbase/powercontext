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
    ArtifactReference,
    ContinueHandoffRequest,
    CreateArtifactRequest,
    CreateSourceRequest,
    GetExperienceRequest,
    GetSkillPackageRequest,
    GetSkillRequest,
    HandoffSelection,
    ListArtifactsRequest,
    ReplaceArtifactRequest,
)
from powercontext.server.factory import create_server_app
from powercontext.server.settings import BearerAuthConfig, McpConfig, ServerSettings


def _memory_content() -> dict[str, object]:
    return {
        "entries": [{"kind": "preference", "text": "用户偏好使用中文回答"}],
    }


def _handoff_content(source_id: str, objective: str = "Transfer the API test result.") -> dict[str, object]:
    return {
        "schema": "powercontext.handoff.v1",
        "objective": objective,
        "state": [
            {
                "text": "The Source and Artifact API passed live HTTP tests.",
                "citations": [
                    {
                        "kind": "source",
                        "source_ref": {"name": "content", "source_id": source_id},
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
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as transport,
        ):
            client = PowerContextClient("http://testserver", http_client=transport, trust_transport_security=True)
            scope_id = (await client.get_default_scope()).scope_id
            encoded_scope = quote(scope_id, safe="")
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
                "receipt_identity",
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
                CreateArtifactRequest.model_validate({"family": "memory", "content": _memory_content()}),
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
            assert len(loaded.content["manifest"]["entries"]) == 1
            assert loaded.content["changes"][0]["op"] == "add"
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
                ReplaceArtifactRequest.model_validate({
                    "content": {"entries": [{"kind": "working_note", "text": "继续验证基础 API"}]}
                }),
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
            assert replacement_source.content == {"entries": [{"kind": "working_note", "text": "继续验证基础 API"}]}
            exact_first = await client.get_artifact_revision(scope_id, "memory", created.artifact_id, 1)
            assert exact_first.revision == 1
            assert exact_first.sources == created.sources

            with pytest.raises(ServerResponseError) as stale:
                await client.replace_artifact(
                    scope_id,
                    "memory",
                    created.artifact_id,
                    ReplaceArtifactRequest.model_validate({
                        "content": {"entries": [{"kind": "working_note", "text": "不能覆盖并发更新"}]}
                    }),
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
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as transport,
        ):
            client = PowerContextClient("http://testserver", http_client=transport, trust_transport_security=True)
            scope_id = (await client.get_default_scope()).scope_id
            evidence = await client.create_source(scope_id, CreateSourceRequest(content="verified API test"))
            created = await client.create_artifact(
                scope_id,
                CreateArtifactRequest.model_validate({
                    "family": "handoff",
                    "content": _handoff_content(evidence.source_id),
                }),
            )
            assert created.artifact_id == "handoff"
            loaded = await client.get_artifact(scope_id, "handoff", created.artifact_id)
            assert loaded is not None
            assert loaded.sources[0] == created.sources[0]
            assert loaded.sources[1].source_id == evidence.source_id
            assert loaded.content["objective"] == _handoff_content(evidence.source_id)["objective"]
            assert loaded.content["state"][0]["citations"][0]["source_ref"] == {
                "source_type": "content",
                "source_id": evidence.source_id,
            }
            continued = await client.continue_handoff(
                ContinueHandoffRequest(scope_id=scope_id, selection=HandoffSelection.LATEST)
            )
            assert continued.content is not None
            assert continued.content.objective == "Transfer the API test result."

            with pytest.raises(ServerResponseError) as duplicate:
                await client.create_artifact(
                    scope_id,
                    CreateArtifactRequest.model_validate({
                        "family": "handoff",
                        "content": _handoff_content(evidence.source_id),
                    }),
                )
            assert duplicate.value.status_code == 409
            assert duplicate.value.code == "artifact_already_exists"
            assert duplicate.value.details == {"family": "handoff", "artifact_id": "handoff", "use_replace": True}

            replaced = await client.replace_artifact(
                scope_id,
                "handoff",
                created.artifact_id,
                ReplaceArtifactRequest.model_validate({
                    "content": _handoff_content(evidence.source_id, "Transfer the verified API test result.")
                }),
                expected_etag='"revision:1"',
            )
            assert replaced.revision == 2
            assert replaced.sources[0].source_id != created.sources[0].source_id
            assert replaced.sources[1].source_id == evidence.source_id
            assert replaced.content["objective"] == "Transfer the verified API test result."

    asyncio.run(scenario())


def test_experience_and_skill_base_writes_are_visible_through_existing_apis(tmp_path: Path) -> None:
    app = create_server_app(
        settings=ServerSettings(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'family-base-access.db'}"),
            auth=BearerAuthConfig(enabled=False),
            mcp=McpConfig(enabled=False),
        )
    )

    async def scenario() -> None:
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as transport,
        ):
            client = PowerContextClient("http://testserver", http_client=transport, trust_transport_security=True)
            scope_id = (await client.get_default_scope()).scope_id
            experience_content = {
                "situation": "A compatibility issue was found before release",
                "action": "Add cross-version tests",
                "outcome": "Avoided a production regression",
                "lesson": "Public API changes require compatibility coverage",
            }
            experience = await client.create_artifact(
                scope_id,
                CreateArtifactRequest.model_validate({"family": "experience", "content": experience_content}),
            )
            experience_ref = ArtifactReference(
                family="experience",
                artifact_id=experience.artifact_id,
                revision=experience.revision,
            )
            loaded_experience = await client.get_experience(
                GetExperienceRequest(scope_id=scope_id, artifact=experience_ref)
            )
            assert loaded_experience.content.model_dump() == experience_content

            skill = await client.create_artifact(
                scope_id,
                CreateArtifactRequest.model_validate({
                    "family": "skill",
                    "content": {
                        "name": "compatibility-check",
                        "description": "Check compatibility before release",
                        "instructions": "Run cross-version compatibility tests.",
                        "validation": ["Compatibility tests pass"],
                    },
                }),
            )
            skill_ref = ArtifactReference(family="skill", artifact_id=skill.artifact_id, revision=skill.revision)
            loaded_skill = await client.get_skill(GetSkillRequest(scope_id=scope_id, artifact=skill_ref))
            assert loaded_skill.content.package is not None
            package = await client.get_skill_package_manifest(
                GetSkillPackageRequest(scope_id=scope_id, artifact=skill_ref)
            )
            assert package.name == "compatibility-check"
            assert [item.path for item in package.files] == ["SKILL.md"]

    asyncio.run(scenario())
