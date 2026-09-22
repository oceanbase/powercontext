"""Enforced Memory Dream ownership and exact-version review through HTTP and SDK."""

import asyncio
from typing import cast

import httpx
import pytest
from starlette.middleware import Middleware

from powercontext.artifacts import MemoryCitation
from powercontext.builtin.artifacts.memory.models import MemoryDreamEntryChange, MemoryDreamWrite
from powercontext.builtin.dream.models import DreamPlan
from powercontext.builtin.inference.models import GenerationResult, InferenceUsage
from powercontext.builtin.runtime import ApproveArtifactCandidateRequest, BuiltinConfig
from powercontext.server.app import ServerApplication, create_app
from powercontext.server.authentication import StaticBearerAuthenticationProvider
from powercontext.server.authz import AccessRole, MemoryEntrySelector, PrincipalRef, ResourceRef
from powercontext.server.authz.composition import open_builtin_access_control
from powercontext.server.authz.errors import AccessDeniedError
from powercontext.server.authz.service import AccessAuditContext, CreateBinding
from powercontext.server.context import bind_principal, reset_principal
from powercontext.server.dream_access import DreamAccess
from powercontext.server.middleware import AuthenticationMiddleware
from powercontext.sources import SourceRef
from tests.e2e.dream_support import open_dream_runtime, process_pending
from tests.e2e.test_artifact_dreaming import MemoryPipeline, seed
from tests.e2e.test_artifact_dreaming import database as database


class Correction:
    config_id = "memory-access-regression"
    citation: MemoryCitation | None = None
    source: SourceRef | None = None

    async def generate(self, value):
        assert self.citation is not None and self.source is not None
        return GenerationResult(
            output=DreamPlan(
                outcome="proposed",
                reason="Exact observed correction",
                intent="correct",
                proposal=MemoryDreamWrite(
                    changes=(
                        MemoryDreamEntryChange(
                            entry_id=self.citation.entry_id,
                            entry_version_id=self.citation.entry_version_id,
                            kind="task_record",
                            text="The verified correction is now explicit.",
                            reason="New exact observation",
                            sources=(self.source,),
                        ),
                    )
                ),
                evidence_ids=tuple(
                    item.evidence_id for item in value.evidence.evidence if item.kind in {"memory", "source"}
                ),
            ),
            usage=InferenceUsage(requests=1),
        )


def test_memory_dream_enforced_attestation_and_sdk_http_review(database):
    async def scenario():
        admin, author = PrincipalRef(type="service", id="admin"), PrincipalRef(type="user", id="memory-author")
        context = AccessAuditContext(transport="http", operation="test_memory_dream_access")
        async with open_builtin_access_control(database, bootstrap_administrators=(admin,)) as access:
            adapter, generator = DreamAccess(access), Correction()
            async with open_dream_runtime(
                BuiltinConfig(database=database),
                candidate_pipeline=MemoryPipeline(),
                dream_generator=generator,
                dream_authorizer=adapter.authorize,
                dream_authorization_context=access.defer_decision_audit,
                dream_candidate_attester=adapter.attest_candidate,
            ) as runtime:
                scope, source, citation = await seed(runtime)
                generator.citation, generator.source = citation, source
                entries = await runtime.memory.for_scope(scope).list()
                for entry in entries.entries:
                    await access.establish_artifact_owner(
                        ResourceRef.artifact(
                            scope,
                            family="memory",
                            artifact_id=entry.citation.memory_ref.artifact_id,
                            selector=MemoryEntrySelector(entry_id=entry.citation.entry_id),
                        ),
                        author,
                        idempotency_key="owner:" + entry.citation.entry_id,
                        context=context,
                    )
                for role in (AccessRole.SCOPE_CONTRIBUTOR, AccessRole.SCOPE_REVIEWER):
                    binding = await access.create_binding(
                        admin,
                        CreateBinding(
                            subject=author, resource=ResourceRef.scope(scope), role=role, idempotency_key=str(role)
                        ),
                        context=context,
                    )
                review_binding = binding
                provider = StaticBearerAuthenticationProvider("token", author)
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
                    response = await client.post(
                        f"/v1/scopes/{scope}/dream",
                        json={
                            "operation": "revise_memory",
                            "target": citation.memory_ref.model_dump(),
                            "artifacts": [citation.memory_ref.model_dump()],
                            "memory_citations": [citation.model_dump()],
                            "sources": [source.model_dump()],
                            "idempotency_key": "enforced-memory",
                        },
                    )
                    assert response.status_code == 202, response.text
                    await process_pending(runtime)
                    completed = await client.get(f"/v1/scopes/{scope}/dream/{response.json()['run_id']}")
                    assert completed.status_code == 200, completed.text
                    run = completed.json()
                    assert run["status"] == "succeeded", run
                    candidate = run["candidate"]
                    attestation = await access.candidate_owner(scope, candidate["candidate_id"])
                    assert attestation is not None and attestation.target == ResourceRef.artifact(
                        scope, family="memory", artifact_id=citation.memory_ref.artifact_id
                    )
                    await access.revoke_binding(
                        admin,
                        review_binding.binding_id,
                        expected_version=review_binding.version,
                        idempotency_key="revoke-review",
                        context=context,
                    )
                    principal_token = bind_principal(author)
                    try:
                        with pytest.raises(AccessDeniedError):
                            await runtime.review.for_scope(scope).approve(
                                ApproveArtifactCandidateRequest(
                                    candidate_id=candidate["candidate_id"], expected_version=candidate["version"]
                                )
                            )
                    finally:
                        reset_principal(principal_token)
                    await access.create_binding(
                        admin,
                        CreateBinding(
                            subject=author,
                            resource=ResourceRef.scope(scope),
                            role=AccessRole.SCOPE_REVIEWER,
                            idempotency_key="restore-review",
                        ),
                        context=context,
                    )
                    approved = await client.post(
                        "/v1/artifact-candidates/approve",
                        json={
                            "scope_id": scope,
                            "candidate_id": candidate["candidate_id"],
                            "expected_version": candidate["version"],
                        },
                    )
                    assert approved.status_code == 200, approved.text
                    assert approved.json()["status"] == "approved"
                    reread = await client.post(
                        "/v1/artifact-candidates/get",
                        json={"scope_id": scope, "candidate_id": candidate["candidate_id"]},
                    )
                    assert reread.status_code == 200, reread.text
                    assert reread.json()["result_artifact"]["family"] == "memory"
                    current = await client.post("/v1/memory/entries/list", json={"scope_id": scope})
                    assert current.status_code == 200, current.text
                    assert any(
                        entry["text"] == "The verified correction is now explicit."
                        for entry in current.json()["entries"]
                    )

    asyncio.run(scenario())
