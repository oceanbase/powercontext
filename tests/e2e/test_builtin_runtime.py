from __future__ import annotations

import asyncio

from powercontext.builtin.artifacts.memory import MemoryCandidateRequest, MemoryEntryInput
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import (
    BuiltinConfig,
    CaptureSource,
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

            assert captured.sequence == 1
            assert flushed.current_cursor == captured.sequence
            assert flushed.memory_ref is not None
            assert tuple(hit.text for hit in found.hits) == ("PowerContext composes an atomic SQL provider.",)

    asyncio.run(scenario())
