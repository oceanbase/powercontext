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
import struct

import pytest
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.memory import (
    EmbeddingProfile,
    Memory,
    MemoryCapabilities,
    MemoryChange,
    MemoryCommit,
    MemoryContent,
    MemoryEntryInput,
    MemoryEntryVersion,
    MemoryManifest,
    MemoryManifestEntry,
    MemoryProjection,
    MemoryRerankDecision,
    MemoryService,
)
from powercontext.builtin.artifacts.memory.canonical import entry_content_hash, memory_content_hash
from powercontext.builtin.artifacts.memory.errors import MemoryBackendConfigurationError
from powercontext.builtin.artifacts.search import analyze_text
from powercontext.builtin.inference import EmbeddingResult, InferenceUsage
from powercontext.builtin.persistence.memory import RelationalMemoryBackend
from powercontext.builtin.persistence.memory_index import NoMemoryIndex
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import BuiltinConfig, open_builtin_contexts
from powercontext.builtin.runtime.config import RuntimeConfig


class _SelectingReranker:
    policy_id = "test.memory.rerank.v1"

    def __init__(self) -> None:
        self.candidates = ()

    async def rerank(self, query, candidates, limit, /) -> MemoryRerankDecision:
        assert query == "project"
        assert limit == 2
        self.candidates = candidates
        return MemoryRerankDecision(
            selected_ranks=(3, 1),
            usage=InferenceUsage(requests=1, input_tokens=20, output_tokens=2),
        )


_DENSE_PROFILE = EmbeddingProfile(
    profile_id="dense-test-v1",
    model="test:dense",
    dimension=2,
    distance="l2",
    normalization="unit",
)


class _DenseEmbeddingModel:
    profile = _DENSE_PROFILE

    async def embed(self, texts: tuple[str, ...], /) -> EmbeddingResult:
        return EmbeddingResult(vectors=((0.2407121489724894, -0.9705965492093231),) * len(texts))


class _RecordingVectorIndex(NoMemoryIndex):
    capabilities = MemoryCapabilities(fts=False, vector=True, embedding_profile=_DENSE_PROFILE)

    def __init__(self) -> None:
        self.projections: tuple[MemoryProjection, ...] = ()

    async def replace(
        self,
        _connection: AsyncConnection,
        _scope_id: str,
        _memory_ref: ArtifactRef,
        projections: tuple[MemoryProjection, ...],
        /,
    ) -> None:
        self.projections = projections


class _Float32VectorIndex(_RecordingVectorIndex):
    async def hydrate(
        self,
        _connection: AsyncConnection,
        _scope_id: str,
        projections: tuple[MemoryProjection, ...],
        /,
    ) -> tuple[MemoryProjection, ...]:
        stored = {projection.entry_version.entry_version_id: projection for projection in self.projections}
        hydrated = []
        for projection in projections:
            previous = stored.get(projection.entry_version.entry_version_id)
            if previous is None or previous.embedding is None:
                hydrated.append(projection)
                continue
            embedding = struct.unpack(
                f"={len(previous.embedding)}f",
                struct.pack(f"={len(previous.embedding)}f", *previous.embedding),
            )
            hydrated.append(
                projection.model_copy(
                    update={
                        "embedding": embedding,
                        "embedding_content_hash": previous.embedding_content_hash,
                    }
                )
            )
        return tuple(hydrated)


def test_memory_search_applies_injected_reranker_after_coarse_fusion() -> None:
    async def scenario() -> None:
        reranker = _SelectingReranker()
        config = BuiltinConfig(runtime=RuntimeConfig(memory_rerank_candidate_limit=4))
        async with open_builtin_contexts(config, memory_reranker=reranker) as contexts:
            service = (await contexts.get("rerank")).artifacts.memory
            memory = await service.remember(
                memory=None,
                entries=tuple(MemoryEntryInput(kind="fact", text=f"Project fact {number}.") for number in range(1, 5)),
                mode="append",
            )
            assert memory is not None

            result = await service.search("project", memories=(memory,), limit=2, mode="fts")

            assert len(reranker.candidates) == 4
            assert result.hits == (reranker.candidates[2], reranker.candidates[0])
            assert result.rerank is not None
            assert result.rerank.policy_id == reranker.policy_id
            assert result.rerank.candidate_hits == reranker.candidates
            assert result.rerank.selected_ranks == (3, 1)
            assert result.rerank.usage.requests == 1

    asyncio.run(scenario())


def test_memory_remember_accepts_a_dense_unit_embedding_after_service_normalization() -> None:
    async def scenario() -> None:
        async with open_builtin_contexts(BuiltinConfig(database=SQLiteConfig())) as contexts:
            index = _RecordingVectorIndex()
            backend = RelationalMemoryBackend(
                database=contexts.database,
                scope_id="dense-vector",
                artifacts=contexts.repositories.artifacts,
                index=index,
            )
            service = MemoryService(backend=backend, embedding_model=_DenseEmbeddingModel())

            memory = await service.remember(
                memory=None,
                entries=(MemoryEntryInput(kind="preference", text="User prefers dense vectors."),),
                mode="append",
            )

            assert memory is not None
            assert len(index.projections) == 1
            assert index.projections[0].embedding == (
                0.24071214897248938,
                -0.9705965492093231,
            )

    asyncio.run(scenario())


def test_memory_append_reuses_a_storage_rounded_unit_embedding() -> None:
    async def scenario() -> None:
        async with open_builtin_contexts(BuiltinConfig(database=SQLiteConfig())) as contexts:
            index = _Float32VectorIndex()
            backend = RelationalMemoryBackend(
                database=contexts.database,
                scope_id="storage-rounded-vector",
                artifacts=contexts.repositories.artifacts,
                index=index,
            )
            service = MemoryService(backend=backend, embedding_model=_DenseEmbeddingModel())
            first = await service.remember(
                memory=None,
                entries=(MemoryEntryInput(kind="preference", text="User prefers dense vectors."),),
                mode="append",
            )
            assert first is not None

            second = await service.remember(
                memory=first,
                entries=(MemoryEntryInput(kind="fact", text="Storage uses float32 vectors."),),
                mode="append",
            )

            assert second is not None
            assert second.revision == 2
            assert len(index.projections) == 2

    asyncio.run(scenario())


def test_memory_commit_rejects_a_finite_vector_outside_the_unit_norm_tolerance() -> None:
    async def scenario() -> None:
        async with open_builtin_contexts(BuiltinConfig(database=SQLiteConfig())) as contexts:
            index = _RecordingVectorIndex()
            backend = RelationalMemoryBackend(
                database=contexts.database,
                scope_id="non-unit-vector",
                artifacts=contexts.repositories.artifacts,
                index=index,
            )
            service = MemoryService(backend=backend, embedding_model=_DenseEmbeddingModel())
            plan = await service.plan_remember(
                memory=None,
                entries=(MemoryEntryInput(kind="preference", text="User prefers valid unit vectors."),),
                mode="append",
            )
            assert plan.commit is not None
            projection = plan.commit.projections[0].model_copy(update={"embedding": (0.5, 0.5)})
            candidate = plan.commit.model_copy(update={"projections": (projection,)})

            with pytest.raises(MemoryBackendConfigurationError):
                async with backend.begin() as unit_of_work:
                    await unit_of_work.commit(candidate)

    asyncio.run(scenario())


def test_memory_entry_can_be_deactivated_and_reactivated_without_rewriting_content() -> None:
    async def scenario() -> None:
        async with open_builtin_contexts(BuiltinConfig(database=SQLiteConfig())) as contexts:
            service = (await contexts.get("lifecycle")).artifacts.memory
            initial = await service.remember(
                memory=None,
                entries=(MemoryEntryInput(kind="decision", text="Keep the public behavior stable."),),
                mode="append",
            )
            assert initial is not None
            entry = (await service.entries(initial))[0]

            inactive = await service.forget(initial, entries=(entry,), reason="paused")
            restored = await service.reactivate(
                inactive,
                entries=((await service.entries(inactive))[0],),
                reason="resumed",
            )

            assert inactive.revision == 2
            assert inactive.content.manifest.entries[0].state == "inactive"
            assert restored.revision == 3
            assert restored.content.manifest.entries[0].state == "active"
            assert restored.content.manifest.entries[0].entry_version_id == entry.entry_version_id
            assert restored.content.changes[0].op == "reactivate"
            assert restored.content.changes[0].reason == "resumed"
            assert (await service.entries(restored))[0] == entry

    asyncio.run(scenario())


def test_memory_organize_deduplicates_and_normalizes_existing_entries() -> None:
    async def scenario() -> None:
        async with open_builtin_contexts(BuiltinConfig(database=SQLiteConfig())) as contexts:
            backend = RelationalMemoryBackend(
                database=contexts.database,
                scope_id="organize",
                artifacts=contexts.repositories.artifacts,
                index=contexts.index,
            )
            content_hash = entry_content_hash(
                kind="fact",
                text="Duplicate.",
                source_refs=(),
                artifact_refs=(),
            )
            versions = tuple(
                MemoryEntryVersion(
                    memory_artifact_id="memory",
                    entry_id=entry_id,
                    entry_version_id=f"{entry_id}-v1",
                    version=1,
                    previous_version_id=None,
                    kind=" fact ",
                    text="  Duplicate.  ",
                    entry_content_hash=content_hash,
                    created_in_revision=1,
                )
                for entry_id in ("entry-a", "entry-b")
            )
            content = MemoryContent(
                manifest=MemoryManifest(
                    entries=tuple(
                        MemoryManifestEntry(
                            entry_id=version.entry_id,
                            entry_version_id=version.entry_version_id,
                            entry_content_hash=version.entry_content_hash,
                            state="active",
                        )
                        for version in versions
                    )
                ),
                changes=tuple(
                    MemoryChange(
                        op="add",
                        entry_id=version.entry_id,
                        from_entry_version_id=None,
                        to_entry_version_id=version.entry_version_id,
                    )
                    for version in versions
                ),
            )
            memory = Memory(artifact_id="memory", revision=1, content=content)
            projections = tuple(
                MemoryProjection(entry_version=version, searchable_text=analyze_text(version.text))
                for version in versions
            )
            async with backend.begin() as unit_of_work:
                await unit_of_work.commit(
                    MemoryCommit(
                        base=None,
                        memory=memory,
                        content_hash=memory_content_hash(content),
                        entry_versions=versions,
                        projections=projections,
                    )
                )
            service = MemoryService(backend=backend)

            organized = await service.organize(memory)
            entries = await service.entries(organized)

            assert organized.revision == 2
            assert tuple(item.state for item in organized.content.manifest.entries) == ("active", "inactive")
            assert tuple(change.op for change in organized.content.changes) == ("revise", "deactivate")
            assert entries[0].kind == "fact"
            assert entries[0].text == "Duplicate."
            assert entries[0].version == 2
            assert entries[0].previous_version_id == "entry-a-v1"
            assert entries[1] == versions[1]

    asyncio.run(scenario())


def test_memory_organize_normalizes_an_inactive_entry_without_semantic_revision() -> None:
    async def scenario() -> None:
        async with open_builtin_contexts(BuiltinConfig(database=SQLiteConfig())) as contexts:
            backend = RelationalMemoryBackend(
                database=contexts.database,
                scope_id="normalize-inactive",
                artifacts=contexts.repositories.artifacts,
                index=contexts.index,
            )
            content_hash = entry_content_hash(
                kind="preference",
                text="User prefers black tea.",
                source_refs=(),
                artifact_refs=(),
            )
            original = MemoryEntryVersion(
                memory_artifact_id="memory",
                entry_id="preference",
                entry_version_id="preference-v1",
                version=1,
                previous_version_id=None,
                kind=" preference ",
                text="  User prefers black tea.  ",
                entry_content_hash=content_hash,
                created_in_revision=1,
            )
            content = MemoryContent(
                manifest=MemoryManifest(
                    entries=(
                        MemoryManifestEntry(
                            entry_id=original.entry_id,
                            entry_version_id=original.entry_version_id,
                            entry_content_hash=original.entry_content_hash,
                            state="active",
                        ),
                    )
                ),
                changes=(
                    MemoryChange(
                        op="add",
                        entry_id=original.entry_id,
                        from_entry_version_id=None,
                        to_entry_version_id=original.entry_version_id,
                    ),
                ),
            )
            memory = Memory(artifact_id="memory", revision=1, content=content)
            async with backend.begin() as unit_of_work:
                await unit_of_work.commit(
                    MemoryCommit(
                        base=None,
                        memory=memory,
                        content_hash=memory_content_hash(content),
                        entry_versions=(original,),
                        projections=(
                            MemoryProjection(entry_version=original, searchable_text=analyze_text(original.text)),
                        ),
                    )
                )
            service = MemoryService(backend=backend)
            inactive = await service.forget(memory, entries=(original,))

            normalized = await service.organize(inactive, mode="normalize")
            entry = (await service.entries(normalized))[0]

            assert normalized.revision == 3
            assert normalized.content.manifest.entries[0].state == "inactive"
            assert normalized.content.changes[0].op == "revise"
            assert normalized.content.changes[0].reason == "normalize"
            assert entry.kind == "preference"
            assert entry.text == "User prefers black tea."
            assert entry.previous_version_id == original.entry_version_id

    asyncio.run(scenario())
