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
import logging
import pickle
import threading
from collections import deque
from datetime import UTC, datetime
from functools import partial
from typing import Any, Generic, TypeVar, cast

import pytest
from pydantic import BaseModel, SecretStr
from sqlalchemy import event, select
from sqlalchemy.engine import Engine

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.memory import EmbeddingProfile
from powercontext.builtin.artifacts.search import analyze_text
from powercontext.builtin.artifacts.skill import ExternalSkillRegistration, ExternalSkillSnapshot, SkillPackageRef
from powercontext.builtin.artifacts.topic_memory import (
    MAX_TOPIC_MEMORY_QUERY_LENGTH,
    MAX_TOPIC_MEMORY_QUERY_TERMS,
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
    TOPIC_MEMORY_PROBE_INSTRUCTIONS,
    TopicMemoryEvidence,
    TopicMemoryEvolveInput,
    TopicMemoryEvolveOutput,
    TopicMemoryGenerationError,
    TopicMemoryGlobalOutput,
    TopicMemoryHistoricalPreview,
    TopicMemoryPlanItem,
    TopicMemoryPlannerInput,
    TopicMemoryPlannerOutput,
    TopicMemoryProbe,
    TopicMemoryProbeCandidates,
    TopicMemoryProbeInput,
    TopicMemoryProbeOutput,
    TopicMemoryProposal,
    TopicMemoryTemporaryInput,
    TopicMemoryTemporaryOutput,
    topic_memory_stage_fixed_prompt,
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
from powercontext.builtin.persistence.processing_intents import ArtifactProcessingIntentRepository
from powercontext.builtin.persistence.sources import SourceRepository
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.persistence.sqlite.topic_memory_index import SQLiteTopicMemoryFTSIndex
from powercontext.builtin.persistence.supervision import ArtifactProcessingLeaseRepository
from powercontext.builtin.persistence.tables import (
    BUILTIN_TABLES,
    TOPIC_MEMORY_RETRIEVAL_SHAPE_TABLE,
    TOPIC_MEMORY_WORK_BUDGETS_TABLE,
)
from powercontext.builtin.persistence.topic_memory import TopicMemoryRepository
from powercontext.builtin.persistence.topic_memory_index import (
    CompositeTopicMemoryIndex,
    topic_memory_embedding_profile_fingerprint,
)
from powercontext.builtin.runtime.artifact_processing import (
    ArtifactProcessingBinding,
    ArtifactProcessingSupervisor,
    ArtifactProcessingWorkerCompletion,
    ArtifactProcessingWorkerOutcome,
    SpawnArtifactProcessingWorkerLauncher,
)
from powercontext.builtin.runtime.composition import (
    BuiltinConfigurationError,
    _artifact_processing_bindings,
    open_builtin_contexts,
    open_builtin_runtime,
)
from powercontext.builtin.runtime.config import BuiltinConfig, InferenceConfig, RuntimeConfig
from powercontext.builtin.runtime.processing_contracts import ArtifactProcessingWorkAssignment as ScopeAssignment
from powercontext.builtin.runtime.topic_memory_processing import (
    ArtifactProcessingWaveKind,
    PreparedTopicMemoryOperation,
    TopicMemoryAtomicPublisher,
    TopicMemoryProcessor,
    TopicMemoryStageSet,
    TopicMemoryWindowSelector,
    TopicMemoryWorkerSpec,
    run_topic_memory_worker,
)
from powercontext.builtin.runtime.topic_memory_processing import (
    TopicMemoryWindowAssignment as ArtifactProcessingWorkAssignment,
)
from powercontext.builtin.runtime.topic_memory_scope import TopicMemoryScopeProcessor
from powercontext.builtin.sources import (
    CONTENT_SOURCE_ADAPTER,
    EXTERNAL_SKILL_SNAPSHOT_SOURCE_ADAPTER,
    SKILL_PACKAGE_UPLOAD_SOURCE_ADAPTER,
    SKILL_USAGE_SOURCE_ADAPTER,
    ContentCapture,
    ContentSource,
    ContentSourceInternal,
    ContentSourceTarget,
    ExternalSkillImportMode,
    ExternalSkillSnapshotCapture,
    SkillPackageUploadCapture,
    SkillUsageCapture,
    SourceCursor,
)
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


class _RepeatingGenerator(Generic[OutputT]):
    def __init__(self, output: OutputT) -> None:
        self.output = output
        self.inputs: list[object] = []

    async def generate(self, value, /) -> GenerationResult[OutputT]:
        self.inputs.append(value)
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
    def __init__(self, processor: TopicMemoryScopeProcessor) -> None:
        self.processor = processor

    async def start(self, assignment: ScopeAssignment):
        return _ProcessorHandle(self.processor, assignment)


class _ProcessorHandle:
    def __init__(self, processor: TopicMemoryScopeProcessor, assignment: ScopeAssignment) -> None:
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
                # Tail-fence rollback restores the debit row deleted earlier
                # in the same publication transaction; costs cannot be reset.
                budget = (await connection.execute(select(TOPIC_MEMORY_WORK_BUDGETS_TABLE))).mappings().one()
                assert budget["attempts"] == 1 and budget["requests"] == 4
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

            projected, candidates, planner_history = await processor._history("scope-a", probes)

            assert [item.topic.artifact_id for item in candidates.values()] == [
                "alpha",
                "zulu",
                "low-1",
                "low-2",
                "low-3",
            ]
            assert projected[0].query == "stable"
            assert len(projected[1].candidate_ids) == 5
            assert len(planner_history) == 5
            assert candidates["candidate-0001"].topic.as_ref() == stored["alpha"].topic.as_ref()

            cap_projected, cap_candidates, cap_planner_history = await processor._history(
                "scope-a",
                (TopicMemoryProbe(query="cap", evidence_ids=("e1",)),),
            )
            assert len(cap_projected[0].candidate_ids) == 20
            assert len(cap_planner_history) == 20
            assert [item.topic.artifact_id for item in cap_candidates.values()] == [
                f"cap-{index:02d}" for index in range(5, 25)
            ]
        finally:
            await manager.__aexit__(None, None, None)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "global_targets",
    [
        pytest.param(("candidate-0001", "candidate-0001"), id="duplicate-target"),
        pytest.param(("candidate-missing",), id="unknown-target"),
    ],
)
def test_global_structural_ambiguity_falls_back_to_lightweight_planner(
    global_targets: tuple[str, ...],
) -> None:
    async def scenario() -> None:
        manager, profile, sources, _ = await _repositories()
        try:
            historical = TopicMemory(
                artifact_id="topic-history",
                revision=1,
                content=TopicMemoryContent(
                    title="historical title",
                    summary="historical summary",
                    detail="private-full-detail-" + "x" * 20_000,
                ),
            )
            published = PublishedTopicMemory(
                topic=historical,
                published_at=datetime(2026, 1, 1, tzinfo=UTC),
                is_current=True,
                current_artifact=historical.as_ref(),
            )

            class FakeTopics:
                async def search(self, _connection, _scope_id, _query, **_kwargs):
                    return TopicMemorySearchResult(
                        mode="fts",
                        hits=(
                            TopicMemorySearchHit(
                                artifact_ref=historical.as_ref(),
                                title="historical title",
                                summary="historical summary",
                                snippet="bounded matching snippet",
                                score=90,
                                matched_by=("topic_fts",),
                            ),
                        ),
                    )

                async def get_exact(self, _connection, _scope_id, _ref):
                    return published

            global_proposals = tuple(
                TopicMemoryProposal(
                    candidate_id=target,
                    content=_content(f"global-{index}"),
                    evidence_ids=("evidence-0001",),
                )
                for index, target in enumerate(global_targets)
            )
            stages = _stages(
                probe=TopicMemoryProbeOutput(
                    probes=(
                        TopicMemoryProbe(
                            query="durable history",
                            keywords=("history", "durable"),
                            evidence_ids=("evidence-0001",),
                        ),
                    )
                ),
                global_output=TopicMemoryGlobalOutput(proposals=global_proposals),
                planner=TopicMemoryPlannerOutput(items=(TopicMemoryPlanItem(probe_ids=("probe-0001",)),)),
                evolve=(TopicMemoryEvolveOutput(),),
            )
            processor = TopicMemoryProcessor(
                database=profile.database,
                sources=sources,
                topics=cast(Any, FakeTopics()),
                stages=stages,
                publisher=cast(Any, None),
            )
            evidence = (
                TopicMemoryEvidence(
                    evidence_id="evidence-0001",
                    source_type="note",
                    content="new durable evidence",
                ),
            )

            proposals, _ = await processor._generate("scope-a", evidence)

            assert proposals == ()
            assert len(stages.planner.inputs) == 1
            planner_input = cast(TopicMemoryPlannerInput, stages.planner.inputs[0])
            assert planner_input.probes[0].query == "durable history"
            assert planner_input.probes[0].keywords == ("history", "durable")
            assert planner_input.probes[0].candidate_ids == ("candidate-0001",)
            assert len(planner_input.historical) == 1
            assert planner_input.historical[0].snippet == "bounded matching snippet"
            assert "private-full-detail" not in planner_input.model_dump_json()
        finally:
            await manager.__aexit__(None, None, None)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("probes", "plan"),
    [
        pytest.param(
            (
                TopicMemoryProbeCandidates(
                    probe_id="probe-0001",
                    query="first",
                    evidence_ids=("evidence-0001",),
                    candidate_ids=("candidate-0001",),
                ),
            ),
            TopicMemoryPlannerOutput(
                items=(
                    TopicMemoryPlanItem(
                        probe_ids=("probe-0001",),
                        candidate_id="candidate-0002",
                    ),
                )
            ),
            id="target-not-returned-for-probe",
        ),
        pytest.param(
            (
                TopicMemoryProbeCandidates(
                    probe_id="probe-0001",
                    query="first",
                    evidence_ids=("evidence-0001",),
                    candidate_ids=("candidate-0001",),
                ),
                TopicMemoryProbeCandidates(
                    probe_id="probe-0002",
                    query="second",
                    evidence_ids=("evidence-0002",),
                    candidate_ids=("candidate-0001",),
                ),
            ),
            TopicMemoryPlannerOutput(
                items=(
                    TopicMemoryPlanItem(
                        probe_ids=("probe-0001",),
                        candidate_id="candidate-0001",
                    ),
                    TopicMemoryPlanItem(probe_ids=("probe-0002",)),
                )
            ),
            id="shared-candidate-split-across-work-items",
        ),
        pytest.param(
            (
                TopicMemoryProbeCandidates(
                    probe_id="probe-0001",
                    query="first",
                    evidence_ids=("evidence-0001",),
                    candidate_ids=("candidate-0001",),
                ),
                TopicMemoryProbeCandidates(
                    probe_id="probe-0002",
                    query="second",
                    evidence_ids=("evidence-0002",),
                    candidate_ids=("candidate-0001",),
                ),
            ),
            TopicMemoryPlannerOutput(
                items=(
                    TopicMemoryPlanItem(probe_ids=("probe-0001",)),
                    TopicMemoryPlanItem(probe_ids=("probe-0002",)),
                )
            ),
            id="shared-candidate-split-across-create-items",
        ),
        pytest.param(
            (
                TopicMemoryProbeCandidates(
                    probe_id="probe-0001",
                    query="first",
                    evidence_ids=("evidence-0001",),
                    candidate_ids=("candidate-0001",),
                ),
                TopicMemoryProbeCandidates(
                    probe_id="probe-0002",
                    query="second",
                    evidence_ids=("evidence-0002",),
                    candidate_ids=("candidate-0001", "candidate-0002"),
                ),
                TopicMemoryProbeCandidates(
                    probe_id="probe-0003",
                    query="third",
                    evidence_ids=("evidence-0003",),
                    candidate_ids=("candidate-0002",),
                ),
            ),
            TopicMemoryPlannerOutput(
                items=(
                    TopicMemoryPlanItem(probe_ids=("probe-0001", "probe-0002")),
                    TopicMemoryPlanItem(probe_ids=("probe-0003",)),
                )
            ),
            id="overlapping-candidate-chain-split",
        ),
    ],
)
def test_planner_rejects_unbound_or_inconsistently_split_targets(
    probes: tuple[TopicMemoryProbeCandidates, ...],
    plan: TopicMemoryPlannerOutput,
) -> None:
    async def scenario() -> None:
        stages = _stages(
            probe=TopicMemoryProbeOutput(),
            global_output=TopicMemoryGlobalOutput(),
            planner=plan,
        )
        processor = TopicMemoryProcessor(
            database=cast(Any, None),
            sources=cast(Any, None),
            topics=cast(Any, None),
            stages=stages,
            publisher=cast(Any, None),
        )
        evidence = tuple(
            TopicMemoryEvidence(
                evidence_id=f"evidence-{index:04d}",
                source_type="note",
                content=f"evidence {index}",
            )
            for index in range(1, len(probes) + 1)
        )

        with pytest.raises(TopicMemoryGenerationError, match="invalid_plan"):
            await processor._plan(
                probes,
                (),
                evidence,
                cast(Any, {"candidate-0001": object(), "candidate-0002": object()}),
            )

    asyncio.run(scenario())


def test_planner_allows_independent_candidate_components() -> None:
    async def scenario() -> None:
        probes = (
            TopicMemoryProbeCandidates(
                probe_id="probe-0001",
                query="first",
                evidence_ids=("evidence-0001",),
                candidate_ids=("candidate-0001",),
            ),
            TopicMemoryProbeCandidates(
                probe_id="probe-0002",
                query="second",
                evidence_ids=("evidence-0002",),
                candidate_ids=("candidate-0002",),
            ),
        )
        stages = _stages(
            probe=TopicMemoryProbeOutput(),
            global_output=TopicMemoryGlobalOutput(),
            planner=TopicMemoryPlannerOutput(
                items=(
                    TopicMemoryPlanItem(probe_ids=("probe-0001",)),
                    TopicMemoryPlanItem(probe_ids=("probe-0002",)),
                )
            ),
            evolve=(TopicMemoryEvolveOutput(), TopicMemoryEvolveOutput()),
        )
        processor = TopicMemoryProcessor(
            database=cast(Any, None),
            sources=cast(Any, None),
            topics=cast(Any, None),
            stages=stages,
            publisher=cast(Any, None),
        )
        evidence = (
            TopicMemoryEvidence(evidence_id="evidence-0001", source_type="note", content="first"),
            TopicMemoryEvidence(evidence_id="evidence-0002", source_type="note", content="second"),
        )

        assert (
            await processor._plan(
                probes,
                (),
                evidence,
                cast(Any, {"candidate-0001": object(), "candidate-0002": object()}),
            )
            == ()
        )

    asyncio.run(scenario())


def test_planner_uses_deterministic_candidate_components_when_preview_cannot_fit() -> None:
    async def scenario() -> None:
        probes = (
            TopicMemoryProbeCandidates(
                probe_id="probe-0001",
                query="甲" * 8_192,
                evidence_ids=("evidence-0001",),
                candidate_ids=("candidate-0001",),
            ),
            TopicMemoryProbeCandidates(
                probe_id="probe-0002",
                query="乙" * 8_192,
                evidence_ids=("evidence-0002",),
                candidate_ids=("candidate-0001", "candidate-0002"),
            ),
            TopicMemoryProbeCandidates(
                probe_id="probe-0003",
                query="丙" * 8_192,
                evidence_ids=("evidence-0003",),
                candidate_ids=("candidate-0002",),
            ),
        )
        stages = _stages(
            probe=TopicMemoryProbeOutput(),
            global_output=TopicMemoryGlobalOutput(),
            evolve=(TopicMemoryEvolveOutput(),),
            limit=1_000,
        )
        processor = TopicMemoryProcessor(
            database=cast(Any, None),
            sources=cast(Any, None),
            topics=cast(Any, None),
            stages=stages,
            publisher=cast(Any, None),
        )
        evidence = tuple(
            TopicMemoryEvidence(
                evidence_id=f"evidence-{index:04d}",
                source_type="note",
                content=f"evidence {index}",
            )
            for index in range(1, 4)
        )

        assert (
            await processor._plan(
                probes,
                (),
                evidence,
                cast(Any, {"candidate-0001": object(), "candidate-0002": object()}),
            )
            == ()
        )
        assert stages.planner.inputs == []
        evolve_input = cast(TopicMemoryEvolveInput, stages.evolver.inputs[0])
        assert tuple(item.evidence_id for item in evolve_input.evidence) == (
            "evidence-0001",
            "evidence-0002",
            "evidence-0003",
        )

    asyncio.run(scenario())


def test_planner_bounds_twenty_legal_cjk_previews_before_provider_call() -> None:
    async def scenario() -> None:
        probes = tuple(
            TopicMemoryProbeCandidates(
                probe_id=f"probe-{index:04d}",
                query="查询" * 4_096,
                keywords=("关键词" * 4_096,),
                evidence_ids=(f"evidence-{index:04d}",),
            )
            for index in range(1, 21)
        )
        history = tuple(
            TopicMemoryHistoricalPreview(
                candidate_id=f"candidate-{index:04d}",
                title="标题" * 256,
                summary="摘要" * 4_000,
                snippet="片段" * 4_000,
            )
            for index in range(1, 21)
        )
        plan = TopicMemoryPlannerOutput(
            items=tuple(TopicMemoryPlanItem(probe_ids=(f"probe-{index:04d}",)) for index in range(1, 21))
        )
        stages = _stages(
            probe=TopicMemoryProbeOutput(),
            global_output=TopicMemoryGlobalOutput(),
            planner=plan,
            evolve=tuple(TopicMemoryEvolveOutput() for _ in range(20)),
        )
        processor = TopicMemoryProcessor(
            database=cast(Any, None),
            sources=cast(Any, None),
            topics=cast(Any, None),
            stages=stages,
            publisher=cast(Any, None),
        )
        evidence = tuple(
            TopicMemoryEvidence(
                evidence_id=f"evidence-{index:04d}",
                source_type="note",
                content=f"evidence {index}",
            )
            for index in range(1, 21)
        )

        assert await processor._plan(probes, history, evidence, {}) == ()

        planner_input = cast(TopicMemoryPlannerInput, stages.planner.inputs[0])
        assert stages.fits(planner_input, "planner")
        assert all(len(item.query) <= 512 for item in planner_input.probes)
        assert all(sum(map(len, item.keywords)) <= 512 for item in planner_input.probes)
        assert all(
            max(len(item.title), len(item.summary), len(item.snippet or "")) <= 512 for item in planner_input.historical
        )

    asyncio.run(scenario())


def test_related_coordination_fails_closed_for_candidate_union_above_stage_contract() -> None:
    async def scenario() -> None:
        manager, profile, sources, _ = await _repositories()
        published = {
            f"history-{index:04d}": PublishedTopicMemory(
                topic=TopicMemory(
                    artifact_id=f"history-{index:04d}",
                    revision=1,
                    content=_content(f"history-{index:04d}"),
                ),
                published_at=datetime(2026, 1, 1, tzinfo=UTC),
                is_current=True,
                current_artifact=TopicMemory(
                    artifact_id=f"history-{index:04d}",
                    revision=1,
                    content=_content(f"history-{index:04d}"),
                ).as_ref(),
            )
            for index in range(1, 22)
        }

        class FakeTopics:
            def __init__(self) -> None:
                self.calls = 0

            async def search(self, _connection, _scope_id, _query, **_kwargs):
                self.calls += 1
                selected = range(1, 12) if self.calls == 1 else range(11, 22)
                return TopicMemorySearchResult(
                    mode="fts",
                    hits=tuple(
                        TopicMemorySearchHit(
                            artifact_ref=published[f"history-{index:04d}"].topic.as_ref(),
                            title=f"history {index}",
                            summary="shared durable state",
                            score=90,
                            matched_by=("topic_fts",),
                        )
                        for index in selected
                    ),
                )

            async def get_exact(self, _connection, _scope_id, ref):
                return published[ref.artifact_id]

        try:
            stages = _stages(probe=TopicMemoryProbeOutput(), global_output=TopicMemoryGlobalOutput())
            processor = TopicMemoryProcessor(
                database=profile.database,
                sources=sources,
                topics=cast(Any, FakeTopics()),
                stages=stages,
                publisher=cast(Any, None),
            )
            evidence = (
                TopicMemoryEvidence(evidence_id="evidence-0001", source_type="note", content="first"),
                TopicMemoryEvidence(evidence_id="evidence-0002", source_type="note", content="second"),
            )
            proposals = (
                TopicMemoryProposal(content=_content("shared-a"), evidence_ids=("evidence-0001",)),
                TopicMemoryProposal(content=_content("shared-b"), evidence_ids=("evidence-0002",)),
            )

            candidates: dict[str, PublishedTopicMemory] = {}
            with pytest.raises(TopicMemoryGenerationError, match="related_history_limit"):
                await processor._coordinate("scope-a", proposals, candidates, evidence)

            assert len(candidates) == 21
            assert stages.reconciler.inputs == []
        finally:
            await manager.__aexit__(None, None, None)

    asyncio.run(scenario())


def test_related_coordination_fails_closed_and_keeps_cursor_when_history_exceeds_budget() -> None:
    async def scenario() -> None:
        manager, profile, sources, _ = await _repositories()
        historical = TopicMemory(
            artifact_id="history-large",
            revision=1,
            content=TopicMemoryContent(
                title="large history",
                summary="shared durable state",
                detail="界" * 125_000,
            ),
        )
        published = PublishedTopicMemory(
            topic=historical,
            published_at=datetime(2026, 1, 1, tzinfo=UTC),
            is_current=True,
            current_artifact=historical.as_ref(),
        )

        try:
            leases = ArtifactProcessingLeaseRepository()
            async with profile.database.transaction() as connection:
                await sources.add(
                    connection,
                    "scope-a",
                    NoteSource(
                        name="new-state",
                        materialization=SourceMaterialization.CAPTURED,
                        body="new shared state",
                    ),
                )
                term = await leases.start_single_process_term(connection, "holder")

            class FakeTopics:
                def __init__(self) -> None:
                    self.calls = 0

                async def search(self, _connection, _scope_id, _query, **_kwargs):
                    self.calls += 1
                    hits = ()
                    if self.calls == 2:
                        hits = (
                            TopicMemorySearchHit(
                                artifact_ref=historical.as_ref(),
                                title=historical.content.title,
                                summary=historical.content.summary,
                                score=90,
                                matched_by=("topic_fts",),
                            ),
                        )
                    return TopicMemorySearchResult(mode="fts", hits=hits)

                async def get_exact(self, _connection, _scope_id, _ref):
                    return published

            class UnexpectedPublisher:
                async def publish(self, *_args, **_kwargs):
                    raise AssertionError("unexpected publish")  # noqa: TRY003

            def unexpected_id() -> str:
                raise AssertionError("unexpected identity")  # noqa: TRY003

            proposal = TopicMemoryProposal(content=_content("shared"), evidence_ids=("evidence-0001",))
            stages = _stages(
                probe=TopicMemoryProbeOutput(
                    probes=(TopicMemoryProbe(query="shared durable state", evidence_ids=("evidence-0001",)),)
                ),
                global_output=TopicMemoryGlobalOutput(proposals=(proposal,)),
            )
            processor = TopicMemoryProcessor(
                database=profile.database,
                sources=sources,
                topics=cast(Any, FakeTopics()),
                stages=stages,
                publisher=cast(Any, UnexpectedPublisher()),
                id_factory=unexpected_id,
            )

            with pytest.raises(TopicMemoryGenerationError, match="input_budget_exceeded"):
                await processor.process(_assignment(term.fence("single-process")))

            assert stages.reconciler.inputs == []
            async with profile.database.transaction() as connection:
                cursor = await SourceCursorRepository().load(
                    connection,
                    "scope-a",
                    TOPIC_MEMORY_SOURCE_WINDOW_BINDING,
                )
            assert cursor is None
        finally:
            await manager.__aexit__(None, None, None)

    asyncio.run(scenario())


def test_probe_query_is_bounded_before_embedding_and_repository_io() -> None:
    async def scenario() -> None:
        manager, profile, sources, _ = await _repositories()
        observed_queries: list[str] = []
        embedded_queries: list[str] = []

        class FakeTopics:
            async def search(self, _connection, _scope_id, query, **_kwargs):
                observed_queries.append(query)
                return TopicMemorySearchResult(mode="fts")

        class RecordingEmbedding:
            profile = None

            async def embed(self, texts: tuple[str, ...], /) -> EmbeddingResult:
                embedded_queries.extend(texts)
                return EmbeddingResult(vectors=tuple((1.0, 0.0) for _ in texts))

        try:
            processor = TopicMemoryProcessor(
                database=profile.database,
                sources=sources,
                topics=cast(Any, FakeTopics()),
                stages=_stages(probe=TopicMemoryProbeOutput(), global_output=TopicMemoryGlobalOutput()),
                publisher=cast(Any, None),
                embedding_model=cast(EmbeddingModel, RecordingEmbedding()),
            )
            query = "部署环境" * 2_048
            keywords = tuple(f"keyword-{index}" for index in range(MAX_TOPIC_MEMORY_QUERY_TERMS))

            await processor._history(
                "scope-a",
                (TopicMemoryProbe(query=query, keywords=keywords, evidence_ids=("evidence-0001",)),),
            )

            assert observed_queries != embedded_queries
            assert len(observed_queries[0]) <= MAX_TOPIC_MEMORY_QUERY_LENGTH
            assert len(embedded_queries[0]) <= MAX_TOPIC_MEMORY_QUERY_LENGTH
            assert embedded_queries[0].startswith("部署环境")
            assert "u_" not in embedded_queries[0]
            assert "b_" not in embedded_queries[0]
            terms = analyze_text(observed_queries[0]).split()
            assert len(set(terms)) <= MAX_TOPIC_MEMORY_QUERY_TERMS
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


@pytest.mark.parametrize("only_lineage", [False, True])
def test_topic_windows_exclude_lineage_only_sources_and_advance_the_full_journal(only_lineage: bool) -> None:
    async def scenario() -> None:
        manager, profile, _, topics = await _repositories()
        sources = SourceRepository((CONTENT_SOURCE_ADAPTER,))
        estimates: list[str] = []

        def estimate(value: str) -> int:
            estimates.append(value)
            return 1

        try:
            async with profile.database.transaction() as connection:
                for position in range(3):
                    lineage_only = only_lineage or position != 1
                    await sources.add(
                        connection,
                        "scope-a",
                        ContentSource(
                            name=f"source-{position}",
                            materialization=SourceMaterialization.CAPTURED,
                            content="private-lineage-sentinel" if lineage_only else "eligible source",
                            internal=ContentSourceInternal(
                                role="lineage_only",
                                operation="artifact_create",
                                target=ContentSourceTarget(
                                    scope_id="scope-a", family="profile", artifact_id="profile", revision=1
                                ),
                            )
                            if lineage_only
                            else None,
                        ),
                    )
                term = await ArtifactProcessingLeaseRepository().start_single_process_term(connection, "holder")
            estimator = TokenEstimator(character_token_estimator().profile, estimate)
            selector = TopicMemoryWindowSelector(profile.database, sources, estimator, context_window_tokens=125_000)
            assert await selector.select("scope-a", 0, 3) == 3
            stages = _stages(probe=TopicMemoryProbeOutput(), global_output=None, estimator=estimator)
            processor = TopicMemoryProcessor(
                database=profile.database,
                sources=sources,
                topics=topics,
                stages=stages,
                publisher=TopicMemoryAtomicPublisher(profile.database, sources, topics),
            )
            assert (
                await processor.process(_assignment(term.fence("single-process"), through=3))
            ).outcome.value == "succeeded"
            assert all("private-lineage-sentinel" not in value for value in estimates)
            if only_lineage:
                assert stages.probe.inputs == []
            else:
                assert len(stages.probe.inputs) == 1
                request = cast(TopicMemoryProbeInput, stages.probe.inputs[0])
                assert [item.evidence_id for item in request.evidence] == ["evidence-0002"]
                assert "private-lineage-sentinel" not in request.model_dump_json()
            async with profile.database.transaction() as connection:
                cursor = await SourceCursorRepository().load(connection, "scope-a", TOPIC_MEMORY_SOURCE_WINDOW_BINDING)
                assert cursor is not None and cursor.cursor.sequence == 3
        finally:
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


@pytest.mark.parametrize("source_kind", ["external-skill", "skill-usage", "skill-package-upload"])
def test_selector_and_worker_share_adapter_canonical_skill_evidence(source_kind: str) -> None:
    async def scenario() -> None:
        manager, profile, _, topics = await _repositories()
        sources = SourceRepository((
            *SOURCE_ADAPTERS,
            EXTERNAL_SKILL_SNAPSHOT_SOURCE_ADAPTER,
            SKILL_USAGE_SOURCE_ADAPTER,
            SKILL_PACKAGE_UPLOAD_SOURCE_ADAPTER,
        ))
        selector_requests: list[str] = []
        try:
            registration = ExternalSkillRegistration(
                external_skill_id="codex:project:stable-id-sentinel/review",
                host_id="host-id-sentinel",
                installation_scope="project",
                locator="/host-path-sentinel/.agents/skills/review",
                fingerprint="abcdef0123456789" * 4,
                name="review",
                description="Review a bounded change.",
            )
            captured = ExternalSkillSnapshotCapture(
                snapshot=ExternalSkillSnapshot(
                    registration=registration,
                    package=SkillPackageRef(
                        tree_digest="1" * 64,
                        archive_digest="2" * 64,
                        file_count=1,
                        uncompressed_size=64,
                        archive_size=64,
                    ),
                    manifest="# Review\n\nUse the exact review protocol marker.",
                ),
                mode=ExternalSkillImportMode.FORK,
            )
            expected: dict[str, object] = {"manifest": captured.snapshot.manifest, "mode": "fork"}
            if source_kind == "external-skill":
                source = await EXTERNAL_SKILL_SNAPSHOT_SOURCE_ADAPTER.resolve(captured)
            elif source_kind == "skill-usage":
                source = await SKILL_USAGE_SOURCE_ADAPTER.resolve(
                    SkillUsageCapture(
                        observation_id="stable-id-sentinel",
                        skill_ref=ArtifactRef(family="skill", artifact_id="stable-id-sentinel", revision=1),
                        package_digest="sha256:" + "abcdef0123456789" * 4,
                        target_id="host-id-sentinel",
                        selected=True,
                    )
                )
                expected = {"selected": True, "invoked": "unknown", "validation": "unknown", "outcome": "unknown"}
            else:
                source = await SKILL_PACKAGE_UPLOAD_SOURCE_ADAPTER.resolve(
                    SkillPackageUploadCapture(
                        package=captured.snapshot.package,
                        name="review",
                        description="Review a bounded change.",
                    )
                )
                expected = {"name": "review", "description": "Review a bounded change."}
            leases = ArtifactProcessingLeaseRepository()
            async with profile.database.transaction() as connection:
                await sources.add(connection, "scope-a", source)
                term = await leases.start_single_process_term(connection, "holder")

            def capture_request(value: str) -> int:
                selector_requests.append(value)
                return 1

            selector = TopicMemoryWindowSelector(
                profile.database,
                sources,
                TokenEstimator(character_token_estimator().profile, capture_request),
                context_window_tokens=125_000,
            )
            assert await selector.select("scope-a", 0, 1) == 1

            proposal = TopicMemoryProposal(
                content=_content("external-skill"),
                evidence_ids=("evidence-0001",),
            )
            stages = _stages(
                probe=TopicMemoryProbeOutput(
                    probes=(
                        TopicMemoryProbe(
                            query="review protocol",
                            evidence_ids=("evidence-0001",),
                        ),
                    )
                ),
                global_output=TopicMemoryGlobalOutput(ambiguous=True),
                planner=TopicMemoryPlannerOutput(items=(TopicMemoryPlanItem(probe_ids=("probe-0001",)),)),
                evolve=(TopicMemoryEvolveOutput(proposal=proposal),),
            )
            processor = TopicMemoryProcessor(
                database=profile.database,
                sources=sources,
                topics=topics,
                stages=stages,
                publisher=TopicMemoryAtomicPublisher(profile.database, sources, topics, leases=leases),
                id_factory=lambda: "topic-external-skill",
            )
            completion = await processor.process(_assignment(term.fence("single-process")))

            assert completion.outcome is ArtifactProcessingWorkerOutcome.SUCCEEDED
            probe_input = cast(TopicMemoryProbeInput, stages.probe.inputs[0])
            projected = probe_input.evidence[0].content
            selector_input = TopicMemoryProbeInput.model_validate_json(selector_requests[-1].rsplit("\n", 1)[1])
            assert selector_input.evidence[0].content == projected
            assert json.loads(projected) == expected
            async with profile.database.transaction() as connection:
                cursor = await SourceCursorRepository().load(connection, "scope-a", TOPIC_MEMORY_SOURCE_WINDOW_BINDING)
                assert cursor is not None and cursor.cursor.sequence == 1
            for sentinel in (
                "stable-id-sentinel",
                "host-id-sentinel",
                "host-path-sentinel",
                "abcdef0123456789",
                "1" * 64,
                "2" * 64,
            ):
                assert sentinel not in projected
                assert sentinel not in selector_requests[-1]
                assert all(
                    sentinel not in cast(BaseModel, value).model_dump_json()
                    for generator in (
                        stages.probe,
                        stages.global_evolver,
                        stages.planner,
                        stages.evolver,
                    )
                    for value in generator.inputs
                )
            assert "Exact external Skill snapshot captured" not in projected
        finally:
            await manager.__aexit__(None, None, None)

    asyncio.run(scenario())


def test_oversized_source_preserves_all_evidence_and_reaches_tail_at_the_same_budget() -> None:
    async def scenario() -> None:
        manager, profile, _, topics = await _repositories()
        sources = SourceRepository((*SOURCE_ADAPTERS, CONTENT_SOURCE_ADAPTER))
        try:
            oversized_metadata = "界" * 200_000
            oversized_capture = ContentCapture(
                source_id="oversized-content",
                content="body",
                metadata={"unbounded": oversized_metadata},
            )
            async with profile.database.transaction() as connection:
                first = await sources.add(
                    connection,
                    "scope-a",
                    await CONTENT_SOURCE_ADAPTER.resolve(oversized_capture),
                )
                second = await sources.add(
                    connection,
                    "scope-a",
                    NoteSource(
                        name="reachable-tail",
                        materialization=SourceMaterialization.CAPTURED,
                        body="tail remains reachable",
                    ),
                )
                term = await ArtifactProcessingLeaseRepository().start_single_process_term(
                    connection,
                    "holder",
                )

            selector = TopicMemoryWindowSelector(
                profile.database,
                sources,
                character_token_estimator(),
                context_window_tokens=125_000,
            )
            assert await selector.select("scope-a", 0, second.journal_position) == first.journal_position

            small_probe = _RepeatingGenerator(TopicMemoryProbeOutput())
            unexpected = _QueueGenerator()
            small_stages = TopicMemoryStageSet(
                probe=small_probe,
                global_evolver=unexpected,
                planner=unexpected,
                evolver=unexpected,
                temporary=unexpected,
                reconciler=unexpected,
                estimator=character_token_estimator(),
                input_tokens_limit=100_000,
                fixed_prompts={
                    "probe": topic_memory_stage_fixed_prompt(
                        TOPIC_MEMORY_PROBE_INSTRUCTIONS,
                        TopicMemoryProbeInput,
                        TopicMemoryProbeOutput,
                    )
                },
            )
            leases = ArtifactProcessingLeaseRepository()
            processor = TopicMemoryProcessor(
                database=profile.database,
                sources=sources,
                topics=topics,
                stages=small_stages,
                publisher=TopicMemoryAtomicPublisher(profile.database, sources, topics, leases=leases),
            )
            first_completion = await processor.process(
                _assignment(term.fence("single-process"), through=first.journal_position)
            )
            assert first_completion.outcome is ArtifactProcessingWorkerOutcome.SUCCEEDED
            assert len(small_probe.inputs) > 1
            assert all(small_stages.fits(cast(TopicMemoryProbeInput, value), "probe") for value in small_probe.inputs)
            projected = "".join(cast(TopicMemoryProbeInput, value).evidence[0].content for value in small_probe.inputs)
            assert json.loads(projected) == {
                "content": "body",
                "metadata": {"unbounded": oversized_metadata},
            }
            assert "oversized-content" not in projected

            async with profile.database.transaction() as connection:
                cursor = await SourceCursorRepository().load(
                    connection,
                    "scope-a",
                    TOPIC_MEMORY_SOURCE_WINDOW_BINDING,
                )
            assert cursor is not None and cursor.cursor.sequence == first.journal_position
            tail_assignment = ArtifactProcessingWorkAssignment(
                binding_name=TOPIC_MEMORY_SOURCE_WINDOW_BINDING,
                scope_id="scope-a",
                source_after=first.journal_position,
                source_through=second.journal_position,
                wave_target=second.journal_position,
                claimed_flush_generation=1,
                cursor_generation=cursor.generation,
                wave_kind=ArtifactProcessingWaveKind.EXPLICIT,
                fence=term.fence("single-process"),
                worker_id="worker-tail",
            )
            tail_completion = await processor.process(tail_assignment)

            assert tail_completion.outcome is ArtifactProcessingWorkerOutcome.SUCCEEDED
            tail_input = cast(TopicMemoryProbeInput, small_probe.inputs[-1])
            assert tail_input.evidence[0].content == "tail remains reachable"
            async with profile.database.transaction() as connection:
                cursor = await SourceCursorRepository().load(
                    connection,
                    "scope-a",
                    TOPIC_MEMORY_SOURCE_WINDOW_BINDING,
                )
            assert cursor is not None and cursor.cursor.sequence == second.journal_position
        finally:
            await manager.__aexit__(None, None, None)

    asyncio.run(scenario())


def test_oversized_source_temporary_fragments_publish_atomically_with_original_lineage() -> None:
    class Planner:
        async def generate(self, value: TopicMemoryPlannerInput, /):
            return GenerationResult(
                output=TopicMemoryPlannerOutput(
                    items=(TopicMemoryPlanItem(probe_ids=tuple(probe.probe_id for probe in value.probes)),)
                )
            )

    class TemporaryGenerator:
        def __init__(self, proposal: TopicMemoryProposal) -> None:
            self.proposal = proposal
            self.fail_tail = True
            self.inputs: list[TopicMemoryTemporaryInput] = []

        async def generate(self, value: TopicMemoryTemporaryInput, /):
            self.inputs.append(value)
            fragment = value.evidence[0].metadata
            if self.fail_tail and fragment["fragment_end"] == fragment["source_content_length"]:
                raise RuntimeError("temporary generation unavailable")  # noqa: TRY003
            return GenerationResult(output=TopicMemoryTemporaryOutput(proposals=(self.proposal,)))

    async def scenario() -> None:
        manager, profile, sources, topics = await _repositories()
        try:
            original = '界\\"\n' * 50_000 + "END-OF-EVIDENCE"
            async with profile.database.transaction() as connection:
                source = await sources.add(
                    connection,
                    "scope-a",
                    NoteSource(name="oversized", materialization=SourceMaterialization.CAPTURED, body=original),
                )
                term = await ArtifactProcessingLeaseRepository().start_single_process_term(connection, "holder")
            proposal = TopicMemoryProposal(content=_content("segmented"), evidence_ids=("evidence-0001",))
            probe = _RepeatingGenerator(
                TopicMemoryProbeOutput(probes=(TopicMemoryProbe(query="segmented", evidence_ids=("evidence-0001",)),))
            )
            temporary = TemporaryGenerator(proposal)
            stages = TopicMemoryStageSet(
                probe=probe,
                planner=Planner(),
                temporary=temporary,
                evolver=_RepeatingGenerator(TopicMemoryEvolveOutput(proposal=proposal)),
                global_evolver=_QueueGenerator(),
                reconciler=_QueueGenerator(),
                estimator=character_token_estimator(),
                input_tokens_limit=100_000,
            )
            processor = TopicMemoryProcessor(
                database=profile.database,
                sources=sources,
                topics=topics,
                stages=stages,
                publisher=TopicMemoryAtomicPublisher(profile.database, sources, topics),
            )
            assignment = _assignment(term.fence("single-process"))
            with pytest.raises(RuntimeError, match="temporary generation unavailable"):
                await processor.process(assignment)
            async with profile.database.transaction() as connection:
                assert (
                    await SourceCursorRepository().load(connection, "scope-a", TOPIC_MEMORY_SOURCE_WINDOW_BINDING)
                    is None
                )
                assert await topics.browse_current(connection, "scope-a", limit=10) == ()
            temporary.fail_tail = False
            temporary.inputs.clear()
            probe.inputs.clear()
            assert (await processor.process(assignment)).outcome is ArtifactProcessingWorkerOutcome.SUCCEEDED
            for inputs, stage in ((probe.inputs, "probe"), (temporary.inputs, "temporary")):
                assert len(inputs) > 1
                requests = cast(list[TopicMemoryProbeInput | TopicMemoryTemporaryInput], inputs)
                assert all(stages.fits(value, stage) for value in requests)
                assert "".join(value.evidence[0].content for value in requests) == original
                assert {value.evidence[0].evidence_id for value in requests} == {"evidence-0001"}
            async with profile.database.transaction() as connection:
                items = await topics.browse_current(connection, "scope-a", limit=10)
                assert len(items) == 1
                published = await topics.get_exact(connection, "scope-a", items[0].artifact_ref)
                assert published.topic.lineage.sources == (source.ref,)
                cursor = await SourceCursorRepository().load(connection, "scope-a", TOPIC_MEMORY_SOURCE_WINDOW_BINDING)
                assert cursor is not None and cursor.cursor.sequence == source.journal_position
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
                await ArtifactProcessingIntentRepository().request(
                    connection, "scope-a", TOPIC_MEMORY_SOURCE_WINDOW_BINDING
                )
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
                artifact_family="topic-memory",
                max_workers=1,
                worker_timeout_seconds=5,
                launcher=_ProcessorLauncher(
                    TopicMemoryScopeProcessor(
                        profile.database,
                        processor,
                        TopicMemoryWindowSelector(
                            profile.database,
                            sources,
                            character_token_estimator(),
                            context_window_tokens=10_000,
                        ),
                    )
                ),
            )
            async with ArtifactProcessingSupervisor(
                database=profile.database,
                bindings=(binding,),
                lease_mode="single-process",
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


def _run_worker_without_general_fts_writes(spec, assignment):
    # Install inside the real spawned child: an event hook in the parent would
    # not observe the independent engine. This protects a per-Window I/O budget,
    # not a particular sequence of private bootstrap calls.
    def reject_general_fts_writes(_connection, _cursor, statement, _parameters, _context, _executemany):
        sql = statement.lstrip().lower()
        if sql.startswith(("insert", "delete", "update", "replace", "create virtual")) and any(
            table in sql for table in ("pc_memory_entry_fts", "pc_memory_entry_vec", "pc_artifact_fts")
        ):
            raise AssertionError("Topic Worker must not rebuild unrelated search indexes")  # noqa: TRY003

    event.listen(Engine, "before_cursor_execute", reject_general_fts_writes)
    try:
        return run_topic_memory_worker(spec, assignment)
    finally:
        event.remove(Engine, "before_cursor_execute", reject_general_fts_writes)


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
                await ArtifactProcessingIntentRepository().request(
                    connection, "scope-a", TOPIC_MEMORY_SOURCE_WINDOW_BINDING
                )
            assignment = ScopeAssignment(
                binding_name=TOPIC_MEMORY_SOURCE_WINDOW_BINDING,
                scope_id="scope-a",
                artifact_family="topic-memory",
                claimed_request_generation=1,
                fence=term.fence("single-process"),
                worker_id="worker-a",
            )
            launcher = SpawnArtifactProcessingWorkerLauncher(
                partial(_run_worker_without_general_fts_writes, TopicMemoryWorkerSpec(config=config))
            )

            handle = await launcher.start(assignment)
            try:
                with pytest.raises(RuntimeError) as error:
                    # Includes interpreter startup, database initialization, and inference validation.
                    await asyncio.wait_for(handle.wait(), timeout=30)
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


def test_stale_spawn_worker_cannot_reconfigure_the_current_runtime(tmp_path) -> None:
    class Embedding:
        profile = EmbeddingProfile(profile_id="current", model="test", dimension=2, distance="l2", normalization="unit")

        async def embed(self, texts: tuple[str, ...], /) -> EmbeddingResult:
            return EmbeddingResult(vectors=tuple((1.0, 0.0) for _ in texts))

    async def scenario() -> None:
        config = BuiltinConfig(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'stale-worker.db'}"),
            inference=InferenceConfig(generation_model="test"),
        )
        async with open_builtin_contexts(config) as old:
            spec = TopicMemoryWorkerSpec(config=config)
            scope = await old.get("scope-a")
            await scope.sources.capture(ContentCapture(source_id="source", content="Durable evidence"))
            async with old.database.transaction() as connection:
                term = await old.repositories.processing_leases.start_single_process_term(connection, "holder")
            # Keep the old deployment/spec alive while a normal Runtime
            # explicitly changes the empty Topic store from FTS to hybrid.
            embedding = Embedding()
            async with open_builtin_contexts(config, embedding_model=embedding) as current:
                expected_shape = ("hybrid", topic_memory_embedding_profile_fingerprint(embedding.profile))
                shape_query = select(
                    TOPIC_MEMORY_RETRIEVAL_SHAPE_TABLE.c.shape,
                    TOPIC_MEMORY_RETRIEVAL_SHAPE_TABLE.c.profile_fingerprint,
                )
                async with current.database.transaction() as connection:
                    assert tuple((await connection.execute(shape_query)).one()) == expected_shape
                launcher = SpawnArtifactProcessingWorkerLauncher(partial(run_topic_memory_worker, spec))
                handle = await launcher.start(
                    ScopeAssignment(
                        binding_name=TOPIC_MEMORY_SOURCE_WINDOW_BINDING,
                        scope_id="scope-a",
                        artifact_family="topic-memory",
                        claimed_request_generation=1,
                        fence=term.fence("single-process"),
                        worker_id="worker-a",
                    )
                )
                try:
                    with pytest.raises(RuntimeError) as error:
                        await asyncio.wait_for(handle.wait(), timeout=30)
                finally:
                    await handle.terminate()

                async with current.database.transaction() as connection:
                    assert tuple((await connection.execute(shape_query)).one()) == expected_shape
                    assert (
                        await current.repositories.cursors.load(
                            connection, "scope-a", TOPIC_MEMORY_SOURCE_WINDOW_BINDING
                        )
                    ) is None
                # Reject during bootstrap, not later at model-output validation.
                assert cast(Any, error.value).failure.exception_type == "TopicMemoryCapabilityError"
                assert (await current.search_topic_memories("scope-a", "evidence", mode="fts", limit=2)).hits == ()
                assert await current.browse_topic_memories("scope-a", limit=2) == ()

                content = TopicMemoryContent(title="Current", summary="Evidence", detail="Durable evidence")
                chunks = prepare_topic_memory_projection(content).chunks
                projection = prepare_topic_memory_projection(
                    content,
                    topic_embedding=(1.0, 0.0),
                    chunk_embeddings=((1.0, 0.0),) * len(chunks),
                    embedding_profile=embedding.profile,
                )
                async with current.database.transaction() as connection:
                    published = await current.repositories.topic_memories.publish_create(
                        connection, "scope-a", "current", TopicMemoryDraft(content=content), projection
                    )
                result = await current.search_topic_memories(
                    "scope-a",
                    "evidence",
                    limit=2,
                    query_vector=(1.0, 0.0),
                    embedding_profile=embedding.profile,
                )
                assert result.mode == "hybrid"
                assert [hit.artifact_ref for hit in result.hits] == [published.topic.as_ref()]
                assert (await current.get_topic_memory("scope-a", published.topic.as_ref())).topic == published.topic
                assert [item.artifact_ref for item in await current.browse_topic_memories("scope-a", limit=2)] == [
                    published.topic.as_ref()
                ]

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


def test_composition_rejects_spawn_processing_for_in_memory_sqlite() -> None:
    async def scenario() -> None:
        config = BuiltinConfig(inference=InferenceConfig(generation_model="test"))
        with pytest.raises(BuiltinConfigurationError, match="file-backed SQLite"):
            async with open_builtin_runtime(config):
                pytest.fail("in-memory SQLite must not advertise spawned Topic processing")

    asyncio.run(scenario())


def test_composition_registers_complete_binding_only_with_generation_model(tmp_path) -> None:
    async def scenario() -> None:
        config = BuiltinConfig(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'composition.db'}"),
            inference=InferenceConfig(generation_model="test"),
        )
        async with open_builtin_contexts(config) as contexts:
            bindings = _artifact_processing_bindings(config, contexts, ())
            with pytest.raises(BuiltinConfigurationError, match="child-reconstructible"):
                _artifact_processing_bindings(
                    config,
                    contexts,
                    (),
                    injected_token_estimator=character_token_estimator(),
                )
        topic_bindings = tuple(binding for binding in bindings if binding.artifact_family == "topic-memory")
        assert len(topic_bindings) == 1
        assert topic_bindings[0].binding_name == TOPIC_MEMORY_SOURCE_WINDOW_BINDING

        incomplete = BuiltinConfig(
            runtime=RuntimeConfig(topic_memory_schedule_seconds=30),
        )
        async with open_builtin_contexts(incomplete) as contexts:
            with pytest.raises(BuiltinConfigurationError, match="configured generation model"):
                _artifact_processing_bindings(incomplete, contexts, ())

        invalid_budget = config.model_copy(
            update={"inference": config.inference.model_copy(update={"generation_model_context_window_tokens": 1_000})}
        )
        async with open_builtin_contexts(invalid_budget) as contexts:
            with pytest.raises(BuiltinConfigurationError, match="budget"):
                _artifact_processing_bindings(invalid_budget, contexts, ())

    asyncio.run(scenario())


def _run_topic_worker_without_server_imports(spec, assignment):
    # Install inside the actual child; blocking imports in the parent does not
    # constrain a spawned interpreter. Intercept cached modules as well.
    import builtins

    original_import = builtins.__import__

    def reject_server_import(name, *args, **kwargs):
        if name == "powercontext.server" or name.startswith("powercontext.server."):
            raise ModuleNotFoundError("Server dependencies are unavailable in the builtin SDK", name=name)  # noqa: TRY003
        return original_import(name, *args, **kwargs)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(builtins, "__import__", reject_server_import)
        return run_topic_memory_worker(spec, assignment)


def test_topic_worker_without_server_dependencies_acknowledges_and_replays_in_spawn_child(tmp_path) -> None:
    async def scenario() -> None:
        config = BuiltinConfig(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'builtin-worker.db'}"),
            inference=InferenceConfig(generation_model="test"),
        )
        async with open_builtin_contexts(config) as contexts:
            scope = await contexts.get("scope-a")
            intents = ArtifactProcessingIntentRepository()
            async with contexts.database.transaction() as connection:
                term = await contexts.repositories.processing_leases.start_single_process_term(connection, "holder")
                accepted = await intents.request(connection, "scope-a", TOPIC_MEMORY_SOURCE_WINDOW_BINDING)
            assignment = ScopeAssignment(
                binding_name=TOPIC_MEMORY_SOURCE_WINDOW_BINDING,
                scope_id="scope-a",
                artifact_family="topic-memory",
                claimed_request_generation=accepted.requested_generation,
                fence=term.fence("single-process"),
                worker_id="builtin-worker",
            )
            launcher = SpawnArtifactProcessingWorkerLauncher(
                partial(_run_topic_worker_without_server_imports, TopicMemoryWorkerSpec(config=config))
            )

            async def invoke() -> None:
                handle = await launcher.start(assignment)
                try:
                    completion = await asyncio.wait_for(handle.wait(), timeout=30)
                finally:
                    await handle.terminate()
                assert completion.outcome is ArtifactProcessingWorkerOutcome.SUCCEEDED

            await invoke()
            async with contexts.database.transaction() as connection:
                acknowledged = await intents.load(connection, "scope-a", TOPIC_MEMORY_SOURCE_WINDOW_BINDING)
                assert acknowledged is not None
                assert acknowledged.requested_generation == acknowledged.handled_generation == 1
                assert acknowledged.dirty_generation == acknowledged.clean_generation == 0

            # A replay of confirmed G1 must not consume newly captured input.
            await scope.sources.capture(ContentCapture(source_id="later", content="Keep for the next request"))
            await invoke()
            async with contexts.database.transaction() as connection:
                replayed = await intents.load(connection, "scope-a", TOPIC_MEMORY_SOURCE_WINDOW_BINDING)
                cursor = await SourceCursorRepository().load(connection, "scope-a", TOPIC_MEMORY_SOURCE_WINDOW_BINDING)
                assert replayed is not None
                assert replayed.requested_generation == replayed.handled_generation == 1
                assert replayed.dirty_generation > replayed.clean_generation
                assert cursor is None

    asyncio.run(scenario())
