"""Business-specific Runtime operations over composed built-in contexts."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import TYPE_CHECKING

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
    HandoffAudience,
    HandoffDraft,
    HandoffResolution,
    HandoffService,
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
from powercontext.builtin.artifacts.memory.errors import MemoryEntryNotFoundError
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
from powercontext.builtin.runtime.errors import InvalidRuntimeRequestError
from powercontext.builtin.runtime.models import (
    ApproveArtifactCandidateRequest,
    CaptureSource,
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
)
from powercontext.builtin.runtime.prepared_context import PreparedContextBuild, PreparedContextBuilder
from powercontext.builtin.runtime.protocols import BuiltinTriggers, PowerContextProvider
from powercontext.builtin.runtime.readiness import (
    ReadinessCheckStatus,
    RuntimeReadiness,
    RuntimeReadinessChecks,
)
from powercontext.builtin.runtime.statistics import RelationalScopedStatistics
from powercontext.builtin.sources import ContentCapture, ExternalSkillImportMode, SourceCursor, validate_scope_id
from powercontext.builtin.statistics import (
    ModelUsageOperation,
    ModelUsagePurpose,
    RecallTokenMeasurement,
    Statistics,
    StatisticsPeriod,
)
from powercontext.context import PowerContext
from powercontext.errors import ArtifactNotFoundError, RevisionConflictError

if TYPE_CHECKING:
    from apscheduler.schedulers.asyncio import AsyncIOScheduler

    from powercontext.builtin.handoff_report.application import HandoffReportApplication

logger = logging.getLogger(__name__)

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


class ScopedStatisticsApplication:
    """Read product statistics and record model usage for one scope."""

    def __init__(self, runtime: BuiltinRuntime, scope_id: str) -> None:
        self._runtime = runtime
        self.scope_id = validate_scope_id(scope_id)

    async def overview(self, *, period: StatisticsPeriod = StatisticsPeriod.THIRTY_DAYS) -> Statistics:
        async with self._runtime._operation():
            return await self._runtime._statistics(self.scope_id).overview(period, self._runtime._clock())

    async def record_model_usage(
        self,
        purpose: ModelUsagePurpose,
        operation: ModelUsageOperation,
        usage: InferenceUsage,
        /,
    ) -> None:
        try:
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
        builder = PreparedContextBuilder()
        async with (
            self._runtime._context(self.scope_id, embedding_purpose=ModelUsagePurpose.MEMORY_RECALL) as context,
            self._runtime._lock(self.scope_id),
        ):
            service = context.artifacts.memory
            current = await _head_or_none(service, context.artifacts.memory_artifact_id)
            memory_hits = ()
            if current is not None:
                result = await service.search(
                    request.query,
                    memories=(current,),
                    limit=builder.memory_candidate_limit,
                    mode="auto",
                )
                memory_hits = result.hits
            experience_hits = (
                ()
                if self._runtime._experience_recall is None
                else await self._runtime._experience_recall(
                    self.scope_id,
                    request.query,
                    builder.experience_candidate_limit,
                )
            )
            build = builder.build_result(
                request=request,
                memory_ref=None if current is None else current.as_ref(),
                hits=memory_hits,
                experience_hits=experience_hits,
            )
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
        async with self._runtime._scoped_operation(self.scope_id), self._runtime._lock(self.scope_id):
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
            self._runtime._lock(self.scope_id),
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
            self._runtime._lock(self.scope_id),
        ):
            return await incubator(self.scope_id, window_limit)


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
        async with self._runtime._scoped_operation(self.scope_id), self._runtime._lock(self.scope_id):
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
            self._runtime._lock(self.scope_id),
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
        async with self._runtime._context(self.scope_id) as context, self._runtime._lock(self.scope_id):
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


class ScopedExternalSkillApplication:
    """Discover and exactly resolve Agent-native Skills in one local scope."""

    def __init__(self, runtime: BuiltinRuntime, scope_id: str) -> None:
        self._runtime = runtime
        self.scope_id = validate_scope_id(scope_id)

    async def scan(self) -> ExternalSkillScanResult:
        async with self._runtime._scoped_operation(self.scope_id), self._runtime._lock(self.scope_id):
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
            self._runtime._lock(self.scope_id),
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
        async with self._runtime._scoped_operation(self.scope_id), self._runtime._lock(self.scope_id):
            return await self._runtime._review(self.scope_id).approve(
                request.candidate_id,
                request.expected_version,
            )

    async def reject(self, request: RejectArtifactCandidateRequest, /) -> ReviewedCandidate:
        async with self._runtime._scoped_operation(self.scope_id), self._runtime._lock(self.scope_id):
            return await self._runtime._review(self.scope_id).reject(
                request.candidate_id,
                request.expected_version,
                request.reason,
            )

    async def revise(self, request: ReviseArtifactCandidateRequest, /) -> ReviewedCandidate:
        async with self._runtime._scoped_operation(self.scope_id), self._runtime._lock(self.scope_id):
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
            async with self._runtime._lock(self.scope_id):
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
            service = context.artifacts.memory
            current = await _head_or_none(service, context.artifacts.memory_artifact_id)
            if current is None:
                return MemorySearchPage(memory_ref=None, mode=None)
            result = await service.search(
                request.query,
                memories=(current,),
                limit=request.limit,
                mode=request.mode,
            )
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
            async with self._runtime._lock(self.scope_id):
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
            async with self._runtime._lock(self.scope_id):
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
            async with self._runtime._lock(self.scope_id):
                return await context.triggers.flush(limit=window_limit)

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
                else:
                    _log_scheduled_processing(
                        "success" if result.processed else "noop",
                        operation="process_source_window",
                        started_at=started_at,
                        source_count=result.source_count,
                    )


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
                else:
                    _log_scheduled_processing(
                        "success" if result.processed else "noop",
                        operation="incubate_experience_candidates",
                        started_at=started_at,
                        source_count=result.source_count,
                        candidate_count=result.candidate_count,
                    )


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
    ) -> None:
        if source_window_limit < 1:
            raise _RuntimeConfigurationError("source_window_limit")
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
        self.source_window_limit = source_window_limit
        self._locks: dict[str, asyncio.Lock] = {}
        self._processor_lock = asyncio.Lock()
        self._close_lock = asyncio.Lock()
        self._lifecycle = asyncio.Condition()
        self._active_operations = 0
        self._closing = False
        self._closed = False
        self._scheduler: AsyncIOScheduler | None = None
        self._scheduler_runtime_key: str | None = None
        self.sources = SourceApplication(self)
        self.context = ContextApplication(self)
        self.experience = ExperienceApplication(self)
        self.external_skills = ExternalSkillApplication(self)
        self.handoff = HandoffApplication(self)
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
            self._closed = True

    @asynccontextmanager
    async def _operation(self) -> AsyncIterator[None]:
        async with self._lifecycle:
            if self._closing or self._closed:
                raise _RuntimeStateError("closed")
            self._active_operations += 1
        try:
            yield
        finally:
            async with self._lifecycle:
                self._active_operations -= 1
                if self._active_operations == 0:
                    self._lifecycle.notify_all()

    @asynccontextmanager
    async def _scoped_operation(
        self,
        scope_id: str,
        *,
        generation_purpose: ModelUsagePurpose | None = None,
        embedding_purpose: ModelUsagePurpose | None = None,
    ) -> AsyncIterator[None]:
        scope = validate_scope_id(scope_id)
        async with self._operation():
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
            yield await self._provider.get(validate_scope_id(scope_id))

    def _lock(self, scope_id: str) -> asyncio.Lock:
        return self._locks.setdefault(validate_scope_id(scope_id), asyncio.Lock())

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


def _utc_now() -> datetime:
    return datetime.now(UTC)


async def _head_or_none(service: MemoryService, artifact_id: str) -> Memory | None:
    try:
        return await service.head(artifact_id)
    except ArtifactNotFoundError:
        return None


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
