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
from types import SimpleNamespace
from typing import cast

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict

from powercontext.builtin.catalog_changes.application import CatalogChangeApplication
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.client import PowerContextClient
from powercontext.http import (
    ApproveCatalogCandidateRequest,
    GetCatalogCandidateRequest,
    ListCatalogCandidatesRequest,
    ReviseCatalogCandidateRequest,
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
            {"operation": "refine_experience", "output_kind": "artifact_candidate", "effect": "review_then_publish"},
            {
                "operation": "revise_tags",
                "output_kind": "catalog_change_candidate",
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
            application = SimpleNamespace(catalog_changes=CatalogChangeApplication(contexts.catalog_changes))
            app = create_app(application=cast(ServerApplication, application))
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as http:
                legacy = await http.post("/v1/catalog-change-candidates/list", json={"scope_id": scope})
                assert legacy.status_code == 426
                assert legacy.json()["error"]["code"] == "client_upgrade_required"
                async with PowerContextClient(
                    "http://testserver", http_client=http, trust_transport_security=True
                ) as client:
                    listed = await client.list_catalog_candidates(ListCatalogCandidatesRequest(scope_id=scope))
                    assert [item.candidate_id for item in listed.candidates] == [candidate.candidate_id]
                    revised = await client.revise_catalog_candidate(
                        ReviseCatalogCandidateRequest.model_validate({
                            "scope_id": scope,
                            "candidate_id": candidate.candidate_id,
                            "expected_version": 1,
                            "after_tags": ["confirmed", "delivery"],
                            "reason": "Keep the category too",
                        })
                    )
                    assert revised.version == 2
                    stale = await http.post(
                        "/v1/catalog-change-candidates/approve",
                        headers={"X-PowerContext-Dream-Contract": "2"},
                        json={"scope_id": scope, "candidate_id": candidate.candidate_id, "expected_version": 1},
                    )
                    assert stale.status_code == 409
                    approved = await client.approve_catalog_candidate(
                        ApproveCatalogCandidateRequest(
                            scope_id=scope, candidate_id=candidate.candidate_id, expected_version=2
                        )
                    )
                    assert approved.status.value == "approved" and approved.result is not None
                    assert approved.result.etag.startswith('"tags:')
                    assert "result_artifact" not in approved.model_dump()
                    history = await client.get_catalog_candidate_history(
                        GetCatalogCandidateRequest(scope_id=scope, candidate_id=candidate.candidate_id)
                    )
                    assert [(item.version, item.status.value) for item in history.versions] == [
                        (1, "pending"),
                        (2, "approved"),
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
    result = dashboard.post(
        "/v1/artifact-candidates/get", json={"scope_id": scope, "candidate_id": candidate["candidate_id"]}
    )
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
