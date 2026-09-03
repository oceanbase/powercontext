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
import json

import pytest

from powercontext.builtin.artifacts.memory import MemoryCandidateRequest, MemoryEntryInput, MemoryRerankDecision
from powercontext.builtin.inference import InferenceUsage
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import (
    BuiltinConfig,
    CaptureSource,
    CommitConnectorCheckpoint,
    PrepareContextRequest,
    RememberMemoryRequest,
    SearchMemoryRequest,
    SubmitSourceObservation,
    open_builtin_contexts,
    open_builtin_runtime,
)
from powercontext.builtin.scope import ScopeDraft, ScopeMutation, ScopeNotFoundError
from powercontext.builtin.sources import CONTENT_SOURCE_DEFINITION, ContentCapture, ContentSource
from powercontext.sources import ConnectorBinding, SourceDefinitionRegistry, project_source_for_transport


class _ContentCandidatePipeline:
    async def extract(self, request: MemoryCandidateRequest, /) -> tuple[MemoryEntryInput, ...]:
        return tuple(
            MemoryEntryInput(
                kind="fact",
                text=source.content,
                sources=(source,),
            )
            for source in request.sources
            if isinstance(source, ContentSource)
        )


def test_builtin_runtime_rejects_unregistered_scope_without_persisting_data(tmp_path) -> None:
    async def scenario() -> None:
        database = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'runtime.db'}")
        config = BuiltinConfig(database=database)
        orphan_scope_id = "project:orphan"
        binding = ConnectorBinding(
            scope_id=orphan_scope_id,
            binding_id="orphan-connector",
            connector_name="content",
            connector_version="1",
        )
        source_registry = SourceDefinitionRegistry((CONTENT_SOURCE_DEFINITION,))
        observation = project_source_for_transport(
            source_registry,
            await source_registry.resolve(ContentCapture(source_id="remote-turn", content="orphan remote content")),
        )

        async with open_builtin_runtime(config) as runtime:
            assert runtime.scopes is not None
            registered = await runtime.scopes.create(
                ScopeDraft(title="Registered", summary="Registered scope", idempotency_key="registered")
            )

            captured = await runtime.sources.for_scope(registered.scope_id).capture(
                CaptureSource(source_id="registered-turn", content="registered content", metadata={})
            )
            prepared = await runtime.context.for_scope(registered.scope_id).prepare(
                PrepareContextRequest(query="registered")
            )

            assert captured.sequence == 1
            assert prepared.status == "empty"
            with pytest.raises(ScopeNotFoundError):
                await runtime.sources.for_scope(orphan_scope_id).capture(
                    CaptureSource(source_id="orphan-turn", content="orphan content", metadata={})
                )
            with pytest.raises(ScopeNotFoundError):
                await runtime.context.for_scope(orphan_scope_id).prepare(PrepareContextRequest(query="orphan"))
            with pytest.raises(ScopeNotFoundError):
                await runtime.ingestion.checkpoint(binding)
            with pytest.raises(ScopeNotFoundError):
                await runtime.ingestion.submit(
                    SubmitSourceObservation(scope_id=orphan_scope_id, observation=observation)
                )
            with pytest.raises(ScopeNotFoundError):
                await runtime.ingestion.commit(
                    CommitConnectorCheckpoint(binding=binding, expected=None, checkpoint={"cursor": "1"})
                )

        async with open_builtin_contexts(config) as contexts:
            orphan = await contexts.get(orphan_scope_id)
            assert await orphan.sources.journal.entries() == ()
            assert (await contexts.connector_checkpoint(binding)).checkpoint is None

    asyncio.run(scenario())


def test_builtin_runtime_uses_sqlite_fts_without_vector_extension(tmp_path, monkeypatch) -> None:
    missing_extension = tmp_path / "missing-sqlite-vec"
    monkeypatch.setattr(
        "powercontext.builtin.persistence.sqlite.profile.sqlite_vec.loadable_path",
        lambda: str(missing_extension),
    )

    async def scenario() -> None:
        async with open_builtin_runtime(
            BuiltinConfig(database=SQLiteConfig()),
            candidate_pipeline=_ContentCandidatePipeline(),
        ) as runtime:
            assert runtime.scopes is not None
            project = await runtime.scopes.create(
                ScopeDraft(title="Project", summary="Runtime acceptance", idempotency_key="project")
            )
            empty = await runtime.scopes.create(
                ScopeDraft(title="Empty", summary="Empty acceptance", idempotency_key="empty-project")
            )
            captured = await runtime.sources.for_scope(project.scope_id).capture(
                CaptureSource(
                    source_id="turn-1",
                    content="PowerContext composes an atomic SQL provider.",
                    metadata={"origin": "e2e"},
                )
            )
            flushed = await runtime.memory.for_scope(project.scope_id).flush()
            found = await runtime.memory.for_scope(project.scope_id).search(
                SearchMemoryRequest(query="atomic SQL provider")
            )
            prepared = await runtime.context.for_scope(project.scope_id).prepare(
                PrepareContextRequest(query="atomic SQL provider")
            )
            no_memory = await runtime.context.for_scope(empty.scope_id).prepare(PrepareContextRequest(query="anything"))
            no_match = await runtime.context.for_scope(project.scope_id).prepare(
                PrepareContextRequest(query="unrelated-zebra-phrase")
            )

            assert captured.sequence == 1
            assert flushed.current_cursor == captured.sequence
            assert flushed.memory_ref is not None
            assert tuple(hit.text for hit in found.hits) == ("PowerContext composes an atomic SQL provider.",)
            assert prepared.status == "ready"
            assert prepared.content is not None
            item = json.loads(prepared.content.splitlines()[-2])["items"][0]
            assert item["content"] == "PowerContext composes an atomic SQL provider."
            assert item["citation"]["memory_ref"] == flushed.memory_ref.model_dump(mode="json")
            assert no_memory.status == "empty"
            assert no_memory.content is None
            assert no_match.status == "empty"
            assert no_match.content is None

    asyncio.run(scenario())


def test_prepare_context_reads_only_direct_context_references() -> None:
    async def scenario() -> None:
        async with open_builtin_runtime(BuiltinConfig(database=SQLiteConfig())) as runtime:
            assert runtime.scopes is not None
            shared = await runtime.scopes.create(
                ScopeDraft(title="Shared", summary="Reusable evidence", idempotency_key="shared")
            )
            middle = await runtime.scopes.create(
                ScopeDraft(
                    title="Middle",
                    summary="Reads shared evidence",
                    context_references=(shared.scope_id,),
                    idempotency_key="middle",
                )
            )
            reader = await runtime.scopes.create(
                ScopeDraft(
                    title="Reader",
                    summary="Reads middle only",
                    context_references=(middle.scope_id,),
                    idempotency_key="reader",
                )
            )
            child = await runtime.scopes.create(
                ScopeDraft(
                    title="Child",
                    summary="Organized under shared",
                    parent_scope_id=shared.scope_id,
                    idempotency_key="child",
                )
            )
            await runtime.memory.for_scope(shared.scope_id).remember(
                RememberMemoryRequest(entries=(MemoryEntryInput(kind="fact", text="Shared direct context evidence."),))
            )
            await runtime.memory.for_scope(middle.scope_id).remember(
                RememberMemoryRequest(entries=(MemoryEntryInput(kind="fact", text="Middle reverse-only evidence."),))
            )

            direct = await runtime.context.for_scope(middle.scope_id).prepare(
                PrepareContextRequest(query="direct context evidence")
            )
            transitive = await runtime.context.for_scope(reader.scope_id).prepare(
                PrepareContextRequest(query="direct context evidence")
            )
            reverse = await runtime.context.for_scope(shared.scope_id).prepare(
                PrepareContextRequest(query="middle reverse-only evidence")
            )
            parent_only = await runtime.context.for_scope(child.scope_id).prepare(
                PrepareContextRequest(query="direct context evidence")
            )

            assert direct.status == "ready"
            assert direct.content is not None
            item = json.loads(direct.content.splitlines()[-2])["items"][0]
            assert item["citation"]["memory"]["scope_id"] == shared.scope_id
            assert transitive.status == "empty"
            assert reverse.status == "empty"
            assert parent_only.status == "empty"

            updated = await runtime.scopes.update(
                reader.scope_id,
                ScopeMutation(
                    expected_version=reader.version,
                    title=reader.title,
                    summary=reader.summary,
                    context_references=(shared.scope_id,),
                ),
            )
            assert updated.context_references == (shared.scope_id,)
            now_direct = await runtime.context.for_scope(reader.scope_id).prepare(
                PrepareContextRequest(query="direct context evidence")
            )
            assert now_direct.status == "ready"

    asyncio.run(scenario())


def test_prepare_context_keeps_referenced_scope_eligible_when_local_recall_is_full() -> None:
    async def scenario() -> None:
        async with open_builtin_runtime(BuiltinConfig(database=SQLiteConfig())) as runtime:
            assert runtime.scopes is not None
            shared = await runtime.scopes.create(
                ScopeDraft(title="Shared", summary="Reusable evidence", idempotency_key="shared-full-recall")
            )
            reader = await runtime.scopes.create(
                ScopeDraft(
                    title="Reader",
                    summary="Reads shared evidence",
                    context_references=(shared.scope_id,),
                    idempotency_key="reader-full-recall",
                )
            )
            await runtime.memory.for_scope(reader.scope_id).remember(
                RememberMemoryRequest(
                    entries=tuple(
                        MemoryEntryInput(kind="fact", text=f"Candidate saturation local evidence {index}.")
                        for index in range(16)
                    )
                )
            )
            await runtime.memory.for_scope(shared.scope_id).remember(
                RememberMemoryRequest(
                    entries=(MemoryEntryInput(kind="fact", text="Candidate saturation shared evidence."),)
                )
            )

            prepared = await runtime.context.for_scope(reader.scope_id).prepare(
                PrepareContextRequest(query="candidate saturation evidence")
            )

            assert prepared.status == "ready"
            assert prepared.content is not None
            items = json.loads(prepared.content.splitlines()[-2])["items"]
            assert any(item["citation"].get("memory", {}).get("scope_id") == shared.scope_id for item in items)

    asyncio.run(scenario())


class _ConcurrentReranker:
    policy_id = "test.concurrent-rerank.v1"

    def __init__(self) -> None:
        self._entered = 0
        self._both_entered = asyncio.Event()

    async def rerank(self, query, candidates, limit, /) -> MemoryRerankDecision:
        self._entered += 1
        if self._entered == 2:
            self._both_entered.set()
        await self._both_entered.wait()
        return MemoryRerankDecision(
            selected_ranks=(1,),
            usage=InferenceUsage(requests=1),
        )


def test_same_scope_read_only_searches_do_not_serialize_reranking() -> None:
    async def scenario() -> None:
        reranker = _ConcurrentReranker()
        async with open_builtin_runtime(BuiltinConfig(), memory_reranker=reranker) as runtime:
            assert runtime.scopes is not None
            scope = await runtime.scopes.create(
                ScopeDraft(title="Parallel", summary="Concurrent read acceptance", idempotency_key="parallel-search")
            )
            memory = runtime.memory.for_scope(scope.scope_id)
            await memory.remember(
                RememberMemoryRequest(entries=(MemoryEntryInput(kind="fact", text="Parallel search fact."),))
            )

            first = asyncio.create_task(memory.search(SearchMemoryRequest(query="parallel", mode="fts", limit=1)))
            second = asyncio.create_task(memory.search(SearchMemoryRequest(query="parallel", mode="fts", limit=1)))
            pages = await asyncio.wait_for(asyncio.gather(first, second), timeout=5)

            assert all(page.rerank is not None for page in pages)

    asyncio.run(scenario())
