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

"""Related recall preserves identities across query embedding and fallback paths."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime

import httpx
import pytest

from powercontext.artifacts.search import ArtifactSearchExecutionContext
from powercontext.builtin.artifacts.atomic_memory.extraction import (
    AtomicMemoryCandidate,
    AtomicMemoryExtractionOutput,
    AtomicMemoryGenerationPipeline,
)
from powercontext.builtin.artifacts.atomic_memory.reconciliation import (
    AtomicMemoryReconciliationContent,
    AtomicMemoryReconciliationInput,
    AtomicMemoryReconciliationOutput,
)
from powercontext.builtin.artifacts.memory import EmbeddingProfile
from powercontext.builtin.inference import (
    EmbeddingResult,
    GenerationResult,
    InferenceTimeoutError,
    InferenceUnavailableError,
    character_token_estimator,
)
from powercontext.builtin.inference.minimax import MiniMaxEmbeddingModel
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import BuiltinConfig, RuntimeConfig, open_builtin_contexts
from powercontext.server.authz import (
    AccessAction,
    AccessAuditContext,
    AccessBinding,
    AccessBindingState,
    AccessControlService,
    AccessDecision,
    AccessRole,
    AccessUnavailableError,
    BuiltinAuthorizationProvider,
    PrincipalRef,
    ResourceRef,
)
from powercontext.server.authz.repository import RelationalAccessRepository
from tests.e2e.dream_support import memory_source_text

PROFILE = EmbeddingProfile(
    profile_id="related-query-test",
    model="embo-01",
    dimension=3,
    distance="l2",
    normalization="unit",
)
ORIGINAL = "Prefer the database deployment plan."
EQUIVALENT = "Choose the existing rollout strategy."


class _Extractor:
    async def generate(self, request):
        return GenerationResult(
            output=AtomicMemoryExtractionOutput(
                candidates=tuple(
                    AtomicMemoryCandidate(kind="fact", text=text, evidence_ids=(evidence.evidence_id,))
                    for evidence in request.evidence
                    if (text := memory_source_text(evidence)) is not None
                )
            )
        )


class _Reconciler:
    def __init__(self, *, revise=False):
        self.revise = revise

    async def generate(self, request):
        compared = tuple(item.item_id for item in request.related)
        if request.related and not self.revise:
            output = AtomicMemoryReconciliationOutput(
                action="noop",
                compared_ids=compared,
                target_ids=(request.related[0].item_id,),
                reason="The recalled identity already expresses this preference.",
            )
        else:
            output = AtomicMemoryReconciliationOutput(
                action="revise" if request.related else "create",
                compared_ids=compared,
                target_ids=(request.related[0].item_id,) if request.related else (),
                content=AtomicMemoryReconciliationContent(kind="fact", text=request.proposal.text),
                evidence_ids=request.proposal.evidence_ids,
                reason="Retain the preference and its supplied Source evidence.",
            )
        return GenerationResult(output=output)


def _pipeline(*, revise=False):
    return AtomicMemoryGenerationPipeline(
        extractor=_Extractor(), reconciler=_Reconciler(revise=revise), estimator=character_token_estimator()
    )


def _config(tmp_path, *, fallback=False):
    return BuiltinConfig(
        database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'related.db'}"),
        runtime=RuntimeConfig(
            atomic_memory_related_mode="vector",
            atomic_memory_related_max_distance=0.1,
            atomic_memory_related_fts_fallback=fallback,
        ),
    )


def _minimax(client):
    return MiniMaxEmbeddingModel(
        base_url="https://api.minimaxi.com/v1", model="embo-01", profile=PROFILE, http_client=client
    )


class _SymmetricEmbedding:
    profile = PROFILE

    async def embed(self, texts, /):
        return EmbeddingResult(vectors=tuple((1.0, 0.0, 0.0) for _ in texts))


class _ConfiguredArtifactProvider:
    def __init__(self, repository):
        self.builtin = BuiltinAuthorizationProvider(repository)
        self.denied = None
        self.unavailable = None

    async def check(self, request, /):
        requirement = (request.action, request.resource)
        if requirement == self.unavailable:
            raise AccessUnavailableError("configured_artifact_authority_unavailable")
        decision = await self.builtin.check(request)
        if requirement == self.denied:
            return AccessDecision(False, "configured-artifact-denial", decision.policy_revision)
        return decision

    async def check_batch(self, requests, /):
        return tuple([await self.check(request) for request in requests])

    async def resolve_resource_filter(self, request, /):
        return await self.builtin.resolve_resource_filter(request)


class _RecordingCreateReconciler:
    def __init__(self):
        self.inputs: list[AtomicMemoryReconciliationInput] = []

    async def generate(self, request):
        self.inputs.append(request)
        return GenerationResult(
            output=AtomicMemoryReconciliationOutput(
                action="create",
                compared_ids=tuple(item.item_id for item in request.related),
                content=AtomicMemoryReconciliationContent(kind="fact", text=request.proposal.text),
                evidence_ids=request.proposal.evidence_ids,
                reason="Retain the independent Source fact after comparing the permitted owned memories.",
            )
        )


async def _configured_flush_context(contexts, principal, binding_id="related-contributor"):
    repository = RelationalAccessRepository(contexts.database)
    await repository.create_binding(
        AccessBinding(
            binding_id=binding_id,
            subject=principal,
            resource=ResourceRef.scope("project"),
            role=AccessRole.SCOPE_CONTRIBUTOR,
            granted_by=principal,
            reason="Fixture Scope contribution authority",
            created_at=datetime.now(UTC),
            expires_at=None,
            state=AccessBindingState.ACTIVE,
            version=1,
            policy_revision="pending",
            idempotency_key=binding_id,
        )
    )
    provider = _ConfiguredArtifactProvider(repository)
    access = AccessControlService(provider, relationships=repository, audit=repository)
    return (
        ArtifactSearchExecutionContext(
            principal=principal,
            access=access,
            audit=AccessAuditContext(transport="background", operation="related-source-flush"),
        ),
        provider,
    )


@pytest.mark.parametrize("denied_action", [AccessAction.ARTIFACT_READ, AccessAction.ARTIFACT_WRITE])
def test_source_flush_compares_all_permitted_owned_matches_with_a_custom_provider(tmp_path, denied_action):
    reconciler = _RecordingCreateReconciler()
    pipeline = AtomicMemoryGenerationPipeline(
        extractor=_Extractor(), reconciler=reconciler, estimator=character_token_estimator()
    )
    configured = _config(tmp_path)
    configured = configured.model_copy(
        update={"runtime": configured.runtime.model_copy(update={"atomic_memory_comparison_batch_size": 5})}
    )
    owner = PrincipalRef(type="user", id="related-owner")
    other = PrincipalRef(type="user", id="other-owner")

    async def scenario():
        async with open_builtin_contexts(
            configured, embedding_model=_SymmetricEmbedding(), candidate_pipeline=pipeline
        ) as contexts:
            context = await contexts.get("project")
            memory = contexts.atomic_memory.for_scope("project")
            execution, provider = await _configured_flush_context(contexts, owner)
            foreign, _ = await _configured_flush_context(contexts, other, "related-other-contributor")
            # Exceed both a comparison batch and ordinary search's default topK.
            owned = await contexts.records.create_atomic_memories(
                "project",
                tuple({"kind": "fact", "text": f"Permitted owned rollout fact {number}."} for number in range(23)),
                execution_context=execution,
            )
            (denied,) = await contexts.records.create_atomic_memories(
                "project",
                ({"kind": "fact", "text": "DENIED_OWNED_MATCH_SENTINEL"},),
                execution_context=execution,
            )
            await contexts.records.create_atomic_memories(
                "project",
                ({"kind": "fact", "text": "FOREIGN_OWNER_MATCH_SENTINEL"},),
                execution_context=foreign,
            )
            await context.triggers.flush(limit=100)
            before = (await memory.list(limit=100)).items
            cursor = (await context.triggers.cursor()).sequence
            provider.denied = (
                denied_action,
                ResourceRef.artifact("project", family="atomic-memory", artifact_id=denied.artifact_id),
            )
            await contexts.records.create_source("project", "content", EQUIVALENT)

            result = await context.triggers.flush(limit=1, atomic_context=execution)

            assert (result.previous_cursor, result.current_cursor) == (cursor, cursor + 1)
            compared = {
                original.ref.artifact_id
                for request in reconciler.inputs
                for item in request.related
                for original in item.original_refs
            }
            assert compared == {item.artifact_id for item in owned}
            projected = "\n".join(request.model_dump_json() for request in reconciler.inputs)
            assert "DENIED_OWNED_MATCH_SENTINEL" not in projected
            assert "FOREIGN_OWNER_MATCH_SENTINEL" not in projected
            after = {item.ref.artifact_id: item for item in (await memory.list(limit=100)).items}
            for original in before:
                assert after.pop(original.ref.artifact_id) == original
            (created,) = after.values()
            assert created.artifact.content.text == EQUIVALENT
            ownership = await execution.access.artifact_owner(
                ResourceRef.artifact("project", family="atomic-memory", artifact_id=created.ref.artifact_id)
            )
            assert ownership is not None and ownership.owner == owner

    asyncio.run(scenario())


@pytest.mark.parametrize("unavailable_action", [AccessAction.ARTIFACT_READ, AccessAction.ARTIFACT_WRITE])
def test_source_flush_propagates_custom_artifact_authority_unavailable_without_advancing_cursor(
    tmp_path, unavailable_action
):
    reconciler = _RecordingCreateReconciler()
    pipeline = AtomicMemoryGenerationPipeline(
        extractor=_Extractor(), reconciler=reconciler, estimator=character_token_estimator()
    )
    owner = PrincipalRef(type="user", id="related-owner")

    async def scenario():
        async with open_builtin_contexts(
            _config(tmp_path), embedding_model=_SymmetricEmbedding(), candidate_pipeline=pipeline
        ) as contexts:
            context = await contexts.get("project")
            memory = contexts.atomic_memory.for_scope("project")
            execution, provider = await _configured_flush_context(contexts, owner)
            (original,) = await contexts.records.create_atomic_memories(
                "project",
                ({"kind": "fact", "text": ORIGINAL},),
                execution_context=execution,
            )
            await context.triggers.flush(limit=100)
            before = (await memory.list()).items
            cursor = (await context.triggers.cursor()).sequence
            provider.unavailable = (
                unavailable_action,
                ResourceRef.artifact("project", family="atomic-memory", artifact_id=original.artifact_id),
            )
            await contexts.records.create_source("project", "content", EQUIVALENT)

            with pytest.raises(AccessUnavailableError) as failure:
                await context.triggers.flush(limit=1, atomic_context=execution)

            assert failure.value.code == "configured_artifact_authority_unavailable"
            assert (await context.triggers.cursor()).sequence == cursor
            assert (await memory.list()).items == before
            assert not reconciler.inputs

            provider.unavailable = None
            assert (await context.triggers.flush(limit=1, atomic_context=execution)).current_cursor == cursor + 1
            assert {
                read.ref.artifact_id
                for request in reconciler.inputs
                for item in request.related
                for read in item.original_refs
            } == {original.artifact_id}

    asyncio.run(scenario())


def test_source_flush_uses_minimax_query_vectors_to_recall_the_prior_identity(tmp_path):
    requests = []

    def respond(request):
        body = json.loads(request.content)
        requests.append(body)
        vectors = [
            [1.0, 0.0, 0.0] if body["type"] == "query" or ORIGINAL in text else [0.0, 1.0, 0.0]
            for text in body["texts"]
        ]
        return httpx.Response(
            200, json={"vectors": vectors, "total_tokens": len(vectors), "base_resp": {"status_code": 0}}
        )

    async def scenario():
        async with (
            httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client,
            open_builtin_contexts(
                _config(tmp_path), embedding_model=_minimax(client), candidate_pipeline=_pipeline()
            ) as contexts,
        ):
            context = await contexts.get("project")
            memory = contexts.atomic_memory.for_scope("project")
            await contexts.records.create_source("project", "content", ORIGINAL)
            assert (await context.triggers.flush(limit=1)).current_cursor == 1
            original = (await memory.list()).items[0]

            await contexts.records.create_source("project", "content", EQUIVALENT)
            assert (await context.triggers.flush(limit=1)).current_cursor == 2
            assert (await memory.list()).items == (original,)
            assert [body["type"] for body in requests if body["texts"] == [EQUIVALENT]] == ["query"]
            assert any(body["type"] == "db" and ORIGINAL in body["texts"][0] for body in requests)

    asyncio.run(scenario())


def test_source_flush_recall_supports_symmetric_models_without_embed_query(tmp_path):
    async def scenario():
        async with open_builtin_contexts(
            _config(tmp_path), embedding_model=_SymmetricEmbedding(), candidate_pipeline=_pipeline()
        ) as contexts:
            context = await contexts.get("project")
            memory = contexts.atomic_memory.for_scope("project")
            await contexts.records.create_source("project", "content", ORIGINAL)
            await context.triggers.flush(limit=1)
            original = (await memory.list()).items[0]
            await contexts.records.create_source("project", "content", EQUIVALENT)
            assert (await context.triggers.flush(limit=1)).current_cursor == 2
            assert (await memory.list()).items == (original,)

    asyncio.run(scenario())


class _EmbeddingEndpoint:
    def __init__(self):
        self.query_failure = None
        self.document_failure = None
        self.document_requests = []

    def respond(self, request):
        body = json.loads(request.content)
        if body["type"] == "db":
            self.document_requests.append(body)
        failure = self.query_failure if body["type"] == "query" else self.document_failure
        if failure == "timeout":
            raise httpx.ReadTimeout("Embedding deadline expired", request=request)  # noqa: TRY003
        if failure == "unavailable":
            return httpx.Response(503, json={"error": "temporarily unavailable"})
        vectors = [[1.0, 0.0, 0.0] for _ in body["texts"]]
        return httpx.Response(
            200, json={"vectors": vectors, "total_tokens": len(vectors), "base_resp": {"status_code": 0}}
        )


@pytest.mark.parametrize("failure", ["unavailable", "timeout"])
@pytest.mark.parametrize("fallback", [False, True], ids=["fallback-disabled", "fallback-enabled"])
def test_related_query_failure_uses_fts_only_when_opted_in(tmp_path, failure, fallback):
    endpoint = _EmbeddingEndpoint()
    expected_error = InferenceTimeoutError if failure == "timeout" else InferenceUnavailableError

    async def scenario():
        async with (
            httpx.AsyncClient(transport=httpx.MockTransport(endpoint.respond)) as client,
            open_builtin_contexts(
                _config(tmp_path, fallback=fallback), embedding_model=_minimax(client), candidate_pipeline=_pipeline()
            ) as contexts,
        ):
            context = await contexts.get("project")
            memory = contexts.atomic_memory.for_scope("project")
            await contexts.records.create_source("project", "content", ORIGINAL)
            assert (await context.triggers.flush(limit=1)).current_cursor == 1
            original = (await memory.list()).items[0]
            documents = tuple(endpoint.document_requests)

            endpoint.query_failure = failure
            await contexts.records.create_source("project", "content", ORIGINAL)
            if fallback:
                result = await context.triggers.flush(limit=1)
                assert result.previous_cursor == 1 and result.current_cursor == 2
            else:
                with pytest.raises(expected_error):
                    await context.triggers.flush(limit=1)
                assert (await context.triggers.cursor()).sequence == 1
            assert (await memory.list()).items == (original,)
            # Duplicate noops and failed recall must not send a new document embedding.
            assert tuple(endpoint.document_requests) == documents

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["unavailable", "timeout"])
@pytest.mark.parametrize("operation", ["create", "revise"])
def test_related_fts_fallback_does_not_hide_document_embedding_failure(tmp_path, failure, operation):
    endpoint = _EmbeddingEndpoint()
    expected_error = InferenceTimeoutError if failure == "timeout" else InferenceUnavailableError

    async def scenario():
        async with (
            httpx.AsyncClient(transport=httpx.MockTransport(endpoint.respond)) as client,
            open_builtin_contexts(
                _config(tmp_path, fallback=True),
                embedding_model=_minimax(client),
                candidate_pipeline=_pipeline(revise=operation == "revise"),
            ) as contexts,
        ):
            context = await contexts.get("project")
            memory = contexts.atomic_memory.for_scope("project")
            if operation == "revise":
                await contexts.records.create_source("project", "content", ORIGINAL)
                await context.triggers.flush(limit=1)
            before = (await memory.list()).items
            cursor = (await context.triggers.cursor()).sequence
            documents = tuple(endpoint.document_requests)

            endpoint.query_failure = failure
            endpoint.document_failure = failure
            text = f"{ORIGINAL} Keep the rollout gradual." if operation == "revise" else ORIGINAL
            await contexts.records.create_source("project", "content", text)
            with pytest.raises(expected_error):
                await context.triggers.flush(limit=1)
            assert (await context.triggers.cursor()).sequence == cursor
            assert (await memory.list()).items == before
            assert tuple(endpoint.document_requests) != documents
            assert text in endpoint.document_requests[-1]["texts"][0]

            endpoint.query_failure = None
            endpoint.document_failure = None
            assert (await context.triggers.flush(limit=1)).current_cursor == cursor + 1
            (recovered,) = (await memory.list()).items
            assert recovered.artifact.content.text == text
            if operation == "revise":
                assert recovered.artifact.artifact_id == before[0].artifact.artifact_id
                assert recovered.artifact.revision == before[0].artifact.revision + 1

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", [ValueError("query implementation defect"), asyncio.CancelledError()])
def test_related_fts_fallback_propagates_programming_failure_and_cancellation(tmp_path, failure):
    class FailedQueryEmbedding(_SymmetricEmbedding):
        async def embed_query(self, texts, /):
            raise failure

    async def scenario():
        async with open_builtin_contexts(
            _config(tmp_path, fallback=True), embedding_model=FailedQueryEmbedding(), candidate_pipeline=_pipeline()
        ) as contexts:
            context = await contexts.get("project")
            await contexts.records.create_source("project", "content", ORIGINAL)
            with pytest.raises(type(failure)):
                await context.triggers.flush(limit=1)
            assert (await context.triggers.cursor()).sequence == 0
            assert not (await contexts.atomic_memory.for_scope("project").list()).items

    asyncio.run(scenario())
