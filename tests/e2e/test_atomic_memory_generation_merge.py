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

"""Source-driven reconciliation publishes a merge and preserves its exact inputs."""

from __future__ import annotations

import asyncio
from pathlib import Path

from powercontext.builtin.artifacts.atomic_memory import AtomicMemoryContent
from powercontext.builtin.artifacts.atomic_memory.extraction import (
    AtomicMemoryCandidate,
    AtomicMemoryEvidence,
    AtomicMemoryExtractionInput,
    AtomicMemoryExtractionOutput,
    AtomicMemoryGenerationPipeline,
)
from powercontext.builtin.artifacts.atomic_memory.reconciliation import (
    ATOMIC_MEMORY_RECONCILIATION_INSTRUCTIONS,
    AtomicMemoryArtifactEvidence,
    AtomicMemoryReconciliationInput,
    AtomicMemoryReconciliationOutput,
)
from powercontext.builtin.inference import GenerationResult, character_token_estimator
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import BuiltinConfig, RuntimeConfig, open_builtin_contexts
from powercontext.sources import SourceRef
from tests.e2e.dream_support import memory_source_text

SCOPE = "project"
CANARY = "Database rollout uses canary deployments."
PAUSE = "Database rollout pauses when error rate increases."
CONSOLIDATION = "Keep the database rollout canary and error rate rules together as one deployment policy."
POLICY = "Database rollout uses canary deployments and pauses when error rate increases."


class _PolicyExtractor:
    async def generate(self, request: AtomicMemoryExtractionInput, /):
        return GenerationResult(
            output=AtomicMemoryExtractionOutput(
                candidates=tuple(
                    AtomicMemoryCandidate(
                        kind="constraint",
                        text=POLICY if text == CONSOLIDATION else text,
                        evidence_ids=(item.evidence_id,),
                    )
                    for item in request.evidence
                    if (text := memory_source_text(item)) is not None
                )
            )
        )


class _PolicyReconciler:
    def __init__(self) -> None:
        self.requests: list[AtomicMemoryReconciliationInput] = []

    async def generate(self, request: AtomicMemoryReconciliationInput, /):
        self.requests.append(request)
        inputs = tuple(item for item in request.related if item.text in {CANARY, PAUSE})
        merge = request.proposal.text == POLICY and {item.text for item in inputs} == {CANARY, PAUSE}
        evidence_ids = tuple(
            dict.fromkeys(
                identifier
                for item in (request.proposal, *inputs)
                if merge or item is request.proposal
                for identifier in item.evidence_ids
            )
        )
        return GenerationResult(
            output=AtomicMemoryReconciliationOutput(
                action="merge" if merge else "create",
                compared_ids=tuple(item.item_id for item in request.related),
                target_ids=tuple(item.item_id for item in inputs) if merge else (),
                content=AtomicMemoryContent(kind=request.proposal.kind, text=request.proposal.text),
                evidence_ids=evidence_ids,
                reason="Combine the two existing rollout rules with the consolidation Source evidence."
                if merge
                else "Retain an independent rollout rule with its Source evidence.",
            )
        )


class _RevisionExtractor:
    async def generate(self, request: AtomicMemoryExtractionInput, /):
        return GenerationResult(
            output=AtomicMemoryExtractionOutput(
                candidates=tuple(
                    AtomicMemoryCandidate(kind="fact", text=text.split("\n", 1)[0], evidence_ids=(item.evidence_id,))
                    for item in request.evidence
                    if (text := memory_source_text(item)) is not None
                )
            )
        )


class _RevisionReconciler:
    def __init__(self) -> None:
        self.requests: list[AtomicMemoryReconciliationInput] = []

    async def generate(self, request: AtomicMemoryReconciliationInput, /):
        self.requests.append(request)
        target = next(iter(request.related), None)
        return GenerationResult(
            output=AtomicMemoryReconciliationOutput(
                action="create" if target is None else "merge" if request.proposal.original_refs else "revise",
                compared_ids=tuple(item.item_id for item in request.related),
                target_ids=() if target is None else (target.item_id,),
                content=AtomicMemoryContent(kind="fact", text=request.proposal.text),
                evidence_ids=request.proposal.evidence_ids
                if target is None
                else (*request.proposal.evidence_ids, *target.evidence_ids),
                reason="Apply the latest rollout budget value using its new Source and the published fact.",
            )
        )


class _SupportedCreateReconciler(_RevisionReconciler):
    async def generate(self, request: AtomicMemoryReconciliationInput, /):
        self.requests.append(request)
        return GenerationResult(
            output=AtomicMemoryReconciliationOutput(
                action="create",
                compared_ids=tuple(item.item_id for item in request.related),
                content=AtomicMemoryContent(kind="constraint", text=request.proposal.text),
                evidence_ids=tuple(item.evidence_id for item in request.evidence),
                reason="Keep the independent precheck rule with supporting budget facts without consuming them.",
            )
        )


def test_repeated_source_evolution_keeps_flush_bounded_and_exact_history_readable(tmp_path: Path) -> None:
    reconciler = _RevisionReconciler()
    estimator = character_token_estimator()
    pipeline = AtomicMemoryGenerationPipeline(
        extractor=_RevisionExtractor(), reconciler=reconciler, estimator=estimator
    )
    config = BuiltinConfig(
        database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'bounded-evolution.db'}"),
        runtime=RuntimeConfig(atomic_memory_related_mode="fts"),
    )

    async def scenario() -> None:
        async with open_builtin_contexts(config, candidate_pipeline=pipeline) as contexts:
            context = await contexts.get(SCOPE)
            memory = contexts.atomic_memory.for_scope(SCOPE)
            originals = []
            sources = []
            for position in range(1, 13):
                fact = f"Database rollout budget is {position} units."
                raw = f"{fact}\n" + "Raw rollout diagnostics. " * 250
                source = await contexts.records.capture_source(SCOPE, "content", f"budget-{position}", raw, {})
                sources.append((source, raw))
                result = await context.triggers.flush(limit=1)
                assert result.previous_cursor == position - 1
                assert result.current_cursor == result.high_watermark == position
                assert result.source_count == 1 and not result.remaining_work
                (record,) = (await memory.list()).items
                assert record.ref.revision == position
                assert record.artifact.content.text == fact
                assert record.artifact.lineage.sources == (
                    SourceRef(source_type="content", source_id=source.source_id),
                )
                if originals:
                    assert record.ref.artifact_id == originals[0].ref.artifact_id
                    assert record.artifact.lineage.artifacts == (originals[-1].ref,)
                originals.append(record)

            assert (await context.triggers.cursor()).sequence == len(originals)
            for original, (source, raw) in zip(originals, sources, strict=True):
                historical = await memory.get(original.ref.artifact_id, revision=original.ref.revision)
                assert historical.artifact == original.artifact
                stored = await contexts.records.get_source(SCOPE, "content", source.source_id)
                assert stored.content == raw

            # The default budget continues to admit a new Source and the compact
            # published fact while exact predecessor revisions grow independently.
            assert all(
                estimator.estimate(f"{ATOMIC_MEMORY_RECONCILIATION_INSTRUCTIONS}\n{request.model_dump_json()}")
                <= config.runtime.atomic_memory_input_tokens_limit
                for request in reconciler.requests
            )
            for request in reconciler.requests:
                (new_source,) = tuple(item for item in request.evidence if isinstance(item, AtomicMemoryEvidence))
                source, raw = sources[new_source.journal_position - 1]
                assert new_source.source_ref == SourceRef(source_type="content", source_id=source.source_id)
                assert memory_source_text(new_source) == raw
                assert request.proposal.evidence_ids == (new_source.evidence_id,)
                assert tuple(
                    item.artifact_ref for item in request.evidence if isinstance(item, AtomicMemoryArtifactEvidence)
                ) == tuple(read.ref for item in request.related for read in item.original_refs)
                for item in request.evidence:
                    if isinstance(item, AtomicMemoryArtifactEvidence):
                        original = originals[item.artifact_ref.revision - 1]
                        assert item.artifact_ref == original.ref
                        assert item.content.kind == original.artifact.content.kind
                        assert item.content.text == original.artifact.content.text
            assert not (await context.triggers.flush(limit=1)).processed

    asyncio.run(scenario())


def test_batched_reconciliation_keeps_published_evidence_fixed_when_working_content_changes(tmp_path: Path) -> None:
    reconciler = _RevisionReconciler()
    pipeline = AtomicMemoryGenerationPipeline(
        extractor=_RevisionExtractor(), reconciler=reconciler, estimator=character_token_estimator()
    )
    config = BuiltinConfig(
        database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'batched-evidence.db'}"),
        runtime=RuntimeConfig(atomic_memory_related_mode="fts", atomic_memory_comparison_batch_size=1),
    )

    async def scenario() -> None:
        async with open_builtin_contexts(config, candidate_pipeline=pipeline) as contexts:
            context = await contexts.get(SCOPE)
            memory = contexts.atomic_memory.for_scope(SCOPE)
            originals = await contexts.records.create_atomic_memories(
                SCOPE,
                (
                    {"kind": "fact", "text": "Database rollout budget is 1 units."},
                    {"kind": "fact", "text": "Database rollout budget is 2 units."},
                ),
            )
            published = {record.ref.artifact_id: record for record in (await memory.list()).items}
            await context.triggers.flush(limit=20)
            latest = "Database rollout budget is 3 units."
            source = await contexts.records.capture_source(SCOPE, "content", "budget-correction", latest, {})
            result = await context.triggers.flush(limit=1)
            assert result.current_cursor == result.high_watermark == source.position
            assert result.source_count == 1 and not result.remaining_work
            (continued,) = tuple(request for request in reconciler.requests if request.proposal.original_refs)
            assert continued.proposal.text == latest
            for evidence in continued.evidence:
                if isinstance(evidence, AtomicMemoryArtifactEvidence):
                    original = published[evidence.artifact_ref.artifact_id]
                    assert evidence.artifact_ref == original.ref
                    assert evidence.content.kind == original.artifact.content.kind
                    assert evidence.content.text == original.artifact.content.text
                    assert evidence.content.text != continued.proposal.text
                else:
                    assert evidence.source_ref == SourceRef(source_type="content", source_id=source.source_id)
                    assert memory_source_text(evidence) == latest
            (merged,) = (await memory.list()).items
            assert merged.artifact.content.text == latest
            assert merged.artifact.lineage.sources == (SourceRef(source_type="content", source_id=source.source_id),)
            assert len(merged.artifact.lineage.artifacts) == len(published)
            assert all(record.ref in merged.artifact.lineage.artifacts for record in published.values())
            for original in originals:
                assert (await memory.get(original.artifact_id)).state.state == "merged"

    asyncio.run(scenario())


def test_batched_create_can_cite_existing_artifacts_without_consuming_their_identities(tmp_path: Path) -> None:
    reconciler = _SupportedCreateReconciler()
    pipeline = AtomicMemoryGenerationPipeline(
        extractor=_RevisionExtractor(), reconciler=reconciler, estimator=character_token_estimator()
    )
    config = BuiltinConfig(
        database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'supported-create.db'}"),
        runtime=RuntimeConfig(atomic_memory_related_mode="fts", atomic_memory_comparison_batch_size=1),
    )

    async def scenario() -> None:
        async with open_builtin_contexts(config, candidate_pipeline=pipeline) as contexts:
            context = await contexts.get(SCOPE)
            memory = contexts.atomic_memory.for_scope(SCOPE)
            await contexts.records.create_atomic_memories(
                SCOPE,
                (
                    {"kind": "fact", "text": "Database rollout budget is 1 units."},
                    {"kind": "fact", "text": "Database rollout budget is 2 units."},
                ),
            )
            originals = (await memory.list()).items
            await context.triggers.flush(limit=20)
            latest = "Database rollout budget is checked before deployment."
            source = await contexts.records.capture_source(SCOPE, "content", "budget-precheck", latest, {})
            result = await context.triggers.flush(limit=1)
            assert result.current_cursor == result.high_watermark == source.position
            continued = tuple(request for request in reconciler.requests if len(request.proposal.evidence_ids) > 1)
            assert continued, "A later batch must reuse evidence cited by the independent working proposal."
            assert all(not request.proposal.original_refs for request in continued)
            (created,) = tuple(
                record for record in (await memory.list()).items if record.artifact.content.text == latest
            )
            assert created.ref.artifact_id not in {original.ref.artifact_id for original in originals}
            assert created.artifact.lineage.sources == (SourceRef(source_type="content", source_id=source.source_id),)
            assert len(created.artifact.lineage.artifacts) == len(originals)
            for original in originals:
                assert original.ref in created.artifact.lineage.artifacts
                assert await memory.get(original.ref.artifact_id) == original

    asyncio.run(scenario())


def test_source_flush_automatically_merges_existing_memories_and_preserves_history(tmp_path: Path) -> None:
    reconciler = _PolicyReconciler()
    pipeline = AtomicMemoryGenerationPipeline(
        extractor=_PolicyExtractor(), reconciler=reconciler, estimator=character_token_estimator()
    )
    config = BuiltinConfig(
        database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'generation-merge.db'}"),
        runtime=RuntimeConfig(atomic_memory_related_mode="fts"),
    )

    async def scenario() -> None:
        async with open_builtin_contexts(config, candidate_pipeline=pipeline) as contexts:
            context = await contexts.get(SCOPE)
            memory = contexts.atomic_memory.for_scope(SCOPE)
            originals = []
            for position, (source_id, text) in enumerate((("canary-rule", CANARY), ("pause-rule", PAUSE)), 1):
                source = await contexts.records.capture_source(SCOPE, "content", source_id, text, {})
                assert source.position == position
                flush = await context.triggers.flush(limit=1)
                assert flush.previous_cursor == position - 1
                assert flush.current_cursor == position
                original = next(item for item in (await memory.list()).items if item.artifact.content.text == text)
                assert original.state.state == "active"
                assert original.artifact.lineage.sources == (SourceRef(source_type="content", source_id=source_id),)
                originals.append(original)
            assert len((await memory.list()).items) == 2

            source = await contexts.records.capture_source(SCOPE, "content", "consolidate-rules", CONSOLIDATION, {})
            source_ref = SourceRef(source_type="content", source_id=source.source_id)
            result = await context.triggers.flush(limit=1)
            assert result.previous_cursor == 2
            assert result.current_cursor == result.high_watermark == source.position == 3
            assert result.source_count == 1
            assert result.processed and not result.remaining_work
            assert (await context.triggers.cursor()).sequence == 3

            (merged,) = (await memory.list()).items
            assert merged.ref.artifact_id not in {item.ref.artifact_id for item in originals}
            assert merged.ref.revision == 1
            assert merged.state.state == "active" and merged.state.merged_into_id is None
            assert merged.artifact.content.text == POLICY
            assert merged.artifact.content.creation is not None
            assert set(merged.artifact.content.creation.input_artifact_ids) == {
                item.ref.artifact_id for item in originals
            }
            assert merged.artifact.lineage.sources == (source_ref,)
            assert len(merged.artifact.lineage.artifacts) == len(originals)
            assert all(item.ref in merged.artifact.lineage.artifacts for item in originals)

            (comparison,) = tuple(request for request in reconciler.requests if request.proposal.text == POLICY)
            recalled = tuple(read.ref for item in comparison.related for read in item.original_refs)
            assert len(recalled) == len(originals)
            assert all(item.ref in recalled for item in originals)
            evidence = {item.evidence_id: item for item in comparison.evidence}
            (new_source,) = tuple(evidence[key] for key in comparison.proposal.evidence_ids)
            assert isinstance(new_source, AtomicMemoryEvidence)
            assert new_source.source_ref == source_ref
            assert memory_source_text(new_source) == CONSOLIDATION
            for original in originals:
                (related,) = tuple(item for item in comparison.related if original.as_read() in item.original_refs)
                (published,) = tuple(evidence[key] for key in related.evidence_ids)
                assert isinstance(published, AtomicMemoryArtifactEvidence)
                assert published.artifact_ref == original.ref
                assert published.content.kind == original.artifact.content.kind
                assert published.content.text == original.artifact.content.text
                current = await memory.get(original.ref.artifact_id)
                assert current.state.state == "merged"
                assert current.state.merged_into_id == merged.ref.artifact_id
                assert current.artifact == original.artifact
                assert (
                    await memory.get(original.ref.artifact_id, revision=original.ref.revision)
                ).artifact == original.artifact
                historical = await contexts.records.get_artifact_revision(
                    SCOPE, "atomic-memory", original.ref.artifact_id, original.ref.revision
                )
                assert historical.content["text"] == original.artifact.content.text
                assert historical.sources == original.artifact.lineage.sources

            assert tuple(hit.hit.artifact_ref for hit in (await memory.search("Database rollout")).hits) == (
                merged.ref,
            )
            snapshot = (await memory.list(states=("active", "merged"))).items
            assert len(snapshot) == len(originals) + 1
            assert all(record.ref in tuple(item.ref for item in snapshot) for record in (merged, *originals))
            idle = await context.triggers.flush(limit=1)
            assert idle.previous_cursor == idle.current_cursor == idle.high_watermark == 3
            assert idle.source_count == 0 and not idle.processed
            assert (await memory.list(states=("active", "merged"))).items == snapshot

        # A new runtime sees the committed merge, frozen inputs and consumed cursor.
        async with open_builtin_contexts(config) as contexts:
            context = await contexts.get(SCOPE)
            memory = contexts.atomic_memory.for_scope(SCOPE)
            assert (await memory.list()).items == (merged,)
            assert (await memory.list(states=("active", "merged"))).items == snapshot
            assert (await context.triggers.cursor()).sequence == 3
            assert not (await context.triggers.flush(limit=1)).processed
            assert tuple(hit.hit.artifact_ref for hit in (await memory.search("Database rollout")).hits) == (
                merged.ref,
            )

    asyncio.run(scenario())
