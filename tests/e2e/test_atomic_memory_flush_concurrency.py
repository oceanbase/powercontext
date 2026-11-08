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

"""Source capture can progress while a scoped Atomic Memory flush prepares."""

from __future__ import annotations

import asyncio
from pathlib import Path

from powercontext import PowerContext
from powercontext.builtin.artifacts.atomic_memory.extraction import (
    AtomicMemoryCandidate,
    AtomicMemoryExtractionInput,
    AtomicMemoryExtractionOutput,
    AtomicMemoryGenerationPipeline,
)
from powercontext.builtin.artifacts.atomic_memory.reconciliation import (
    AtomicMemoryReconciliationContent,
    AtomicMemoryReconciliationInput,
    AtomicMemoryReconciliationOutput,
)
from powercontext.builtin.inference import GenerationResult, character_token_estimator
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.records import ArtifactWrite
from powercontext.builtin.runtime import (
    BuiltinConfig,
    BuiltinRuntime,
    CaptureSource,
    MemoryFlushResult,
    RuntimeCapabilities,
    RuntimeConfig,
    open_builtin_contexts,
    open_builtin_runtime,
)
from powercontext.builtin.scope import ScopeDraft
from powercontext.sources import SourceSequenceLocator
from tests.e2e.dream_support import atomic_memory_pipeline, memory_source_text


class _PausedExtractor:
    def __init__(self) -> None:
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def generate(self, request: AtomicMemoryExtractionInput, /):
        self.entered.set()
        await self.release.wait()
        return GenerationResult(
            output=AtomicMemoryExtractionOutput(
                candidates=tuple(
                    AtomicMemoryCandidate(
                        kind="fact", text=text, description="Rollout budget", evidence_ids=(item.evidence_id,)
                    )
                    for item in request.evidence
                    if (text := memory_source_text(item)) is not None
                )
            )
        )


class _PausedReconciler:
    def __init__(self) -> None:
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.related_texts: list[tuple[str, ...]] = []

    async def generate(self, request: AtomicMemoryReconciliationInput, /):
        self.related_texts.append(tuple(item.text for item in request.related))
        self.entered.set()
        await self.release.wait()
        (target,) = request.related
        return GenerationResult(
            output=AtomicMemoryReconciliationOutput(
                action="revise",
                compared_ids=(target.item_id,),
                target_ids=(target.item_id,),
                content=AtomicMemoryReconciliationContent(
                    kind="fact", text=request.proposal.text, description="Updated rollout budget"
                ),
                evidence_ids=(*request.proposal.evidence_ids, *target.evidence_ids),
                reason="Update the supplied rollout budget using the new Source evidence.",
            )
        )


def _config(path: Path) -> BuiltinConfig:
    return BuiltinConfig(
        database=SQLiteConfig(url=f"sqlite+aiosqlite:///{path}"),
        runtime=RuntimeConfig(atomic_memory_related_mode="fts"),
    )


def test_capture_during_extraction_stays_outside_the_selected_window(tmp_path: Path) -> None:
    async def scenario() -> None:
        extractor = _PausedExtractor()
        async with open_builtin_runtime(
            _config(tmp_path / "capture.db"), candidate_pipeline=atomic_memory_pipeline(extractor)
        ) as runtime:
            assert runtime.scopes is not None and runtime.atomic_memory is not None
            scope = await runtime.scopes.create(
                ScopeDraft(title="Concurrent capture", summary="Flush isolation", idempotency_key="capture")
            )
            sources = runtime.sources.for_scope(scope.scope_id)
            memory = runtime.atomic_memory.for_scope(scope.scope_id)
            original = await sources.capture(CaptureSource(source_id="first", content="First fact.", metadata={}))
            flushing = asyncio.create_task(memory.flush(limit=1))
            try:
                await asyncio.wait_for(extractor.entered.wait(), timeout=3)
                later = await asyncio.wait_for(
                    sources.capture(CaptureSource(source_id="later", content="Later fact.", metadata={})), timeout=1
                )
                assert later.sequence == original.sequence + 1
                assert (await memory.cursor()).sequence == 0
            finally:
                extractor.release.set()
                first = await flushing
            assert first.previous_cursor == 0
            assert first.current_cursor == first.high_watermark == original.sequence
            (record,) = (await memory.list()).items
            assert record.artifact.content.text == "First fact."
            assert record.artifact.lineage.sources == (
                original.source_ref.model_copy(update={"locator": SourceSequenceLocator(sequence=original.sequence)}),
            )
            following = await memory.flush(limit=1)
            assert following.previous_cursor == original.sequence
            assert following.current_cursor == later.sequence
            assert {record.artifact.content.text for record in (await memory.list()).items} == {
                "First fact.",
                "Later fact.",
            }

    asyncio.run(scenario())


def test_overlapping_same_runtime_flushes_publish_the_window_once(tmp_path: Path) -> None:
    async def scenario() -> None:
        extractor = _PausedExtractor()
        async with open_builtin_runtime(
            _config(tmp_path / "same-runtime.db"), candidate_pipeline=atomic_memory_pipeline(extractor)
        ) as runtime:
            assert runtime.scopes is not None and runtime.atomic_memory is not None
            scope = await runtime.scopes.create(
                ScopeDraft(title="Concurrent flushes", summary="Window publication", idempotency_key="flushes")
            )
            source = await runtime.sources.for_scope(scope.scope_id).capture(
                CaptureSource(source_id="one", content="Publish once.", metadata={})
            )
            memory = runtime.atomic_memory.for_scope(scope.scope_id)
            first = asyncio.create_task(memory.flush(limit=1))
            second = None
            try:
                await asyncio.wait_for(extractor.entered.wait(), timeout=3)
                second = asyncio.create_task(memory.flush(limit=1))
            finally:
                extractor.release.set()
                first_result = await first
            assert second is not None
            second_result = await second
            assert first_result.processed
            assert not second_result.processed
            assert second_result.previous_cursor == second_result.current_cursor == source.sequence
            (record,) = (await memory.list()).items
            assert record.ref.revision == 1
            assert record.artifact.lineage.sources == (
                source.source_ref.model_copy(update={"locator": SourceSequenceLocator(sequence=source.sequence)}),
            )

    asyncio.run(scenario())


def test_overlapping_runtimes_recheck_the_cursor_before_publication(tmp_path: Path) -> None:
    async def scenario() -> None:
        first_extractor = _PausedExtractor()
        second_extractor = _PausedExtractor()
        config = _config(tmp_path / "cross-runtime.db")
        async with (
            open_builtin_runtime(config, candidate_pipeline=atomic_memory_pipeline(first_extractor)) as first_runtime,
            open_builtin_runtime(config, candidate_pipeline=atomic_memory_pipeline(second_extractor)) as second_runtime,
        ):
            assert first_runtime.scopes is not None
            assert first_runtime.atomic_memory is not None and second_runtime.atomic_memory is not None
            scope = await first_runtime.scopes.create(
                ScopeDraft(title="Shared database", summary="Cursor CAS", idempotency_key="shared")
            )
            source = await first_runtime.sources.for_scope(scope.scope_id).capture(
                CaptureSource(source_id="one", content="One shared fact.", metadata={})
            )
            memory = first_runtime.atomic_memory.for_scope(scope.scope_id)
            other_memory = second_runtime.atomic_memory.for_scope(scope.scope_id)
            first = asyncio.create_task(memory.flush(limit=1))
            second = asyncio.create_task(other_memory.flush(limit=1))
            try:
                await asyncio.wait_for(
                    asyncio.gather(first_extractor.entered.wait(), second_extractor.entered.wait()), timeout=3
                )
                first_extractor.release.set()
                first_result = await first
            finally:
                first_extractor.release.set()
                second_extractor.release.set()
                await first
                second_result = await second
            assert first_result.processed
            assert not second_result.processed
            assert (await memory.cursor()).sequence == source.sequence
            assert (await other_memory.cursor()).sequence == source.sequence
            (record,) = (await memory.list()).items
            assert record.ref.revision == 1
            assert record.artifact.lineage.sources == (
                source.source_ref.model_copy(update={"locator": SourceSequenceLocator(sequence=source.sequence)}),
            )

    asyncio.run(scenario())


def test_revision_during_reconciliation_retries_with_current_authority(tmp_path: Path) -> None:
    async def scenario() -> None:
        extractor = _PausedExtractor()
        extractor.release.set()
        reconciler = _PausedReconciler()
        pipeline = AtomicMemoryGenerationPipeline(
            extractor=extractor, reconciler=reconciler, estimator=character_token_estimator()
        )
        async with open_builtin_runtime(_config(tmp_path / "stale-read.db"), candidate_pipeline=pipeline) as runtime:
            assert runtime.scopes is not None and runtime.atomic_memory is not None
            scope = await runtime.scopes.create(
                ScopeDraft(title="Rollout budget", summary="Concurrent revision", idempotency_key="revision")
            )
            records = runtime.records.for_scope(scope.scope_id)
            original = await records.create_artifact(
                "atomic-memory", ArtifactWrite(content={"kind": "fact", "text": "Database rollout budget is 2 units."})
            )
            memory = runtime.atomic_memory.for_scope(scope.scope_id)
            await runtime.sources.for_scope(scope.scope_id).capture(
                CaptureSource(source_id="budget", content="Database rollout budget is 4 units.", metadata={})
            )
            flushing = asyncio.create_task(memory.flush(limit=100))
            try:
                await asyncio.wait_for(reconciler.entered.wait(), timeout=3)
                updated = await asyncio.wait_for(
                    records.replace_artifact(
                        "atomic-memory",
                        original.artifact_id,
                        '"revision:1"',
                        ArtifactWrite(content={"kind": "fact", "text": "Database rollout budget is 3 units."}),
                    ),
                    timeout=1,
                )
            finally:
                reconciler.release.set()
                result = await flushing
            assert result.processed
            assert reconciler.related_texts == [
                ("Database rollout budget is 2 units.",),
                ("Database rollout budget is 3 units.",),
            ]
            (current,) = (await memory.list()).items
            assert current.ref.artifact_id == original.artifact_id
            assert current.ref.revision == 3
            assert current.artifact.content.text == "Database rollout budget is 4 units."
            historical = await memory.get(original.artifact_id, revision=2)
            assert historical.artifact.content.text == updated.content["text"]

    asyncio.run(scenario())


def test_custom_triggers_keep_runtime_serialization(tmp_path: Path) -> None:
    class CustomTriggers:
        def __init__(self) -> None:
            self.entered = asyncio.Event()
            self.release = asyncio.Event()
            self.sequence = 0

        async def flush(self, *, limit: int, atomic_context=None) -> MemoryFlushResult:
            self.entered.set()
            previous = self.sequence
            await self.release.wait()
            self.sequence = previous + 1
            return MemoryFlushResult(
                previous_cursor=previous,
                current_cursor=self.sequence,
                high_watermark=self.sequence,
                source_count=1,
                memory_ref=None,
            )

    async def scenario() -> None:
        custom = CustomTriggers()
        async with open_builtin_contexts(_config(tmp_path / "custom.db")) as contexts:
            scope = await contexts.scopes.create(
                ScopeDraft(title="Custom provider", summary="Trigger serialization", idempotency_key="custom")
            )
            resolved_twice = asyncio.Event()

            class Provider:
                resolutions = 0

                async def get(self, scope_id: str, /):
                    context = await contexts.get(scope_id)
                    self.resolutions += 1
                    if self.resolutions == 2:
                        resolved_twice.set()
                    return PowerContext(sources=context.sources, artifacts=context.artifacts, triggers=custom)

            async with BuiltinRuntime(
                provider=Provider(),
                capabilities=RuntimeCapabilities(memory_extraction=True, memory_search_modes=("fts",)),
                scope_application=contexts.scopes,
                atomic_memory_application=contexts.atomic_memory,
            ) as runtime:
                assert runtime.atomic_memory is not None
                memory = runtime.atomic_memory.for_scope(scope.scope_id)
                first = asyncio.create_task(memory.flush(limit=1))
                second = None
                try:
                    await asyncio.wait_for(custom.entered.wait(), timeout=3)
                    second = asyncio.create_task(memory.flush(limit=1))
                    await asyncio.wait_for(resolved_twice.wait(), timeout=3)
                finally:
                    custom.release.set()
                    first_result = await first
                assert second is not None
                second_result = await second
                assert first_result.previous_cursor == 0 and first_result.current_cursor == 1
                assert second_result.previous_cursor == 1 and second_result.current_cursor == 2

    asyncio.run(scenario())
