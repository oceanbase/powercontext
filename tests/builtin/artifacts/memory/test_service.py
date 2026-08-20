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

from powercontext.builtin.artifacts.memory import (
    Memory,
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
from powercontext.builtin.inference import InferenceUsage
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
