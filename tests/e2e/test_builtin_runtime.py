from __future__ import annotations

import asyncio
import json

from powercontext.builtin.artifacts.memory import MemoryCandidateRequest, MemoryEntryInput
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import (
    BuiltinConfig,
    CaptureSource,
    PrepareContextRequest,
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
