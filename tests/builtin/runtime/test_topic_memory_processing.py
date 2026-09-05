# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");

from __future__ import annotations

import asyncio
import logging
import pickle
import threading
from collections import deque
from datetime import UTC, datetime
from functools import partial
from typing import Any, Generic, TypeVar, cast

import pytest
from pydantic import SecretStr

from powercontext.builtin.artifacts.topic_memory import (
    TOPIC_MEMORY_SOURCE_WINDOW_BINDING,
    PublishedTopicMemory,
    TopicMemory,
    TopicMemoryContent,
    TopicMemoryDraft,
    TopicMemoryProjectionError,
    TopicMemorySearchHit,
    TopicMemorySearchResult,
    prepare_topic_memory_projection,
)
from powercontext.builtin.artifacts.topic_memory.generation import (
    TopicMemoryEvidence,
    TopicMemoryEvolveOutput,
    TopicMemoryGenerationError,
    TopicMemoryGlobalOutput,
    TopicMemoryPlanItem,
    TopicMemoryPlannerOutput,
    TopicMemoryProbe,
    TopicMemoryProbeOutput,
    TopicMemoryProposal,
    TopicMemoryTemporaryOutput,
)
from powercontext.builtin.inference import (
    EmbeddingModel,
    EmbeddingResult,
    GenerationResult,
    InferenceUsage,
    TokenEstimator,
    character_token_estimator,
)
from powercontext.builtin.inference.usage import UsageReportingEmbeddingModel, UsageReportingStructuredGenerator
from powercontext.builtin.persistence.cursors import SourceCursorRepository
from powercontext.builtin.persistence.processing import ArtifactProcessingPendingRepository
from powercontext.builtin.persistence.sources import SourceRepository
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.persistence.sqlite.topic_memory_index import SQLiteTopicMemoryFTSIndex
from powercontext.builtin.persistence.supervision import ArtifactProcessingLeaseRepository
from powercontext.builtin.persistence.tables import BUILTIN_TABLES
from powercontext.builtin.persistence.topic_memory import TopicMemoryRepository
from powercontext.builtin.persistence.topic_memory_index import CompositeTopicMemoryIndex
from powercontext.builtin.runtime.artifact_processing import (
    ArtifactProcessingBinding,
    ArtifactProcessingSupervisor,
    ArtifactProcessingWaveKind,
    ArtifactProcessingWorkAssignment,
    ArtifactProcessingWorkerCompletion,
    ArtifactProcessingWorkerOutcome,
    SpawnArtifactProcessingWorkerLauncher,
)
from powercontext.builtin.runtime.composition import (
    BuiltinConfigurationError,
    _topic_memory_processing_bindings,
    open_builtin_contexts,
)
from powercontext.builtin.runtime.config import BuiltinConfig, InferenceConfig, RuntimeConfig
from powercontext.builtin.runtime.topic_memory_processing import (
    PreparedTopicMemoryOperation,
    TopicMemoryAtomicPublisher,
    TopicMemoryProcessor,
    TopicMemoryStageSet,
    TopicMemoryWindowSelector,
    TopicMemoryWorkerSpec,
    run_topic_memory_worker,
)
from powercontext.builtin.sources import ContentCapture, SourceCursor
from powercontext.builtin.statistics import ModelUsagePurpose
from powercontext.errors import RevisionConflictError
from powercontext.sources import SourceMaterialization
from tests.builtin.persistence.contract import SOURCE_ADAPTERS, NoteSource

OutputT = TypeVar("OutputT")


class _QueueGenerator(Generic[OutputT]):
    def __init__(self, *outputs: OutputT) -> None:
        self.outputs = deque(outputs)
        self.inputs: list[object] = []

    async def generate(self, value, /) -> GenerationResult[OutputT]:
        self.inputs.append(value)
        if not self.outputs:
            raise AssertionError("unexpected generation stage")  # noqa: TRY003
        return GenerationResult(output=self.outputs.popleft())


class _BarrierGenerator(Generic[OutputT]):
    def __init__(self, output: OutputT) -> None:
        self.output = output
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def generate(self, _value, /) -> GenerationResult[OutputT]:
        self.started.set()
        await self.release.wait()
        return GenerationResult(output=self.output)


class _UsageEmbeddingModel:
    profile = None

    async def embed(self, texts: tuple[str, ...], /) -> EmbeddingResult:
        return EmbeddingResult(
            vectors=tuple((1.0, 0.0) for _ in texts),
            usage=InferenceUsage(requests=1, input_tokens=len(texts)),
        )


class _FenceRevokingTopicRepository(TopicMemoryRepository):
    def __init__(self, delegate: TopicMemoryRepository, leases: ArtifactProcessingLeaseRepository) -> None:
        super().__init__(artifacts=delegate.artifacts, index=delegate.index)
        self._leases = leases

    async def publish_create(self, connection, scope_id, artifact_id, draft, projection, /):
        published = await super().publish_create(connection, scope_id, artifact_id, draft, projection)
        await self._leases.start_single_process_term(connection, "replacement-holder")
        return published


class _ProcessorLauncher:
    def __init__(self, processor: TopicMemoryProcessor) -> None:
        self.processor = processor

    async def start(self, assignment: ArtifactProcessingWorkAssignment):
        return _ProcessorHandle(self.processor, assignment)


class _ProcessorHandle:
    def __init__(self, processor: TopicMemoryProcessor, assignment: ArtifactProcessingWorkAssignment) -> None:
        self.processor = processor
        self.assignment = assignment

    async def wait(self) -> ArtifactProcessingWorkerCompletion:
        return await self.processor.process(self.assignment)

    async def terminate(self) -> None:
        return None


def _content(label: str, term: str = "durable") -> TopicMemoryContent:
    return TopicMemoryContent(
        title=f"{label} topic",
        summary=f"{label} state is durable",
        detail=f"# {label}\n\nEvidence contains {term} state.",
    )


def _assignment(fence, *, generation: int | None = None, through: int = 1) -> ArtifactProcessingWorkAssignment:
    return ArtifactProcessingWorkAssignment(
        binding_name=TOPIC_MEMORY_SOURCE_WINDOW_BINDING,
        scope_id="scope-a",
        source_after=0,
        source_through=through,
        wave_target=through,
        claimed_flush_generation=1,
        cursor_generation=generation,
        wave_kind=ArtifactProcessingWaveKind.EXPLICIT,
        fence=fence,
        worker_id="worker-a",
    )


def _stages(
    *, probe, global_output, planner=None, evolve=None, temporary=None, reconcile=None, estimator=None, limit=100_000
):
    unexpected = _QueueGenerator()
    return TopicMemoryStageSet(
        probe=_QueueGenerator(probe),
        global_evolver=_QueueGenerator(global_output),
        planner=unexpected if planner is None else _QueueGenerator(planner),
        evolver=unexpected if evolve is None else _QueueGenerator(*evolve),
        temporary=unexpected if temporary is None else _QueueGenerator(*temporary),
        reconciler=unexpected if reconcile is None else _QueueGenerator(*reconcile),
        estimator=character_token_estimator() if estimator is None else estimator,
        input_tokens_limit=limit,
    )


async def _repositories(config: SQLiteConfig | None = None):
    index = CompositeTopicMemoryIndex(SQLiteTopicMemoryFTSIndex())
    profile = SQLiteProfile.open(SQLiteConfig() if config is None else config, tables=BUILTIN_TABLES + index.tables)
    opened = await profile.__aenter__()
    topics = TopicMemoryRepository(index=index)
    async with opened.database.transaction() as connection:
        await topics.initialize(connection)
    return profile, opened, SourceRepository(SOURCE_ADAPTERS), topics


def test_processor_create_and_noop_advance_cursor_atomically() -> None:
    async def scenario() -> None:
        manager, profile, sources, topics = await _repositories()
        try:
            leases = ArtifactProcessingLeaseRepository()
            async with profile.database.transaction() as connection:
                stored = await sources.add(
                    connection,
                    "scope-a",
                    NoteSource(name="note-1", materialization=SourceMaterialization.CAPTURED, body="new topic"),
                )
                term = await leases.start_single_process_term(connection, "holder")
            publisher = TopicMemoryAtomicPublisher(profile.database, sources, topics, leases=leases)
            proposals = (
                TopicMemoryProposal(
                    content=TopicMemoryContent(title="alpha", summary="bravo", detail="charlie"),
                    evidence_ids=("evidence-0001",),
                ),
                TopicMemoryProposal(
                    content=TopicMemoryContent(title="delta", summary="echo", detail="foxtrot"),
                    evidence_ids=("evidence-0001",),
                ),
            )
            artifact_ids = iter(("topic-created-a", "topic-created-b"))
            stages = _stages(
                probe=TopicMemoryProbeOutput(
                    probes=(TopicMemoryProbe(query="created", evidence_ids=("evidence-0001",)),)
                ),
                global_output=TopicMemoryGlobalOutput(proposals=proposals),
            )
            processor = TopicMemoryProcessor(
                database=profile.database,
                sources=sources,
                topics=topics,
                stages=stages,
                publisher=publisher,
                id_factory=lambda: next(artifact_ids),
            )

            assert (await processor.process(_assignment(term.fence("single-process")))).outcome.value == "succeeded"
            async with profile.database.transaction() as connection:
                page = await topics.browse_current(connection, "scope-a", limit=10)
                cursor = await SourceCursorRepository().load(connection, "scope-a", TOPIC_MEMORY_SOURCE_WINDOW_BINDING)
            assert {item.artifact_ref.artifact_id for item in page} == {"topic-created-a", "topic-created-b"}
            assert all(item.source_count == 1 for item in page)
            assert cursor is not None and cursor.cursor.sequence == stored.journal_position
            probe_input = stages.probe.inputs[0]
            assert "note-1" not in probe_input.model_dump_json()
        finally:
            await manager.__aexit__(None, None, None)

    asyncio.run(scenario())


def test_processor_zero_probe_noop_and_exact_history_update() -> None:
    async def scenario() -> None:
        manager, profile, sources, topics = await _repositories()
        try:
            leases = ArtifactProcessingLeaseRepository()
            async with profile.database.transaction() as connection:
                first_source = await sources.add(
                    connection,
                    "scope-a",
                    NoteSource(name="note-1", materialization=SourceMaterialization.CAPTURED, body="legacy zircon"),
                )
                current = await topics.publish_create(
                    connection,
                    "scope-a",
                    "topic-history",
                    TopicMemoryDraft(content=_content("legacy", "zircon"), sources=(first_source.ref,)),
                    prepare_topic_memory_projection(_content("legacy", "zircon")),
                )
                second_source = await sources.add(
                    connection,
                    "scope-a",
                    NoteSource(name="note-2", materialization=SourceMaterialization.CAPTURED, body="updated zircon"),
                )
                term = await leases.start_single_process_term(connection, "holder")
                await SourceCursorRepository().save(
                    connection,
                    "scope-a",
                    TOPIC_MEMORY_SOURCE_WINDOW_BINDING,
                    SourceCursor(sequence=1),
                    expected_generation=None,
                )
            updated = TopicMemoryProposal(
                candidate_id="candidate-0001",
                content=_content("updated", "zircon"),
                evidence_ids=("evidence-0001",),
            )
            processor = TopicMemoryProcessor(
                database=profile.database,
                sources=sources,
                topics=topics,
                stages=_stages(
                    probe=TopicMemoryProbeOutput(
                        probes=(TopicMemoryProbe(query="zircon", evidence_ids=("evidence-0001",)),)
                    ),
                    global_output=TopicMemoryGlobalOutput(proposals=(updated,)),
                ),
                publisher=TopicMemoryAtomicPublisher(profile.database, sources, topics, leases=leases),
            )
            assignment = ArtifactProcessingWorkAssignment(
                binding_name=TOPIC_MEMORY_SOURCE_WINDOW_BINDING,
                scope_id="scope-a",
                source_after=1,
                source_through=2,
                wave_target=2,
                claimed_flush_generation=1,
                cursor_generation=1,
                wave_kind=ArtifactProcessingWaveKind.EXPLICIT,
                fence=term.fence("single-process"),
                worker_id="worker-update",
            )

            assert (await processor.process(assignment)).outcome.value == "succeeded"
            async with profile.database.transaction() as connection:
                revised = await topics.get_exact(
                    connection,
                    "scope-a",
                    current.topic.as_ref().model_copy(update={"revision": 2}),
                )
                cursor = await SourceCursorRepository().load(connection, "scope-a", TOPIC_MEMORY_SOURCE_WINDOW_BINDING)
            assert revised.topic.content.title == "updated topic"
            assert revised.topic.lineage.sources == (second_source.ref,)
            assert revised.topic.lineage.artifacts == (current.topic.as_ref(),)
            assert cursor is not None and cursor.cursor.sequence == 2

            async with profile.database.transaction() as connection:
                third_source = await sources.add(
                    connection,
                    "scope-a",
                    NoteSource(name="note-3", materialization=SourceMaterialization.CAPTURED, body="nothing durable"),
                )
            noop = TopicMemoryProcessor(
                database=profile.database,
                sources=sources,
                topics=topics,
                stages=_stages(
                    probe=TopicMemoryProbeOutput(probes=()),
                    global_output=TopicMemoryGlobalOutput(),
                ),
                publisher=TopicMemoryAtomicPublisher(profile.database, sources, topics, leases=leases),
            )
            noop_assignment = ArtifactProcessingWorkAssignment(
                binding_name=TOPIC_MEMORY_SOURCE_WINDOW_BINDING,
                scope_id="scope-a",
                source_after=2,
                source_through=third_source.journal_position,
                wave_target=third_source.journal_position,
                claimed_flush_generation=1,
                cursor_generation=2,
                wave_kind=ArtifactProcessingWaveKind.EXPLICIT,
                fence=term.fence("single-process"),
                worker_id="worker-noop",
            )
            assert (await noop.process(noop_assignment)).outcome.value == "succeeded"
            async with profile.database.transaction() as connection:
                cursor = await SourceCursorRepository().load(connection, "scope-a", TOPIC_MEMORY_SOURCE_WINDOW_BINDING)
                revisions = await topics.artifacts.revisions(connection, "scope-a", "topic-memory", "topic-history")
            assert cursor is not None and cursor.cursor.sequence == 3
            assert len(revisions) == 2
        finally:
            await manager.__aexit__(None, None, None)

    asyncio.run(scenario())


def test_global_ambiguity_falls_back_to_planner_and_temporary_topics() -> None:
    async def scenario() -> None:
        manager, profile, sources, topics = await _repositories()
        try:
            leases = ArtifactProcessingLeaseRepository()
            async with profile.database.transaction() as connection:
                await sources.add(
                    connection,
                    "scope-a",
                    NoteSource(name="note-1", materialization=SourceMaterialization.CAPTURED, body="oversized"),
                )
                term = await leases.start_single_process_term(connection, "holder")
            direct_too_large = TokenEstimator(
                character_token_estimator().profile,
                lambda text: 100 if '"work_id"' in text and '"temporary":[]' in text else 1,
            )
            proposal = TopicMemoryProposal(content=_content("flattened"), evidence_ids=("evidence-0001",))
            stages = _stages(
                probe=TopicMemoryProbeOutput(
                    probes=(TopicMemoryProbe(query="oversized", evidence_ids=("evidence-0001",)),)
                ),
                global_output=TopicMemoryGlobalOutput(ambiguous=True),
                planner=TopicMemoryPlannerOutput(items=(TopicMemoryPlanItem(probe_ids=("probe-0001",)),)),
                evolve=(TopicMemoryEvolveOutput(proposal=proposal),),
                temporary=(TopicMemoryTemporaryOutput(proposals=(proposal,)),),
                estimator=direct_too_large,
                limit=10,
            )
            processor = TopicMemoryProcessor(
                database=profile.database,
                sources=sources,
                topics=topics,
                stages=stages,
                publisher=TopicMemoryAtomicPublisher(profile.database, sources, topics, leases=leases),
                id_factory=lambda: "topic-temp",
            )

            completion = await processor.process(_assignment(term.fence("single-process")))

            assert completion.outcome.value == "succeeded"
            assert len(stages.temporary.inputs) == 1
            assert len(stages.evolver.inputs) == 1
        finally:
            await manager.__aexit__(None, None, None)

    asyncio.run(scenario())


def test_temporary_topics_plus_exact_history_fail_closed_when_flattened_input_is_oversized() -> None:
    async def scenario() -> None:
        manager, profile, sources, topics = await _repositories()
        try:
            leases = ArtifactProcessingLeaseRepository()
            async with profile.database.transaction() as connection:
                original = await sources.add(
                    connection,
                    "scope-a",
                    NoteSource(name="original", materialization=SourceMaterialization.CAPTURED, body="zircon original"),
                )
                await topics.publish_create(
                    connection,
                    "scope-a",
                    "topic-history",
                    TopicMemoryDraft(content=_content("history", "zircon"), sources=(original.ref,)),
                    prepare_topic_memory_projection(_content("history", "zircon")),
                )
                updated = await sources.add(
                    connection,
                    "scope-a",
                    NoteSource(name="updated", materialization=SourceMaterialization.CAPTURED, body="zircon update"),
                )
                term = await leases.start_single_process_term(connection, "holder")
                await SourceCursorRepository().save(
                    connection,
                    "scope-a",
                    TOPIC_MEMORY_SOURCE_WINDOW_BINDING,
                    SourceCursor(sequence=original.journal_position),
                    expected_generation=None,
                )
            estimator = TokenEstimator(
                character_token_estimator().profile,
                lambda text: 100 if '"work_id"' in text and '"temporary"' in text else 1,
            )
            temporary = TopicMemoryProposal(content=_content("temporary"), evidence_ids=("evidence-0001",))
            stages = _stages(
                probe=TopicMemoryProbeOutput(
                    probes=(TopicMemoryProbe(query="zircon", evidence_ids=("evidence-0001",)),)
                ),
                global_output=TopicMemoryGlobalOutput(ambiguous=True),
                planner=TopicMemoryPlannerOutput(
                    items=(TopicMemoryPlanItem(probe_ids=("probe-0001",), candidate_id="candidate-0001"),)
                ),
                temporary=(TopicMemoryTemporaryOutput(proposals=(temporary,)),),
                estimator=estimator,
                limit=10,
            )
            processor = TopicMemoryProcessor(
                database=profile.database,
                sources=sources,
                topics=topics,
                stages=stages,
                publisher=TopicMemoryAtomicPublisher(profile.database, sources, topics, leases=leases),
            )
            assignment = ArtifactProcessingWorkAssignment(
                binding_name=TOPIC_MEMORY_SOURCE_WINDOW_BINDING,
                scope_id="scope-a",
                source_after=original.journal_position,
                source_through=updated.journal_position,
                wave_target=updated.journal_position,
                claimed_flush_generation=1,
                cursor_generation=1,
                wave_kind=ArtifactProcessingWaveKind.EXPLICIT,
                fence=term.fence("single-process"),
                worker_id="worker-oversized",
            )

            with pytest.raises(TopicMemoryGenerationError, match="input_budget_exceeded"):
                await processor.process(assignment)
            async with profile.database.transaction() as connection:
                cursor = await SourceCursorRepository().load(connection, "scope-a", TOPIC_MEMORY_SOURCE_WINDOW_BINDING)
                revisions = await topics.artifacts.revisions(
                    connection,
                    "scope-a",
                    "topic-memory",
                    "topic-history",
                )
            assert cursor is not None and cursor.cursor.sequence == 1
            assert len(revisions) == 1
        finally:
            await manager.__aexit__(None, None, None)

    asyncio.run(scenario())


def test_atomic_publisher_rolls_back_first_update_when_second_head_cas_fails() -> None:
    async def scenario() -> None:
        manager, profile, sources, topics = await _repositories()
        try:
            leases = ArtifactProcessingLeaseRepository()
            async with profile.database.transaction() as connection:
                source = await sources.add(
                    connection,
                    "scope-a",
                    NoteSource(name="note-1", materialization=SourceMaterialization.CAPTURED, body="evidence"),
                )
                term = await leases.start_single_process_term(connection, "holder")
                first = await topics.publish_create(
                    connection,
                    "scope-a",
                    "topic-a",
                    TopicMemoryDraft(content=_content("a"), sources=(source.ref,)),
                    prepare_topic_memory_projection(_content("a")),
                )
                second = await topics.publish_create(
                    connection,
                    "scope-a",
                    "topic-b",
                    TopicMemoryDraft(content=_content("b"), sources=(source.ref,)),
                    prepare_topic_memory_projection(_content("b")),
                )
            async with profile.database.transaction() as connection:
                await topics.publish_revision(
                    connection,
                    "scope-a",
                    second.topic,
                    TopicMemoryDraft(content=_content("b2"), sources=(source.ref,)),
                    prepare_topic_memory_projection(_content("b2")),
                )
            operations = tuple(
                PreparedTopicMemoryOperation(
                    proposal_id=f"proposal-{index}",
                    artifact_id=current.topic.artifact_id,
                    current=current.topic,
                    draft=TopicMemoryDraft(
                        content=_content(label),
                        sources=(source.ref,),
                        artifacts=(current.topic.as_ref(),),
                    ),
                    projection=prepare_topic_memory_projection(_content(label)),
                )
                for index, (current, label) in enumerate(((first, "a2"), (second, "b-stale")), start=1)
            )
            publisher = TopicMemoryAtomicPublisher(profile.database, sources, topics, leases=leases)

            with pytest.raises(RevisionConflictError, match="revision"):
                await publisher.publish(
                    _assignment(term.fence("single-process")),
                    {"evidence-0001": source},
                    operations,
                )
            async with profile.database.transaction() as connection:
                first_revisions = await topics.artifacts.revisions(connection, "scope-a", "topic-memory", "topic-a")
                cursor = await SourceCursorRepository().load(connection, "scope-a", TOPIC_MEMORY_SOURCE_WINDOW_BINDING)
            assert len(first_revisions) == 1
            assert cursor is None
        finally:
            await manager.__aexit__(None, None, None)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("conflict", "expected"),
    (
        ("cursor", ArtifactProcessingWorkerOutcome.CURSOR_CONFLICT),
        ("lease", ArtifactProcessingWorkerOutcome.LEADERSHIP_LOST),
    ),
)
def test_model_work_outside_transaction_cannot_cross_cursor_or_fence_conflict(
    tmp_path,
    conflict: str,
    expected: ArtifactProcessingWorkerOutcome,
) -> None:
    async def scenario() -> None:
        manager, profile, sources, topics = await _repositories(
            SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / f'{conflict}.db'}")
        )
        try:
            leases = ArtifactProcessingLeaseRepository()
            async with profile.database.transaction() as connection:
                await sources.add(
                    connection,
                    "scope-a",
                    NoteSource(name="note-1", materialization=SourceMaterialization.CAPTURED, body="barrier"),
                )
                term = await leases.start_single_process_term(connection, "holder")
            global_stage = _BarrierGenerator(
                TopicMemoryGlobalOutput(
                    proposals=(TopicMemoryProposal(content=_content("blocked"), evidence_ids=("evidence-0001",)),)
                )
            )
            stages = _stages(
                probe=TopicMemoryProbeOutput(
                    probes=(TopicMemoryProbe(query="blocked", evidence_ids=("evidence-0001",)),)
                ),
                global_output=TopicMemoryGlobalOutput(),
            )
            stages = TopicMemoryStageSet(
                probe=stages.probe,
                global_evolver=global_stage,
                planner=stages.planner,
                evolver=stages.evolver,
                temporary=stages.temporary,
                reconciler=stages.reconciler,
                estimator=stages.estimator,
                input_tokens_limit=stages.input_tokens_limit,
            )
            processor = TopicMemoryProcessor(
                database=profile.database,
                sources=sources,
                topics=topics,
                stages=stages,
                publisher=TopicMemoryAtomicPublisher(profile.database, sources, topics, leases=leases),
                id_factory=lambda: "topic-blocked",
            )
            task = asyncio.create_task(processor.process(_assignment(term.fence("single-process"))))
            await asyncio.wait_for(global_stage.started.wait(), timeout=2)
            async with profile.database.transaction() as connection:
                if conflict == "cursor":
                    await SourceCursorRepository().save(
                        connection,
                        "scope-a",
                        TOPIC_MEMORY_SOURCE_WINDOW_BINDING,
                        SourceCursor(sequence=0),
                        expected_generation=None,
                    )
                else:
                    await leases.start_single_process_term(connection, "replacement-holder")
            global_stage.release.set()

            assert (await task).outcome is expected
            async with profile.database.transaction() as connection:
                assert await topics.browse_current(connection, "scope-a", limit=10) == ()
                cursor = await SourceCursorRepository().load(connection, "scope-a", TOPIC_MEMORY_SOURCE_WINDOW_BINDING)
            if conflict == "cursor":
                assert cursor is not None and cursor.cursor.sequence == 0
            else:
                assert cursor is None
        finally:
            global_stage.release.set()
            await manager.__aexit__(None, None, None)

    asyncio.run(scenario())


def test_tail_fence_recheck_rolls_back_topic_and_cursor() -> None:
    async def scenario() -> None:
        manager, profile, sources, topics = await _repositories()
        try:
            leases = ArtifactProcessingLeaseRepository()
            async with profile.database.transaction() as connection:
                await sources.add(
                    connection,
                    "scope-a",
                    NoteSource(name="note-1", materialization=SourceMaterialization.CAPTURED, body="tail fence"),
                )
                term = await leases.start_single_process_term(connection, "holder")
            revoking = _FenceRevokingTopicRepository(topics, leases)
            processor = TopicMemoryProcessor(
                database=profile.database,
                sources=sources,
                topics=revoking,
                stages=_stages(
                    probe=TopicMemoryProbeOutput(
                        probes=(TopicMemoryProbe(query="tail fence", evidence_ids=("evidence-0001",)),)
                    ),
                    global_output=TopicMemoryGlobalOutput(
                        proposals=(TopicMemoryProposal(content=_content("tail"), evidence_ids=("evidence-0001",)),)
                    ),
                ),
                publisher=TopicMemoryAtomicPublisher(profile.database, sources, revoking, leases=leases),
                id_factory=lambda: "topic-tail",
            )

            completion = await processor.process(_assignment(term.fence("single-process")))

            assert completion.outcome is ArtifactProcessingWorkerOutcome.LEADERSHIP_LOST
            async with profile.database.transaction() as connection:
                assert await topics.browse_current(connection, "scope-a", limit=10) == ()
                assert (
                    await SourceCursorRepository().load(connection, "scope-a", TOPIC_MEMORY_SOURCE_WINDOW_BINDING)
                    is None
                )
        finally:
            await manager.__aexit__(None, None, None)

    asyncio.run(scenario())


def test_invalid_evidence_and_projection_never_advance_cursor() -> None:
    async def scenario() -> None:
        manager, profile, sources, topics = await _repositories()
        try:
            leases = ArtifactProcessingLeaseRepository()
            async with profile.database.transaction() as connection:
                source = await sources.add(
                    connection,
                    "scope-a",
                    NoteSource(name="in-scope", materialization=SourceMaterialization.CAPTURED, body="allowed"),
                )
                foreign = await sources.add(
                    connection,
                    "scope-b",
                    NoteSource(name="foreign", materialization=SourceMaterialization.CAPTURED, body="foreign"),
                )
                term = await leases.start_single_process_term(connection, "holder")
            publisher = TopicMemoryAtomicPublisher(profile.database, sources, topics, leases=leases)
            assignment = _assignment(term.fence("single-process"))
            content = _content("valid")
            invalid_evidence = PreparedTopicMemoryOperation(
                proposal_id="proposal-a",
                artifact_id="topic-a",
                current=None,
                draft=TopicMemoryDraft(content=content, sources=(foreign.ref,)),
                projection=prepare_topic_memory_projection(content),
            )
            with pytest.raises(TopicMemoryGenerationError, match="invalid_evidence"):
                await publisher.publish(assignment, {"evidence-0001": source}, (invalid_evidence,))

            invalid_projection = PreparedTopicMemoryOperation(
                proposal_id="proposal-b",
                artifact_id="topic-b",
                current=None,
                draft=TopicMemoryDraft(content=content, sources=(source.ref,)),
                projection=prepare_topic_memory_projection(_content("different")),
            )
            with pytest.raises(TopicMemoryProjectionError):
                await publisher.publish(assignment, {"evidence-0001": source}, (invalid_projection,))

            async with profile.database.transaction() as connection:
                assert await topics.browse_current(connection, "scope-a", limit=10) == ()
                assert (
                    await SourceCursorRepository().load(connection, "scope-a", TOPIC_MEMORY_SOURCE_WINDOW_BINDING)
                    is None
                )
        finally:
            await manager.__aexit__(None, None, None)

    asyncio.run(scenario())


def test_reconciliation_cannot_swap_existing_topic_slots() -> None:
    stages = _stages(probe=TopicMemoryProbeOutput(), global_output=TopicMemoryGlobalOutput())
    processor = TopicMemoryProcessor(
        database=cast(Any, None),
        sources=cast(Any, None),
        topics=cast(Any, None),
        stages=stages,
        publisher=cast(Any, None),
    )
    evidence = (
        TopicMemoryEvidence(evidence_id="e1", source_type="note", content="first"),
        TopicMemoryEvidence(evidence_id="e2", source_type="note", content="second"),
    )
    inputs = (
        TopicMemoryProposal(proposal_id="p1", candidate_id="c1", content=_content("first"), evidence_ids=("e1",)),
        TopicMemoryProposal(proposal_id="p2", candidate_id="c2", content=_content("second"), evidence_ids=("e2",)),
    )
    outputs = (
        inputs[0].model_copy(update={"candidate_id": "c2"}),
        inputs[1].model_copy(update={"candidate_id": "c1"}),
    )

    with pytest.raises(TopicMemoryGenerationError, match="identity_merge"):
        processor._validate_reconciliation(
            inputs,
            outputs,
            cast(Any, {"c1": object(), "c2": object()}),
            evidence,
            allowed_targets={"c1", "c2"},
        )


def test_history_selection_applies_threshold_floor_cap_and_stable_exact_binding() -> None:
    async def scenario() -> None:
        manager, profile, sources, topics = await _repositories()
        try:

            def published(artifact_id: str, revision: int = 1) -> PublishedTopicMemory:
                topic = TopicMemory(artifact_id=artifact_id, revision=revision, content=_content(artifact_id))
                return PublishedTopicMemory(
                    topic=topic,
                    published_at=datetime(2026, 1, 1, tzinfo=UTC),
                    is_current=True,
                    current_artifact=topic.as_ref(),
                )

            stored = {
                name: published(name)
                for name in (
                    "alpha",
                    "zulu",
                    "low-1",
                    "low-2",
                    "low-3",
                    *(f"cap-{index:02d}" for index in range(25)),
                )
            }

            class FakeTopics:
                async def search(self, _connection, _scope_id, query, **_kwargs):
                    if query == "stable":
                        ordered = (("zulu", 80.0), ("alpha", 80.0))
                    elif query == "cap":
                        ordered = tuple((f"cap-{index:02d}", 90.0) for index in reversed(range(25)))
                    else:
                        ordered = (
                            ("alpha", 80.0),
                            ("low-1", 60.0),
                            ("low-2", 59.0),
                            ("low-3", 58.0),
                            ("zulu", 57.0),
                        )
                    return TopicMemorySearchResult(
                        mode="fts",
                        hits=tuple(
                            TopicMemorySearchHit(
                                artifact_ref=stored[name].topic.as_ref(),
                                title=name,
                                summary=name,
                                score=score,
                                matched_by=("topic_fts",),
                            )
                            for name, score in ordered
                        ),
                    )

                async def get_exact(self, _connection, _scope_id, ref):
                    return stored[ref.artifact_id]

            processor = TopicMemoryProcessor(
                database=profile.database,
                sources=sources,
                topics=cast(Any, FakeTopics()),
                stages=_stages(probe=TopicMemoryProbeOutput(), global_output=TopicMemoryGlobalOutput()),
                publisher=TopicMemoryAtomicPublisher(profile.database, sources, topics),
            )
            probes = (
                TopicMemoryProbe(query="stable", evidence_ids=("e1",)),
                TopicMemoryProbe(query="floor", evidence_ids=("e1",)),
            )

            projected, candidates = await processor._history("scope-a", probes)

            assert [item.topic.artifact_id for item in candidates.values()] == [
                "alpha",
                "zulu",
                "low-1",
                "low-2",
                "low-3",
            ]
            assert len(projected[1].candidates) == 5
            assert candidates["candidate-0001"].topic.as_ref() == stored["alpha"].topic.as_ref()

            cap_projected, cap_candidates = await processor._history(
                "scope-a",
                (TopicMemoryProbe(query="cap", evidence_ids=("e1",)),),
            )
            assert len(cap_projected[0].candidates) == 20
            assert [item.topic.artifact_id for item in cap_candidates.values()] == [
                f"cap-{index:02d}" for index in range(5, 25)
            ]
        finally:
            await manager.__aexit__(None, None, None)

    asyncio.run(scenario())


def test_selector_closes_read_transaction_and_runs_estimator_off_loop() -> None:
    async def scenario() -> None:
        manager, profile, sources, _ = await _repositories()
        started = threading.Event()
        release = threading.Event()
        calls = 0

        def count(text: str) -> int:
            nonlocal calls
            calls += 1
            started.set()
            release.wait(timeout=2)
            return calls * 40

        try:
            async with profile.database.transaction() as connection:
                for index in range(3):
                    await sources.add(
                        connection,
                        "scope-a",
                        NoteSource(
                            name=f"note-{index}",
                            materialization=SourceMaterialization.CAPTURED,
                            body="evidence",
                        ),
                    )
            selector = TopicMemoryWindowSelector(
                profile.database,
                sources,
                TokenEstimator(character_token_estimator().profile, count),
                context_window_tokens=100,
            )
            task = asyncio.create_task(selector.select("scope-a", 0, 3))
            assert await asyncio.to_thread(started.wait, 2)
            await asyncio.sleep(0)
            async with profile.database.transaction() as connection:
                assert await sources.journal_position(connection, "scope-a") == 3
            release.set()
            assert await task == 2
        finally:
            release.set()
            await manager.__aexit__(None, None, None)

    asyncio.run(scenario())


def test_selector_keeps_one_oversized_source_without_skipping_or_truncating() -> None:
    async def scenario() -> None:
        manager, profile, sources, _ = await _repositories()
        try:
            async with profile.database.transaction() as connection:
                for index in range(2):
                    await sources.add(
                        connection,
                        "scope-a",
                        NoteSource(
                            name=f"oversized-{index}",
                            materialization=SourceMaterialization.CAPTURED,
                            body="x" * 1_000,
                        ),
                    )
            selector = TopicMemoryWindowSelector(
                profile.database,
                sources,
                TokenEstimator(character_token_estimator().profile, lambda _text: 10_000),
                context_window_tokens=100,
            )

            assert await selector.select("scope-a", 0, 2) == 1
        finally:
            await manager.__aexit__(None, None, None)

    asyncio.run(scenario())


def test_r3_supervisor_drives_processor_and_clears_explicit_pending(tmp_path) -> None:
    async def scenario() -> None:
        manager, profile, sources, topics = await _repositories(
            SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'topic-memory.db'}")
        )
        try:
            pending = ArtifactProcessingPendingRepository()
            proposal = TopicMemoryProposal(content=_content("e2e"), evidence_ids=("evidence-0001",))
            async with profile.database.transaction() as connection:
                source = await sources.add(
                    connection,
                    "scope-a",
                    NoteSource(name="note-e2e", materialization=SourceMaterialization.CAPTURED, body="e2e"),
                )
                await pending.raise_source(
                    connection, "scope-a", TOPIC_MEMORY_SOURCE_WINDOW_BINDING, source.journal_position
                )
                await pending.request_flush(connection, "scope-a", TOPIC_MEMORY_SOURCE_WINDOW_BINDING)
            processor = TopicMemoryProcessor(
                database=profile.database,
                sources=sources,
                topics=topics,
                stages=_stages(
                    probe=TopicMemoryProbeOutput(
                        probes=(TopicMemoryProbe(query="e2e", evidence_ids=("evidence-0001",)),)
                    ),
                    global_output=TopicMemoryGlobalOutput(proposals=(proposal,)),
                ),
                publisher=TopicMemoryAtomicPublisher(profile.database, sources, topics),
                id_factory=lambda: "topic-e2e",
            )
            binding = ArtifactProcessingBinding(
                binding_name=TOPIC_MEMORY_SOURCE_WINDOW_BINDING,
                source_window_limit=10,
                launcher=_ProcessorLauncher(processor),
                window_selector=TopicMemoryWindowSelector(
                    profile.database,
                    sources,
                    character_token_estimator(),
                    context_window_tokens=10_000,
                ),
            )
            async with ArtifactProcessingSupervisor(
                database=profile.database,
                bindings=(binding,),
                lease_mode="single-process",
                max_workers=1,
                worker_timeout_seconds=5,
                pending=pending,
                retry_base_seconds=0.01,
                retry_cap_seconds=0.01,
                retry_jitter=lambda: 1.0,
            ):
                for _ in range(200):
                    async with profile.database.transaction() as connection:
                        cursor = await SourceCursorRepository().load(
                            connection, "scope-a", TOPIC_MEMORY_SOURCE_WINDOW_BINDING
                        )
                        row = await pending.load(connection, "scope-a", TOPIC_MEMORY_SOURCE_WINDOW_BINDING)
                    if cursor is not None and row is None:
                        break
                    await asyncio.sleep(0.01)
                else:
                    raise AssertionError("processor did not finish")  # noqa: TRY003
            async with profile.database.transaction() as connection:
                page = await topics.browse_current(connection, "scope-a", limit=10)
            assert page[0].artifact_ref.artifact_id == "topic-e2e"
        finally:
            await manager.__aexit__(None, None, None)

    asyncio.run(scenario())


def test_worker_spec_pickle_and_repr_do_not_expose_secret() -> None:
    secret = "r5-secret-sentinel"  # noqa: S105
    spec = TopicMemoryWorkerSpec(
        config=BuiltinConfig(
            inference=InferenceConfig(
                generation_model="test",
                generation_headers={"x-key": SecretStr(secret)},
            )
        )
    )

    restored = pickle.loads(pickle.dumps(spec))  # noqa: S301

    assert restored.config.inference.generation_headers["x-key"].get_secret_value() == secret
    assert secret not in repr(spec)
    assert secret not in str(spec)
    assert secret not in repr(restored)


def test_topic_worker_entrypoint_runs_and_sanitizes_failure_in_real_spawn_child(tmp_path) -> None:
    async def scenario() -> None:
        config = BuiltinConfig(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'spawn-r5.db'}"),
            inference=InferenceConfig(generation_model="test"),
        )
        async with open_builtin_contexts(config) as contexts:
            scope = await contexts.get("scope-a")
            secret = "spawn-source-secret-sentinel"  # noqa: S105
            await scope.sources.capture(ContentCapture(source_id="spawn", content=secret))
            async with contexts.database.transaction() as connection:
                term = await contexts.repositories.processing_leases.start_single_process_term(connection, "holder")
            assignment = _assignment(term.fence("single-process"), through=1)
            launcher = SpawnArtifactProcessingWorkerLauncher(
                partial(run_topic_memory_worker, TopicMemoryWorkerSpec(config=config))
            )

            handle = await launcher.start(assignment)
            try:
                with pytest.raises(RuntimeError) as error:
                    await asyncio.wait_for(handle.wait(), timeout=10)
            finally:
                await handle.terminate()

            failure = cast(Any, error.value).failure
            assert failure.exception_type == "InvalidInferenceOutputError"
            assert secret not in str(error.value)
            assert secret not in failure.traceback
            async with contexts.database.transaction() as connection:
                cursor = await SourceCursorRepository().load(
                    connection,
                    "scope-a",
                    TOPIC_MEMORY_SOURCE_WINDOW_BINDING,
                )
            assert cursor is None

    asyncio.run(scenario())


def test_topic_usage_purposes_are_fail_open_and_secret_safe(caplog: pytest.LogCaptureFixture) -> None:
    async def scenario() -> None:
        observed: list[ModelUsagePurpose] = []

        async def failing_report(purpose, _operation, _usage) -> None:
            observed.append(purpose)
            raise RuntimeError("usage-secret-sentinel")

        stages = _stages(probe=TopicMemoryProbeOutput(), global_output=TopicMemoryGlobalOutput())
        processor = TopicMemoryProcessor(
            database=cast(Any, None),
            sources=cast(Any, None),
            topics=cast(Any, None),
            stages=stages,
            publisher=cast(Any, None),
            usage_reporter=failing_report,
        )
        generation = UsageReportingStructuredGenerator(_QueueGenerator(TopicMemoryProbeOutput()))
        embedding = UsageReportingEmbeddingModel(cast(EmbeddingModel, _UsageEmbeddingModel()))
        with processor._usage(ModelUsagePurpose.TOPIC_MEMORY_GENERATION):
            await generation.generate(object())
        with processor._usage(ModelUsagePurpose.TOPIC_MEMORY_RECALL, embedding=True):
            await embedding.embed(("recall",))
        with processor._usage(ModelUsagePurpose.TOPIC_MEMORY_INDEXING, embedding=True):
            await embedding.embed(("index",))

        assert observed == [
            ModelUsagePurpose.TOPIC_MEMORY_GENERATION,
            ModelUsagePurpose.TOPIC_MEMORY_RECALL,
            ModelUsagePurpose.TOPIC_MEMORY_INDEXING,
        ]

    with caplog.at_level(logging.ERROR):
        asyncio.run(scenario())
    assert "usage-secret-sentinel" not in caplog.text


def test_composition_registers_complete_binding_only_with_generation_model() -> None:
    async def scenario() -> None:
        config = BuiltinConfig(
            inference=InferenceConfig(generation_model="test"),
        )
        async with open_builtin_contexts(config) as contexts:
            bindings = _topic_memory_processing_bindings(config, contexts, ())
            with pytest.raises(BuiltinConfigurationError, match="child-reconstructible"):
                _topic_memory_processing_bindings(
                    config,
                    contexts,
                    (),
                    injected_token_estimator=character_token_estimator(),
                )
        assert len(bindings) == 1
        assert bindings[0].binding_name == TOPIC_MEMORY_SOURCE_WINDOW_BINDING
        assert bindings[0].window_selector is not None

        incomplete = BuiltinConfig(
            runtime=RuntimeConfig(topic_memory_schedule_seconds=30),
        )
        async with open_builtin_contexts(incomplete) as contexts:
            with pytest.raises(BuiltinConfigurationError, match="generation model"):
                _topic_memory_processing_bindings(incomplete, contexts, ())

    asyncio.run(scenario())
