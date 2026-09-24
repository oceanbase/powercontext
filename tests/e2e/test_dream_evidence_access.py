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

"""Dream evidence preserves Scope authorization and exact supporting Artifacts."""

import asyncio
from contextlib import nullcontext
from dataclasses import replace
from datetime import UTC, datetime
from typing import cast

import httpx
import pytest
from starlette.middleware import Middleware

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.handoff.models import HandoffContent, HandoffSourceCitation, HandoffStatement
from powercontext.builtin.artifacts.profile.models import ProfileContent, ProfileGeneration
from powercontext.builtin.artifacts.skill import SkillContent
from powercontext.builtin.artifacts.topic_memory import TopicMemoryContent
from powercontext.builtin.catalog_changes.models import TagChangeProposal, TagDreamTarget
from powercontext.builtin.dream import provenance
from powercontext.builtin.dream.models import CreateDreamRunRequest, DreamPlan, GetDreamRunRequest
from powercontext.builtin.evidence.resolver import evidence_id
from powercontext.builtin.inference.models import GenerationResult, InferenceUsage
from powercontext.builtin.persistence.artifacts import RepositoryArtifactDraft
from powercontext.builtin.records import ArtifactWrite
from powercontext.builtin.review.errors import InvalidCandidateError
from powercontext.builtin.runtime import BuiltinConfig, CaptureSource
from powercontext.builtin.scope import ScopeDraft
from powercontext.builtin.tags import ArtifactTagTarget
from powercontext.server.app import ServerApplication, create_app
from powercontext.server.authentication import StaticBearerAuthenticationProvider
from powercontext.server.authz import AccessRole, PrincipalRef, ResourceRef
from powercontext.server.authz.composition import open_builtin_access_control
from powercontext.server.authz.service import AccessAuditContext, CreateBinding
from powercontext.server.middleware import AuthenticationMiddleware
from tests.e2e.dream_support import open_dream_runtime, process_pending
from tests.e2e.test_artifact_dreaming import database as database


class TopicGenerator:
    config_id = "topic-access-test"

    async def generate(self, value):
        return GenerationResult(
            output=DreamPlan(
                outcome="proposed",
                intent="correct",
                reason="Verified delivery evidence",
                proposal=TagChangeProposal(after_tags=("verified",))
                if value.operation == "revise_tags"
                else TopicMemoryContent(title="Delivery", summary="Confirmed", detail="Verified wrong address."),
                evidence_ids=tuple(item.evidence_id for item in value.evidence.evidence if item.kind == "source"),
            ),
            usage=InferenceUsage(requests=1),
        )


@pytest.mark.parametrize("operation", ["revise_topic_memory", "revise_tags"])
def test_topic_dream_enforced_http_admission_and_review(database, operation):
    async def scenario():
        admin = PrincipalRef(type="service", id="admin")
        reviewer = PrincipalRef(type="user", id="topic-reviewer")
        audit = AccessAuditContext(transport="http", operation="test_topic_dream")
        async with (
            open_builtin_access_control(database, bootstrap_administrators=(admin,)) as access,
            open_dream_runtime(BuiltinConfig(database=database), dream_generator=TopicGenerator()) as runtime,
        ):
            scope = (
                await runtime.scopes.create(
                    ScopeDraft(title="Topic access", summary="Scope-owned topic", idempotency_key="topic")
                )
            ).scope_id
            admin_binding = None
            for role in (AccessRole.SCOPE_ADMIN, AccessRole.SCOPE_CONTRIBUTOR, AccessRole.SCOPE_REVIEWER):
                binding = await access.create_binding(
                    admin,
                    CreateBinding(
                        subject=reviewer, resource=ResourceRef.scope(scope), role=role, idempotency_key=str(role)
                    ),
                    context=audit,
                )
                if role == AccessRole.SCOPE_ADMIN:
                    admin_binding = binding
            assert admin_binding is not None
            source = await runtime.sources.for_scope(scope).capture(
                CaptureSource(source_id="proof", content="Delivery to the wrong address was confirmed.", metadata={})
            )
            provider = StaticBearerAuthenticationProvider("token", reviewer)
            app = create_app(
                application=cast(ServerApplication, runtime),
                access_control=access,
                access_mode="enforced",
                authentication_provider=provider,
                middleware=[Middleware(AuthenticationMiddleware, provider=provider)],
            )
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://testserver",
                headers={"Authorization": "Bearer token", "X-PowerContext-Dream-Contract": "2"},
            ) as client:
                created = await client.post(
                    f"/v1/scopes/{scope}/artifacts",
                    json={
                        "family": "topic-memory",
                        "content": {"title": "Delivery", "summary": "Unknown", "detail": "Needs verification."},
                    },
                )
                assert created.status_code == 201, created.text
                body = created.json()
                ref = {key: body[key] for key in ("family", "artifact_id", "revision")}
                path = f"/v1/scopes/{scope}/artifacts/topic-memory/{ref['artifact_id']}"
                assert (await client.get(path)).status_code == 200
                tags = await client.get(path + "/tags")
                assert tags.status_code == 200, tags.text
                request = {
                    "operation": operation,
                    "sources": [source.source_ref.model_dump()],
                    "idempotency_key": operation,
                }
                if operation == "revise_tags":
                    request["tag_target"] = {
                        "target": {"type": "artifact", "family": "topic-memory", "artifact_id": ref["artifact_id"]},
                        "expected_etag": tags.headers["ETag"],
                        "basis_ref": ref,
                    }
                else:
                    request.update(target=ref, artifacts=[ref])
                accepted = await client.post(f"/v1/scopes/{scope}/dream", json=request)
                assert accepted.status_code == 202, accepted.text
                await process_pending(runtime)
                completed = await client.get(f"/v1/scopes/{scope}/dream/{accepted.json()['run_id']}")
                run = completed.json()
                assert run["outcome"] == "proposed", (run["status"], run.get("error"), run.get("reason"))
                candidate = run["candidate"]
                resource = "candidates"
                identity = {"scope_id": scope, "candidate_id": candidate["candidate_id"]}
                read = await client.post(f"/v1/{resource}/get", json=identity)
                assert read.status_code == 200, read.text
                await access.revoke_binding(
                    admin,
                    admin_binding.binding_id,
                    expected_version=admin_binding.version,
                    idempotency_key="revoke",
                    context=audit,
                )
                decision = {**identity, "expected_version": candidate["version"]}
                denied = await client.post(f"/v1/{resource}/approve", json=decision)
                assert denied.status_code == 403, denied.text
                assert (await client.post(f"/v1/{resource}/get", json=identity)).json()["status"] == "pending"
                await access.create_binding(
                    admin,
                    CreateBinding(
                        subject=reviewer,
                        resource=ResourceRef.scope(scope),
                        role=AccessRole.SCOPE_ADMIN,
                        idempotency_key="restore",
                    ),
                    context=audit,
                )
                approved = await client.post(f"/v1/{resource}/approve", json=decision)
                assert approved.status_code == 200, approved.text
                assert approved.json()["status"] == "approved"
                latest = (await client.get(path)).json()
                assert latest["revision"] == ref["revision"] + (operation == "revise_topic_memory")
                if operation == "revise_tags":
                    assert (await client.get(path + "/tags")).json()["tags"] == ["verified"]
                else:
                    assert latest["content"]["detail"] == "Verified wrong address."

    asyncio.run(scenario())


@pytest.mark.parametrize("family", ["skill", "profile", "topic-memory", "handoff"])
@pytest.mark.parametrize("legacy_contract", [False, True])
def test_tag_dream_preserves_supporting_artifact_and_rechecks_access(database, family, monkeypatch, legacy_contract):
    class Generator:
        config_id = "tag-support-test"
        support: ArtifactRef | None = None

        async def generate(self, value):
            assert self.support is not None
            return GenerationResult(
                output=DreamPlan(
                    outcome="proposed",
                    reason="Classify using the supporting Artifact",
                    intent="correct",
                    proposal=TagChangeProposal(after_tags=("verified",)),
                    evidence_ids=(evidence_id(self.support),),
                ),
                usage=InferenceUsage(requests=1),
            )

    async def scenario():
        generator = Generator()
        async with open_dream_runtime(BuiltinConfig(database=database), dream_generator=generator) as runtime:
            scope = (
                await runtime.scopes.create(
                    ScopeDraft(title="Support", summary="Exact evidence", idempotency_key="support")
                )
            ).scope_id
            source = await runtime.sources.for_scope(scope).capture(
                CaptureSource(source_id="proof", content="Verified delivery error", metadata={})
            )
            contents = {
                "skill": SkillContent(
                    name="verify-delivery",
                    description="Verify address",
                    instructions="Check proof.",
                    validation=("Verify receipt",),
                ),
                "profile": ProfileContent(
                    content="# Profile\nConfirmed recipient.",
                    generation=ProfileGeneration(mode="manual_create", created_at=datetime.now(UTC)),
                ),
                "topic-memory": TopicMemoryContent(
                    title="Delivery", summary="Verified", detail="Wrong address confirmed."
                ),
                "handoff": HandoffContent(
                    objective="Resolve delivery",
                    state=(
                        HandoffStatement(
                            text="Address verified.", citations=(HandoffSourceCitation(source_ref=source.source_ref),)
                        ),
                    ),
                    disposition="complete",
                ),
            }
            async with runtime._provider.database.transaction() as connection:
                support = await runtime._provider.repositories.artifacts.create(
                    connection, scope, "support", RepositoryArtifactDraft(family=family, content=contents[family])
                )
            generator.support = support.as_ref()
            records = runtime.records.for_scope(scope)
            target = await records.create_artifact(
                "experience",
                ArtifactWrite(
                    content={
                        "situation": "Delivery",
                        "action": "Verify",
                        "outcome": "Confirmed",
                        "lesson": "Use evidence",
                    }
                ),
            )
            ref = ArtifactRef(family="experience", artifact_id=target.artifact_id, revision=target.revision)
            tag_target = ArtifactTagTarget(family="experience", artifact_id=target.artifact_id)
            before = await records.get_tags(tag_target)
            request = CreateDreamRunRequest(
                operation="revise_tags",
                artifacts=(support.as_ref(),),
                tag_target=TagDreamTarget(target=tag_target, expected_etag=before.etag, basis_ref=ref),
                idempotency_key="support",
            )
            run = await runtime.dream.for_scope(scope).create(request)
            with monkeypatch.context() as legacy:
                if legacy_contract:
                    legacy.setattr(
                        provenance,
                        "DREAM_OPERATIONS",
                        tuple(
                            replace(spec, spec_version="powercontext.dream.operation.v1")
                            if spec.operation == "revise_tags"
                            else spec
                            for spec in provenance.DREAM_OPERATIONS
                        ),
                    )
                await process_pending(runtime)
            run = await runtime.dream.for_scope(scope).get(GetDreamRunRequest(run_id=run.run_id))
            assert run.candidate is not None, run
            service = runtime.catalog_changes.for_scope(scope)
            candidate = await service.get(run.candidate.candidate_id)
            if legacy_contract:
                with pytest.raises(InvalidCandidateError, match="operation contract changed"):
                    await service.approve(candidate.candidate_id, candidate.version)
                assert (await records.get_tags(tag_target)).etag == before.etag
                old_id = candidate.candidate_id
                await service.reject(old_id, candidate.version, "Regenerate under the complete evidence contract")
                run = await runtime.dream.for_scope(scope).create(
                    request.model_copy(update={"idempotency_key": "regenerated"})
                )
                await process_pending(runtime)
                run = await runtime.dream.for_scope(scope).get(GetDreamRunRequest(run_id=run.run_id))
                assert run.candidate is not None and not run.reused
                candidate = await service.get(run.candidate.candidate_id)
                assert candidate.candidate_id != old_id
            assert support.as_ref() in candidate.artifacts
            assert support.as_ref() in (await service.history(candidate.candidate_id))[-1].artifacts

            async def revoked(reference):
                if reference == support.as_ref():
                    raise PermissionError("Supporting Artifact access revoked")  # noqa: TRY003

            service.configure_authorization(revoked, nullcontext)
            with pytest.raises(PermissionError, match="access revoked"):
                await service.approve(candidate.candidate_id, candidate.version)
            assert (await service.get(candidate.candidate_id)).status == "pending"
            assert (await records.get_tags(tag_target)).etag == before.etag

            async def restored(reference):
                return None

            service.configure_authorization(restored, nullcontext)
            approved = await service.approve(candidate.candidate_id, candidate.version)
            assert approved.result is not None and approved.result.tags == ("verified",)
            assert (await records.get_artifact("experience", target.artifact_id)).revision == target.revision

    asyncio.run(scenario())
