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
from contextlib import suppress
from typing import cast

from sqlalchemy import event, text

from powercontext.builtin.artifacts.memory import (
    EmbeddingProfile,
    Memory,
    MemoryBackend,
    MemoryCapabilities,
    MemoryCommit,
    MemoryContent,
    MemoryEntryInput,
    MemoryEntryVersion,
    MemoryManifest,
    MemoryManifestEntry,
    MemoryProjection,
    MemoryRerankDecision,
    MemorySearchChannels,
    MemoryService,
)
from powercontext.builtin.artifacts.memory.canonical import entry_content_hash, memory_content_hash
from powercontext.builtin.inference import EmbeddingResult, InferenceUsage
from powercontext.builtin.persistence.memory import RelationalMemoryBackend
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


QUERY_EMBEDDING_PROFILE = EmbeddingProfile(profile_id="query-v1", model="test:query", dimension=3)


class _QueryRecordingEmbedding:
    def __init__(self) -> None:
        self.document_texts: list[str] = []
        self.query_texts: list[str] = []
        self.profile = QUERY_EMBEDDING_PROFILE

    async def embed(self, texts: tuple[str, ...], /) -> EmbeddingResult:
        self.document_texts.extend(texts)
        return EmbeddingResult(vectors=tuple((0.0, 1.0, 0.0) for _ in texts))

    async def embed_query(self, texts: tuple[str, ...], /) -> EmbeddingResult:
        self.query_texts.extend(texts)
        return EmbeddingResult(vectors=tuple((1.0, 0.0, 0.0) for _ in texts))


class _QuerySearchBackend:
    def __init__(self, memory: Memory) -> None:
        self._memory = memory
        self.query_vector: tuple[float, ...] | None = None

    async def capabilities(self) -> MemoryCapabilities:
        return MemoryCapabilities(fts=True, vector=True, hybrid=True, embedding_profile=QUERY_EMBEDDING_PROFILE)

    async def get(self, _ref):
        return self._memory

    async def latest(self, _artifact_id):
        return self._memory

    async def vector_complete(self, _memories, _profile) -> bool:
        return True

    async def search(self, request, /) -> MemorySearchChannels:
        self.query_vector = request.query_vector
        return MemorySearchChannels()


def test_memory_vector_search_uses_query_embedding_path() -> None:
    memory = Memory(
        artifact_id="memory",
        revision=1,
        content=MemoryContent(manifest=MemoryManifest(entries=())),
    )

    async def scenario() -> None:
        embedding = _QueryRecordingEmbedding()
        backend = _QuerySearchBackend(memory)
        service = MemoryService(backend=cast(MemoryBackend, backend), embedding_model=embedding)

        await service.search("project", memories=(memory,), mode="vector")

        assert embedding.document_texts == []
        assert embedding.query_texts == ["project"]
        assert backend.query_vector == (1.0, 0.0, 0.0)

    asyncio.run(scenario())


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


def test_memory_append_projection_writes_do_not_grow_with_entry_history() -> None:
    """One append must only rewrite the projections of the entry it changed (#1321)."""

    async def scenario() -> None:
        async with open_builtin_contexts(BuiltinConfig(database=SQLiteConfig())) as contexts:
            service = (await contexts.get("lifecycle")).artifacts.memory
            statements: list[str] = []

            def record_statement(_connection: object, _cursor: object, statement: str, *_rest: object) -> None:
                statements.append(statement)

            async def append(memory, number: int):
                return await service.remember(
                    memory=memory,
                    entries=(MemoryEntryInput(kind="fact", text=f"Bounded fact {number:04d}."),),
                    mode="append",
                )

            async def measured_append(memory, number: int):
                statements.clear()
                engine = contexts.database.engine.sync_engine
                event.listen(engine, "before_cursor_execute", record_statement)
                try:
                    updated = await append(memory, number)
                finally:
                    event.remove(engine, "before_cursor_execute", record_statement)
                writes = len([
                    statement
                    for statement in statements
                    if "pc_memory_entry_heads" in statement or "pc_memory_entry_fts" in statement
                ])
                return updated, writes

            memory = None
            for number in range(3):
                memory = await append(memory, number)
            memory, early_writes = await measured_append(memory, 3)
            for number in range(4, 40):
                memory = await append(memory, number)
            _, late_writes = await measured_append(memory, 40)

            assert early_writes == late_writes
            assert early_writes <= 4

    asyncio.run(scenario())


def test_memory_append_leaves_untouched_projection_rows_identical() -> None:
    """Appending an entry must not rewrite the projection rows of entries it did not change (#1321)."""

    async def scenario() -> None:
        async with open_builtin_contexts(BuiltinConfig(database=SQLiteConfig())) as contexts:
            service = (await contexts.get("lifecycle")).artifacts.memory
            tables = (
                (
                    "pc_memory_entry_heads",
                    text("SELECT * FROM pc_memory_entry_heads WHERE memory_artifact_id = :memory_artifact_id"),
                ),
                (
                    "pc_memory_entry_fts",
                    text("SELECT * FROM pc_memory_entry_fts WHERE memory_artifact_id = :memory_artifact_id"),
                ),
            )

            async def append(memory, number: int):
                return await service.remember(
                    memory=memory,
                    entries=(MemoryEntryInput(kind="fact", text=f"Bounded fact {number:04d}."),),
                    mode="append",
                )

            async def snapshot(artifact_id: str) -> dict[tuple[str, str], dict[str, object]]:
                rows: dict[tuple[str, str], dict[str, object]] = {}
                async with contexts.database.transaction() as connection:
                    for name, statement in tables:
                        result = await connection.execute(statement, {"memory_artifact_id": artifact_id})
                        for row in result.mappings():
                            rows[(name, str(row["entry_id"]))] = dict(row)
                return rows

            memory = None
            for number in range(3):
                memory = await append(memory, number)
            before = await snapshot(memory.artifact_id)
            assert {name for name, _ in before} == {name for name, _ in tables}

            updated = await append(memory, 3)
            after = await snapshot(updated.artifact_id)

            new_ids = {item.entry_id for item in updated.content.manifest.entries} - {
                item.entry_id for item in memory.content.manifest.entries
            }
            assert new_ids
            assert {key for key in after if key not in before} == {
                (name, entry_id) for name, _ in tables for entry_id in new_ids
            }
            # Every row written before the append stays byte-identical, stamps included.
            assert {key: value for key, value in after.items() if key[1] not in new_ids} == before

    asyncio.run(scenario())


def test_memory_append_recovers_after_an_outer_transaction_rolls_back_the_cached_revision() -> None:
    """A rolled-back revision must not satisfy the projection cache for a later reuse of its ref (#1709)."""

    class _RolledBack(Exception):
        pass

    async def scenario() -> None:
        async with open_builtin_contexts(BuiltinConfig(database=SQLiteConfig())) as contexts:

            def writer() -> MemoryService:
                return MemoryService(
                    backend=RelationalMemoryBackend(
                        database=contexts.database,
                        scope_id="rollback",
                        artifacts=contexts.repositories.artifacts,
                        index=contexts.index,
                    )
                )

            def fact(text: str) -> MemoryEntryInput:
                return MemoryEntryInput(kind="fact", text=text)

            writer_a = writer()
            writer_b = writer()
            first = await writer_a.remember(memory=None, entries=(fact("First fact."),), mode="append")
            assert first is not None

            async def rolled_back_append() -> None:
                async with contexts.database.transaction():
                    # Joins the outer transaction, so the commit above is rolled back while
                    # writer_a still caches the projections of this revision.
                    await writer_a.remember(memory=first, entries=(fact("Rolled back fact."),), mode="append")
                    raise _RolledBack

            with suppress(_RolledBack):
                await rolled_back_append()

            # Another writer reuses the rolled-back revision number for real.
            second = await writer_b.remember(memory=first, entries=(fact("Second fact."),), mode="append")
            assert second is not None
            assert second.revision == 2

            third = await writer_a.remember(memory=second, entries=(fact("Third fact."),), mode="append")

            assert third is not None
            assert third.revision == 3
            assert [item.state for item in third.content.manifest.entries] == ["active"] * 3

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
                )
            )
            memory = Memory(artifact_id="memory", revision=1, content=content)
            projections = tuple(
                MemoryProjection(entry_version=version, searchable_text="duplicate.") for version in versions
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


def test_memory_head_entries_matches_head_and_entries_read_separately() -> None:
    async def scenario() -> None:
        async with open_builtin_contexts(BuiltinConfig(database=SQLiteConfig())) as contexts:
            service = (await contexts.get("head-entries")).artifacts.memory
            initial = await service.remember(
                memory=None,
                entries=(
                    MemoryEntryInput(kind="decision", text="Read a head and its entries together."),
                    MemoryEntryInput(kind="fact", text="A head read from storage is already canonical."),
                ),
                mode="append",
            )
            assert initial is not None
            forgotten = await service.forget(initial, entries=((await service.entries(initial))[0],), reason="done")

            head = await service.head(forgotten.artifact_id)
            separate = await service.entries(head)
            combined_head, combined_entries = await service.head_entries(forgotten.artifact_id)

            assert combined_head == head
            assert combined_entries == separate
            assert [item.state for item in combined_head.content.manifest.entries] == ["inactive", "active"]

    asyncio.run(scenario())
