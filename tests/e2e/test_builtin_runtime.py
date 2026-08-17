from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator, Mapping
from contextlib import contextmanager

from powercontext.builtin.artifacts.memory import MemoryCandidateRequest, MemoryEntryInput, MemoryRerankDecision
from powercontext.builtin.inference import InferenceUsage
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import (
    BuiltinConfig,
    BuiltinRuntime,
    CaptureSource,
    PrepareContextRequest,
    RememberMemoryRequest,
    RuntimeCapabilities,
    SearchMemoryRequest,
    open_builtin_contexts,
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
        tracing = _RecordingTracing()
        async with open_builtin_runtime(
            BuiltinConfig(database=SQLiteConfig()),
            candidate_pipeline=_ContentCandidatePipeline(),
            tracing=tracing,
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
            assert not any(name == "memory.rerank" for name, _attributes in tracing.stages)

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


_TraceValue = str | bool | int | float


class _RecordingSpan:
    def __init__(self, attributes: dict[str, _TraceValue]) -> None:
        self.attributes = attributes

    def set_attributes(self, attributes: Mapping[str, _TraceValue], /) -> None:
        self.attributes.update(attributes)


class _RecordingTracing:
    def __init__(self) -> None:
        self.stages: list[tuple[str, dict[str, _TraceValue]]] = []

    @contextmanager
    def stage(
        self,
        name: str,
        *,
        attributes: Mapping[str, _TraceValue],
    ) -> Iterator[_RecordingSpan]:
        recorded = dict(attributes)
        self.stages.append((name, recorded))
        yield _RecordingSpan(recorded)


class _DynamicPolicyReranker:
    policy_id = "test.dynamic-rerank.v1"

    async def rerank(self, query, candidates, limit, /) -> MemoryRerankDecision:
        self.policy_id = "test.dynamic-rerank.v2"
        return MemoryRerankDecision(
            selected_ranks=(1,),
            usage=InferenceUsage(requests=1),
            discarded_rank_count=2,
        )


def test_traced_reranker_preserves_dynamic_policy_and_reports_rank_discards() -> None:
    async def scenario() -> None:
        tracing = _RecordingTracing()
        reranker = _DynamicPolicyReranker()
        async with open_builtin_runtime(
            BuiltinConfig(),
            memory_reranker=reranker,
            tracing=tracing,
        ) as runtime:
            memory = runtime.memory.for_scope("traced-reranker")
            await memory.remember(
                RememberMemoryRequest(entries=(MemoryEntryInput(kind="fact", text="Trace this fact."),))
            )
            page = await memory.search(SearchMemoryRequest(query="trace", mode="fts", limit=1))

        assert page.rerank is not None
        assert page.rerank.policy_id == "test.dynamic-rerank.v2"
        rerank_attributes = next(attributes for name, attributes in tracing.stages if name == "memory.rerank")
        assert rerank_attributes == {
            "powercontext.memory.rerank.candidate_count": 1,
            "powercontext.memory.rerank.limit": 1,
            "powercontext.memory.rerank.selected_count": 1,
            "powercontext.memory.rerank.discarded_rank_count": 2,
            "powercontext.memory.rerank.used_fallback": False,
        }

    asyncio.run(scenario())


def test_context_trace_is_stable_without_memory_or_experience_recall() -> None:
    async def scenario() -> None:
        tracing = _RecordingTracing()
        async with (
            open_builtin_contexts(BuiltinConfig()) as contexts,
            BuiltinRuntime(
                provider=contexts,
                capabilities=RuntimeCapabilities(memory_extraction=False, memory_search_modes=("fts",)),
                tracing=tracing,
            ) as runtime,
        ):
            prepared = await runtime.context.for_scope("empty-traced-context").prepare(
                PrepareContextRequest(query="private empty query")
            )

        assert prepared.status == "empty"
        stages = dict(tracing.stages)
        memory_search = stages["memory.search"]
        assert memory_search["powercontext.memory.search.requested_mode"] == "auto"
        assert isinstance(memory_search["powercontext.memory.search.limit"], int)
        assert memory_search["powercontext.memory.search.memory_present"] is False
        assert memory_search["powercontext.memory.search.result_count"] == 0
        experience_search = stages["experience.search"]
        assert experience_search["powercontext.experience.search.configured"] is False
        assert isinstance(experience_search["powercontext.experience.search.limit"], int)
        assert experience_search["powercontext.experience.search.result_count"] == 0
        assert stages["context.prepare"] == {
            "powercontext.context.prepare.memory_candidate_count": 0,
            "powercontext.context.prepare.experience_candidate_count": 0,
            "powercontext.context.prepare.selected_count": 0,
            "powercontext.context.prepare.status": "empty",
            "powercontext.context.prepare.content_bytes": 0,
        }

    asyncio.run(scenario())
