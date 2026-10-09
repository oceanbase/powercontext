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

"""Atomic public retrieval reuses bounded search, complete artifacts and deployment policy."""

from __future__ import annotations

import asyncio

import pytest

from powercontext.artifacts import (
    ArtifactSearchContractError,
    ArtifactSearchExecutionContext,
    ArtifactSearchUnsupported,
)
from powercontext.builtin.artifacts.atomic_memory import AtomicMemoryContent
from powercontext.builtin.artifacts.memory import EmbeddingProfile
from powercontext.builtin.artifacts.memory.reranking import MemoryRerankDecision
from powercontext.builtin.inference import (
    EmbeddingResult,
    InferenceTimeoutError,
    InferenceUnavailableError,
    InferenceUsage,
)
from powercontext.builtin.persistence.atomic_memory_index import AtomicMemoryRelatedRequest
from powercontext.builtin.persistence.errors import RepositoryNotFoundError
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.records import ArtifactWrite
from powercontext.builtin.runtime import BuiltinConfig, open_builtin_contexts
from powercontext.builtin.runtime.artifact_search import ArtifactSearchService
from powercontext.builtin.runtime.atomic_memory_search import AtomicArtifactSearcher
from powercontext.builtin.tags import ArtifactTagTarget
from powercontext.server.authz import AccessDeniedError, AccessIdentityRequiredError


class Embedding:
    profile = EmbeddingProfile(profile_id="atomic-public", model="test", dimension=3, normalization="unit")

    def __init__(self):
        self.failure = None

    async def embed(self, texts, /):
        return EmbeddingResult(vectors=((1.0, 0.0, 0.0),) * len(texts))

    async def embed_query(self, texts, /):
        if self.failure is not None:
            raise self.failure
        return await self.embed(texts)


def service(contexts):
    search = ArtifactSearchService(known_families=("atomic-memory",))
    search.register(AtomicArtifactSearcher(application=contexts.atomic_memory))
    return search


def config(tmp_path):
    return BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'atomic.db'}"))


@pytest.mark.parametrize("mode", ["text", "vector", "hybrid"])
def test_atomic_public_exact_revisions_scores_and_threshold_order(tmp_path, mode):
    async def scenario():
        async with open_builtin_contexts(config(tmp_path), embedding_model=Embedding()) as contexts:
            await contexts.get("project")
            original = await contexts.records.create_atomic_memories(
                "project",
                (
                    {"kind": "fact", "text": "Alpha rollback plan."},
                    {"kind": "decision", "text": "Alpha rollout plan."},
                    {"kind": "fact", "text": "Alpha recovery plan."},
                ),
            )
            changed = await contexts.records.replace_artifact(
                "project",
                "atomic-memory",
                original[0].artifact_id,
                '"revision:1"',
                ArtifactWrite(content={"kind": "fact", "text": "Alpha revised rollback plan."}),
            )
            search = service(contexts)
            plain = await search.search("project", "atomic-memory", {"query": "alpha", "mode": mode, "limit": 100})
            scored = await search.search(
                "project", "atomic-memory", {"query": "alpha", "mode": mode, "limit": 100, "include_scores": True}
            )
            assert [match.artifact_ref for match in plain.matches] == [match.artifact_ref for match in scored.matches]
            assert len(scored.matches) == 3
            assert scored.artifacts is not None
            assert [artifact.as_ref() for artifact in scored.artifacts] == [
                match.artifact_ref for match in scored.matches
            ]
            revised = next(item for item in scored.artifacts if item.artifact_id == changed.artifact_id)
            assert revised.revision == 2 and revised.content.text == "Alpha revised rollback plan."
            for artifact in scored.artifacts:
                expected = await contexts.atomic_memory.for_scope("project").get(artifact.artifact_id)
                assert artifact == expected.artifact
            assert all(match.channel_scores is None for match in plain.matches)
            for match in scored.matches:
                assert match.channel_scores is not None
                assert set(match.channel_scores) == ({"text", "vector"} if mode == "hybrid" else {mode})
                for name, channel in match.channel_scores.items():
                    assert channel.metric == ("sqlite_bm25" if name == "text" else "l2_distance")
                    assert channel.higher_is_better is False
                    assert channel.raw < 0 if name == "text" else channel.raw == 0
            threshold = scored.matches[0].retrieval_score
            kept = await search.search(
                "project", "atomic-memory", {"query": "alpha", "mode": mode, "min_score": threshold}
            )
            assert [match.artifact_ref for match in kept.matches] == [
                match.artifact_ref for match in scored.matches if match.retrieval_score >= threshold
            ]
            assert len(kept.matches) < 3
            legacy = await contexts.atomic_memory.for_scope("project").search("alpha", mode=mode)
            assert legacy.artifacts is None and legacy.matches == ()
            assert [hit.hit.artifact_ref for hit in legacy.hits] == [match.artifact_ref for match in scored.matches]

    asyncio.run(scenario())


def test_atomic_public_kind_tags_and_inactive_filters_precede_bounded_search(tmp_path):
    async def scenario():
        async with open_builtin_contexts(config(tmp_path)) as contexts:
            await contexts.get("project")
            created = await contexts.records.create_atomic_memories(
                "project",
                (
                    {"kind": "fact", "text": "Alpha database plan."},
                    {"kind": "decision", "text": "Alpha database plan."},
                    {"kind": "fact", "text": "Alpha retired plan."},
                ),
            )
            for item in created:
                target = ArtifactTagTarget(family="atomic-memory", artifact_id=item.artifact_id)
                current = await contexts.records.get_tags("project", target)
                await contexts.records.replace_tags("project", target, ("API", "数据库"), expected_etag=current.etag)
            memory = contexts.atomic_memory.for_scope("project")
            await memory.forget(created[2].artifact_id, expected_revision=1, expected_state_version=0)
            result = await service(contexts).search(
                "project",
                "atomic-memory",
                {
                    "query": "alpha",
                    "limit": 1,
                    "filters": {"kind": "fact", "tags": ["api", "数据库"], "tag_match": "all"},
                },
            )
            assert [match.artifact_ref.artifact_id for match in result.matches] == [created[0].artifact_id]
            assert result.artifacts[0].content.kind == "fact"

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["vector", "hybrid"])
def test_atomic_public_missing_vector_deployment_is_unsupported_before_empty_scope(tmp_path, mode):
    async def scenario():
        async with open_builtin_contexts(config(tmp_path)) as contexts:
            await contexts.get("project")
            with pytest.raises(ArtifactSearchUnsupported):
                await service(contexts).search("project", "atomic-memory", {"query": "alpha", "mode": mode})

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "failure", [InferenceUnavailableError("embedding"), InferenceTimeoutError("embedding", 1), TimeoutError()]
)
def test_atomic_public_embedding_failure_remains_service_failure(tmp_path, failure):
    async def scenario():
        model = Embedding()
        model.failure = failure
        async with open_builtin_contexts(config(tmp_path), embedding_model=model) as contexts:
            await contexts.get("project")
            expected = InferenceUnavailableError if type(failure) is TimeoutError else type(failure)
            with pytest.raises(expected):
                await service(contexts).search("project", "atomic-memory", {"query": "alpha", "mode": "hybrid"})
            legacy = await contexts.atomic_memory.for_scope("project").search("alpha", mode="auto")
            assert legacy.mode == "text" and legacy.embedding_calls == 1

    asyncio.run(scenario())


def test_atomic_public_context_never_turns_missing_identity_into_local_authority(tmp_path):
    async def scenario():
        async with open_builtin_contexts(config(tmp_path)) as contexts:
            await contexts.get("project")
            search = service(contexts)
            for context in (ArtifactSearchExecutionContext(), ArtifactSearchExecutionContext(access=object())):
                with pytest.raises(AccessIdentityRequiredError):
                    await search.search("project", "atomic-memory", {"query": "alpha"}, execution_context=context)
            with pytest.raises(AccessDeniedError):
                await search.search(
                    "project",
                    "atomic-memory",
                    {"query": "alpha"},
                    execution_context=ArtifactSearchExecutionContext(access=object(), trusted_local=True),
                )
            assert (await search.search("project", "atomic-memory", {"query": "alpha"})).artifacts == ()
            assert (
                await search.search(
                    "project",
                    "atomic-memory",
                    {"query": "alpha"},
                    execution_context=ArtifactSearchExecutionContext(trusted_local=True),
                )
            ).artifacts == ()

    asyncio.run(scenario())


def test_atomic_public_threshold_precedes_configured_rerank_and_retains_retrieval_score(tmp_path):
    class Reranker:
        supports_atomic_memory = True
        policy_id = "test-policy"

        async def rerank(self, query, candidates, limit, /):
            assert len(candidates) == 1 and limit == 1
            return MemoryRerankDecision((1,), InferenceUsage(requests=1))

    async def scenario():
        async with open_builtin_contexts(config(tmp_path), memory_reranker=Reranker()) as contexts:
            await contexts.get("project")
            await contexts.records.create_atomic_memories(
                "project",
                (
                    {"kind": "fact", "text": "Alpha plan."},
                    {"kind": "fact", "text": "Alpha longer plan details."},
                ),
            )
            result = await service(contexts).search(
                "project", "atomic-memory", {"query": "alpha", "min_score": 1, "include_scores": True}
            )
            assert len(result.matches) == 1 and result.matches[0].retrieval_score == 1
            assert result.rerank is not None and result.generation_calls == 1
            assert result.artifacts is not None
            assert result.artifacts[0].as_ref() == result.matches[0].artifact_ref

    asyncio.run(scenario())


@pytest.mark.parametrize("boundary", ["candidate-validation", "rerank-reauthorization"])
def test_atomic_public_missing_selected_authority_is_internal_failure(tmp_path, monkeypatch, boundary):
    class Reranker:
        supports_atomic_memory = True
        policy_id = "test-policy"

        async def rerank(self, query, candidates, limit, /):
            return MemoryRerankDecision((1,), InferenceUsage(requests=1))

    async def missing(*args, **kwargs):
        raise RepositoryNotFoundError("artifact", "selected")

    async def scenario():
        async with open_builtin_contexts(config(tmp_path), memory_reranker=Reranker()) as contexts:
            await contexts.get("project")
            await contexts.records.create_atomic_memories("project", ({"kind": "fact", "text": "Alpha plan."},))
            if boundary == "candidate-validation":
                monkeypatch.setattr(contexts.atomic_memory.artifacts, "get_many", missing)
            else:
                monkeypatch.setattr(contexts.atomic_memory.service, "get", missing)
            with pytest.raises(ArtifactSearchContractError):
                await service(contexts).search("project", "atomic-memory", {"query": "alpha"})
            with pytest.raises(RepositoryNotFoundError):
                await contexts.atomic_memory.for_scope("project").search("alpha")

    asyncio.run(scenario())


def test_atomic_public_merge_artifact_retains_full_lineage(tmp_path):
    async def scenario():
        async with open_builtin_contexts(config(tmp_path)) as contexts:
            await contexts.get("project")
            created = await contexts.records.create_atomic_memories(
                "project",
                (
                    {"kind": "fact", "text": "Alpha first plan."},
                    {"kind": "fact", "text": "Alpha second plan."},
                ),
            )
            memory = contexts.atomic_memory.for_scope("project")
            inputs = tuple([await memory.get(item.artifact_id) for item in created])
            merged = await memory.merge(
                tuple(item.as_read() for item in inputs),
                AtomicMemoryContent(kind="decision", text="Alpha merged plan."),
            )
            result = await service(contexts).search("project", "atomic-memory", {"query": "alpha"})
            assert len(result.matches) == 1 and result.artifacts is not None
            artifact = result.artifacts[0]
            assert artifact.artifact_id == merged.primary_artifact_id
            assert artifact.content.creation is not None
            assert len(artifact.lineage.artifacts) == len(inputs)
            assert all(item.ref in artifact.lineage.artifacts for item in inputs)
            assert artifact == (await memory.get(artifact.artifact_id)).artifact

    asyncio.run(scenario())


def test_atomic_bounded_public_search_keeps_complete_related_enumeration(tmp_path):
    async def scenario():
        async with open_builtin_contexts(config(tmp_path)) as contexts:
            await contexts.get("project")
            created = await contexts.records.create_atomic_memories(
                "project", tuple({"kind": "fact", "text": f"Alpha plan number {index}."} for index in range(101))
            )
            bounded = await service(contexts).search("project", "atomic-memory", {"query": "alpha", "limit": 100})
            assert len(bounded.matches) == 100 and bounded.artifacts is not None
            application = contexts.atomic_memory
            async with application.database.transaction(consistent_snapshot=True) as connection:
                filters = await application.security.filters(
                    "project", application.default_context, connection=connection
                )
                complete = await application.index.enumerate_related(
                    connection, "project", AtomicMemoryRelatedRequest("alpha", filters)
                )
            assert {hit.artifact_ref.artifact_id for hit in complete} == {item.artifact_id for item in created}

    asyncio.run(scenario())


def test_atomic_public_exact_artifacts_remain_in_retrieval_authority_snapshot(tmp_path, monkeypatch):
    async def scenario():
        async with open_builtin_contexts(config(tmp_path)) as contexts:
            await contexts.get("project")
            (created,) = await contexts.records.create_atomic_memories(
                "project", ({"kind": "fact", "text": "Alpha original plan."},)
            )
            index = contexts.atomic_memory.index
            original_search = index.search

            async def interleaved(connection, scope_id, request):
                channels = await original_search(connection, scope_id, request)
                changed = await asyncio.create_task(
                    contexts.records.replace_artifact(
                        "project",
                        "atomic-memory",
                        created.artifact_id,
                        '"revision:1"',
                        ArtifactWrite(content={"kind": "fact", "text": "Alpha revised plan."}),
                    )
                )
                assert changed.revision == 2
                return channels

            with monkeypatch.context() as patch:
                patch.setattr(index, "search", interleaved)
                result = await asyncio.wait_for(
                    service(contexts).search("project", "atomic-memory", {"query": "alpha"}), timeout=20
                )
            assert result.artifacts is not None
            assert result.artifacts[0].revision == 1 and result.artifacts[0].content.text == "Alpha original plan."
            current = await service(contexts).search("project", "atomic-memory", {"query": "alpha"})
            assert current.artifacts is not None and current.artifacts[0].revision == 2

    asyncio.run(scenario())


def test_atomic_public_configured_rerank_orders_exact_artifacts_with_original_retrieval_scores(tmp_path):
    class Reranker:
        supports_atomic_memory = True
        policy_id = "reverse-policy"

        async def rerank(self, query, candidates, limit, /):
            assert len(candidates) == 2
            return MemoryRerankDecision((2, 1), InferenceUsage(requests=1))

    async def scenario():
        async with open_builtin_contexts(config(tmp_path), memory_reranker=Reranker()) as contexts:
            await contexts.get("project")
            await contexts.records.create_atomic_memories(
                "project",
                (
                    {"kind": "fact", "text": "Alpha short plan."},
                    {"kind": "fact", "text": "Alpha much longer plan details."},
                ),
            )
            result = await service(contexts).search(
                "project", "atomic-memory", {"query": "alpha", "include_scores": True}
            )
            assert result.artifacts is not None
            assert [artifact.as_ref() for artifact in result.artifacts] == [
                match.artifact_ref for match in result.matches
            ]
            assert result.matches[0].retrieval_score < result.matches[1].retrieval_score == 1
            assert result.rerank is not None and result.rerank.selected_ranks == (2, 1)
            for artifact in result.artifacts:
                assert (
                    artifact == (await contexts.atomic_memory.for_scope("project").get(artifact.artifact_id)).artifact
                )

    asyncio.run(scenario())
