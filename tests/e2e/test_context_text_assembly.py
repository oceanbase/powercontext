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

"""Client-to-storage acceptance for request-local context assembly."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import httpx
import pytest

from powercontext.builtin.artifacts.memory import MemoryService
from powercontext.builtin.artifacts.topic_memory import (
    TopicMemoryContent,
    TopicMemoryDraft,
    chunk_topic_memory_detail,
    prepare_topic_memory_projection,
)
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import BuiltinConfig, InvalidRuntimeRequestError, open_builtin_contexts
from powercontext.builtin.runtime import PrepareContextRequest as RuntimePrepareContextRequest
from powercontext.client import PowerContextClient
from powercontext.http import (
    ApproveArtifactCandidateRequest,
    CaptureContentSourceRequest,
    CreateScopeRequest,
    ExperienceProposal,
    GetMemoryEntryRequest,
    PrepareContextRequest,
    ProposeExperienceRequest,
    RememberMemoryRequest,
    ReviseMemoryEntryRequest,
)
from powercontext.server.factory import create_server_app
from powercontext.server.settings import McpConfig, MetricsConfig, ServerSettings


@asynccontextmanager
async def _server(tmp_path):
    app = create_server_app(
        settings=ServerSettings(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'assembly.db'}"),
            mcp=McpConfig(enabled=False),
            metrics=MetricsConfig(enabled=False),
        ),
        scheduler_path=tmp_path / "scheduler.db",
    )
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as transport,
    ):
        yield (
            app.state.application,
            transport,
            PowerContextClient(
                "http://testserver",
                http_client=transport,
                trust_transport_security=True,
            ),
        )


async def _seed_topics(database, scope_ids, embedding_model=None):
    async with open_builtin_contexts(BuiltinConfig(database=database), embedding_model=embedding_model) as contexts:
        for scope_id in scope_ids:
            content = TopicMemoryContent(
                title=f"OpenAPI client topic in {scope_id}",
                summary="OpenAPI client contract assembly evidence.",
                detail="OpenAPI client contract details. " * 100,
            )
            projection = prepare_topic_memory_projection(content)
            if embedding_model is not None:
                chunks = chunk_topic_memory_detail(content.detail)
                result = await embedding_model.embed((f"{content.title}\n{content.summary}", *(c.text for c in chunks)))
                projection = prepare_topic_memory_projection(
                    content,
                    topic_embedding=result.vectors[0],
                    chunk_embeddings=result.vectors[1:],
                    embedding_profile=embedding_model.profile,
                )
            async with contexts.database.transaction() as connection:
                await contexts.repositories.topic_memories.publish_create(
                    connection, scope_id, "assembly-topic", TopicMemoryDraft(content=content), projection
                )


def test_client_assembles_approved_evidence_and_preserves_exact_memory_versions(tmp_path, monkeypatch):
    async def scenario():
        async with _server(tmp_path) as (_, transport, client):
            scope = await client.create_scope(
                CreateScopeRequest(
                    title="Assembly",
                    summary="Context delivery",
                    idempotency_key="assembly",
                )
            )
            scope_id = scope.scope_id
            await _seed_topics(SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'assembly.db'}"), [scope_id])
            profile = await transport.post(
                f"/v1/scopes/{scope_id}/artifacts",
                json={"family": "profile", "content": {"content": "Prefers concise Chinese explanations."}},
            )
            assert profile.status_code == 201, profile.text
            remembered = await client.remember_memory(
                RememberMemoryRequest(
                    scope_id=scope_id,
                    kind="constraint",
                    text="Regenerate the OpenAPI client before contract tests.",
                )
            )
            assert remembered.entry is not None
            citation = remembered.entry.citation
            source = await client.capture_content_source(
                CaptureContentSourceRequest(
                    scope_id=scope_id,
                    source_id="verified-task",
                    content="OpenAPI regeneration repaired the client.",
                )
            )
            candidate = await client.propose_experience(
                ProposeExperienceRequest(
                    scope_id=scope_id,
                    proposal=ExperienceProposal(
                        situation="The OpenAPI client was stale.",
                        action="Regenerate the OpenAPI client.",
                        outcome="The OpenAPI contract tests passed.",
                        lesson="Regenerate the client before contract tests.",
                    ),
                    source_refs=[source.source],
                    artifact_refs=[],
                )
            )
            request = PrepareContextRequest.model_validate({
                "scope_id": scope_id,
                "query": "OpenAPI client",
                "assembly": {
                    "sections": [
                        {"family": "profile", "limit": 1},
                        {"family": "topic-memory", "limit": 2},
                        {"family": "experience", "limit": 2},
                        {"family": "memory", "limit": 3},
                    ],
                },
            })
            pending = await client.prepare_context(request)
            assert pending.content is not None
            assert "## Experience" not in pending.content
            await client.approve_artifact_candidate(
                ApproveArtifactCandidateRequest(
                    scope_id=scope_id,
                    candidate_id=candidate.candidate_id,
                    expected_version=candidate.version,
                )
            )
            prepared = await client.prepare_context(request)
            assert prepared.content is not None
            assert prepared.content.index("## Profile") < prepared.content.index("## Topic Memory")
            assert prepared.content.index("## Topic Memory") < prepared.content.index("## Experience")
            assert 'Artifact: family="topic-memory", id="assembly-topic", revision=1' in prepared.content
            assert ">     Title: OpenAPI client topic" in prepared.content
            assert "Prefers concise Chinese explanations." in prepared.content
            assert 'Artifact: family="profile", id="profile", revision=1' in prepared.content
            assert prepared.content.index("## Experience") < prepared.content.index("## Memory")
            assert citation.entry_version_id in prepared.content
            assert prepared.content_bytes == len(prepared.content.encode("utf-8")) <= request.max_bytes

            await client.revise_memory_entry(
                ReviseMemoryEntryRequest(
                    scope_id=scope_id,
                    citation=citation,
                    kind="constraint",
                    text="OpenAPI validation now also includes generated JS.",
                )
            )
            exact = await client.get_memory_entry(GetMemoryEntryRequest(scope_id=scope_id, citation=citation))
            assert exact.text == remembered.entry.text

            legacy = await client.prepare_context(PrepareContextRequest(scope_id=scope_id, query="OpenAPI client"))
            assert legacy.content is not None
            assert '"items":[' in legacy.content
            assert "BEGIN_POWERCONTEXT_PREPARED_CONTEXT_V1" in legacy.content
            assert "Prefers concise Chinese explanations." not in legacy.content
            default_text = await client.prepare_context(
                PrepareContextRequest.model_validate({"scope_id": scope_id, "query": "OpenAPI", "assembly": {}})
            )
            assert default_text.content is not None and "## Profile" not in default_text.content
            assert "## Topic Memory" not in default_text.content
            empty = await transport.post(
                "/v1/context/prepare",
                json={
                    "scope_id": scope_id,
                    "query": "OpenAPI",
                    "assembly": {"sections": []},
                },
            )
            assert empty.json() == {
                "schema": "powercontext.prepared-context.v1",
                "status": "empty",
                "content": None,
                "content_bytes": 0,
            }

            async def unavailable_memory(*args, **kwargs):
                raise RuntimeError("Excluded Memory backend is unavailable")  # noqa: TRY003

            monkeypatch.setattr(MemoryService, "search", unavailable_memory)
            experience_only = await client.prepare_context(
                PrepareContextRequest.model_validate({
                    "scope_id": scope_id,
                    "query": "OpenAPI client",
                    "assembly": {"sections": [{"family": "experience", "limit": 2}]},
                })
            )
            assert experience_only.content is not None
            assert "## Experience" in experience_only.content and "## Memory" not in experience_only.content

    asyncio.run(scenario())


def test_topic_only_assembly_searches_current_scope_and_skips_other_families(tmp_path, monkeypatch):
    async def scenario():
        async with _server(tmp_path) as (runtime, _, client):
            shared = await client.create_scope(
                CreateScopeRequest(title="Shared", summary="Topic", idempotency_key="shared")
            )
            current = await client.create_scope(
                CreateScopeRequest(
                    title="Current", summary="Topic", idempotency_key="current", context_references=[shared.scope_id]
                )
            )
            await _seed_topics(
                SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'assembly.db'}"),
                [shared.scope_id, current.scope_id],
            )

            async def unavailable(*args, **kwargs):
                raise RuntimeError("Unselected backend is unavailable")  # noqa: TRY003

            monkeypatch.setattr(MemoryService, "search", unavailable)
            monkeypatch.setattr(runtime, "_experience_recall", unavailable)
            assert runtime.profiles is not None
            monkeypatch.setattr(runtime.profiles, "latest", unavailable)
            prepared = await client.prepare_context(
                PrepareContextRequest.model_validate({
                    "scope_id": current.scope_id,
                    "query": "OpenAPI client",
                    "assembly": {"sections": [{"family": "topic-memory", "limit": 8}]},
                })
            )
            assert prepared.content is not None
            assert prepared.content.count('family="topic-memory"') == 1
            assert f'Scope: "{current.scope_id}"' in prepared.content
            assert shared.scope_id not in prepared.content
            assert "## Memory" not in prepared.content and "## Experience" not in prepared.content
            assert "## Profile" not in prepared.content

    asyncio.run(scenario())


@pytest.mark.parametrize("max_entries", [1, 8, 9])
def test_configured_assembly_total_limit_applies_before_recall(tmp_path, monkeypatch, max_entries):
    monkeypatch.setenv("POWERCONTEXT_SERVER_RUNTIME_CONTEXT_ASSEMBLY_MAX_ENTRIES", str(max_entries))

    async def scenario():
        async with _server(tmp_path) as (runtime, transport, client):
            scope = await client.create_scope(
                CreateScopeRequest(title="Configured assembly", summary="Entry limit", idempotency_key="entry-limit")
            )
            for index in range(8):
                await client.remember_memory(
                    RememberMemoryRequest(
                        scope_id=scope.scope_id, kind="fact", text=f"OpenAPI assembly requirement number {index}."
                    )
                )
            profile = await transport.post(
                f"/v1/scopes/{scope.scope_id}/artifacts",
                json={"family": "profile", "content": {"content": "Prefers explicit verification."}},
            )
            assert profile.status_code == 201
            legacy = await client.prepare_context(
                PrepareContextRequest(scope_id=scope.scope_id, query="OpenAPI assembly")
            )
            assert legacy.content is not None and legacy.content.count('"entry_id"') == 8
            sections = [{"family": "profile", "limit": 1}, {"family": "memory", "limit": 1 if max_entries == 1 else 8}]
            request = PrepareContextRequest.model_validate({
                "scope_id": scope.scope_id,
                "query": "OpenAPI assembly",
                "assembly": {"sections": sections},
            })
            if max_entries >= 9:
                prepared = await client.prepare_context(request)
                assert prepared.content is not None
                assert prepared.content.count("Artifact: family=") == 9
                assert prepared.content.count('family="memory"') == 8
                assert prepared.content_bytes == len(prepared.content.encode("utf-8")) <= request.max_bytes
            else:

                async def unavailable(*args, **kwargs):
                    raise RuntimeError("Recall must not run for an oversized assembly")  # noqa: TRY003

                monkeypatch.setattr(MemoryService, "search", unavailable)
                assert runtime.profiles is not None
                monkeypatch.setattr(runtime.profiles, "latest", unavailable)
                rejected = await transport.post("/v1/context/prepare", json=request.model_dump(mode="json"))
                assert rejected.status_code == 422
                assert rejected.json()["error"]["code"] == "invalid_request"
                assert "OpenAPI assembly" not in rejected.text
                with pytest.raises(InvalidRuntimeRequestError, match="context-assembly-entry-limit"):
                    await runtime.context.for_scope(scope.scope_id).prepare(
                        RuntimePrepareContextRequest.model_validate({
                            "query": "OpenAPI assembly",
                            "assembly": {"sections": sections},
                        })
                    )

            empty = await client.prepare_context(
                PrepareContextRequest.model_validate({
                    "scope_id": scope.scope_id,
                    "query": "OpenAPI",
                    "assembly": {"sections": []},
                })
            )
            assert empty.status == "empty"

    asyncio.run(scenario())


def test_excluded_recall_source_failure_does_not_affect_selected_memory(tmp_path, monkeypatch):
    async def scenario():
        async with _server(tmp_path) as (runtime, transport, client):
            scope = await client.create_scope(
                CreateScopeRequest(
                    title="Assembly",
                    summary="Source exclusion",
                    idempotency_key="exclude",
                )
            )
            await client.remember_memory(
                RememberMemoryRequest(
                    scope_id=scope.scope_id,
                    kind="fact",
                    text="OpenAPI contract evidence.",
                )
            )

            async def unavailable(*args):
                raise RuntimeError("Excluded Experience backend is unavailable")  # noqa: TRY003

            monkeypatch.setattr(runtime, "_experience_recall", unavailable)
            monkeypatch.setattr(runtime, "_topic_memory_search", unavailable)
            result = await client.prepare_context(
                PrepareContextRequest.model_validate({
                    "scope_id": scope.scope_id,
                    "query": "OpenAPI",
                    "assembly": {
                        "sections": [{"family": "memory", "limit": 8}],
                    },
                })
            )
            assert result.content is not None and "OpenAPI contract evidence." in result.content
            assert "## Experience" not in result.content

            async def no_memory(*args, **kwargs):
                raise RuntimeError("Excluded Memory backend is unavailable")  # noqa: TRY003

            monkeypatch.setattr(MemoryService, "search", no_memory)
            empty = await transport.post(
                "/v1/context/prepare",
                json={
                    "scope_id": scope.scope_id,
                    "query": "OpenAPI",
                    "assembly": {"sections": []},
                },
            )
            assert empty.status_code == 200 and empty.json()["status"] == "empty"

    asyncio.run(scenario())


def test_profile_selection_reads_only_current_and_direct_scopes_without_search(tmp_path, monkeypatch):
    async def scenario():
        async with _server(tmp_path) as (runtime, transport, client):
            scopes = {}
            for name, references in [
                ("transitive", []),
                ("unrelated", []),
                ("shared", ["transitive"]),
                ("current", ["shared"]),
            ]:
                scope = await client.create_scope(
                    CreateScopeRequest(
                        title=name,
                        summary="Profile assembly scope",
                        idempotency_key=name,
                        context_references=[scopes[reference] for reference in references],
                    )
                )
                scopes[name] = scope.scope_id
                created = await transport.post(
                    f"/v1/scopes/{scope.scope_id}/artifacts",
                    json={"family": "profile", "content": {"content": f"{name} snapshot"}},
                )
                assert created.status_code == 201, created.text

            async def unavailable(*args, **kwargs):
                raise RuntimeError("Unselected search backend is unavailable")  # noqa: TRY003

            monkeypatch.setattr(MemoryService, "search", unavailable)
            monkeypatch.setattr(runtime, "_experience_recall", unavailable)
            monkeypatch.setattr(runtime, "_topic_memory_search", unavailable)
            assert runtime.profiles is not None
            monkeypatch.setattr(runtime.profiles, "generator", None)
            for limit, expected in [(1, ["current"]), (8, ["current", "shared"])]:
                result = await client.prepare_context(
                    PrepareContextRequest.model_validate({
                        "scope_id": scopes["current"],
                        "query": "a completely unrelated query",
                        "assembly": {
                            "sections": [{"family": "profile", "limit": limit}],
                            "show": ["recall_rank", "confidence"],
                        },
                    })
                )
                assert result.content is not None
                assert result.content.count('Artifact: family="profile"') == len(expected)
                for name in scopes:
                    assert (f"{name} snapshot" in result.content) == (name in expected)
                if limit == 8:
                    assert result.content.index("current snapshot") < result.content.index("shared snapshot")
                    assert 'Scope: "' + scopes["shared"] + '"' in result.content
                    assert "Recall rank: 2" in result.content
                assert "Confidence: unknown (not assessed)" in result.content
                assert "## Memory" not in result.content and "## Experience" not in result.content
                assert result.content_bytes == len(result.content.encode("utf-8"))

    asyncio.run(scenario())


def test_prepare_profile_uses_committed_head_across_review_and_replacement(tmp_path):
    class Generator:
        async def generate(self, value):
            return "Reviewed preference"

    async def scenario():
        async with _server(tmp_path) as (runtime, transport, client):
            scope = await client.create_scope(
                CreateScopeRequest(title="Profile", summary="Snapshot lifecycle", idempotency_key="profile")
            )
            path = f"/v1/scopes/{scope.scope_id}"
            request = PrepareContextRequest.model_validate({
                "scope_id": scope.scope_id,
                "query": "preferences",
                "assembly": {"sections": [{"family": "profile", "limit": 1}]},
            })
            assert (await client.prepare_context(request)).status == "empty"
            policy = await transport.put(
                path + "/profile-policy",
                json={"generation_enabled": True, "activation_mode": "review_required", "expected_version": 0},
            )
            assert policy.status_code == 200, policy.text
            assert runtime.profiles is not None
            runtime.profiles.generator = Generator()
            source = await transport.post(path + "/sources", json={"content": "Reviewed preference evidence"})
            assert source.status_code == 201, source.text
            pending = await transport.post("/v1/profile/flush", json={"scope_id": scope.scope_id})
            assert pending.status_code == 200 and pending.json()["status"] == "review_pending"
            assert (await client.prepare_context(request)).status == "empty"
            approved = await transport.post(
                "/v1/artifact-candidates/approve",
                json={
                    "scope_id": scope.scope_id,
                    "candidate_id": pending.json()["candidate_id"],
                    "expected_version": 1,
                },
            )
            assert approved.status_code == 200, approved.text
            first = await client.prepare_context(request)
            assert first.content is not None and "Reviewed preference" in first.content
            assert 'family="profile", id="profile", revision=1' in first.content

            current = await transport.get(path + "/artifacts/profile/profile")
            replaced = await transport.put(
                path + "/artifacts/profile/profile",
                headers={"If-Match": current.headers["ETag"]},
                json={"content": {"content": "Updated preference"}},
            )
            assert replaced.status_code == 200, replaced.text
            latest = await client.prepare_context(request)
            assert latest.content is not None and "Updated preference" in latest.content
            assert "Reviewed preference" not in latest.content
            assert 'family="profile", id="profile", revision=2' in latest.content
            exact = await transport.get(path + "/artifacts/profile/profile/revisions/1")
            assert exact.json()["content"]["content"].strip() == "Reviewed preference"

            source = await transport.post(path + "/sources", json={"content": "Another review window"})
            assert source.status_code == 201
            pending = await transport.post("/v1/profile/flush", json={"scope_id": scope.scope_id})
            assert pending.status_code == 200 and pending.json()["status"] == "review_pending"
            assert (await client.prepare_context(request)).content == latest.content

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "assembly",
    [
        None,
        {"sections": [{"family": "experience", "limit": 3}]},
        {"sections": [{"family": "profile", "limit": 1}, {"family": "profile", "limit": 1}]},
        {"sections": [{"family": "profile", "limit": 9}]},
        {"sections": [{"family": "profile", "limit": 8}, {"family": "memory", "limit": 1}]},
        {"sections": [{"family": "topic-memory", "limit": 9}]},
        {"sections": [{"family": "topic-memory", "limit": 8}, {"family": "profile", "limit": 1}]},
        {"sections": [{"family": "topic-memory", "limit": 1}, {"family": "topic-memory", "limit": 1}]},
        {"sections": [{"family": "memory", "limit": 1}, {"family": "memory", "limit": 1}]},
        {"sections": [{"family": "memory", "limit": 8}, {"family": "experience", "limit": 1}]},
        {"show": ["confidence", "confidence"]},
        {"show": ["score"]},
        {"format": "json"},
        {"sort_by": "confidence"},
    ],
)
def test_http_rejects_invalid_assembly_without_echoing_inputs(tmp_path, assembly):
    async def scenario():
        async with _server(tmp_path) as (_, transport, client):
            scope = await client.create_scope(
                CreateScopeRequest(
                    title="Assembly",
                    summary="Request validation",
                    idempotency_key="invalid",
                )
            )
            response = await transport.post(
                "/v1/context/prepare",
                json={
                    "scope_id": scope.scope_id,
                    "query": "private-query-text",
                    "assembly": assembly,
                },
            )
            assert response.status_code == 422
            assert response.json()["error"]["code"] == "invalid_request"
            assert "private-query-text" not in response.text

    asyncio.run(scenario())
