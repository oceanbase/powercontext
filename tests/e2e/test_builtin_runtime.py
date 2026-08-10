from __future__ import annotations

import asyncio
import json

from powercontext.builtin.artifacts.memory import MemoryCandidateRequest, MemoryEntryInput, MemoryRerankDecision
from powercontext.builtin.inference import InferenceUsage
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import (
    BuiltinConfig,
    CaptureSource,
    PrepareContextRequest,
    RememberMemoryRequest,
    SearchMemoryRequest,
    open_builtin_runtime,
)
from powercontext.builtin.sources import ContentSource


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


def test_builtin_runtime_uses_the_selected_sqlite_database() -> None:
    async def scenario() -> None:
        async with open_builtin_runtime(
            BuiltinConfig(database=SQLiteConfig()),
            candidate_pipeline=_ContentCandidatePipeline(),
        ) as runtime:
            captured = await runtime.sources.for_scope("project").capture(
                CaptureSource(
                    source_id="turn-1",
                    content="PowerContext composes an atomic SQL provider.",
                    metadata={"origin": "e2e"},
                )
            )
            flushed = await runtime.memory.for_scope("project").flush()
            found = await runtime.memory.for_scope("project").search(SearchMemoryRequest(query="atomic SQL provider"))
            prepared = await runtime.context.for_scope("project").prepare(
                PrepareContextRequest(query="atomic SQL provider")
            )
            no_memory = await runtime.context.for_scope("empty-project").prepare(
                PrepareContextRequest(query="anything")
            )
            no_match = await runtime.context.for_scope("project").prepare(
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
            memory = runtime.memory.for_scope("parallel-search")
            await memory.remember(
                RememberMemoryRequest(entries=(MemoryEntryInput(kind="fact", text="Parallel search fact."),))
            )

            first = asyncio.create_task(memory.search(SearchMemoryRequest(query="parallel", mode="fts", limit=1)))
            second = asyncio.create_task(memory.search(SearchMemoryRequest(query="parallel", mode="fts", limit=1)))
            pages = await asyncio.wait_for(asyncio.gather(first, second), timeout=5)

            assert all(page.rerank is not None for page in pages)

    asyncio.run(scenario())
