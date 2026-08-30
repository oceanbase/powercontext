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

"""Business-specific Runtime operations over composed built-in contexts."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable, Mapping
from contextlib import AbstractContextManager, asynccontextmanager, nullcontext
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ValidationError

from powercontext._logging import log_safely
from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.experience import (
    EXPERIENCE_INCUBATION_WINDOW_LIMIT,
    Experience,
    ExperienceSearchHit,
)
from powercontext.builtin.artifacts.handoff import (
    ActivateHandoff,
    Handoff,
    HandoffActivation,
    HandoffArtifactCitation,
    HandoffAudience,
    HandoffCitation,
    HandoffDraft,
    HandoffOmission,
    HandoffResolution,
    HandoffService,
    HandoffSourceCitation,
    HandoffStatement,
    PreparedHandoff,
    PrepareHandoff,
)
from powercontext.builtin.artifacts.memory import (
    Memory,
    MemoryCitation,
    MemoryEntryInput,
    MemoryEntryVersion,
    MemoryService,
)
from powercontext.builtin.artifacts.memory.errors import (
    CapabilityNotSupportedError,
    InvalidMemoryCitationError,
    MemoryEntryNotFoundError,
)
from powercontext.builtin.artifacts.skill import (
    ExternalSkillRegistryUnavailableError,
    ExternalSkillResolution,
    Skill,
)
from powercontext.builtin.artifacts.skill.registry import ExternalSkillRegistryService
from powercontext.builtin.context import BuiltinArtifacts, BuiltinSources
from powercontext.builtin.inference.models import InferenceUsage
from powercontext.builtin.inference.usage import bind_usage_reporter
from powercontext.builtin.review.generation import GeneratedCandidateResult, ReviewedGenerationService
from powercontext.builtin.review.service import ReviewService
from powercontext.builtin.runtime._scope_cache import (
    DEFAULT_SCOPE_CACHE_SIZE,
    ScopeCache,
    ScopeCacheObserver,
    ScopeEvictor,
)
from powercontext.builtin.runtime.errors import InvalidRuntimeRequestError
from powercontext.builtin.runtime.models import (
    ApproveArtifactCandidateRequest,
    CaptureSource,
    CommitConnectorCheckpoint,
    ConnectorCheckpointState,
    ExperienceCandidate,
    ExperienceIncubationResult,
    ExternalSkillList,
    ExternalSkillScanResult,
    GenerateExperienceRequest,
    GenerateSkillRequest,
    GetArtifactCandidateRequest,
    GetExperienceRequest,
    GetMemoryEntryRequest,
    GetSkillRequest,
    ImportExternalSkillRequest,
    ListArtifactCandidatesRequest,
    ListExternalSkillsRequest,
    MemoryChangesPage,
    MemoryEntriesPage,
    MemoryEntryRecord,
    MemoryFlushResult,
    MemoryMutationResult,
    MemorySearchPage,
    PrepareContextRequest,
    PreparedContext,
    ProposeExperienceRequest,
    ProposeSkillRequest,
    RejectArtifactCandidateRequest,
    RememberMemoryRequest,
    ResolveExternalSkillRequest,
    RetireMemoryEntryRequest,
    ReviewedCandidate,
    ReviewedCandidatePage,
    ReviseArtifactCandidateRequest,
    ReviseMemoryEntryRequest,
    RuntimeCapabilities,
    SearchMemoryRequest,
    SkillCandidate,
    SourceReceipt,
    SubmitSourceObservation,
)
from powercontext.builtin.runtime.prepared_context import PreparedContextBuild, PreparedContextBuilder
from powercontext.builtin.runtime.protocols import (
    BuiltinTriggers,
    PowerContextProvider,
    RemoteIngestion,
    RuntimeSpan,
    RuntimeTracing,
    TraceAttribute,
)
from powercontext.builtin.runtime.readiness import (
    ReadinessCheckStatus,
    RuntimeReadiness,
    RuntimeReadinessChecks,
)
from powercontext.builtin.runtime.statistics import RelationalScopedStatistics
from powercontext.builtin.sources import (
    ContentCapture,
    ContentSource,
    ExternalSkillImportMode,
    SourceCursor,
    validate_scope_id,
)
from powercontext.builtin.statistics import (
    ModelUsageOperation,
    ModelUsagePurpose,
    RecallTokenMeasurement,
    Statistics,
    StatisticsPeriod,
)
from powercontext.builtin.work import (
    HANDOFF_BOUNDARY_SOURCE_KIND,
    HANDOFF_RECEIPT_SOURCE_KIND,
    TASK_OUTCOME_SOURCE_KIND,
    WORK_CONTRACT_SOURCE_KIND,
    AcknowledgeHandoff,
    CreateWorkContract,
    HandoffAcknowledgement,
    HandoffCurrentWork,
    HandoffReceipt,
    PreparedWorkHandoff,
    RecordTaskOutcome,
    TaskOutcome,
    WorkClaim,
    WorkContinuity,
    WorkContract,
    WorkSourceKind,
    WorkSourceReceipt,
    content_digest,
    project_work_continuity,
)
from powercontext.context import PowerContext
from powercontext.errors import ArtifactNotFoundError, RevisionConflictError
from powercontext.sources import ConnectorBinding, SourceDefinitionManifest, SourceRef

if TYPE_CHECKING:
    from apscheduler.schedulers.asyncio import AsyncIOScheduler

    from powercontext.builtin.handoff_report.application import HandoffReportApplication

logger = logging.getLogger(__name__)

_MEMORY_SEARCH_STAGE = "memory.search"
_MEMORY_SEARCH_REQUESTED_MODE = "powercontext.memory.search.requested_mode"
_MEMORY_SEARCH_LIMIT = "powercontext.memory.search.limit"
_MEMORY_SEARCH_MEMORY_PRESENT = "powercontext.memory.search.memory_present"
_MEMORY_SEARCH_MODE = "powercontext.memory.search.mode"
_MEMORY_SEARCH_RESULT_COUNT = "powercontext.memory.search.result_count"

ScopeIds = Callable[[], Awaitable[tuple[str, ...]]]
ReviewServiceFactory = Callable[[str], ReviewService]
GenerationServiceFactory = Callable[[str], ReviewedGenerationService]
ExternalSkillRegistryFactory = Callable[[str], ExternalSkillRegistryService]
ExternalSkillImporter = Callable[
    [str, str, str, ExternalSkillImportMode, str | None],
    Awaitable[GeneratedCandidateResult],
]
ExperienceIncubator = Callable[[str, int], Awaitable[ExperienceIncubationResult]]
ExperienceRecall = Callable[[str, str, int], Awaitable[tuple[ExperienceSearchHit, ...]]]
StatisticsServiceFactory = Callable[[str], RelationalScopedStatistics]
RecallTokenEstimator = Callable[[str, PreparedContextBuild], Awaitable[RecallTokenMeasurement | None]]
Clock = Callable[[], datetime]
_MEMORY_SEARCH_ATTEMPTS = 3


class _RuntimeConfigurationError(ValueError):
    def __init__(self, field: str) -> None:
        super().__init__(f"{field} must be positive")


class _RuntimeStateError(RuntimeError):
    def __init__(self, code: str) -> None:
        messages = {
            "closed": "Built-in Runtime is closed",
            "empty-write": "explicit Memory write did not produce a Memory",
            "experience-incubation": "Experience incubation is not configured",
            "external-skill-registry": "External Skill Registry is not configured",
            "remote-ingestion": "Remote Source ingestion is not configured",
            "review": "Candidate Review services are not configured",
            "scheduler": "Built-in Runtime scheduler is already started",
            "statistics": "Statistics services are not configured",
        }
        super().__init__(messages[code])


class ScopedSourceApplication:
    """Capture raw integration content in one Source partition."""

    def __init__(self, runtime: BuiltinRuntime, scope_id: str) -> None:
        self._runtime = runtime
        self.scope_id = validate_scope_id(scope_id)

    async def capture(self, value: CaptureSource, /) -> SourceReceipt:
        async with self._runtime._context(self.scope_id) as context:
            source, sequence = await context.sources.capture(
                ContentCapture(
                    source_id=value.source_id,
                    content=value.content,
                    metadata=value.model_dump(mode="json")["metadata"],
                )
            )
            return SourceReceipt(source_ref=context.sources.catalog.as_ref(source), sequence=sequence)


class SourceApplication:
    """Select a scoped Source application service."""

    def __init__(self, runtime: BuiltinRuntime) -> None:
        self._runtime = runtime

    def for_scope(self, scope_id: str, /) -> ScopedSourceApplication:
        return ScopedSourceApplication(self._runtime, scope_id)


class RemoteIngestionApplication:
    """Expose worker-owned Definition and observation operations."""

    def __init__(self, runtime: BuiltinRuntime, service: RemoteIngestion | None) -> None:
        self._runtime = runtime
        self._service = service

    def _require_service(self) -> RemoteIngestion:
        if self._service is None:
            raise _RuntimeStateError("remote-ingestion")
        return self._service

    async def register(self, manifest: SourceDefinitionManifest, /) -> SourceDefinitionManifest:
        async with self._runtime._operation():
            return await self._require_service().register_source_definition(manifest)

    async def checkpoint(self, binding: ConnectorBinding, /) -> ConnectorCheckpointState:
        async with self._runtime._operation():
            return await self._require_service().connector_checkpoint(binding)

    async def submit(self, request: SubmitSourceObservation, /) -> SourceReceipt:
        async with self._runtime._operation():
            return await self._require_service().submit_source_observation(request)

    async def commit(self, request: CommitConnectorCheckpoint, /) -> ConnectorCheckpointState:
        async with self._runtime._operation():
            return await self._require_service().commit_connector_checkpoint(request)


class ScopedStatisticsApplication:
    """Read product statistics and record model usage for one scope."""

    def __init__(self, runtime: BuiltinRuntime, scope_id: str) -> None:
        self._runtime = runtime
        self.scope_id = validate_scope_id(scope_id)

    async def overview(self, *, period: StatisticsPeriod = StatisticsPeriod.THIRTY_DAYS) -> Statistics:
        async with self._runtime._scope_operation(self.scope_id):
            return await self._runtime._statistics(self.scope_id).overview(period, self._runtime._clock())

    async def record_model_usage(
        self,
        purpose: ModelUsagePurpose,
        operation: ModelUsageOperation,
        usage: InferenceUsage,
        /,
    ) -> None:
        try:
            async with self._runtime._scope_operation(self.scope_id):
                await self._runtime._statistics(self.scope_id).record(
                    purpose,
                    operation,
                    usage,
                    self._runtime._clock().astimezone(UTC).date(),
                )
        except Exception as error:
            log_safely(
                logger,
                logging.ERROR,
                "Model usage recording failed",
                exc_info=error,
                extra={
                    "event": "statistics.model_usage.failed",
                    "purpose": purpose.value,
                    "operation": operation.value,
                    "outcome": "failure",
                    "unit": "statistics",
                },
            )

    async def record_recall(self, measurement: RecallTokenMeasurement, /) -> None:
        try:
            async with self._runtime._scope_operation(self.scope_id):
                await self._runtime._statistics(self.scope_id).record_recall(
                    measurement,
                    self._runtime._clock().astimezone(UTC).date(),
                )
        except Exception as error:
            log_safely(
                logger,
                logging.ERROR,
                "Recall token recording failed",
                exc_info=error,
                extra={
                    "event": "statistics.recall_tokens.failed",
                    "estimator_id": measurement.estimator.estimator_id,
                    "outcome": "failure",
                    "unit": "statistics",
                },
            )


class StatisticsApplication:
    """Select scoped product statistics."""

    def __init__(self, runtime: BuiltinRuntime) -> None:
        self._runtime = runtime

    def for_scope(self, scope_id: str, /) -> ScopedStatisticsApplication:
        return ScopedStatisticsApplication(self._runtime, scope_id)


class ScopedContextApplication:
    """Prepare final context for one scope using Runtime-owned source policy."""

    def __init__(self, runtime: BuiltinRuntime, scope_id: str) -> None:
        self._runtime = runtime
        self.scope_id = validate_scope_id(scope_id)

    async def prepare(self, request: PrepareContextRequest, /) -> PreparedContext:
        async with self._runtime._scope_operation(self.scope_id):
            return await self._prepare(request)

    async def _prepare(self, request: PrepareContextRequest, /) -> PreparedContext:
        builder = PreparedContextBuilder()
        async with (
            self._runtime._context(self.scope_id, embedding_purpose=ModelUsagePurpose.MEMORY_RECALL) as context,
            self._runtime._locked(self.scope_id),
        ):
            with self._runtime._stage(
                _MEMORY_SEARCH_STAGE,
                attributes={
                    _MEMORY_SEARCH_REQUESTED_MODE: "auto",
                    _MEMORY_SEARCH_LIMIT: builder.memory_candidate_limit,
                },
            ) as span:
                service = context.artifacts.memory
                current = await _head_or_none(service, context.artifacts.memory_artifact_id)
                memory_hits = ()
                search_mode: str | None = None
                if current is not None:
                    result = await service.search(
                        request.query,
                        memories=(current,),
                        limit=builder.memory_candidate_limit,
                        mode="auto",
                    )
                    memory_hits = result.hits
                    search_mode = result.mode
                if span is not None:
                    attributes: dict[str, TraceAttribute] = {
                        _MEMORY_SEARCH_MEMORY_PRESENT: current is not None,
                        _MEMORY_SEARCH_RESULT_COUNT: len(memory_hits),
                    }
                    if search_mode is not None:
                        attributes[_MEMORY_SEARCH_MODE] = search_mode
                    span.set_attributes(attributes)

            experience_recall = self._runtime._experience_recall
            with self._runtime._stage(
                "experience.search",
                attributes={
                    "powercontext.experience.search.configured": experience_recall is not None,
                    "powercontext.experience.search.limit": builder.experience_candidate_limit,
                },
            ) as span:
                experience_hits = (
                    ()
                    if experience_recall is None
                    else await experience_recall(
                        self.scope_id,
                        request.query,
                        builder.experience_candidate_limit,
                    )
                )
                if span is not None:
                    span.set_attributes({"powercontext.experience.search.result_count": len(experience_hits)})

            with self._runtime._stage(
                "context.build",
                attributes={
                    "powercontext.context.build.memory_candidate_count": len(memory_hits),
                    "powercontext.context.build.experience_candidate_count": len(experience_hits),
                },
            ) as span:
                build = builder.build_result(
                    request=request,
                    memory_ref=None if current is None else current.as_ref(),
                    hits=memory_hits,
                    experience_hits=experience_hits,
                )
                if span is not None:
                    span.set_attributes({
                        "powercontext.context.build.selected_count": len(build.origins),
                        "powercontext.context.build.status": build.context.status,
                        "powercontext.context.build.content_bytes": build.context.content_bytes,
                    })
        if self._runtime._recall_token_estimator is not None:
            try:
                measurement = await self._runtime._recall_token_estimator(self.scope_id, build)
            except Exception as error:
                log_safely(
                    logger,
                    logging.ERROR,
                    "Recall token estimation failed",
                    exc_info=error,
                    extra={
                        "event": "statistics.recall_tokens.estimation_failed",
                        "outcome": "failure",
                        "unit": "statistics",
                    },
                )
            else:
                if measurement is not None:
                    await self._runtime.statistics.for_scope(self.scope_id).record_recall(measurement)
        return build.context


class ContextApplication:
    """Select the scoped context-preparation application service."""

    def __init__(self, runtime: BuiltinRuntime) -> None:
        self._runtime = runtime

    def for_scope(self, scope_id: str, /) -> ScopedContextApplication:
        return ScopedContextApplication(self._runtime, scope_id)


class ScopedExperienceApplication:
    """Propose and exactly read Experience Artifacts in one scope."""

    def __init__(self, runtime: BuiltinRuntime, scope_id: str) -> None:
        self._runtime = runtime
        self.scope_id = validate_scope_id(scope_id)

    async def propose(self, request: ProposeExperienceRequest, /) -> ExperienceCandidate:
        async with self._runtime._scoped_operation(self.scope_id), self._runtime._locked(self.scope_id):
            service = self._runtime._review(self.scope_id)
            return await service.propose_experience(
                request.proposal,
                sources=request.sources,
                artifacts=request.artifacts,
                target=request.target,
                reason=request.reason,
            )

    async def generate(self, request: GenerateExperienceRequest, /) -> GeneratedCandidateResult:
        async with (
            self._runtime._scoped_operation(
                self.scope_id,
                generation_purpose=ModelUsagePurpose.EXPERIENCE_GENERATION,
            ),
            self._runtime._locked(self.scope_id),
        ):
            return await self._runtime._generation(self.scope_id).experience(
                sources=request.sources,
                artifacts=request.artifacts,
                target=request.target,
                reason=request.reason,
            )

    async def get(self, request: GetExperienceRequest, /) -> Experience:
        async with self._runtime._scoped_operation(self.scope_id):
            return await self._runtime._review(self.scope_id).get_experience(request.artifact)

    async def incubate(self, /, *, limit: int | None = None) -> ExperienceIncubationResult:
        """Process one independent Task Outcome Source window into Review."""

        incubator = self._runtime._experience_incubator
        if incubator is None:
            raise _RuntimeStateError("experience-incubation")
        window_limit = EXPERIENCE_INCUBATION_WINDOW_LIMIT if limit is None else limit
        if window_limit < 1:
            raise _RuntimeConfigurationError("limit")
        async with (
            self._runtime._scoped_operation(
                self.scope_id,
                generation_purpose=ModelUsagePurpose.EXPERIENCE_GENERATION,
            ),
            self._runtime._locked(self.scope_id),
        ):
            with self._runtime._stage("experience.incubation", attributes={}) as span:
                result = await incubator(self.scope_id, window_limit)
                if span is not None:
                    span.set_attributes({
                        "powercontext.experience.incubation.source_count": result.source_count,
                        "powercontext.experience.incubation.candidate_count": result.candidate_count,
                    })
                    span.set_outcome("success" if result.processed else "noop")
                return result


class ExperienceApplication:
    """Select a scoped Experience application service."""

    def __init__(self, runtime: BuiltinRuntime) -> None:
        self._runtime = runtime

    def for_scope(self, scope_id: str, /) -> ScopedExperienceApplication:
        return ScopedExperienceApplication(self._runtime, scope_id)


class ScopedSkillApplication:
    """Propose and exactly read managed Skill Artifacts in one scope."""

    def __init__(self, runtime: BuiltinRuntime, scope_id: str) -> None:
        self._runtime = runtime
        self.scope_id = validate_scope_id(scope_id)

    async def propose(self, request: ProposeSkillRequest, /) -> SkillCandidate:
        async with self._runtime._scoped_operation(self.scope_id), self._runtime._locked(self.scope_id):
            service = self._runtime._review(self.scope_id)
            return await service.propose_skill(
                request.proposal,
                sources=request.sources,
                artifacts=request.artifacts,
                target=request.target,
                reason=request.reason,
            )

    async def generate(self, request: GenerateSkillRequest, /) -> GeneratedCandidateResult:
        async with (
            self._runtime._scoped_operation(
                self.scope_id,
                generation_purpose=ModelUsagePurpose.SKILL_GENERATION,
            ),
            self._runtime._locked(self.scope_id),
        ):
            return await self._runtime._generation(self.scope_id).skill(
                origin=request.origin,
                sources=request.sources,
                artifacts=request.artifacts,
                target=request.target,
                reason=request.reason,
            )

    async def get(self, request: GetSkillRequest, /) -> Skill:
        async with self._runtime._scoped_operation(self.scope_id):
            return await self._runtime._review(self.scope_id).get_skill(request.artifact)


class SkillApplication:
    """Select a scoped managed Skill application service."""

    def __init__(self, runtime: BuiltinRuntime) -> None:
        self._runtime = runtime

    def for_scope(self, scope_id: str, /) -> ScopedSkillApplication:
        return ScopedSkillApplication(self._runtime, scope_id)


class ScopedHandoffApplication:
    """Operate temporary and committed Handoffs for one scope."""

    def __init__(self, runtime: BuiltinRuntime, scope_id: str) -> None:
        self._runtime = runtime
        self.scope_id = validate_scope_id(scope_id)

    async def activate(self, request: ActivateHandoff, /) -> HandoffActivation:
        async with self._runtime._context(
            self.scope_id,
            generation_purpose=ModelUsagePurpose.HANDOFF_GENERATION,
        ) as context:
            return await context.triggers.activate_handoff(request)

    async def prepare(self, action: PrepareHandoff, /) -> HandoffDraft:
        async with self._runtime._context(
            self.scope_id,
            generation_purpose=ModelUsagePurpose.HANDOFF_GENERATION,
        ) as context:
            return await context.artifacts.handoff.prepare(action)

    async def finalize(self, draft: HandoffDraft, /) -> PreparedHandoff:
        async with self._runtime._context(self.scope_id) as context:
            return await context.artifacts.handoff.finalize(draft)

    async def commit(self, prepared: PreparedHandoff, /) -> Handoff:
        async with self._runtime._context(self.scope_id) as context, self._runtime._locked(self.scope_id):
            return await context.artifacts.handoff.commit(prepared)

    async def continue_from(
        self,
        handoff: PreparedHandoff | ArtifactRef,
        /,
    ) -> HandoffResolution:
        async with self._runtime._context(self.scope_id) as context:
            return await context.artifacts.handoff.continue_from(handoff)

    async def continue_latest(self) -> HandoffResolution:
        async with self._runtime._context(self.scope_id) as context:
            return await context.artifacts.handoff.continue_latest()

    async def latest(self) -> Handoff | None:
        async with self._runtime._context(self.scope_id) as context:
            return await context.artifacts.handoff.latest()

    async def revision(self, reference: ArtifactRef, /) -> Handoff:
        async with self._runtime._context(self.scope_id) as context:
            return await context.artifacts.handoff.revision(reference)

    async def revisions(self) -> tuple[Handoff, ...]:
        async with self._runtime._context(self.scope_id) as context:
            return await context.artifacts.handoff.revisions()

    async def validate_evidence(self, citations: tuple[HandoffCitation, ...], /) -> None:
        """Validate exact same-scope evidence for a higher-level Work record."""

        async with self._runtime._context(self.scope_id) as context:
            await context.artifacts.handoff.validate_evidence(citations)

    @staticmethod
    def render(
        handoff: HandoffDraft | PreparedHandoff | Handoff,
        /,
        *,
        audience: HandoffAudience,
    ) -> str:
        return HandoffService.render(handoff, audience=audience)


class HandoffApplication:
    """Select the application service for one Handoff family scope."""

    def __init__(self, runtime: BuiltinRuntime) -> None:
        self._runtime = runtime

    def for_scope(self, scope_id: str, /) -> ScopedHandoffApplication:
        return ScopedHandoffApplication(self._runtime, scope_id)


class ScopedWorkApplication:
    """Orchestrate the minimal Delegation, Handoff, Continue, and Outcome loop."""

    def __init__(self, runtime: BuiltinRuntime, scope_id: str) -> None:
        self._runtime = runtime
        self.scope_id = validate_scope_id(scope_id)

    async def create_contract(self, request: CreateWorkContract, /) -> WorkSourceReceipt:
        await self._validate(_contract_evidence(request.contract))
        return await self._capture(WORK_CONTRACT_SOURCE_KIND, request.source_id, request.contract)

    async def continuity(self, selected_handoff: ArtifactRef | None = None) -> WorkContinuity:
        """Read a bounded timeline and loop coverage from the scoped Source journal."""

        async with self._runtime._context(self.scope_id) as context:
            entries = await context.sources.journal.entries()
            if selected_handoff is None:
                latest = await context.artifacts.handoff.latest()
                selected_handoff = None if latest is None else latest.as_ref()
        return project_work_continuity(self.scope_id, entries, selected_handoff=selected_handoff)

    async def record_outcome(self, request: RecordTaskOutcome, /) -> WorkSourceReceipt:
        await self._validate(_outcome_evidence(request.outcome))
        if request.outcome.handoff_receipt_ref is not None:
            await self._validate_outcome_receipt(request.outcome.handoff_receipt_ref)
        return await self._capture(TASK_OUTCOME_SOURCE_KIND, request.source_id, request.outcome)

    async def handoff_current(self, request: HandoffCurrentWork, /) -> PreparedWorkHandoff:
        await self._validate(_claims_evidence((*request.handoff.state, request.handoff.next_action)))
        boundary = await self._capture(HANDOFF_BOUNDARY_SOURCE_KIND, request.source_id, request.handoff)
        boundary_citation = HandoffSourceCitation(source_ref=boundary.source_ref)
        draft = HandoffDraft(
            objective=request.handoff.objective,
            state=tuple(_handoff_statement(claim, boundary_citation) for claim in request.handoff.state),
            disposition=request.handoff.disposition,
            next_action=(
                None
                if request.handoff.next_action is None
                else _handoff_statement(request.handoff.next_action, boundary_citation)
            ),
            omissions=tuple(HandoffOmission(text=text) for text in request.handoff.omissions),
        )
        prepared = await self._runtime.handoff.for_scope(self.scope_id).finalize(draft)
        return PreparedWorkHandoff(boundary=boundary, handoff=prepared)

    async def acknowledge(self, request: AcknowledgeHandoff, /) -> HandoffAcknowledgement:
        handoff = self._runtime.handoff.for_scope(self.scope_id)
        if request.selection == "prepared":
            if request.prepared is None:
                raise InvalidRuntimeRequestError("handoff-selection")
            resolution = await handoff.continue_from(request.prepared)
        else:
            if request.revision is None:
                raise InvalidRuntimeRequestError("handoff-selection")
            resolution = await handoff.continue_from(request.revision)
        if resolution.status == "empty":
            raise InvalidRuntimeRequestError("handoff-empty")

        unavailable = _unavailable_evidence(resolution)
        if request.status == "accepted" and unavailable:
            raise InvalidRuntimeRequestError("handoff-evidence-unavailable")
        receipt_record = HandoffReceipt(
            receiver=request.receiver,
            status=request.status,
            selection=request.selection,
            selected_revision=resolution.selected_revision,
            prepared_digest=(None if request.prepared is None else content_digest(request.prepared)),
            receiver_checks=request.receiver_checks,
            evidence_status="unavailable" if unavailable else "available",
            unavailable_evidence=unavailable,
            message=request.message,
        )
        receipt = await self._capture(HANDOFF_RECEIPT_SOURCE_KIND, request.source_id, receipt_record)
        return HandoffAcknowledgement(resolution=resolution, receipt=receipt)

    async def _validate_outcome_receipt(self, receipt_ref: SourceRef) -> None:
        async with self._runtime._context(self.scope_id) as context:
            entries = await context.sources.journal.entries()
        matching_entry = next((entry for entry in entries if entry.source_ref == receipt_ref), None)
        if matching_entry is None or not isinstance(matching_entry.source, ContentSource):
            raise InvalidRuntimeRequestError("task-outcome-handoff-receipt")
        if matching_entry.source.metadata.get("kind") != HANDOFF_RECEIPT_SOURCE_KIND:
            raise InvalidRuntimeRequestError("task-outcome-handoff-receipt")
        try:
            receipt = HandoffReceipt.model_validate_json(matching_entry.source.content)
        except ValidationError as error:
            raise InvalidRuntimeRequestError("task-outcome-handoff-receipt") from error
        if receipt.status != "accepted" or receipt.selection != "exact" or receipt.selected_revision is None:
            raise InvalidRuntimeRequestError("task-outcome-handoff-receipt")

    async def _validate(self, citations: tuple[HandoffCitation, ...]) -> None:
        if citations:
            await self._runtime.handoff.for_scope(self.scope_id).validate_evidence(citations)

    async def _capture(self, kind: WorkSourceKind, source_id: str, value: BaseModel) -> WorkSourceReceipt:
        receipt = await self._runtime.sources.for_scope(self.scope_id).capture(
            CaptureSource(
                source_id=source_id,
                content=value.model_dump_json(by_alias=True, exclude_none=False, indent=2),
                metadata={"kind": kind, "schema": value.model_dump(by_alias=True)["schema"]},
            )
        )
        return WorkSourceReceipt(
            kind=kind,
            source_ref=receipt.source_ref,
            position=receipt.sequence,
            content_digest=content_digest(value),
        )


class WorkApplication:
    """Select the high-level Work application for one stable scope."""

    def __init__(self, runtime: BuiltinRuntime) -> None:
        self._runtime = runtime

    def for_scope(self, scope_id: str, /) -> ScopedWorkApplication:
        return ScopedWorkApplication(self._runtime, scope_id)


class ScopedExternalSkillApplication:
    """Discover and exactly resolve Agent-native Skills in one local scope."""

    def __init__(self, runtime: BuiltinRuntime, scope_id: str) -> None:
        self._runtime = runtime
        self.scope_id = validate_scope_id(scope_id)

    async def scan(self) -> ExternalSkillScanResult:
        async with self._runtime._scoped_operation(self.scope_id), self._runtime._locked(self.scope_id):
            return await self._runtime._external_skills(self.scope_id).scan()

    async def list(self, request: ListExternalSkillsRequest, /) -> ExternalSkillList:
        async with self._runtime._scoped_operation(self.scope_id):
            return await self._runtime._external_skills(self.scope_id).list(
                include_unavailable=request.include_unavailable
            )

    async def resolve(self, request: ResolveExternalSkillRequest, /) -> ExternalSkillResolution:
        async with self._runtime._scoped_operation(self.scope_id):
            return await self._runtime._external_skills(self.scope_id).resolve(
                request.external_skill_id,
                request.fingerprint,
            )

    async def import_managed(self, request: ImportExternalSkillRequest, /) -> GeneratedCandidateResult:
        importer = self._runtime._external_skill_importer
        if importer is None:
            raise ExternalSkillRegistryUnavailableError()
        async with (
            self._runtime._scoped_operation(
                self.scope_id,
                generation_purpose=ModelUsagePurpose.SKILL_GENERATION,
            ),
            self._runtime._locked(self.scope_id),
        ):
            return await importer(
                self.scope_id,
                request.external_skill_id,
                request.fingerprint,
                request.mode,
                request.reason,
            )


class ExternalSkillApplication:
    """Select a scoped host-local external Skill Registry."""

    def __init__(self, runtime: BuiltinRuntime) -> None:
        self._runtime = runtime

    def for_scope(self, scope_id: str, /) -> ScopedExternalSkillApplication:
        return ScopedExternalSkillApplication(self._runtime, scope_id)


class ScopedReviewApplication:
    """Inspect and decide current Candidate heads in one scope."""

    def __init__(self, runtime: BuiltinRuntime, scope_id: str) -> None:
        self._runtime = runtime
        self.scope_id = validate_scope_id(scope_id)

    async def list(self, request: ListArtifactCandidatesRequest, /) -> ReviewedCandidatePage:
        async with self._runtime._scoped_operation(self.scope_id):
            return await self._runtime._review(self.scope_id).list_candidates(
                status=request.status,
                family=request.family,
                cursor=request.cursor,
                limit=request.limit,
            )

    async def get(self, request: GetArtifactCandidateRequest, /) -> ReviewedCandidate:
        async with self._runtime._scoped_operation(self.scope_id):
            return await self._runtime._review(self.scope_id).get_candidate(request.candidate_id)

    async def approve(self, request: ApproveArtifactCandidateRequest, /) -> ReviewedCandidate:
        async with self._runtime._scoped_operation(self.scope_id), self._runtime._locked(self.scope_id):
            return await self._runtime._review(self.scope_id).approve(
                request.candidate_id,
                request.expected_version,
            )

    async def reject(self, request: RejectArtifactCandidateRequest, /) -> ReviewedCandidate:
        async with self._runtime._scoped_operation(self.scope_id), self._runtime._locked(self.scope_id):
            return await self._runtime._review(self.scope_id).reject(
                request.candidate_id,
                request.expected_version,
                request.reason,
            )

    async def revise(self, request: ReviseArtifactCandidateRequest, /) -> ReviewedCandidate:
        async with self._runtime._scoped_operation(self.scope_id), self._runtime._locked(self.scope_id):
            return await self._runtime._review(self.scope_id).revise(
                request.candidate_id,
                request.expected_version,
                request.proposal,
                sources=request.sources,
                artifacts=request.artifacts,
                target=request.target,
                reason=request.reason,
            )


class ReviewApplication:
    """Select a scoped Candidate Review application service."""

    def __init__(self, runtime: BuiltinRuntime) -> None:
        self._runtime = runtime

    def for_scope(self, scope_id: str, /) -> ScopedReviewApplication:
        return ScopedReviewApplication(self._runtime, scope_id)


class ScopedMemoryApplication:
    """Operate one Memory Artifact identity and its Source trigger state."""

    def __init__(self, runtime: BuiltinRuntime, scope_id: str) -> None:
        self._runtime = runtime
        self.scope_id = validate_scope_id(scope_id)

    async def remember(self, request: RememberMemoryRequest, /) -> MemoryMutationResult:
        async with self._runtime._context(
            self.scope_id,
            embedding_purpose=ModelUsagePurpose.MEMORY_INDEXING,
        ) as context:
            async with self._runtime._locked(self.scope_id):
                service = context.artifacts.memory
                current = await _head_or_none(service, context.artifacts.memory_artifact_id)
                _validate_expected_revision(current, request.expected_revision)
                updated = await service.remember(memory=current, entries=request.entries, mode="append")
            if updated is None:
                raise _RuntimeStateError("empty-write")
            return MemoryMutationResult(
                previous_revision=None if current is None else current.revision,
                memory_ref=updated.as_ref(),
                entry=(
                    None
                    if current is not None and updated.as_ref() == current.as_ref()
                    else await _last_changed_entry(service, updated)
                ),
            )

    async def search(self, request: SearchMemoryRequest, /) -> MemorySearchPage:
        async with self._runtime._context(
            self.scope_id,
            generation_purpose=ModelUsagePurpose.MEMORY_RECALL,
            embedding_purpose=ModelUsagePurpose.MEMORY_RECALL,
        ) as context:
            with self._runtime._stage(
                _MEMORY_SEARCH_STAGE,
                attributes={
                    _MEMORY_SEARCH_REQUESTED_MODE: request.mode,
                    _MEMORY_SEARCH_LIMIT: request.limit,
                },
            ) as span:
                service = context.artifacts.memory
                attempt = 1
                while True:
                    current = await _head_or_none(service, context.artifacts.memory_artifact_id)
                    if current is None:
                        if span is not None:
                            span.set_attributes({
                                _MEMORY_SEARCH_MEMORY_PRESENT: False,
                                _MEMORY_SEARCH_RESULT_COUNT: 0,
                            })
                        return MemorySearchPage(memory_ref=None, mode=None)
                    try:
                        result = await service.search(
                            request.query,
                            memories=(current,),
                            limit=request.limit,
                            mode=request.mode,
                        )
                    except (CapabilityNotSupportedError, InvalidMemoryCitationError) as error:
                        latest = await _head_or_none(service, context.artifacts.memory_artifact_id)
                        if not _is_stale_memory_search(error) or latest is None or latest.as_ref() == current.as_ref():
                            raise
                        if attempt == _MEMORY_SEARCH_ATTEMPTS:
                            raise RevisionConflictError(current, latest) from error
                        attempt += 1
                        continue
                    if span is not None:
                        span.set_attributes({
                            _MEMORY_SEARCH_MEMORY_PRESENT: True,
                            _MEMORY_SEARCH_MODE: result.mode,
                            _MEMORY_SEARCH_RESULT_COUNT: len(result.hits),
                        })
                    return MemorySearchPage(
                        memory_ref=current.as_ref(),
                        mode=result.mode,
                        hits=result.hits,
                        rerank=result.rerank,
                    )

    async def list(self, *, include_inactive: bool = False) -> MemoryEntriesPage:
        async with self._runtime._context(self.scope_id) as context:
            service = context.artifacts.memory
            current = await _head_or_none(service, context.artifacts.memory_artifact_id)
            if current is None:
                return MemoryEntriesPage(memory_ref=None)
            entries = tuple(_entry_record(current, entry) for entry in await service.entries(current))
            if not include_inactive:
                entries = tuple(entry for entry in entries if entry.state == "active")
            return MemoryEntriesPage(
                memory_ref=current.as_ref(),
                entries=entries,
            )

    async def get(self, request: GetMemoryEntryRequest, /) -> MemoryEntryRecord:
        async with self._runtime._context(self.scope_id) as context:
            service = context.artifacts.memory
            citation = request.citation
            memory = await service.revision(citation.memory_ref)
            _validate_memory_identity(context.artifacts.memory_artifact_id, memory)
            return _entry_record(memory, await _cited_entry(service, memory, citation))

    async def revise(self, request: ReviseMemoryEntryRequest, /) -> MemoryMutationResult:
        async with self._runtime._context(
            self.scope_id,
            embedding_purpose=ModelUsagePurpose.MEMORY_INDEXING,
        ) as context:
            async with self._runtime._locked(self.scope_id):
                service = context.artifacts.memory
                current, entry = await _current_citation(
                    service,
                    context.artifacts.memory_artifact_id,
                    request.citation,
                )
                updated = await service.remember(
                    memory=current,
                    entries=(
                        MemoryEntryInput(
                            entry=entry,
                            kind=request.kind,
                            text=request.text,
                            reason=request.reason,
                        ),
                    ),
                    mode="append",
                )
            if updated is None:
                raise _RuntimeStateError("empty-write")
            revised = next(item for item in await service.entries(updated) if item.entry_id == entry.entry_id)
            return MemoryMutationResult(
                previous_revision=current.revision,
                memory_ref=updated.as_ref(),
                entry=_entry_record(updated, revised),
            )

    async def retire(self, request: RetireMemoryEntryRequest, /) -> MemoryMutationResult:
        async with self._runtime._context(
            self.scope_id,
            embedding_purpose=ModelUsagePurpose.MEMORY_INDEXING,
        ) as context:
            async with self._runtime._locked(self.scope_id):
                service = context.artifacts.memory
                current, entry = await _current_citation(
                    service,
                    context.artifacts.memory_artifact_id,
                    request.citation,
                )
                updated = await service.forget(current, entries=(entry,), reason=request.reason)
            retired = next(item for item in await service.entries(updated) if item.entry_id == entry.entry_id)
            return MemoryMutationResult(
                previous_revision=current.revision,
                memory_ref=updated.as_ref(),
                entry=_entry_record(updated, retired),
            )

    async def changes(self, *, since_revision: int | None = None) -> MemoryChangesPage:
        async with self._runtime._context(self.scope_id) as context:
            service = context.artifacts.memory
            current = await _head_or_none(service, context.artifacts.memory_artifact_id)
            if current is None:
                return MemoryChangesPage(memory_ref=None)
            if since_revision is not None and since_revision > current.revision:
                raise InvalidRuntimeRequestError("since-revision")
            return MemoryChangesPage(
                memory_ref=current.as_ref(),
                revisions=await service.changes(current, since_revision=since_revision),
            )

    async def flush(self, /, *, limit: int | None = None) -> MemoryFlushResult:
        async with self._runtime._context(
            self.scope_id,
            generation_purpose=ModelUsagePurpose.MEMORY_EXTRACTION,
            embedding_purpose=ModelUsagePurpose.MEMORY_INDEXING,
        ) as context:
            window_limit = self._runtime.source_window_limit if limit is None else limit
            async with self._runtime._locked(self.scope_id):
                with self._runtime._stage("memory.flush", attributes={}) as span:
                    result = await context.triggers.flush(limit=window_limit)
                    if span is not None:
                        span.set_attributes({"powercontext.memory.flush.source_count": result.source_count})
                        span.set_outcome("success" if result.processed else "noop")
                    return result

    async def cursor(self) -> SourceCursor:
        async with self._runtime._context(self.scope_id) as context:
            return await context.triggers.cursor()


class MemoryApplication:
    """Select the application service for one Memory family scope."""

    def __init__(self, runtime: BuiltinRuntime) -> None:
        self._runtime = runtime

    def for_scope(self, scope_id: str, /) -> ScopedMemoryApplication:
        return ScopedMemoryApplication(self._runtime, scope_id)


class ScheduledSourceProcessor:
    """Map APScheduler activations to scoped Source-window policies."""

    def __init__(self, runtime: BuiltinRuntime, scope_ids: ScopeIds) -> None:
        self._runtime = runtime
        self._scope_ids = scope_ids

    async def run(self) -> None:
        async with self._runtime._processor_lock:
            if self._runtime._closing or self._runtime._closed:
                return
            for scope_id in await self._scope_ids():
                if self._runtime._closing or self._runtime._closed:
                    return
                started_at = perf_counter()
                with self._runtime._background(
                    "scheduled.process_source_window",
                    operation="process_source_window",
                ) as span:
                    try:
                        result = await self._runtime.memory.for_scope(scope_id).flush()
                    except asyncio.CancelledError:
                        _log_scheduled_processing(
                            "cancelled",
                            operation="process_source_window",
                            started_at=started_at,
                        )
                        raise
                    except Exception as error:
                        _log_scheduled_processing(
                            "failure",
                            operation="process_source_window",
                            started_at=started_at,
                            error=error,
                        )
                        if span is not None:
                            span.set_outcome("failure")
                    else:
                        outcome = "success" if result.processed else "noop"
                        _log_scheduled_processing(
                            outcome,
                            operation="process_source_window",
                            started_at=started_at,
                            source_count=result.source_count,
                        )
                        if span is not None:
                            span.set_outcome(outcome)
                            span.set_attributes({"powercontext.background.source_count": result.source_count})


class ScheduledExperienceProcessor:
    """Map APScheduler activations to scoped Experience incubation windows."""

    def __init__(self, runtime: BuiltinRuntime, scope_ids: ScopeIds) -> None:
        self._runtime = runtime
        self._scope_ids = scope_ids

    async def run(self) -> None:
        async with self._runtime._processor_lock:
            if self._runtime._closing or self._runtime._closed:
                return
            for scope_id in await self._scope_ids():
                if self._runtime._closing or self._runtime._closed:
                    return
                started_at = perf_counter()
                with self._runtime._background(
                    "scheduled.incubate_experience_candidates",
                    operation="incubate_experience_candidates",
                ) as span:
                    try:
                        result = await self._runtime.experience.for_scope(scope_id).incubate()
                    except asyncio.CancelledError:
                        _log_scheduled_processing(
                            "cancelled",
                            operation="incubate_experience_candidates",
                            started_at=started_at,
                        )
                        raise
                    except Exception as error:
                        _log_scheduled_processing(
                            "failure",
                            operation="incubate_experience_candidates",
                            started_at=started_at,
                            error=error,
                        )
                        if span is not None:
                            span.set_outcome("failure")
                    else:
                        outcome = "success" if result.processed else "noop"
                        _log_scheduled_processing(
                            outcome,
                            operation="incubate_experience_candidates",
                            started_at=started_at,
                            source_count=result.source_count,
                            candidate_count=result.candidate_count,
                        )
                        if span is not None:
                            span.set_outcome(outcome)
                            span.set_attributes({
                                "powercontext.background.source_count": result.source_count,
                                "powercontext.background.candidate_count": result.candidate_count,
                            })


def _log_scheduled_processing(
    outcome: str,
    *,
    operation: str,
    started_at: float,
    error: Exception | None = None,
    source_count: int | None = None,
    candidate_count: int | None = None,
) -> None:
    extra = {
        "event": "background.operation.completed",
        "operation": operation,
        "outcome": outcome,
        "unit": "background",
        "duration_ms": max(perf_counter() - started_at, 0) * 1_000,
    }
    if source_count is not None:
        extra["source_count"] = source_count
    if candidate_count is not None:
        extra["candidate_count"] = candidate_count
    level = logging.ERROR if error is not None else logging.INFO
    log_safely(
        logger,
        level,
        "Scheduled background processing completed" if error is None else "Scheduled background processing failed",
        exc_info=error,
        extra=extra,
    )


class BuiltinRuntime:
    """Add business-specific operations over composed built-in contexts."""

    def __init__(
        self,
        *,
        provider: PowerContextProvider[BuiltinSources, BuiltinArtifacts, BuiltinTriggers],
        capabilities: RuntimeCapabilities,
        source_window_limit: int = 100,
        scope_cache_size: int = DEFAULT_SCOPE_CACHE_SIZE,
        scope_evictor: ScopeEvictor | None = None,
        scope_cache_observer: ScopeCacheObserver | None = None,
        scope_ids: ScopeIds | None = None,
        review_service: ReviewServiceFactory | None = None,
        generation_service: GenerationServiceFactory | None = None,
        experience_recall: ExperienceRecall | None = None,
        experience_incubator: ExperienceIncubator | None = None,
        external_skill_registry: ExternalSkillRegistryFactory | None = None,
        external_skill_importer: ExternalSkillImporter | None = None,
        statistics_service: StatisticsServiceFactory | None = None,
        recall_token_estimator: RecallTokenEstimator | None = None,
        readiness: RuntimeReadinessChecks | None = None,
        clock: Clock | None = None,
        tracing: RuntimeTracing | None = None,
        remote_ingestion: RemoteIngestion | None = None,
    ) -> None:
        if source_window_limit < 1:
            raise _RuntimeConfigurationError("source_window_limit")
        if scope_cache_size < 1:
            raise _RuntimeConfigurationError("scope_cache_size")
        self._provider = provider
        self._capabilities = capabilities
        self._review_service = review_service
        self._generation_service = generation_service
        self._experience_recall = experience_recall
        self._experience_incubator = experience_incubator
        self._external_skill_registry = external_skill_registry
        self._external_skill_importer = external_skill_importer
        self._statistics_service = statistics_service
        self._recall_token_estimator = recall_token_estimator
        self._readiness = RuntimeReadinessChecks() if readiness is None else readiness
        self._clock = _utc_now if clock is None else clock
        self._tracing = tracing
        self.source_window_limit = source_window_limit
        self._scope_cache = ScopeCache(
            scope_cache_size,
            evictor=scope_evictor,
            observer=scope_cache_observer,
        )
        self._processor_lock = asyncio.Lock()
        self._close_lock = asyncio.Lock()
        self._lifecycle = asyncio.Condition()
        self._active_operations = 0
        self._operation_depths: dict[asyncio.Task[Any], int] = {}
        self._closing = False
        self._closed = False
        self._scheduler: AsyncIOScheduler | None = None
        self._scheduler_runtime_key: str | None = None
        self.sources = SourceApplication(self)
        self.ingestion = RemoteIngestionApplication(self, remote_ingestion)
        self.context = ContextApplication(self)
        self.experience = ExperienceApplication(self)
        self.external_skills = ExternalSkillApplication(self)
        self.handoff = HandoffApplication(self)
        self.work = WorkApplication(self)
        self.memory = MemoryApplication(self)
        self.review = ReviewApplication(self)
        self.skill = SkillApplication(self)
        self.statistics = StatisticsApplication(self)
        self.handoff_report: HandoffReportApplication | None = None
        self.processor = None if scope_ids is None else ScheduledSourceProcessor(self, scope_ids)
        self.experience_processor = (
            None if scope_ids is None or experience_incubator is None else ScheduledExperienceProcessor(self, scope_ids)
        )

    async def __aenter__(self) -> BuiltinRuntime:
        return self

    async def __aexit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        await self.close()

    async def capabilities(self) -> RuntimeCapabilities:
        async with self._operation():
            return self._capabilities

    async def readiness(self) -> RuntimeReadiness:
        """Check whether the Runtime and its assembled dependencies can accept work."""

        async with self._operation():
            dependencies = await self._readiness.run()
        return RuntimeReadiness(
            status=dependencies.status,
            checks={"runtime": ReadinessCheckStatus.READY, **dependencies.checks},
        )

    def start_scheduler(
        self,
        scheduler_path: str | Path,
        schedule_seconds: float | None,
        *,
        experience_schedule_seconds: float | None = None,
    ) -> None:
        """Start the APScheduler time adapter for this Runtime."""

        if schedule_seconds is None and experience_schedule_seconds is None:
            raise _RuntimeConfigurationError("schedule_seconds")
        if schedule_seconds is not None and schedule_seconds <= 0:
            raise _RuntimeConfigurationError("schedule_seconds")
        if experience_schedule_seconds is not None and experience_schedule_seconds <= 0:
            raise _RuntimeConfigurationError("experience_schedule_seconds")
        if schedule_seconds is not None and self.processor is None:
            raise _RuntimeConfigurationError("scope_ids")
        if experience_schedule_seconds is not None and self.experience_processor is None:
            raise _RuntimeStateError("experience-incubation")
        if self._scheduler is not None:
            raise _RuntimeStateError("scheduler")
        from powercontext.builtin.runtime.scheduler import (
            configure_experience_incubation_job,
            configure_source_window_job,
            create_scheduler,
            register_processors,
            scheduler_runtime_key,
            unregister_processor,
        )

        runtime_key = scheduler_runtime_key(scheduler_path)
        scheduler: AsyncIOScheduler | None = None
        register_processors(
            runtime_key,
            source_window=None if schedule_seconds is None or self.processor is None else self.processor.run,
            experience_incubation=(
                None
                if experience_schedule_seconds is None or self.experience_processor is None
                else self.experience_processor.run
            ),
        )
        self._scheduler_runtime_key = runtime_key
        try:
            scheduler = create_scheduler(scheduler_path)
            self._scheduler = scheduler
            scheduler.start(paused=True)
            configure_source_window_job(
                scheduler,
                runtime_key=runtime_key,
                schedule_seconds=schedule_seconds,
            )
            configure_experience_incubation_job(
                scheduler,
                runtime_key=runtime_key,
                schedule_seconds=experience_schedule_seconds,
            )
            scheduler.resume()
        except BaseException:
            if scheduler is not None and scheduler.running:
                scheduler.shutdown(wait=False)
            unregister_processor(runtime_key)
            self._scheduler_runtime_key = None
            self._scheduler = None
            raise

    async def close(self) -> None:
        """Stop accepting work and await in-flight operations without closing the provider."""

        async with self._close_lock:
            if self._closed:
                return
            if self._scheduler is not None and self._scheduler.running:
                self._scheduler.pause()
            async with self._lifecycle:
                self._closing = True
                await self._lifecycle.wait_for(lambda: self._active_operations == 0)
            async with self._processor_lock:
                pass
            try:
                if self._scheduler is not None and self._scheduler.running:
                    self._scheduler.shutdown(wait=False)
                    await asyncio.sleep(0)
            finally:
                if self._scheduler_runtime_key is not None:
                    from powercontext.builtin.runtime.scheduler import unregister_processor

                    unregister_processor(self._scheduler_runtime_key)
                    self._scheduler_runtime_key = None
                self._scheduler = None
            self._scope_cache.clear()
            self._closed = True

    @asynccontextmanager
    async def _operation(self) -> AsyncIterator[None]:
        task = asyncio.current_task()
        if task is None:
            raise RuntimeError("Runtime operations require an asyncio Task")  # noqa: TRY003
        async with self._lifecycle:
            depth = self._operation_depths.get(task, 0)
            if depth == 0:
                if self._closing or self._closed:
                    raise _RuntimeStateError("closed")
                self._active_operations += 1
            self._operation_depths[task] = depth + 1
        try:
            yield
        finally:
            async with self._lifecycle:
                depth = self._operation_depths[task] - 1
                if depth > 0:
                    self._operation_depths[task] = depth
                else:
                    del self._operation_depths[task]
                    self._active_operations -= 1
                    if self._active_operations == 0:
                        self._lifecycle.notify_all()

    @asynccontextmanager
    async def _scope_operation(self, scope_id: str) -> AsyncIterator[None]:
        scope = validate_scope_id(scope_id)
        async with self._operation():
            with self._scope_cache.lease(scope):
                yield

    @asynccontextmanager
    async def _scoped_operation(
        self,
        scope_id: str,
        *,
        generation_purpose: ModelUsagePurpose | None = None,
        embedding_purpose: ModelUsagePurpose | None = None,
    ) -> AsyncIterator[None]:
        scope = validate_scope_id(scope_id)
        async with self._scope_operation(scope):
            with bind_usage_reporter(
                self.statistics.for_scope(scope).record_model_usage,
                generation_purpose=generation_purpose,
                embedding_purpose=embedding_purpose,
            ):
                yield

    @asynccontextmanager
    async def _context(
        self,
        scope_id: str,
        *,
        generation_purpose: ModelUsagePurpose | None = None,
        embedding_purpose: ModelUsagePurpose | None = None,
    ) -> AsyncIterator[PowerContext[BuiltinSources, BuiltinArtifacts, BuiltinTriggers]]:
        async with self._scoped_operation(
            scope_id,
            generation_purpose=generation_purpose,
            embedding_purpose=embedding_purpose,
        ):
            # The stage covers provider resolution only; a third-party provider may do I/O here.
            with self._stage("scope.context", attributes={}):
                context = await self._provider.get(validate_scope_id(scope_id))
            yield context

    def _lock(self, scope_id: str) -> asyncio.Lock:
        return self._scope_cache.lock(validate_scope_id(scope_id))

    @asynccontextmanager
    async def _locked(self, scope_id: str) -> AsyncIterator[None]:
        """Serialize writes for one scope and trace only the wait, not the critical section."""

        lock = self._lock(scope_id)
        acquired = False
        try:
            # Injected tracing must never leak the lock, so the release is armed before the stage is closed.
            with self._stage("scope.lock", attributes={"powercontext.scope.lock.contended": lock.locked()}):
                await lock.acquire()
                acquired = True
            yield
        finally:
            if acquired:
                lock.release()

    def _stage(
        self,
        name: str,
        *,
        attributes: Mapping[str, TraceAttribute],
    ) -> AbstractContextManager[RuntimeSpan | None]:
        if self._tracing is None:
            return nullcontext(None)
        return self._tracing.stage(name, attributes=attributes)

    def _background(
        self,
        name: str,
        *,
        operation: str,
    ) -> AbstractContextManager[RuntimeSpan | None]:
        if self._tracing is None:
            return nullcontext(None)
        return self._tracing.background(name, operation=operation, attributes={})

    def _review(self, scope_id: str) -> ReviewService:
        if self._review_service is None:
            raise _RuntimeStateError("review")
        return self._review_service(validate_scope_id(scope_id))

    def _generation(self, scope_id: str) -> ReviewedGenerationService:
        if self._generation_service is None:
            raise _RuntimeStateError("review")
        return self._generation_service(validate_scope_id(scope_id))

    def _external_skills(self, scope_id: str) -> ExternalSkillRegistryService:
        if self._external_skill_registry is None:
            raise ExternalSkillRegistryUnavailableError
        return self._external_skill_registry(validate_scope_id(scope_id))

    def _statistics(self, scope_id: str) -> RelationalScopedStatistics:
        if self._statistics_service is None:
            raise _RuntimeStateError("statistics")
        return self._statistics_service(validate_scope_id(scope_id))


def _contract_evidence(contract: WorkContract) -> tuple[HandoffCitation, ...]:
    return _claims_evidence(contract.facts)


def _outcome_evidence(outcome: TaskOutcome) -> tuple[HandoffCitation, ...]:
    citations = [*_claims_evidence(outcome.observations)]
    citations.extend(citation for check in outcome.checks for citation in check.evidence)
    citations.extend(HandoffArtifactCitation(artifact_ref=reference) for reference in outcome.produced_artifacts)
    return _unique_citations(citations)


def _claims_evidence(claims: tuple[WorkClaim | None, ...]) -> tuple[HandoffCitation, ...]:
    return _unique_citations(citation for claim in claims if claim is not None for citation in claim.evidence)


def _unique_citations(citations: Iterable[HandoffCitation]) -> tuple[HandoffCitation, ...]:
    unique: list[HandoffCitation] = []
    for citation in citations:
        if citation not in unique:
            unique.append(citation)
    return tuple(unique)


def _handoff_statement(claim: WorkClaim, boundary: HandoffSourceCitation) -> HandoffStatement:
    return HandoffStatement(
        text=claim.text,
        citations=_unique_citations((boundary, *claim.evidence)),
    )


def _unavailable_evidence(resolution: HandoffResolution) -> tuple[HandoffCitation, ...]:
    return _unique_citations(
        citation for check in resolution.evidence_checks for citation in check.unavailable_evidence
    )


def _utc_now() -> datetime:
    return datetime.now(UTC)


async def _head_or_none(service: MemoryService, artifact_id: str) -> Memory | None:
    try:
        return await service.head(artifact_id)
    except ArtifactNotFoundError:
        return None


def _is_stale_memory_search(error: CapabilityNotSupportedError | InvalidMemoryCitationError) -> bool:
    return (isinstance(error, CapabilityNotSupportedError) and error.capability == "head") or (
        isinstance(error, InvalidMemoryCitationError) and error.code == "memory-mismatch"
    )


def _validate_expected_revision(memory: Memory | None, expected_revision: int | None) -> None:
    if expected_revision is None:
        return
    if memory is None:
        raise ArtifactNotFoundError(expected_revision)
    if memory.revision != expected_revision:
        raise RevisionConflictError(expected_revision, memory)


def _validate_memory_identity(memory_artifact_id: str, memory: Memory) -> None:
    if memory.artifact_id != memory_artifact_id:
        raise ArtifactNotFoundError(memory.as_ref())


async def _current_citation(
    service: MemoryService,
    memory_artifact_id: str,
    citation: MemoryCitation,
) -> tuple[Memory, MemoryEntryVersion]:
    current = await service.head(memory_artifact_id)
    if citation.memory_ref.artifact_id != current.artifact_id:
        raise ArtifactNotFoundError(citation.memory_ref)
    if citation.memory_ref.revision != current.revision:
        raise RevisionConflictError(citation.memory_ref, current)
    return current, await _cited_entry(service, current, citation)


async def _cited_entry(
    service: MemoryService,
    memory: Memory,
    citation: MemoryCitation,
) -> MemoryEntryVersion:
    if not any(
        item.entry_id == citation.entry_id and item.entry_version_id == citation.entry_version_id
        for item in memory.content.manifest.entries
    ):
        raise MemoryEntryNotFoundError(citation.entry_id)
    return await service.validate_citation(citation)


def _entry_record(memory: Memory, entry: MemoryEntryVersion) -> MemoryEntryRecord:
    manifest_entry = next(
        item
        for item in memory.content.manifest.entries
        if item.entry_id == entry.entry_id and item.entry_version_id == entry.entry_version_id
    )
    return MemoryEntryRecord(
        memory_ref=memory.as_ref(),
        state=manifest_entry.state,
        entry=entry,
    )


async def _last_changed_entry(service: MemoryService, memory: Memory) -> MemoryEntryRecord | None:
    if not memory.content.changes:
        return None
    entry_id = memory.content.changes[-1].entry_id
    entry = next((item for item in await service.entries(memory) if item.entry_id == entry_id), None)
    return None if entry is None else _entry_record(memory, entry)
