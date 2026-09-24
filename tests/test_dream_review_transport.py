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

"""Public review, compatibility, and browser decisions against isolated state."""

from __future__ import annotations

import asyncio
from typing import cast

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict

from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import BuiltinRuntime, RuntimeCapabilities
from powercontext.client import PowerContextClient
from powercontext.http import (
    ApproveCandidateRequest,
    GetCandidateRequest,
    ListCandidatesRequest,
    ReviseCandidateRequest,
)
from powercontext.server.app import ServerApplication, create_app
from powercontext.server.dashboard.preferences import CATALOGS
from powercontext.server.dashboard.routes import ENV
from powercontext.server.dream_compatibility import negotiate_dream_contract
from tests.e2e.test_catalog_changes import proposal, seed
from tests.test_dashboard import create_scope
from tests.test_dashboard import dashboard as dashboard


@pytest.mark.parametrize("enabled", [False, True])
def test_capabilities_remain_decodable_by_legacy_clients(enabled):
    # Freeze the pre-extension field set: old generated clients reject extra fields.
    class LegacyCapabilities(BaseModel):
        model_config = ConfigDict(extra="forbid")

        prompts: dict[str, object] = {}
        artifact_dreaming: bool = False
        source_types: list[str]
        artifact_families: list[str]
        memory_extraction: bool
        experience_generation: bool = False
        managed_skill_generation: bool = False
        external_skill_registry: bool = False
        handoff_generation: bool
        search_modes: list[str]
        context_versions: list[str]

    legacy = LegacyCapabilities(
        artifact_dreaming=enabled,
        source_types=["content"],
        artifact_families=["memory"],
        memory_extraction=False,
        handoff_generation=False,
        search_modes=["fts"],
        context_versions=["powercontext.prepared-context.v1"],
    )
    operations = (
        [
            {"operation": "refine_experience", "output_kind": "candidate", "effect": "review_then_publish"},
            {
                "operation": "revise_tags",
                "output_kind": "tag_candidate",
                "effect": "review_then_replace_tags",
            },
        ]
        if enabled
        else []
    )
    payload = {**legacy.model_dump(), "artifact_dreaming_operations": operations}
    app = FastAPI()
    app.middleware("http")(negotiate_dream_contract)

    @app.get("/v1/capabilities")
    async def capabilities():
        return payload

    with TestClient(app) as client:
        old = client.get("/v1/capabilities")
        assert old.status_code == 200
        assert LegacyCapabilities.model_validate(old.json()) == legacy
        current = client.get("/v1/capabilities", headers={"X-PowerContext-Dream-Contract": "2"})
        assert current.status_code == 200
        assert current.json() == payload


def test_catalog_http_client_preserves_separate_result_and_version_history(tmp_path):
    async def scenario():
        async with seed(SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path}/catalog-http.db")) as (
            contexts,
            scope,
            source,
            artifact,
        ):
            candidate = await contexts.catalog_changes(scope).propose(
                await proposal(contexts, scope, artifact), sources=(source,), reason="Confirmed observation"
            )
            application = BuiltinRuntime(
                scope_application=contexts.scopes,
                provider=contexts,
                capabilities=RuntimeCapabilities(memory_extraction=False, memory_search_modes=("fts",)),
                review_service=contexts.review,
                catalog_change_service=contexts.catalog_changes,
            )
            app = create_app(application=cast(ServerApplication, application))
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as http:
                legacy = await http.post("/v1/catalog-change-candidates/list", json={"scope_id": scope})
                assert legacy.status_code == 404
                assert (await http.post("/v1/artifact-candidates/list", json={"scope_id": scope})).status_code == 404
                async with PowerContextClient(
                    "http://testserver", http_client=http, trust_transport_security=True
                ) as client:
                    listed = await client.list_candidates(ListCandidatesRequest(scope_id=scope))
                    assert [item.candidate_id for item in listed.candidates] == [candidate.candidate_id]
                    assert listed.candidates[0].candidate_kind.value == "tag"
                    artifact_candidate = await contexts.review(scope).propose_experience(
                        artifact.content, sources=(source,), artifacts=(), target=None, reason="Review content"
                    )
                    first = await client.list_candidates(ListCandidatesRequest(scope_id=scope, limit=1))
                    second = await client.list_candidates(
                        ListCandidatesRequest(scope_id=scope, limit=1, cursor=first.next_cursor)
                    )
                    assert [item.candidate_id for item in (*first.candidates, *second.candidates)] == sorted([
                        candidate.candidate_id,
                        artifact_candidate.candidate_id,
                    ])
                    assert second.next_cursor is None
                    filtered = await client.list_candidates(
                        ListCandidatesRequest.model_validate({
                            "scope_id": scope,
                            "candidate_kind": "tag",
                            "family": "experience",
                        })
                    )
                    assert [item.candidate_id for item in filtered.candidates] == [candidate.candidate_id]
                    bad = await http.post(
                        "/v1/candidates/revise",
                        json={
                            "scope_id": scope,
                            "candidate_id": candidate.candidate_id,
                            "expected_version": 1,
                            "proposal": {**candidate.proposal.model_dump(mode="json"), "expected_etag": "changed"},
                            "reason": "Cannot reset baseline",
                        },
                    )
                    assert bad.status_code == 422
                    revised = await client.revise_candidate(
                        ReviseCandidateRequest.model_validate({
                            "scope_id": scope,
                            "candidate_id": candidate.candidate_id,
                            "expected_version": 1,
                            "proposal": {
                                **candidate.proposal.model_dump(mode="json"),
                                "after_tags": ["confirmed", "delivery"],
                            },
                            "reason": "Keep the category too",
                        })
                    )
                    assert revised.version == 2
                    assert revised.source_refs == listed.candidates[0].source_refs
                    stale = await http.post(
                        "/v1/candidates/approve",
                        headers={"X-PowerContext-Dream-Contract": "2"},
                        json={"scope_id": scope, "candidate_id": candidate.candidate_id, "expected_version": 1},
                    )
                    assert stale.status_code == 409
                    approved = await client.approve_candidate(
                        ApproveCandidateRequest(scope_id=scope, candidate_id=candidate.candidate_id, expected_version=2)
                    )
                    assert approved.status.value == "approved" and approved.result is not None
                    assert approved.result.etag.startswith('"tags:')
                    assert approved.result_artifact is None
                    assert approved.candidate_kind.value == "tag"
                    replay = await client.approve_candidate(
                        ApproveCandidateRequest(scope_id=scope, candidate_id=candidate.candidate_id, expected_version=2)
                    )
                    assert replay == approved
                    history = await client.get_candidate_history(
                        GetCandidateRequest(scope_id=scope, candidate_id=candidate.candidate_id)
                    )
                    assert [(item.version, item.status.value) for item in history.versions] == [
                        (1, "pending"),
                        (2, "pending"),
                    ]

    asyncio.run(scenario())


def test_legacy_profile_read_gets_upgrade_signal_without_disguising_mode():
    app = FastAPI()
    app.middleware("http")(negotiate_dream_contract)

    @app.get("/v1/scopes/example/artifacts/profile/profile")
    async def profile():
        return {
            "schema": "powercontext.profile.v1",
            "content": "Based in Shenzhen",
            "generation": {"mode": "dream_review_approved", "dream_run_id": "run-1"},
        }

    with TestClient(app) as client:
        old = client.get("/v1/scopes/example/artifacts/profile/profile")
        assert old.status_code == 426
        assert old.json()["error"]["details"]["required_dream_contract"] == 2
        current = client.get(
            "/v1/scopes/example/artifacts/profile/profile", headers={"X-PowerContext-Dream-Contract": "2"}
        )
        assert current.status_code == 200
        assert current.json()["generation"]["mode"] == "dream_review_approved"


def test_legacy_original_dream_retains_decodable_envelope():
    app = FastAPI()
    app.middleware("http")(negotiate_dream_contract)

    @app.get("/v1/scopes/example/dream/run-1")
    async def run():
        return {
            "run_id": "run-1",
            "operation": "refine_experience",
            "accepted_at": "2026-09-22T00:00:00Z",
            "candidate": {"candidate_id": "candidate-1", "version": 1, "kind": "artifact"},
            "tag_target": None,
            "reused": False,
        }

    with TestClient(app) as client:
        old = client.get("/v1/scopes/example/dream/run-1")
        assert old.status_code == 200
        assert "tag_target" not in old.json() and "reused" not in old.json()
        assert old.json()["candidate"] == {"candidate_id": "candidate-1", "version": 1}


def test_legacy_dream_with_extended_evidence_requires_upgrade():
    app = FastAPI()
    app.middleware("http")(negotiate_dream_contract)

    @app.get("/v1/scopes/example/dream/run-1")
    async def run():
        return {
            "run_id": "run-1",
            "operation": "refine_experience",
            "accepted_at": "2026-09-22T00:00:00Z",
            "input_manifest": {"nodes": [{"kind": "skill"}]},
        }

    with TestClient(app) as client:
        response = client.get("/v1/scopes/example/dream/run-1")
        assert response.status_code == 426
        assert response.json()["error"]["code"] == "client_upgrade_required"


def test_review_dashboard_exposes_evidence_and_requires_same_origin_decisions(dashboard):
    scope = create_scope(dashboard, "Review inbox")["scope_id"]
    source = dashboard.post(
        f"/v1/scopes/{scope}/sources", json={"content": "Confirmed delivery to the wrong address."}
    ).json()
    proposed = dashboard.post(
        "/v1/experience/propose",
        json={
            "scope_id": scope,
            "proposal": {
                "situation": "Delivery failed",
                "action": "Check the address",
                "outcome": "Incorrect address confirmed",
                "lesson": "Verify the destination",
            },
            "source_refs": [{"name": "content", "source_id": source["source_id"]}],
            "artifact_refs": [],
        },
    )
    assert proposed.status_code == 201, proposed.text
    candidate = proposed.json()
    page = dashboard.get(
        "/dashboard/review", params={"scope": scope, "candidate_id": candidate["candidate_id"], "lang": "en"}
    )
    assert page.status_code == 200, page.text
    assert "Tag change candidates" in page.text and "Artifact candidates" in page.text
    assert "Confirmed delivery to the wrong address." in page.text
    assert "Approve this version" in page.text
    form = {
        "scope": scope,
        "candidate_kind": "artifact",
        "candidate_id": candidate["candidate_id"],
        "expected_version": 1,
        "action": "approve",
    }
    rejected = dashboard.post("/dashboard/review/decision", data=form, headers={"Origin": "https://untrusted.example"})
    assert rejected.status_code == 403
    approved = dashboard.post(
        "/dashboard/review/decision", data=form, headers={"Origin": "http://testserver"}, follow_redirects=False
    )
    assert approved.status_code == 303
    result = dashboard.post("/v1/candidates/get", json={"scope_id": scope, "candidate_id": candidate["candidate_id"]})
    assert result.json()["status"] == "approved"


def test_profile_dream_generation_label_renders_in_both_locales():
    template = ENV.from_string("{{ t['profile_mode_' ~ mode] }}")
    for locale in ("zh", "en"):
        rendered = template.render(t=CATALOGS[locale], mode="dream_review_approved")
        assert "Dream" in rendered


def test_enforced_dream_writes_use_scope_config_and_entry_permissions(tmp_path):
    import pytest

    from powercontext.artifacts import ArtifactRef, MemoryCitation
    from powercontext.builtin.catalog_changes.models import TagDreamTarget
    from powercontext.builtin.dream.models import DreamError
    from powercontext.builtin.tags import ArtifactTagTarget
    from powercontext.server.authz import AccessRole, MemoryEntrySelector, PrincipalRef, ResourceRef
    from powercontext.server.authz.composition import open_builtin_access_control
    from powercontext.server.authz.service import AccessAuditContext, CreateBinding
    from powercontext.server.context import bind_principal, reset_principal
    from powercontext.server.dream_access import DreamAccess, principal_identity

    async def scenario():
        database = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path}/permissions.db")
        admin = PrincipalRef(type="service", id="admin")
        reviewer = PrincipalRef(type="user", id="reviewer")
        context = AccessAuditContext(transport="http", operation="test_dream_family_permissions")
        async with (
            seed(database) as (_contexts, scope, _source, _artifact),
            open_builtin_access_control(database, bootstrap_administrators=(admin,)) as access,
        ):
            await access.create_binding(
                admin,
                CreateBinding(
                    subject=reviewer,
                    resource=ResourceRef.scope(scope),
                    role=AccessRole.SCOPE_REVIEWER,
                    idempotency_key="reviewer",
                ),
                context=context,
            )
            adapter = DreamAccess(access)
            for family in ("prompt", "topic-memory"):
                ref = ArtifactRef(family=family, artifact_id="configuration", revision=1)
                await access.establish_artifact_owner(
                    ResourceRef.artifact(scope, family=family, artifact_id=ref.artifact_id),
                    reviewer,
                    idempotency_key="old-owner:" + family,
                    context=context,
                )
                with pytest.raises(DreamError, match="access_revoked"):
                    await adapter.authorize(scope, principal_identity(reviewer), "write", ref)
                with pytest.raises(DreamError, match="access_revoked"):
                    await adapter.authorize(scope, principal_identity(reviewer), "write_tags", ref)
            memory = ArtifactRef(family="memory", artifact_id="memory", revision=1)
            owned = MemoryCitation(memory_ref=memory, entry_id="owned", entry_version_id="version-1")
            foreign = MemoryCitation(memory_ref=memory, entry_id="foreign", entry_version_id="version-2")
            await access.establish_artifact_owner(
                ResourceRef.artifact(
                    scope, family="memory", artifact_id="memory", selector=MemoryEntrySelector(entry_id="owned")
                ),
                reviewer,
                idempotency_key="entry-owner",
                context=context,
            )
            await access.establish_artifact_owner(
                ResourceRef.artifact(
                    scope, family="memory", artifact_id="memory", selector=MemoryEntrySelector(entry_id="foreign")
                ),
                admin,
                idempotency_key="foreign-owner",
                context=context,
            )
            await adapter.authorize(scope, principal_identity(reviewer), "write", owned)
            with pytest.raises(DreamError, match="access_revoked"):
                await adapter.authorize(scope, principal_identity(reviewer), "write", foreign)
            with pytest.raises(DreamError, match="access_revoked"):
                await adapter.authorize(scope, principal_identity(reviewer), "write_tags", memory)
            target = TagDreamTarget(
                target=ArtifactTagTarget(family="prompt", artifact_id="configuration"),
                expected_etag='"old"',
                basis_ref=ArtifactRef(family="prompt", artifact_id="configuration", revision=1),
            )
            token = bind_principal(reviewer)
            try:
                await adapter.catalog_action(scope, "reject", target)
                with pytest.raises(DreamError, match="access_revoked"):
                    await adapter.catalog_action(scope, "approve", target)
                await access.create_binding(
                    admin,
                    CreateBinding(
                        subject=reviewer,
                        resource=ResourceRef.scope(scope),
                        role=AccessRole.SCOPE_ADMIN,
                        idempotency_key="configuration-admin",
                    ),
                    context=context,
                )
                await adapter.catalog_action(scope, "approve", target)
            finally:
                reset_principal(token)

    asyncio.run(scenario())


def test_review_dashboard_without_explicit_scope_and_invalid_filter(dashboard):
    page = dashboard.get("/dashboard/review", params={"lang": "en"})
    assert page.status_code == 200
    assert "Review inbox" in page.text
    invalid = dashboard.get("/dashboard/review", params={"resource_type": "invalid"})
    assert invalid.status_code == 422


def test_memory_inbox_diff_shows_exact_entry_text(tmp_path):
    from html import unescape

    from powercontext.builtin.artifacts.memory.models import MemoryDreamCandidateProposal, MemoryDreamEntryChange
    from powercontext.builtin.runtime import BuiltinConfig
    from powercontext.server.dashboard.routes import router
    from tests.e2e.dream_support import open_dream_runtime
    from tests.e2e.test_artifact_dreaming import Generator, MemoryPipeline
    from tests.e2e.test_artifact_dreaming import seed as seed_memory

    async def scenario():
        async with open_dream_runtime(
            BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path}/inbox.db")),
            candidate_pipeline=MemoryPipeline(),
            dream_generator=Generator(),
        ) as runtime:
            scope, source, citation = await seed_memory(runtime)
            candidate = await runtime._review(scope).propose_memory_dream(
                MemoryDreamCandidateProposal(
                    base=citation.memory_ref,
                    dream_run_id="diff-test",
                    changes=(
                        MemoryDreamEntryChange(
                            entry_id=citation.entry_id,
                            entry_version_id=citation.entry_version_id,
                            kind="task_record",
                            text="The new evidence corrected the replay result.",
                            reason="A later exact observation",
                            sources=(source,),
                        ),
                    ),
                ),
                sources=(source,),
                artifacts=(citation.memory_ref,),
                memory_citations=(citation,),
                target=citation.memory_ref,
                reason="Correct the selected entry",
                candidate_id="memory-diff-candidate",
            )
            app = create_app(application=cast(ServerApplication, runtime))
            app.include_router(router, prefix="/dashboard")
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://testserver"
            ) as client:
                response = await client.get(
                    "/dashboard/review", params={"scope": scope, "candidate_id": candidate.candidate_id, "lang": "en"}
                )
                assert response.status_code == 200, response.text
                rendered = unescape(response.text)
                assert '-    "text": "The replay test passed without duplicate writes."' in rendered
                assert '+    "text": "The new evidence corrected the replay result."' in rendered
                assert "UNSELECTED_SIBLING_SENTINEL" not in rendered

    asyncio.run(scenario())


def test_legacy_prompt_publication_preserves_business_json_that_resembles_protocol(tmp_path):
    from powercontext.builtin.runtime import InferenceConfig
    from powercontext.server.factory import create_server_app
    from powercontext.server.settings import McpConfig, ServerSettings

    app = create_server_app(
        settings=ServerSettings(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'prompt-compat.db'}"),
            inference=InferenceConfig(generation_model="test"),
            mcp=McpConfig(enabled=False),
        ),
        scheduler_path=tmp_path / "prompt-compat-scheduler.db",
    )

    async def scenario():
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http,
        ):
            scope_response = await http.post(
                "/v1/scopes",
                json={"title": "Prompt compatibility", "summary": "Opaque evidence JSON", "idempotency_key": "compat"},
            )
            scope = scope_response.json()["scope_id"]
            content = {
                "schema_version": "powercontext.prompt.v1",
                "mode": "auto",
                "instructions": "",
                "demonstrations": [],
            }
            created = await http.post(
                f"/v1/scopes/{scope}/artifacts",
                json={"family": "prompt", "prompt_key": "memory.extract", "content": content},
            )
            assert created.status_code == 201
            path = f"/v1/scopes/{scope}/artifacts/prompt/memory.extract"
            current = await http.get(path)
            evidence = {
                "run_id": "business-operation",
                "operation": "unrelated-business-operation",
                "accepted_at": "2026-01-01",
                "audit": {"source": "business"},
                "tag_target": "business-field",
                "reused": True,
            }
            content.update(
                mode="custom",
                instructions="Extract only supported facts.",
                demonstrations=[
                    {
                        "input": {
                            "evidence": [{"evidence_id": "source-1", "evidence_type": "source", "content": evidence}],
                            "current_entries": [],
                        },
                        "expected_output": {"candidates": []},
                    }
                ],
            )
            published = await http.put(path, headers={"If-Match": current.headers["ETag"]}, json={"content": content})
            assert published.status_code == 200, published.text
            assert published.json()["revision"] == 2
            assert published.json()["content"] == content
            assert (await http.get(path)).json() == published.json()

    asyncio.run(scenario())


@pytest.mark.parametrize("pause_at", ["admission", "publication"])
@pytest.mark.parametrize("winner", ["approve", "reject", "advance"])
def test_overlapping_approval_across_servers(  # noqa: C901 - two scheduling boundaries and three terminal outcomes
    tmp_path, monkeypatch, pause_at, winner
):
    """Independent servers return the committed decision after an overlapping request."""
    from contextvars import ContextVar

    from powercontext.builtin.persistence.artifacts import ArtifactRepository
    from powercontext.builtin.review.service import ReviewService
    from powercontext.builtin.runtime import (
        ApproveCandidateRequest,
        BuiltinConfig,
        CaptureSource,
        ProposeExperienceRequest,
        open_builtin_runtime,
    )
    from powercontext.builtin.scope import ScopeDraft
    from tests.e2e.test_artifact_dreaming import experience

    delayed = ContextVar("delayed_approval", default=False)

    async def scenario():  # noqa: C901 - keep both real HTTP instances and the scheduling barrier together
        config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'replay.db'}"))
        async with open_builtin_runtime(config) as first, open_builtin_runtime(config) as second:
            assert first.scopes is not None
            scope = (
                await first.scopes.create(ScopeDraft(title="Replay", summary="Review", idempotency_key="replay"))
            ).scope_id
            source = await first.sources.for_scope(scope).capture(
                CaptureSource(source_id="task", content="The task verified the result.", metadata={})
            )
            original = await first.experience.for_scope(scope).propose(
                ProposeExperienceRequest(proposal=experience(), sources=(source.source_ref,))
            )
            original = await first.review.for_scope(scope).approve(
                ApproveCandidateRequest(candidate_id=original.candidate_id, expected_version=original.version)
            )
            target = original.result_artifact
            assert target is not None
            candidate = await first.experience.for_scope(scope).propose(
                ProposeExperienceRequest(
                    proposal=experience().model_copy(update={"lesson": "Verify the stored result before retrying."}),
                    sources=(source.source_ref,),
                    artifacts=(target,),
                    target=target,
                )
            )
            competing = None
            if winner == "advance":
                competing = await first.experience.for_scope(scope).propose(
                    ProposeExperienceRequest(
                        proposal=experience().model_copy(update={"lesson": "A separate correction."}),
                        sources=(source.source_ref,),
                        artifacts=(target,),
                        target=target,
                    )
                )
            entered, release = asyncio.Event(), asyncio.Event()
            paused = False

            async def barrier():
                nonlocal paused
                if delayed.get() and not paused:
                    paused = True
                    entered.set()
                    await asyncio.wait_for(release.wait(), 10)

            authorize = ReviewService._authorize_decision
            latest = ArtifactRepository.latest

            async def controlled_authorize(self, action, value):
                await authorize(self, action, value)
                if pause_at == "admission" and action == "approve":
                    await barrier()

            async def controlled_latest(self, *args, **kwargs):
                if pause_at == "publication" and kwargs.get("for_update"):
                    await barrier()
                return await latest(self, *args, **kwargs)

            monkeypatch.setattr(ReviewService, "_authorize_decision", controlled_authorize)
            monkeypatch.setattr(ArtifactRepository, "latest", controlled_latest)
            payload = {"scope_id": scope, "candidate_id": candidate.candidate_id, "expected_version": candidate.version}
            async with (
                httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=create_app(application=cast(ServerApplication, first))),
                    base_url="http://first",
                ) as a,
                httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=create_app(application=cast(ServerApplication, second))),
                    base_url="http://second",
                ) as b,
            ):

                async def delayed_request():
                    token = delayed.set(True)
                    try:
                        return await b.post("/v1/candidates/approve", json=payload)
                    finally:
                        delayed.reset(token)

                pending = asyncio.create_task(delayed_request())
                try:
                    await asyncio.wait_for(entered.wait(), 10)
                    winning_payload = payload
                    if competing is not None:
                        winning_payload = {**payload, "candidate_id": competing.candidate_id}
                    elif winner == "reject":
                        winning_payload = {**payload, "reason": "Not suitable."}
                    result = await a.post(
                        "/v1/candidates/" + ("reject" if winner == "reject" else "approve"),
                        json=winning_payload,
                    )
                    assert result.status_code == 200, result.text
                finally:
                    release.set()
                replay = await asyncio.wait_for(pending, 10)
                if winner == "approve":
                    assert replay.status_code == 200, replay.text
                    assert replay.json() == result.json()
                    assert replay.json()["result_artifact"]["revision"] == target.revision + 1
                else:
                    assert replay.status_code == 409, replay.text
                wrong_version = await b.post(
                    "/v1/candidates/approve", json={**payload, "expected_version": candidate.version + 1}
                )
                assert wrong_version.status_code == 409, wrong_version.text

    asyncio.run(scenario())


def test_tag_dream_reference_roundtrips_through_public_contract():
    from datetime import UTC, datetime

    from powercontext.builtin.dream.models import DreamCandidateRef, DreamRun
    from powercontext.http import DreamRun as HttpDreamRun

    run = DreamRun(
        scope_id="scope",
        run_id="run",
        operation="revise_tags",
        status="succeeded",
        candidate=DreamCandidateRef(kind="tag", candidate_id="candidate", version=1),
        accepted_at=datetime.now(UTC),
    )
    response = HttpDreamRun.model_validate_json(run.model_dump_json())
    assert response.model_dump(mode="json")["candidate"] == {
        "kind": "tag",
        "candidate_id": "candidate",
        "version": 1,
    }
