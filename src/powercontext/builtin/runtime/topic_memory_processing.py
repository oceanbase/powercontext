# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");

"""Topic Memory Source-window orchestration and fenced atomic publication."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextlib import AsyncExitStack, asynccontextmanager, nullcontext
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, cast
from uuid import uuid4

from pydantic import BaseModel, Field
from sqlalchemy import update
from typing_extensions import override

from powercontext._logging import log_safely
from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.topic_memory import (
    TOPIC_MEMORY_SOURCE_WINDOW_BINDING,
    PublishedTopicMemory,
    TopicMemory,
    TopicMemoryContent,
    TopicMemoryDraft,
    TopicMemoryProjection,
    TopicMemorySearchHit,
    chunk_topic_memory_detail,
    prepare_topic_memory_projection,
)
from powercontext.builtin.artifacts.topic_memory.generation import (
    MAX_TOPIC_MEMORY_STAGE_ITEMS,
    TOPIC_MEMORY_PROBE_INSTRUCTIONS,
    TopicMemoryEvidence,
    TopicMemoryEvolveInput,
    TopicMemoryEvolveOutput,
    TopicMemoryGenerationError,
    TopicMemoryGlobalInput,
    TopicMemoryGlobalOutput,
    TopicMemoryHistoricalSlot,
    TopicMemoryPlannerInput,
    TopicMemoryPlannerOutput,
    TopicMemoryProbe,
    TopicMemoryProbeCandidates,
    TopicMemoryProbeInput,
    TopicMemoryProbeOutput,
    TopicMemoryProposal,
    TopicMemoryReconcileInput,
    TopicMemoryReconcileOutput,
    TopicMemoryTemporaryInput,
    TopicMemoryTemporaryOutput,
    topic_memory_stage_fixed_prompt,
)
from powercontext.builtin.artifacts.topic_memory.relatedness import (
    topic_memory_lexical_signature,
    topic_memory_related_components,
    topic_memory_vector_centroid,
)
from powercontext.builtin.inference import EmbeddingModel, StructuredGenerator, TokenEstimator
from powercontext.builtin.inference.usage import bind_usage_reporter
from powercontext.builtin.persistence.cursors import SourceCursorRepository
from powercontext.builtin.persistence.database import AsyncDatabase
from powercontext.builtin.persistence.errors import ArtifactProcessingLeadershipLostError, GenerationConflictError
from powercontext.builtin.persistence.sources import SourceRepository, StoredSource
from powercontext.builtin.persistence.supervision import ArtifactProcessingLeaseRepository
from powercontext.builtin.persistence.tables import SOURCE_JOURNAL_HEADS_TABLE
from powercontext.builtin.persistence.topic_memory import TopicMemoryRepository
from powercontext.builtin.runtime.artifact_processing import (
    ArtifactProcessingWorkAssignment,
    ArtifactProcessingWorkerCompletion,
    ArtifactProcessingWorkerOutcome,
)
from powercontext.builtin.runtime.config import BuiltinConfig
from powercontext.builtin.sources import SourceCursor
from powercontext.builtin.statistics import ModelUsageOperation, ModelUsagePurpose
from powercontext.errors import RevisionConflictError

logger = logging.getLogger(__name__)

UsageReporter = Callable[[ModelUsagePurpose, ModelUsageOperation, Any], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class TopicMemoryStageSet:
    """Injected stage ports; arbitrary generator instances remain process-local."""

    probe: StructuredGenerator[TopicMemoryProbeInput, TopicMemoryProbeOutput]
    global_evolver: StructuredGenerator[TopicMemoryGlobalInput, TopicMemoryGlobalOutput]
    planner: StructuredGenerator[TopicMemoryPlannerInput, TopicMemoryPlannerOutput]
    evolver: StructuredGenerator[TopicMemoryEvolveInput, TopicMemoryEvolveOutput]
    temporary: StructuredGenerator[TopicMemoryTemporaryInput, TopicMemoryTemporaryOutput]
    reconciler: StructuredGenerator[TopicMemoryReconcileInput, TopicMemoryReconcileOutput]
    estimator: TokenEstimator
    input_tokens_limit: int
    fixed_prompts: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.input_tokens_limit < 1:
            raise ValueError("Topic Memory input token limit must be positive")  # noqa: TRY003

    def fits(self, value: BaseModel, stage: str = "") -> bool:
        fixed = self.fixed_prompts.get(stage, stage)
        return self.estimator.estimate(f"{fixed}\n{value.model_dump_json(exclude_none=False)}") <= self.input_tokens_limit


class TopicMemoryWorkerSpec(BaseModel):
    """Picklable child bootstrap configuration with a permanently redacted repr."""

    config: BuiltinConfig = Field(repr=False)

    @override
    def __repr__(self) -> str:
        return "TopicMemoryWorkerSpec(config=**********)"


@dataclass(frozen=True, slots=True)
class PreparedTopicMemoryOperation:
    """One fully projected CREATE or exact-head UPDATE ready for a short transaction."""

    proposal_id: str
    artifact_id: str
    current: TopicMemory | None
    draft: TopicMemoryDraft
    projection: TopicMemoryProjection


class TopicMemoryWindowSelector:
    """Choose the largest contiguous Source prefix within the Probe input budget."""

    def __init__(
        self,
        database: AsyncDatabase,
        sources: SourceRepository,
        estimator: TokenEstimator,
        *,
        context_window_tokens: int,
        probe_fixed_prompt: str | None = None,
    ) -> None:
        self._database = database
        self._sources = sources
        self._estimator = estimator
        self._input_limit = max(1, int(context_window_tokens * 0.80))
        self._probe_fixed_prompt = (
            topic_memory_stage_fixed_prompt(
                TOPIC_MEMORY_PROBE_INSTRUCTIONS,
                TopicMemoryProbeInput,
                TopicMemoryProbeOutput,
            )
            if probe_fixed_prompt is None
            else probe_fixed_prompt
        )

    async def select(self, scope_id: str, source_after: int, source_ceiling: int, /) -> int:
        limit = source_ceiling - source_after
        async with self._database.transaction() as connection:
            stored = await self._sources.list(connection, scope_id, after=source_after, limit=limit)
        if len(stored) != limit or tuple(item.journal_position for item in stored) != tuple(
            range(source_after + 1, source_ceiling + 1)
        ):
            raise TopicMemoryGenerationError("source_window_changed")
        return await asyncio.to_thread(self._largest_prefix, stored, source_after)

    def _largest_prefix(self, stored: tuple[StoredSource, ...], source_after: int) -> int:
        evidence = tuple(_project_evidence(index, item) for index, item in enumerate(stored, start=1))
        selected = 0
        for size in range(1, len(evidence) + 1):
            request = f"{self._probe_fixed_prompt}\n{TopicMemoryProbeInput(evidence=evidence[:size]).model_dump_json()}"
            tokens = self._estimator.estimate(request)
            if tokens > self._input_limit and size > 1:
                break
            selected = size
        if selected == 0:
            selected = 1
        return source_after + selected


class TopicMemoryAtomicPublisher:
    """Publish a complete Window with one Cursor CAS and one database transaction."""

    def __init__(
        self,
        database: AsyncDatabase,
        sources: SourceRepository,
        topics: TopicMemoryRepository,
        *,
        cursors: SourceCursorRepository | None = None,
        leases: ArtifactProcessingLeaseRepository | None = None,
    ) -> None:
        self._database = database
        self._sources = sources
        self._topics = topics
        self._cursors = SourceCursorRepository() if cursors is None else cursors
        self._leases = ArtifactProcessingLeaseRepository() if leases is None else leases

    async def publish(
        self,
        assignment: ArtifactProcessingWorkAssignment,
        evidence: Mapping[str, StoredSource],
        operations: Sequence[PreparedTopicMemoryOperation],
        /,
    ) -> None:
        if (
            assignment.binding_name != TOPIC_MEMORY_SOURCE_WINDOW_BINDING
            or not 0 <= assignment.source_after < assignment.source_through <= assignment.wave_target
            or len(operations) > MAX_TOPIC_MEMORY_STAGE_ITEMS
        ):
            raise TopicMemoryGenerationError("invalid_window")
        artifact_ids = [operation.artifact_id for operation in operations]
        proposal_ids = [operation.proposal_id for operation in operations]
        if (
            len(artifact_ids) != len(set(artifact_ids))
            or len(proposal_ids) != len(set(proposal_ids))
            or any(
                operation.current is not None
                and (
                    operation.current.artifact_id != operation.artifact_id
                    or operation.current.as_ref() not in operation.draft.artifacts
                )
                for operation in operations
            )
        ):
            raise TopicMemoryGenerationError("duplicate_operation")
        async with self._database.transaction() as connection:
            await self._leases.require_fence(connection, assignment.fence)
            # Make SQLite acquire the outer write transaction before the Cursor
            # repository's insert-CAS savepoint. It also freezes Source capture
            # for this scope while the exact Window is revalidated.
            await connection.execute(
                update(SOURCE_JOURNAL_HEADS_TABLE)
                .where(SOURCE_JOURNAL_HEADS_TABLE.c.scope_id == assignment.scope_id)
                .values(position=SOURCE_JOURNAL_HEADS_TABLE.c.position)
            )
            cursor = await self._cursors.load(
                connection,
                assignment.scope_id,
                assignment.binding_name,
                for_update=True,
            )
            actual_position = 0 if cursor is None else cursor.cursor.sequence
            actual_generation = None if cursor is None else cursor.generation
            if actual_position != assignment.source_after or actual_generation != assignment.cursor_generation:
                raise GenerationConflictError(
                    assignment.binding_name,
                    assignment.cursor_generation,
                    actual_generation,
                )
            count = assignment.source_through - assignment.source_after
            stored = await self._sources.list(
                connection,
                assignment.scope_id,
                after=assignment.source_after,
                limit=count,
            )
            ordered_evidence = tuple(sorted(evidence, key=_opaque_ordinal))
            if ordered_evidence != tuple(_evidence_id(index) for index in range(1, count + 1)):
                raise TopicMemoryGenerationError("invalid_evidence")
            expected = tuple(evidence[key] for key in ordered_evidence)
            if (
                len(stored) != count
                or tuple(item.journal_position for item in stored)
                != tuple(range(assignment.source_after + 1, assignment.source_through + 1))
                or tuple((item.ref, item.value) for item in stored) != tuple((item.ref, item.value) for item in expected)
            ):
                raise TopicMemoryGenerationError("source_window_changed")
            allowed_sources = {(item.ref.source_type, item.ref.source_id) for item in stored}
            for operation in operations:
                cited_sources = {(item.source_type, item.source_id) for item in operation.draft.sources}
                if not cited_sources or not cited_sources <= allowed_sources:
                    raise TopicMemoryGenerationError("invalid_evidence")
            await self._cursors.save(
                connection,
                assignment.scope_id,
                assignment.binding_name,
                SourceCursor(sequence=assignment.source_through),
                expected_generation=assignment.cursor_generation,
            )
            for operation in sorted(operations, key=_operation_order):
                if operation.current is None:
                    await self._topics.publish_create(
                        connection,
                        assignment.scope_id,
                        operation.artifact_id,
                        operation.draft,
                        operation.projection,
                    )
                else:
                    await self._topics.publish_revision(
                        connection,
                        assignment.scope_id,
                        operation.current,
                        operation.draft,
                        operation.projection,
                    )
            await self._leases.require_fence(connection, assignment.fence)


class TopicMemoryProcessor:
    """Connect the R3 Worker contract to R4 retrieval and atomic publication."""

    def __init__(
        self,
        *,
        database: AsyncDatabase,
        sources: SourceRepository,
        topics: TopicMemoryRepository,
        stages: TopicMemoryStageSet,
        publisher: TopicMemoryAtomicPublisher,
        embedding_model: EmbeddingModel | None = None,
        usage_reporter: UsageReporter | None = None,
        history_max_candidates: int = 20,
        history_rrf_threshold: int = 70,
        history_min_candidates: int = 5,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._database = database
        self._sources = sources
        self._topics = topics
        self._stages = stages
        self._publisher = publisher
        self._embedding_model = embedding_model
        self._usage_reporter = usage_reporter
        self._history_max = history_max_candidates
        self._history_threshold = history_rrf_threshold
        self._history_min = history_min_candidates
        self._id_factory = (lambda: str(uuid4())) if id_factory is None else id_factory

    async def process(self, assignment: ArtifactProcessingWorkAssignment, /) -> ArtifactProcessingWorkerCompletion:
        if (
            assignment.binding_name != TOPIC_MEMORY_SOURCE_WINDOW_BINDING
            or not 0 <= assignment.source_after < assignment.source_through <= assignment.wave_target
        ):
            raise TopicMemoryGenerationError("invalid_window")
        try:
            stored = await self._read_window(assignment)
            evidence = {_evidence_id(index): item for index, item in enumerate(stored, start=1)}
            projected = tuple(_project_evidence(index, item) for index, item in enumerate(stored, start=1))
            proposals, candidates = await self._generate(assignment.scope_id, projected)
            operations = await self._prepare_operations(assignment.scope_id, proposals, candidates, evidence)
            await self._publisher.publish(assignment, evidence, operations)
        except ArtifactProcessingLeadershipLostError:
            return ArtifactProcessingWorkerCompletion(ArtifactProcessingWorkerOutcome.LEADERSHIP_LOST)
        except GenerationConflictError:
            return ArtifactProcessingWorkerCompletion(ArtifactProcessingWorkerOutcome.CURSOR_CONFLICT)
        except RevisionConflictError:
            return ArtifactProcessingWorkerCompletion(ArtifactProcessingWorkerOutcome.HEAD_CONFLICT)
        return ArtifactProcessingWorkerCompletion()

    async def _read_window(self, assignment: ArtifactProcessingWorkAssignment) -> tuple[StoredSource, ...]:
        count = assignment.source_through - assignment.source_after
        async with self._database.transaction() as connection:
            stored = await self._sources.list(
                connection,
                assignment.scope_id,
                after=assignment.source_after,
                limit=count,
            )
        if len(stored) != count or tuple(item.journal_position for item in stored) != tuple(
            range(assignment.source_after + 1, assignment.source_through + 1)
        ):
            raise TopicMemoryGenerationError("source_window_changed")
        return stored

    async def _generate(
        self,
        scope_id: str,
        evidence: tuple[TopicMemoryEvidence, ...],
    ) -> tuple[tuple[TopicMemoryProposal, ...], dict[str, PublishedTopicMemory]]:
        with self._usage(ModelUsagePurpose.TOPIC_MEMORY_GENERATION):
            probe_output = (await self._stages.probe.generate(TopicMemoryProbeInput(evidence=evidence))).output
        self._validate_probes(probe_output.probes, evidence)
        if not probe_output.probes:
            return (), {}
        probe_candidates, candidates = await self._history(scope_id, probe_output.probes)
        global_input = TopicMemoryGlobalInput(
            evidence=evidence,
            probes=probe_candidates,
            historical=tuple(_historical_slot(key, value) for key, value in candidates.items()),
        )
        if self._stages.fits(global_input, "global"):
            with self._usage(ModelUsagePurpose.TOPIC_MEMORY_GENERATION):
                global_output = (await self._stages.global_evolver.generate(global_input)).output
            if not global_output.ambiguous:
                proposals = self._validate_proposals(global_output.proposals, evidence, candidates)
                return await self._coordinate(scope_id, proposals, candidates, evidence)
        proposals = await self._plan(probe_candidates, evidence, candidates)
        return await self._coordinate(scope_id, proposals, candidates, evidence)

    async def _history(
        self,
        scope_id: str,
        probes: tuple[TopicMemoryProbe, ...],
    ) -> tuple[tuple[TopicMemoryProbeCandidates, ...], dict[str, PublishedTopicMemory]]:
        ranked: dict[tuple[str, int], tuple[float, int, TopicMemorySearchHit]] = {}
        hits_by_probe: list[tuple[TopicMemorySearchHit, ...]] = []
        for probe_index, probe in enumerate(probes):
            result = await self._search(scope_id, " ".join((probe.query, *probe.keywords)))
            selected = tuple(hit for hit in result if hit.score >= self._history_threshold)
            if len(selected) < self._history_min:
                selected = result[: self._history_min]
            selected = selected[: self._history_max]
            hits_by_probe.append(selected)
            for hit in selected:
                key = _artifact_key(hit.artifact_ref)
                current = ranked.get(key)
                candidate = (hit.score, probe_index, hit)
                if current is None or (-candidate[0], candidate[1]) < (-current[0], current[1]):
                    ranked[key] = candidate
        ordered_keys = sorted(
            ranked,
            key=lambda key: (-ranked[key][0], ranked[key][1], key[0].encode(), -key[1]),
        )[: self._history_max]
        id_by_key = {key: f"candidate-{index:04d}" for index, key in enumerate(ordered_keys, start=1)}
        candidates: dict[str, PublishedTopicMemory] = {}
        async with self._database.transaction() as connection:
            for key in ordered_keys:
                ref = ranked[key][2].artifact_ref
                candidates[id_by_key[key]] = await self._topics.get_exact(connection, scope_id, ref)
        projected = tuple(
            TopicMemoryProbeCandidates(
                probe_id=f"probe-{index:04d}",
                evidence_ids=probe.evidence_ids,
                candidates=tuple(
                    _historical_slot(
                        id_by_key[_artifact_key(hit.artifact_ref)],
                        candidates[id_by_key[_artifact_key(hit.artifact_ref)]],
                    )
                    for hit in hits
                    if _artifact_key(hit.artifact_ref) in id_by_key
                ),
            )
            for index, (probe, hits) in enumerate(zip(probes, hits_by_probe, strict=True), start=1)
        )
        return projected, candidates

    async def _search(self, scope_id: str, query: str) -> tuple[TopicMemorySearchHit, ...]:
        query_vector = None
        profile = None
        if self._embedding_model is not None:
            with self._usage(ModelUsagePurpose.TOPIC_MEMORY_RECALL, embedding=True):
                embedded = await self._embedding_model.embed((query,))
            query_vector = embedded.vectors[0]
            profile = self._embedding_model.profile
        async with self._database.transaction() as connection:
            result = await self._topics.search(
                connection,
                scope_id,
                query,
                limit=self._history_max,
                mode="hybrid" if query_vector is not None else "fts",
                query_vector=query_vector,
                embedding_profile=profile,
            )
        return result.hits

    async def _plan(  # noqa: C901
        self,
        probes: tuple[TopicMemoryProbeCandidates, ...],
        evidence: tuple[TopicMemoryEvidence, ...],
        candidates: Mapping[str, PublishedTopicMemory],
    ) -> tuple[TopicMemoryProposal, ...]:
        with self._usage(ModelUsagePurpose.TOPIC_MEMORY_GENERATION):
            plan = (await self._stages.planner.generate(TopicMemoryPlannerInput(probes=probes))).output
        expected_probes = {probe.probe_id for probe in probes}
        observed = [probe_id for item in plan.items for probe_id in item.probe_ids]
        if set(observed) != expected_probes or len(observed) != len(set(observed)):
            raise TopicMemoryGenerationError("invalid_plan")
        targets = [item.candidate_id for item in plan.items if item.candidate_id is not None]
        if len(targets) != len(set(targets)) or any(target not in candidates for target in targets):
            raise TopicMemoryGenerationError("invalid_plan")
        probe_map = {probe.probe_id: probe for probe in probes}
        evidence_map = {item.evidence_id: item for item in evidence}
        proposals: list[TopicMemoryProposal] = []
        for index, item in enumerate(plan.items, start=1):
            ids = tuple(dict.fromkeys(eid for pid in item.probe_ids for eid in probe_map[pid].evidence_ids))
            work_evidence = tuple(evidence_map[eid] for eid in ids)
            historical = None if item.candidate_id is None else _historical_slot(item.candidate_id, candidates[item.candidate_id])
            evolve_input = TopicMemoryEvolveInput(
                work_id=f"work-{index:04d}",
                evidence=work_evidence,
                historical=historical,
            )
            if self._stages.fits(evolve_input, "evolve"):
                with self._usage(ModelUsagePurpose.TOPIC_MEMORY_GENERATION):
                    proposal = (await self._stages.evolver.generate(evolve_input)).output.proposal
            else:
                temporary: list[TopicMemoryProposal] = []
                for chunk_index, source in enumerate(work_evidence, start=1):
                    temporary_input = TopicMemoryTemporaryInput(work_id=evolve_input.work_id, evidence=(source,))
                    if not self._stages.fits(temporary_input, "temporary"):
                        raise TopicMemoryGenerationError("input_budget_exceeded")
                    with self._usage(ModelUsagePurpose.TOPIC_MEMORY_GENERATION):
                        output = (await self._stages.temporary.generate(temporary_input)).output
                    for temp in output.proposals:
                        if temp.candidate_id is not None or not set(temp.evidence_ids) <= {source.evidence_id}:
                            raise TopicMemoryGenerationError("invalid_temporary")
                        temporary.append(
                            temp.model_copy(update={"proposal_id": f"temp-{index:04d}-{chunk_index:04d}-{len(temporary):04d}"})
                        )
                        if len(temporary) > MAX_TOPIC_MEMORY_STAGE_ITEMS:
                            raise TopicMemoryGenerationError("temporary_limit")
                flattened = TopicMemoryEvolveInput(
                    work_id=evolve_input.work_id,
                    temporary=tuple(temporary),
                    historical=historical,
                )
                if not self._stages.fits(flattened, "evolve"):
                    raise TopicMemoryGenerationError("input_budget_exceeded")
                with self._usage(ModelUsagePurpose.TOPIC_MEMORY_GENERATION):
                    proposal = (await self._stages.evolver.generate(flattened)).output.proposal
            if proposal is not None:
                if proposal.candidate_id != item.candidate_id or not set(proposal.evidence_ids) <= set(ids):
                    raise TopicMemoryGenerationError("invalid_work_item")
                proposals.append(proposal)
        return self._validate_proposals(tuple(proposals), evidence, candidates)

    async def _coordinate(
        self,
        scope_id: str,
        proposals: tuple[TopicMemoryProposal, ...],
        candidates: dict[str, PublishedTopicMemory],
        evidence: tuple[TopicMemoryEvidence, ...],
    ) -> tuple[tuple[TopicMemoryProposal, ...], dict[str, PublishedTopicMemory]]:
        proposals = tuple(
            proposal.model_copy(update={"proposal_id": f"proposal-{index:04d}"})
            for index, proposal in enumerate(proposals, start=1)
        )
        secondary: list[frozenset[str]] = []
        vectors: list[tuple[float, ...] | None] = []
        for proposal in proposals:
            if proposal.candidate_id is not None:
                secondary.append(frozenset({proposal.candidate_id}))
                vectors.append(None)
                continue
            signature = topic_memory_lexical_signature(proposal.content)
            vector = await self._signature_vector(proposal.content)
            profile = None if self._embedding_model is None else self._embedding_model.profile
            async with self._database.transaction() as connection:
                result = await self._topics.search(
                    connection,
                    scope_id,
                    signature,
                    limit=self._history_max,
                    mode="hybrid" if vector is not None else "fts",
                    query_vector=vector,
                    embedding_profile=profile,
                )
                ids: set[str] = set()
                for hit in result.hits:
                    candidate_id = next(
                        (key for key, value in candidates.items() if value.topic.as_ref() == hit.artifact_ref),
                        None,
                    )
                    if candidate_id is None:
                        candidate_id = f"candidate-{len(candidates) + 1:04d}"
                        candidates[candidate_id] = await self._topics.get_exact(connection, scope_id, hit.artifact_ref)
                    ids.add(candidate_id)
            secondary.append(frozenset(ids))
            vectors.append(vector)
        components = topic_memory_related_components(
            proposals,
            secondary_candidates=tuple(secondary),
            vectors=tuple(vectors),
        )
        coordinated: list[TopicMemoryProposal] = []
        for component_index, component in enumerate(components, start=1):
            members = tuple(proposals[index] for index in component)
            creates = tuple(member for member in members if member.candidate_id is None)
            history_ids = sorted(
                {*(member.candidate_id for member in members if member.candidate_id is not None), *(cid for index in component for cid in secondary[index])}
                - {None}
            )
            if len(history_ids) > MAX_TOPIC_MEMORY_STAGE_ITEMS:
                raise TopicMemoryGenerationError("related_history_limit")
            if not creates or (len(component) == 1 and not history_ids):
                coordinated.extend(members)
                continue
            reconcile_input = TopicMemoryReconcileInput(
                component_id=f"component-{component_index:04d}",
                proposals=members,
                historical=tuple(_historical_slot(candidate_id, candidates[candidate_id]) for candidate_id in history_ids),
            )
            if not self._stages.fits(reconcile_input, "reconcile"):
                raise TopicMemoryGenerationError("input_budget_exceeded")
            with self._usage(ModelUsagePurpose.TOPIC_MEMORY_GENERATION):
                reconciled = (await self._stages.reconciler.generate(reconcile_input)).output.proposals
            self._validate_reconciliation(
                members,
                reconciled,
                candidates,
                evidence,
                allowed_targets=set(history_ids),
            )
            coordinated.extend(reconciled)
        validated = self._validate_proposals(tuple(coordinated), evidence, candidates)
        return validated, candidates

    async def _signature_vector(self, content: TopicMemoryContent) -> tuple[float, ...] | None:
        if self._embedding_model is None:
            return None
        chunks = chunk_topic_memory_detail(content.detail)
        texts = (f"{content.title}\n{content.summary}", *(chunk.text for chunk in chunks))
        with self._usage(ModelUsagePurpose.TOPIC_MEMORY_RECALL, embedding=True):
            vectors = (await self._embedding_model.embed(tuple(texts))).vectors
        return topic_memory_vector_centroid(
            vectors[0],
            tuple((len(chunk.text), vector) for chunk, vector in zip(chunks, vectors[1:], strict=True)),
            topic_length=len(texts[0]),
        )

    async def _prepare_operations(
        self,
        scope_id: str,
        proposals: tuple[TopicMemoryProposal, ...],
        candidates: Mapping[str, PublishedTopicMemory],
        evidence: Mapping[str, StoredSource],
    ) -> tuple[PreparedTopicMemoryOperation, ...]:
        operations: list[PreparedTopicMemoryOperation] = []
        used_targets: set[str] = set()
        for proposal in proposals:
            proposal_id = proposal.proposal_id
            if proposal_id is None:
                raise TopicMemoryGenerationError("invalid_proposal")
            current = None
            artifact_id = ""
            artifact_lineage: tuple[ArtifactRef, ...] = ()
            if proposal.candidate_id is not None:
                if proposal.candidate_id in used_targets:
                    raise TopicMemoryGenerationError("duplicate_target")
                used_targets.add(proposal.candidate_id)
                published = candidates[proposal.candidate_id]
                current = published.topic
                artifact_id = current.artifact_id
                artifact_lineage = (current.as_ref(),)
            else:
                artifact_id = self._id_factory()
            source_refs = tuple(evidence[evidence_id].ref for evidence_id in proposal.evidence_ids)
            content = TopicMemoryContent.model_validate(proposal.content.model_dump())
            draft = TopicMemoryDraft(
                content=content,
                sources=source_refs,
                artifacts=artifact_lineage,
            )
            projection = await self._projection(content)
            operations.append(
                PreparedTopicMemoryOperation(
                    proposal_id=proposal_id,
                    artifact_id=artifact_id,
                    current=current,
                    draft=draft,
                    projection=projection,
                )
            )
        return tuple(operations)

    async def _projection(self, content: TopicMemoryContent) -> TopicMemoryProjection:
        if self._embedding_model is None:
            return prepare_topic_memory_projection(content)
        chunks = chunk_topic_memory_detail(content.detail)
        texts = (f"{content.title}\n{content.summary}", *(chunk.text for chunk in chunks))
        with self._usage(ModelUsagePurpose.TOPIC_MEMORY_INDEXING, embedding=True):
            vectors = (await self._embedding_model.embed(tuple(texts))).vectors
        return prepare_topic_memory_projection(
            content,
            topic_embedding=vectors[0],
            chunk_embeddings=tuple(vectors[1:]),
            embedding_profile=self._embedding_model.profile,
        )

    def _validate_probes(
        self,
        probes: tuple[TopicMemoryProbe, ...],
        evidence: tuple[TopicMemoryEvidence, ...],
    ) -> None:
        allowed = {item.evidence_id for item in evidence}
        if any(not probe.evidence_ids or not set(probe.evidence_ids) <= allowed for probe in probes):
            raise TopicMemoryGenerationError("invalid_evidence")

    def _validate_proposals(
        self,
        proposals: tuple[TopicMemoryProposal, ...],
        evidence: tuple[TopicMemoryEvidence, ...],
        candidates: Mapping[str, PublishedTopicMemory],
    ) -> tuple[TopicMemoryProposal, ...]:
        if len(proposals) > MAX_TOPIC_MEMORY_STAGE_ITEMS:
            raise TopicMemoryGenerationError("proposal_limit")
        allowed = {item.evidence_id for item in evidence}
        targets: set[str] = set()
        proposal_ids: set[str] = set()
        for proposal in proposals:
            if not proposal.evidence_ids or not set(proposal.evidence_ids) <= allowed:
                raise TopicMemoryGenerationError("invalid_evidence")
            if proposal.candidate_id is not None:
                if proposal.candidate_id not in candidates or proposal.candidate_id in targets:
                    raise TopicMemoryGenerationError("invalid_target")
                targets.add(proposal.candidate_id)
            if proposal.proposal_id is not None:
                if proposal.proposal_id in proposal_ids:
                    raise TopicMemoryGenerationError("invalid_proposal")
                proposal_ids.add(proposal.proposal_id)
        return proposals

    def _validate_reconciliation(
        self,
        inputs: tuple[TopicMemoryProposal, ...],
        outputs: tuple[TopicMemoryProposal, ...],
        candidates: Mapping[str, PublishedTopicMemory],
        evidence: tuple[TopicMemoryEvidence, ...],
        *,
        allowed_targets: set[str],
    ) -> None:
        input_ids = {item.proposal_id for item in inputs}
        output_ids = [item.proposal_id for item in outputs]
        if None in output_ids or not set(output_ids) <= input_ids or len(output_ids) != len(set(output_ids)):
            raise TopicMemoryGenerationError("invalid_reconciliation")
        input_targets = {item.candidate_id for item in inputs if item.candidate_id is not None}
        input_by_id = {item.proposal_id: item for item in inputs}
        output_by_id = {item.proposal_id: item for item in outputs}
        input_evidence = {evidence_id for item in inputs for evidence_id in item.evidence_ids}
        output_targets = [item.candidate_id for item in outputs if item.candidate_id is not None]
        if (
            not input_targets <= set(output_targets)
            or not set(output_targets) <= allowed_targets
            or len(output_targets) != len(set(output_targets))
        ):
            raise TopicMemoryGenerationError("identity_merge")
        if any(
            item.proposal_id not in output_by_id
            or output_by_id[item.proposal_id].candidate_id != item.candidate_id
            for item in input_by_id.values()
            if item.candidate_id is not None
        ):
            raise TopicMemoryGenerationError("identity_merge")
        if any(not set(item.evidence_ids) <= input_evidence for item in outputs):
            raise TopicMemoryGenerationError("invalid_reconciliation")
        self._validate_proposals(outputs, evidence, candidates)

    def _usage(self, purpose: ModelUsagePurpose, *, embedding: bool = False):
        reporter = self._usage_reporter
        if reporter is None:
            return nullcontext()

        async def safe_report(
            reported_purpose: ModelUsagePurpose,
            operation: ModelUsageOperation,
            usage: Any,
        ) -> None:
            try:
                await reporter(reported_purpose, operation, usage)
            except Exception as error:
                log_safely(
                    logger,
                    logging.ERROR,
                    "Topic Memory model usage recording failed",
                    extra={
                        "event": "topic_memory.usage.failed",
                        "exception_type": type(error).__name__,
                        "purpose": reported_purpose.value,
                        "operation": operation.value,
                        "outcome": "failure",
                        "unit": "topic_memory",
                    },
                )

        return bind_usage_reporter(
            safe_report,
            generation_purpose=None if embedding else purpose,
            embedding_purpose=purpose if embedding else None,
        )


def _project_evidence(index: int, stored: StoredSource) -> TopicMemoryEvidence:
    value = stored.value
    content = getattr(value, "content", None)
    if not isinstance(content, str) or not content.strip():
        description = getattr(value, "description", None)
        content = (
            description
            if isinstance(description, str) and description.strip()
            else f"{stored.ref.source_type} source evidence"
        )
    metadata = getattr(value, "metadata", {})
    if not isinstance(metadata, dict):
        metadata = {}
    return TopicMemoryEvidence(
        evidence_id=_evidence_id(index),
        source_type=stored.ref.source_type,
        content=content,
        metadata=metadata,
    )


def _evidence_id(index: int) -> str:
    return f"evidence-{index:04d}"


def _opaque_ordinal(value: str) -> int:
    try:
        return int(value.rsplit("-", maxsplit=1)[1])
    except (IndexError, ValueError):
        raise TopicMemoryGenerationError("invalid_evidence") from None


def _historical_slot(candidate_id: str, published: PublishedTopicMemory) -> TopicMemoryHistoricalSlot:
    return TopicMemoryHistoricalSlot(candidate_id=candidate_id, **published.topic.content.model_dump())


def _artifact_key(ref: ArtifactRef) -> tuple[str, int]:
    return ref.artifact_id, ref.revision


def _operation_order(operation: PreparedTopicMemoryOperation) -> tuple[bytes, bytes]:
    return operation.artifact_id.encode(), operation.proposal_id.encode()


def run_topic_memory_worker(
    spec: TopicMemoryWorkerSpec,
    assignment: ArtifactProcessingWorkAssignment,
    /,
) -> ArtifactProcessingWorkerCompletion:
    """Module-level spawn entrypoint that owns one narrow child lifecycle."""

    return asyncio.run(_run_topic_memory_worker(spec, assignment))


async def _run_topic_memory_worker(
    spec: TopicMemoryWorkerSpec,
    assignment: ArtifactProcessingWorkAssignment,
) -> ArtifactProcessingWorkerCompletion:
    async with _open_topic_memory_processor(spec, assignment.scope_id) as processor:
        return await processor.process(assignment)


@asynccontextmanager
async def _open_topic_memory_processor(spec: TopicMemoryWorkerSpec, scope_id: str):
    """Reconstruct only the DB, index and inference resources needed by one child."""

    from pydantic_ai.settings import ModelSettings

    from powercontext.builtin.artifacts.topic_memory.generation import (
        TOPIC_MEMORY_EVOLVE_INSTRUCTIONS,
        TOPIC_MEMORY_GLOBAL_INSTRUCTIONS,
        TOPIC_MEMORY_PLANNER_INSTRUCTIONS,
        TOPIC_MEMORY_PROBE_INSTRUCTIONS,
        TOPIC_MEMORY_RECONCILE_INSTRUCTIONS,
        TOPIC_MEMORY_TEMPORARY_INSTRUCTIONS,
        BudgetedTopicMemoryGenerator,
        topic_memory_stage_budget,
        topic_memory_stage_fixed_prompt,
    )
    from powercontext.builtin.inference.pydantic_ai import InferenceLimits, PydanticAIStructuredGenerator
    from powercontext.builtin.inference.usage import UsageReportingEmbeddingModel, UsageReportingStructuredGenerator
    from powercontext.builtin.runtime.composition import (
        BuiltinConfigurationError,
        _embedding_models,
        _open_pydantic_ai_model,
        open_builtin_contexts,
    )

    config = spec.config
    inference = config.inference
    if inference.generation_model is None:
        raise BuiltinConfigurationError("topic-memory-generation")
    budget = topic_memory_stage_budget(
        context_window_tokens=inference.generation_model_context_window_tokens,
        max_requests=inference.generation_max_requests,
        model_settings=inference.generation_model_settings,
    )
    limits = InferenceLimits(
        timeout_seconds=inference.generation_timeout_seconds,
        max_requests=inference.generation_max_requests,
        max_output_tokens_per_request=budget.max_output_tokens_per_request,
        output_tokens_limit=budget.output_tokens_limit,
    )
    settings = cast(ModelSettings, dict(inference.generation_model_settings))
    async with AsyncExitStack() as resources:
        _, model = await _open_pydantic_ai_model(
            inference.generation_model,
            base_url=inference.generation_base_url,
            headers=inference.generation_headers,
            resources=resources,
            instrumentation=None,
        )
        raw_embedding, _ = await _embedding_models(inference, resources, None)
        embedding = None if raw_embedding is None else UsageReportingEmbeddingModel(raw_embedding)
        contexts = await resources.enter_async_context(open_builtin_contexts(config, embedding_model=embedding))

        fixed_prompts: dict[str, str] = {}

        def stage(
            input_type: type[BaseModel],
            output_type: type[BaseModel],
            instructions: str,
            name: str,
            stage_name: str,
        ):
            fixed_prompt = topic_memory_stage_fixed_prompt(instructions, input_type, output_type)
            fixed_prompts[stage_name] = fixed_prompt
            raw = PydanticAIStructuredGenerator(
                model=model,
                instructions=instructions,
                input_type=input_type,
                output_type=output_type,
                limits=limits,
                model_settings=settings,
                name=name,
            )
            bounded = BudgetedTopicMemoryGenerator(
                raw,
                estimator=contexts.token_estimator,
                budget=budget,
                fixed_prompt=fixed_prompt,
            )
            return UsageReportingStructuredGenerator(bounded)

        stages = TopicMemoryStageSet(
            probe=stage(
                TopicMemoryProbeInput,
                TopicMemoryProbeOutput,
                TOPIC_MEMORY_PROBE_INSTRUCTIONS,
                "topic_probe",
                "probe",
            ),
            global_evolver=stage(
                TopicMemoryGlobalInput,
                TopicMemoryGlobalOutput,
                TOPIC_MEMORY_GLOBAL_INSTRUCTIONS,
                "topic_global",
                "global",
            ),
            planner=stage(
                TopicMemoryPlannerInput,
                TopicMemoryPlannerOutput,
                TOPIC_MEMORY_PLANNER_INSTRUCTIONS,
                "topic_planner",
                "planner",
            ),
            evolver=stage(
                TopicMemoryEvolveInput,
                TopicMemoryEvolveOutput,
                TOPIC_MEMORY_EVOLVE_INSTRUCTIONS,
                "topic_evolver",
                "evolve",
            ),
            temporary=stage(
                TopicMemoryTemporaryInput,
                TopicMemoryTemporaryOutput,
                TOPIC_MEMORY_TEMPORARY_INSTRUCTIONS,
                "topic_temporary",
                "temporary",
            ),
            reconciler=stage(
                TopicMemoryReconcileInput,
                TopicMemoryReconcileOutput,
                TOPIC_MEMORY_RECONCILE_INSTRUCTIONS,
                "topic_reconciler",
                "reconcile",
            ),
            estimator=contexts.token_estimator,
            input_tokens_limit=budget.input_tokens_limit,
            fixed_prompts=fixed_prompts,
        )

        async def report(purpose: ModelUsagePurpose, operation: ModelUsageOperation, usage: Any) -> None:
            await contexts.statistics(scope_id).record(
                purpose,
                operation,
                usage,
                datetime.now(UTC).date(),
            )

        publisher = TopicMemoryAtomicPublisher(
            contexts.database,
            contexts.repositories.sources,
            contexts.repositories.topic_memories,
            cursors=contexts.repositories.cursors,
            leases=contexts.repositories.processing_leases,
        )
        yield TopicMemoryProcessor(
            database=contexts.database,
            sources=contexts.repositories.sources,
            topics=contexts.repositories.topic_memories,
            stages=stages,
            publisher=publisher,
            embedding_model=embedding,
            usage_reporter=report,
            history_max_candidates=config.runtime.topic_memory_history_max_candidates,
            history_rrf_threshold=config.runtime.topic_memory_history_rrf_threshold,
            history_min_candidates=config.runtime.topic_memory_history_min_candidates,
        )


__all__ = [
    "PreparedTopicMemoryOperation",
    "TopicMemoryAtomicPublisher",
    "TopicMemoryProcessor",
    "TopicMemoryStageSet",
    "TopicMemoryWindowSelector",
    "TopicMemoryWorkerSpec",
    "run_topic_memory_worker",
]
