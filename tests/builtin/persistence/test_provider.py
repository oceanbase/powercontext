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

import pytest

from powercontext import ArtifactNotFoundError, SourceConflictError
from powercontext.builtin.artifacts.memory import MemoryCandidateRequest, MemoryEntryInput
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import BuiltinConfig, open_builtin_contexts
from powercontext.builtin.sources import ContentCapture, ContentSource, SourceCursor


class EchoCandidatePipeline:
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


class BlockingCandidatePipeline(EchoCandidatePipeline):
    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def extract(self, request: MemoryCandidateRequest, /) -> tuple[MemoryEntryInput, ...]:
        self.started.set()
        await self.release.wait()
        return await super().extract(request)


class StateSaveFailure(RuntimeError):
    pass


def test_provider_translates_repository_source_identity_conflicts() -> None:
    async def scenario() -> None:
        async with open_builtin_contexts(BuiltinConfig(database=SQLiteConfig())) as contexts:
            context = await contexts.get("project")
            await context.sources.capture(ContentCapture(source_id="turn-1", content="first"))

            with pytest.raises(SourceConflictError) as caught:
                await context.sources.capture(ContentCapture(source_id="turn-1", content="changed"))

            assert caught.value.field == "identity"

    asyncio.run(scenario())


def test_source_window_artifact_and_cursor_are_one_transaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        pipeline = EchoCandidatePipeline()
        async with open_builtin_contexts(
            BuiltinConfig(database=SQLiteConfig()),
            candidate_pipeline=pipeline,
        ) as contexts:
            context = await contexts.get("project")
            await context.sources.capture(
                ContentCapture(source_id="turn-1", content="Retry the complete atomic window.")
            )
            original_save = contexts.repositories.cursors.save

            async def fail_state_save(*args: object, **kwargs: object) -> None:
                del args, kwargs
                raise StateSaveFailure

            monkeypatch.setattr(contexts.repositories.cursors, "save", fail_state_save)
            with pytest.raises(StateSaveFailure):
                await context.triggers.flush(limit=10)

            assert await context.triggers.cursor() == SourceCursor()
            with pytest.raises(ArtifactNotFoundError):
                await context.artifacts.memory.head("memory")

            monkeypatch.setattr(contexts.repositories.cursors, "save", original_save)
            result = await context.triggers.flush(limit=10)
            assert result.previous_cursor == 0
            assert result.current_cursor == 1
            assert result.source_count == 1
            assert result.memory_ref is not None
            assert result.memory_ref.revision == 1
            assert await context.triggers.cursor() == SourceCursor(sequence=1)

    asyncio.run(scenario())


def test_concurrent_capture_and_flush_preserve_monotonic_idempotent_behavior() -> None:
    async def scenario() -> None:
        pipeline = EchoCandidatePipeline()
        async with open_builtin_contexts(
            BuiltinConfig(database=SQLiteConfig()),
            candidate_pipeline=pipeline,
        ) as contexts:
            context = await contexts.get("project")

            receipts = await asyncio.gather(
                *(
                    context.sources.capture(ContentCapture(source_id=f"turn-{index}", content=f"fact {index}"))
                    for index in range(12)
                )
            )
            assert sorted(sequence for _, sequence in receipts) == list(range(1, 13))

            first, second = await asyncio.gather(
                context.triggers.flush(limit=20),
                context.triggers.flush(limit=20),
            )
            assert sorted((first.source_count, second.source_count)) == [0, 12]
            assert await context.triggers.cursor() == SourceCursor(sequence=12)
            memory = await context.artifacts.memory.head("memory")
            assert memory.revision == 1
            assert len(await context.artifacts.memory.entries(memory)) == 12

    asyncio.run(scenario())


def test_candidate_inference_does_not_hold_the_database_transaction() -> None:
    async def scenario() -> None:
        pipeline = BlockingCandidatePipeline()
        async with open_builtin_contexts(
            BuiltinConfig(database=SQLiteConfig()),
            candidate_pipeline=pipeline,
        ) as contexts:
            context = await contexts.get("project")
            await context.sources.capture(ContentCapture(source_id="turn-1", content="first"))

            flushing = asyncio.create_task(context.triggers.flush(limit=10))
            await pipeline.started.wait()
            _, sequence = await asyncio.wait_for(
                context.sources.capture(ContentCapture(source_id="turn-2", content="second")),
                timeout=1,
            )
            assert sequence == 2

            pipeline.release.set()
            result = await flushing
            assert result.current_cursor == 1
            pending = await context.triggers.flush(limit=10)
            assert pending.previous_cursor == 1
            assert pending.high_watermark == 2
            assert pending.current_cursor == 2
            assert pending.source_count == 1

    asyncio.run(scenario())
