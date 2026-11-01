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

"""Exercise journey persistence audits and cleanup against real database profiles."""

from __future__ import annotations

import asyncio
from typing import cast

import pytest

from powercontext.builtin.artifacts.experience import ExperienceContent
from powercontext.builtin.artifacts.memory import EmbeddingProfile
from powercontext.builtin.inference import EmbeddingResult
from powercontext.builtin.persistence.seekdb import SeekDBConfig, SeekDBProfile
from powercontext.builtin.runtime import BuiltinConfig, DatabaseConfig, open_builtin_contexts
from powercontext.builtin.scope import ScopeDraft
from powercontext.http import ArtifactReference
from tests.e2e.atomic_memory_migration_backend import MigrationBackend
from tests.e2e.real_experience_skill import harness


class _Embedding:
    profile = EmbeddingProfile(profile_id="journey-persistence", model="fixture", dimension=3, normalization="unit")

    async def embed(self, texts: tuple[str, ...], /) -> EmbeddingResult:
        return EmbeddingResult(vectors=((1.0, 0.0, 0.0),) * len(texts))


@pytest.fixture
def harness_database(migration_backend: MigrationBackend) -> DatabaseConfig:
    database = migration_backend.config
    if isinstance(database, SeekDBConfig):

        async def provision() -> None:
            async with SeekDBProfile.open(database, tables=()):
                pass

        asyncio.run(provision())
    return database


def test_restart_audit_matches_persisted_vectors_to_exact_scope_and_revision(harness_database: DatabaseConfig) -> None:
    async def scenario() -> None:
        settings = BuiltinConfig(database=harness_database)
        async with open_builtin_contexts(settings, embedding_model=_Embedding()) as contexts:
            scopes = []
            for key in ("configured-real-memory:audit", "other-project"):
                scope = await contexts.scopes.create(
                    ScopeDraft(title=key, summary="Isolated vector audit evidence", idempotency_key=key)
                )
                scopes.append(scope.scope_id)
                await contexts.records.create_atomic_memories(
                    scope.scope_id,
                    (
                        {
                            "kind": "fact",
                            "text": "Replay committed writes with the same key.",
                            "description": "Retry fact",
                        },
                    ),
                )
        async with open_builtin_contexts(settings, embedding_model=_Embedding()) as restarted:
            results = await restarted.atomic_memory.for_scope(scopes[0]).search("Retry committed writes", mode="vector")
            assert len(results.hits) == 1
            assert "vector" in results.hits[0].matched_by
            reference = results.hits[0].hit.artifact_ref
            ref = ArtifactReference(
                family=reference.family, artifact_id=reference.artifact_id, revision=reference.revision
            )
        assert await harness._persisted_memory_vector_count(harness_database, scopes[0], (ref,)) == 1
        assert await harness._persisted_memory_vector_count(harness_database, scopes[1], (ref,)) == 0
        assert (
            await harness._persisted_memory_vector_count(
                harness_database, scopes[0], (ref.model_copy(update={"revision": ref.revision + 1}),)
            )
            == 0
        )

    asyncio.run(scenario())


def test_restart_audit_does_not_count_text_only_projection_as_vector(harness_database: DatabaseConfig) -> None:
    async def scenario() -> None:
        async with open_builtin_contexts(BuiltinConfig(database=harness_database)) as contexts:
            scope = await contexts.scopes.create(
                ScopeDraft(title="Text audit", summary="Unembedded projection", idempotency_key="text-only-audit")
            )
            (created,) = await contexts.records.create_atomic_memories(
                scope.scope_id,
                ({"kind": "fact", "text": "Text exists without an embedding.", "description": "Unembedded fact"},),
            )
            ref = ArtifactReference(family="atomic-memory", artifact_id=created.artifact_id, revision=created.revision)
        assert await harness._persisted_memory_vector_count(harness_database, scope.scope_id, (ref,)) == 0

    asyncio.run(scenario())


def test_cleanup_removes_current_projections_and_review_records_only_in_requested_scopes(
    harness_database: DatabaseConfig,
) -> None:
    async def scenario() -> None:
        settings = BuiltinConfig(database=harness_database)
        async with open_builtin_contexts(settings, embedding_model=_Embedding()) as contexts:
            scopes = []
            memories = []
            reviewed = []
            for key in ("configured-real-memory:cleanup", "other-project"):
                scope = await contexts.scopes.create(
                    ScopeDraft(title=key, summary="Isolated cleanup evidence", idempotency_key=key)
                )
                scopes.append(scope.scope_id)
                (created,) = await contexts.records.create_atomic_memories(
                    scope.scope_id,
                    ({"kind": "fact", "text": "A retry retained exactly one write.", "description": "Retry evidence"},),
                )
                memory = await contexts.atomic_memory.for_scope(scope.scope_id).get(created.artifact_id)
                memories.append(memory)
                review = contexts.review(scope.scope_id)
                candidate = await review.propose_experience(
                    ExperienceContent(
                        situation="A write timed out.",
                        action="Retry with the original key.",
                        outcome="Exactly one write remains.",
                        lesson="Retain the idempotency key when retrying.",
                    ),
                    sources=(),
                    artifacts=(memory.ref,),
                    target=None,
                    reason="Verified by persisted evidence.",
                )
                reviewed.append(await review.approve(candidate.candidate_id, candidate.version))
            keep_ref = reviewed[1].result_artifact
            assert keep_ref is not None
            keep_before = await contexts.records.get_artifact_revision(
                scopes[1], "experience", keep_ref.artifact_id, keep_ref.revision
            )

        cleanup = await harness._purge_existing_harness_scopes(harness_database)
        assert cleanup["remaining_row_count"] == 0
        rows_before = cast(dict[str, int], cleanup["rows_before"])
        assert rows_before["pc_atomic_memory_current"] == 1
        counts = await harness._database_scope_counts(harness_database, tuple(scopes))
        assert counts[scopes[0]]["pc_atomic_memory_current"] == 0
        assert counts[scopes[1]]["pc_atomic_memory_current"] == 1
        repeated = await harness._purge_database_scopes(harness_database, (scopes[0],))
        assert repeated["remaining_row_count"] == 0
        assert repeated["rows_before"] == {}

        async with open_builtin_contexts(settings, embedding_model=_Embedding()) as restarted:
            assert await restarted.atomic_memory.for_scope(scopes[1]).get(memories[1].ref.artifact_id) == memories[1]
            assert await restarted.review(scopes[1]).get_candidate(reviewed[1].candidate_id) == reviewed[1]
            assert (
                await restarted.records.get_artifact_revision(
                    scopes[1], "experience", keep_ref.artifact_id, keep_ref.revision
                )
                == keep_before
            )
            results = await restarted.atomic_memory.for_scope(scopes[1]).search("Retried write", mode="vector")
            assert tuple(hit.hit.artifact_ref for hit in results.hits) == (memories[1].ref,)

    asyncio.run(scenario())
