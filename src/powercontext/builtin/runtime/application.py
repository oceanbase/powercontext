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
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable, Mapping, Sequence
from contextlib import AbstractContextManager, asynccontextmanager, nullcontext
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import TYPE_CHECKING, Any, Protocol

from pydantic import BaseModel, JsonValue, ValidationError

from powercontext._logging import log_safely
from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.experience import (
    EXPERIENCE_INCUBATION_WINDOW_LIMIT,
    Experience,
    ExperienceSearchHit,
    ExperienceSearchOutcome,
)
from powercontext.builtin.artifacts.handoff import (
    ActivateHandoff,
    Handoff,
    HandoffActivation,
    HandoffArtifactCitation,
    HandoffAudience,
    HandoffCitation,
    HandoffDraft,
    HandoffEvidenceAuthorizer,
    HandoffOmission,
    HandoffResolution,
    HandoffService,
    HandoffSourceCitation,
    HandoffStatement,
    PreparedHandoff,
    PrepareHandoff,
)
from powercontext.builtin.artifacts.memory import (
    EmbeddingProfile,
    Memory,
    MemoryCapacity,
    MemoryCitation,
    MemoryEntryInput,
    MemoryEntryVersion,
    MemoryHit,
    MemoryQueryEmbedding,
    MemoryService,
)
from powercontext.builtin.artifacts.memory.errors import (
    CapabilityNotSupportedError,
    InvalidMemoryCitationError,
    MemoryEntryNotFoundError,
)
from powercontext.builtin.artifacts.profile.service import RelationalProfileService
from powercontext.builtin.artifacts.prompt import (
    GeneratePromptDemonstrations,
    PromptConfiguration,
    PromptDemonstrationResult,
    PromptError,
)
from powercontext.builtin.artifacts.prompt.service import PromptService
from powercontext.builtin.artifacts.search import AdmissionCounts, AdmissionFloor, analyze_text
from powercontext.builtin.artifacts.skill import (
    AgentKind,
    AgentSkillTarget,
    ExternalSkillRegistryUnavailableError,
    ExternalSkillResolution,
    Skill,
    SkillOrigin,
    SkillPackageRef,
    SkillPackageSnapshot,
    SkillSearchHit,
)
from powercontext.builtin.artifacts.skill.distribution import (
    RemoteSkillDistributionService,
    RemoteSkillObservation,
    RemoteSkillReceipt,
    RemoteSkillReceiptResult,
    RemoteSkillReconcileResult,
    RemoteSkillTargetStatus,
    RemoteTargetCredential,
    RemoteTargetEnrollment,
)
from powercontext.builtin.artifacts.skill.publication import (
    ManagedSkillPublicationService,
    ManagedSkillPublicationStatus,
)
from powercontext.builtin.artifacts.skill.registry import ExternalSkillRegistryService
from powercontext.builtin.artifacts.topic_memory import (
    MAX_TOPIC_MEMORY_QUERY_LENGTH,
    MAX_TOPIC_MEMORY_QUERY_TERMS,
    MAX_TOPIC_MEMORY_SEARCH_LIMIT,
    TOPIC_MEMORY_SOURCE_WINDOW_BINDING,
    PublishedTopicMemory,
    TopicMemory,
    TopicMemoryBrowseCursor,
    TopicMemoryCurrentItem,
    TopicMemorySearchHit,
    TopicMemorySearchMode,
    TopicMemorySearchResult,
)
from powercontext.builtin.code.application import CodeApplication
from powercontext.builtin.code.errors import CodeError
from powercontext.builtin.code.models import CodeConfig, CodeQueryRequest, CodeQueryResult
from powercontext.builtin.code.service import CodeService
from powercontext.builtin.context import BuiltinArtifacts, BuiltinSources
from powercontext.builtin.dream.application import DreamApplication
from powercontext.builtin.dream.service import CandidateAttester, DreamAuthorizer, DreamService
from powercontext.builtin.evidence.resolver import AuthorizationContext, ScopedEvidenceAuthorizer
from powercontext.builtin.inference import (
    EmbeddingModel,
    InferenceTimeoutError,
    InferenceUnavailableError,
    InvalidInferenceOutputError,
    embed_query,
)
from powercontext.builtin.inference.models import InferenceUsage
from powercontext.builtin.inference.usage import bind_usage_reporter
from powercontext.builtin.persistence.agent_skill_targets import RemoteAgentSkillTarget
from powercontext.builtin.persistence.artifact_governance import (
    ArtifactGovernance,
    ArtifactLifecycleState,
)
from powercontext.builtin.persistence.skill_publications import SkillPublication
from powercontext.builtin.publication import ArtifactPublicationApplication
from powercontext.builtin.records import (
    ArtifactCreated,
    ArtifactRecord,
    ArtifactRecordPage,
    ArtifactRevisionPage,
    ArtifactWrite,
    BaseValueConflictError,
    LogicalArtifactRecord,
    RecordService,
    ScopeSummaryPage,
    SourceRecord,
    SourceRecordPage,
)
from powercontext.builtin.review.generation import GeneratedCandidateResult, ReviewedGenerationService
from powercontext.builtin.review.service import ReviewService
from powercontext.builtin.runtime._scope_cache import (
    DEFAULT_SCOPE_CACHE_SIZE,
    ScopeCache,
    ScopeCacheObserver,
    ScopeEvictor,
)
from powercontext.builtin.runtime.errors import InvalidRuntimeRequestError, TopicMemoryProcessingUnavailableError
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
    GetTopicMemoryRequest,
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
    SearchTopicMemoryRequest,
    SkillCandidate,
    SourceReceipt,
    SubmitSourceObservation,
    TopicMemoryFlushResult,
)
from powercontext.builtin.runtime.prepared_code import PreparedCodeCandidate, code_candidates
from powercontext.builtin.runtime.prepared_context import (
    PreparedContextBuild,
    PreparedContextBuilder,
    PreparedExperienceCandidates,
    PreparedMemoryCandidates,
    PreparedProfileCandidate,
)
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
    RuntimeReadinessStatus,
)
from powercontext.builtin.runtime.recall_sufficiency import (
    EXPERIENCE_FAMILY,
    MEMORY_FAMILY,
    REASON_AT_MAX_ROUNDS,
    REASON_EXPANSION_FAILED,
    TOPIC_MEMORY_FAMILY,
    RecallEffort,
    RecallExpander,
    RecallSufficiencyGate,
    RecallSufficiencyPolicy,
    build_recall_candidates,
    recall_effort,
)
from powercontext.builtin.runtime.statistics import RelationalScopedStatistics, overview_selection
from powercontext.builtin.scope import ScopeApplication, ScopeDescriptor, ScopeSelection
from powercontext.builtin.scope.subject_sources import SubjectSourceService
from powercontext.builtin.sources import (
    CONTENT_SOURCE_NAME,
    ContentCapture,
    ContentSource,
    ExternalSkillImportMode,
    SkillUsageCapture,
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
from powercontext.builtin.statistics.aggregation import aggregate_statistics
from powercontext.builtin.tags import ArtifactTagSet, TagFilter, TagQuery, TagQueryPage, TagTarget
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
from powercontext.errors import ArtifactNotFoundError, RevisionConflictError, SourceConflictError
from powercontext.sources import ConnectorBinding, SourceDefinitionManifest, SourceRef

if TYPE_CHECKING:
    from apscheduler.schedulers.asyncio import AsyncIOScheduler

    from powercontext.builtin.handoff_report.application import HandoffReportApplication
    from powercontext.builtin.runtime.artifact_processing import ArtifactProcessingSupervisors


class TopicMemorySearch(Protocol):
    """Callable contract for one scoped Topic Memory search."""

    def __call__(
        self,
        scope_id: str,
        query: str,
        /,
        *,
        limit: int,
        mode: TopicMemorySearchMode = "auto",
        query_vector: tuple[float, ...] | None = None,
        embedding_profile: EmbeddingProfile | None = None,
        admission: AdmissionFloor | None = None,
        query_embedding: MemoryQueryEmbedding | None = None,
    ) -> Awaitable[TopicMemorySearchResult]: ...


TopicMemoryGet = Callable[[str, ArtifactRef], Awaitable[PublishedTopicMemory]]
TopicMemoryBrowse = Callable[..., Awaitable[tuple[TopicMemoryCurrentItem, ...]]]
TopicMemoryFlush = Callable[[str], Awaitable[bool]]
TopicMemorySearchObserver = Callable[[str, bool], None]

logger = logging.getLogger(__name__)

# Leave room for database reads and assembly within the default one-second Hook request.
_CONTEXT_TOPIC_EMBEDDING_TIMEOUT_SECONDS = 0.25

_MEMORY_CAPTURE_STAGE = "memory.capture"
_MEMORY_CAPTURE_SOURCE_COUNT = "powercontext.memory.capture.source_count"
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
SkillPublicationServiceFactory = Callable[[str, str, str], ManagedSkillPublicationService]
ExternalSkillImporter = Callable[
    [str, str, str, ExternalSkillImportMode, str | None],
    Awaitable[GeneratedCandidateResult],
]
ExperienceIncubator = Callable[[str, int], Awaitable[ExperienceIncubationResult]]


class ExperienceRecall(Protocol):
    """Callable contract for one scoped Experience recall.

    The outcome carries the hits *and* the admission counts for the search, so the recall gate
    can report retrieved-versus-admitted without a second pass.
    """

    def __call__(
        self,
        scope_id: str,
        query: str,
        limit: int,
        /,
        *,
        admission: AdmissionFloor | None = None,
    ) -> Awaitable[ExperienceSearchOutcome]: ...


@dataclass(frozen=True)
class TopicMemoryRecallOutcome:
    """Topic Memory hits, their admission counts, and the reusable query embedding.

    Produced and consumed entirely inside the Runtime layer, which is why it lives here rather
    than under ``artifacts/**``. ``query_embedding`` is the vector this search resolved (or
    reused); a later expansion round can hand it back so the next search does not re-embed. It
    stays ``None`` when the search ran without a vector channel. Prepare caches that outcome
    too, so expansion rounds retain FTS after a failed embedding attempt.
    """

    hits: tuple[TopicMemorySearchHit, ...] = ()
    admission: AdmissionCounts | None = None
    query_embedding: MemoryQueryEmbedding | None = None
    embedding_calls: int = 0


@dataclass(frozen=True)
class _ScopeRecallOutcome:
    """One Scope's recall result: the two candidate containers plus their admission counts.

    A named type rather than a tuple because the count fields are the whole point of this
    increment and a silent field-order mistake there would be invisible.
    """

    memory: PreparedMemoryCandidates
    experience: PreparedExperienceCandidates
    memory_admission: AdmissionCounts | None = None
    experience_admission: AdmissionCounts | None = None
    memory_query_embedding: MemoryQueryEmbedding | None = None
    embedding_calls: int = 0
    generation_calls: int = 0


@dataclass(frozen=True)
class _RecallRoundOutcome:
    """All observable results produced by one bounded recall pass."""

    memory: tuple[PreparedMemoryCandidates, ...] = ()
    experience: tuple[PreparedExperienceCandidates, ...] = ()
    topic_memory: TopicMemoryRecallOutcome = TopicMemoryRecallOutcome()
    admissions: tuple[AdmissionCounts, ...] = ()
    embedding_calls: int = 0
    generation_calls: int = 0


SkillRecall = Callable[[str, str, int], Awaitable[tuple[SkillSearchHit, ...]]]
SkillLister = Callable[[str, bool, int], Awaitable[tuple[tuple[Skill, ArtifactGovernance], ...]]]
SkillOriginReader = Callable[[str, tuple[Skill, ...]], Awaitable[tuple[SkillOrigin, ...]]]
SkillGovernanceReader = Callable[[str, str], Awaitable[ArtifactGovernance]]
SkillGovernanceUpdater = Callable[[str, str, int, ArtifactLifecycleState, str | None], Awaitable[ArtifactGovernance]]
SkillPackageResolver = Callable[[str, ArtifactRef], Awaitable[SkillPackageSnapshot]]
PackageSnapshotResolver = Callable[[str, SkillPackageRef], Awaitable[SkillPackageSnapshot]]
SkillPackageUploader = Callable[[str, bytes, str | None, ArtifactRef | None], Awaitable[SkillCandidate]]
SkillUsageRecorder = Callable[[str, SkillUsageCapture], Awaitable[SourceReceipt]]
StatisticsServiceFactory = Callable[[str], RelationalScopedStatistics]
RecallTokenEstimator = Callable[[str, PreparedContextBuild], Awaitable[RecallTokenMeasurement | None]]
RecallEffortSink = Callable[[RecallEffort], Awaitable[None]]
Clock = Callable[[], datetime]
ScheduledSourceRunner = Callable[[str, "BuiltinRuntime"], Awaitable[MemoryFlushResult]]
ScheduledExperienceRunner = Callable[[str, "BuiltinRuntime"], Awaitable[ExperienceIncubationResult]]
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
            "records": "Base Source and Artifact access is not configured",
            "remote-skill-distribution": "Remote Skill distribution services are not configured",
            "scheduler": "Built-in Runtime scheduler is already started",
            "scope": "Scope services are not configured",
            "skill-publication": "Managed Skill publication services are not configured",
            "skill-provenance": "Managed Skill provenance services are not configured",
            "skill-governance": "Managed Skill governance services are not configured",
            "skill-package": "Managed Skill package services are not configured",
            "skill-usage": "Managed Skill usage recording is not configured",
            "statistics": "Statistics services are not configured",
            "topic-memory-browse": "Topic Memory browsing is not configured",
        }
        super().__init__(messages[code])


class ScopedSourceApplication:
    """Capture raw integration content in one Source partition."""

    def __init__(self, runtime: BuiltinRuntime, scope_id: str) -> None:
        self._runtime = runtime
        self.scope_id = validate_scope_id(scope_id)

    async def capture(self, value: CaptureSource, /) -> SourceReceipt:
        return await self._capture(value)

    async def _capture(self, value: CaptureSource, /, *, handoff_receipt: bool = False) -> SourceReceipt:
        if self._runtime._record_service is not None:
            try:
                async with self._runtime._scope_operation(self.scope_id), self._runtime._locked(self.scope_id):
                    with self._runtime._stage(_MEMORY_CAPTURE_STAGE, attributes={}) as span:
                        record = await self._runtime._records().capture_source(
                            self.scope_id,
                            CONTENT_SOURCE_NAME,
                            value.source_id,
                            value.content,
                            value.metadata,
                            handoff_receipt=handoff_receipt,
                        )
                        if span is not None:
                            span.set_attributes({_MEMORY_CAPTURE_SOURCE_COUNT: 1})
            except BaseValueConflictError as error:
                raise SourceConflictError("identity", error.identity) from None
            return SourceReceipt(
                source_ref=SourceRef(source_type=record.source_type, source_id=record.source_id),
                sequence=record.position,
            )
        async with self._runtime._context(self.scope_id) as context:
            with self._runtime._stage(_MEMORY_CAPTURE_STAGE, attributes={}) as span:
                source, sequence = await context.sources.capture(
                    ContentCapture(
                        source_id=value.source_id,
                        content=value.content,
                        metadata=value.model_dump(mode="json")["metadata"],
                    ),
                    handoff_receipt=handoff_receipt,
                )
                if span is not None:
                    span.set_attributes({_MEMORY_CAPTURE_SOURCE_COUNT: 1})
            return SourceReceipt(source_ref=context.sources.catalog.as_ref(source), sequence=sequence)


class SourceApplication:
    """Select a scoped Source application service."""

    def __init__(self, runtime: BuiltinRuntime) -> None:
        self._runtime = runtime

    def for_scope(self, scope_id: str, /) -> ScopedSourceApplication:
        return ScopedSourceApplication(self._runtime, scope_id)


class ScopedRecordApplication:
    """Run base Source and Artifact operations in one Scope."""

    def __init__(self, runtime: BuiltinRuntime, scope_id: str) -> None:
        self._runtime = runtime
        self.scope_id = validate_scope_id(scope_id)

    async def create_source(
        self,
        source_type: str,
        content: JsonValue,
        /,
    ) -> SourceRecord:
        async with self._runtime._scope_operation(self.scope_id), self._runtime._locked(self.scope_id):
            return await self._runtime._records().create_source(
                self.scope_id,
                source_type,
                content,
            )

    async def get_source(self, source_type: str, source_id: str, /) -> SourceRecord:
        async with self._runtime._scope_operation(self.scope_id):
            return await self._runtime._records().get_source(self.scope_id, source_type, source_id)

    async def list_sources(
        self,
        *,
        limit: int,
        cursor: str | None,
        caller: str = "runtime",
    ) -> SourceRecordPage:
        async with self._runtime._scope_operation(self.scope_id):
            return await self._runtime._records().list_sources(
                self.scope_id,
                limit=limit,
                cursor=cursor,
                caller=caller,
            )

    async def create_artifact(
        self,
        family: str,
        write: ArtifactWrite,
        /,
    ) -> ArtifactCreated:
        async with self._runtime._scope_operation(self.scope_id), self._runtime._locked(self.scope_id):
            return await self._runtime._records().create_artifact(self.scope_id, family, write)

    async def get_artifact(self, family: str, artifact_id: str, /) -> ArtifactRecord:
        async with self._runtime._scope_operation(self.scope_id):
            return await self._runtime._records().get_artifact(self.scope_id, family, artifact_id)

    async def get_artifact_revision(
        self,
        family: str,
        artifact_id: str,
        revision: int,
        /,
    ) -> ArtifactRecord:
        async with self._runtime._scope_operation(self.scope_id):
            return await self._runtime._records().get_artifact_revision(
                self.scope_id,
                family,
                artifact_id,
                revision,
            )

    async def list_artifact_revisions(
        self,
        family: str,
        artifact_id: str,
        /,
        *,
        limit: int,
        cursor: str | None,
    ) -> ArtifactRevisionPage:
        async with self._runtime._scope_operation(self.scope_id):
            return await self._runtime._records().list_artifact_revisions(
                self.scope_id, family, artifact_id, limit=limit, cursor=cursor
            )

    async def current_memory_entry(self, artifact_id: str, entry_id: str, /) -> MemoryEntryVersion:
        async with self._runtime._scope_operation(self.scope_id):
            return await self._runtime._records().current_memory_entry(self.scope_id, artifact_id, entry_id)

    async def logical_artifacts(self) -> tuple[LogicalArtifactRecord, ...]:
        async with self._runtime._scope_operation(self.scope_id):
            return await self._runtime._records().logical_artifacts(self.scope_id)

    async def query_artifacts(
        self,
        family: str,
        /,
        *,
        limit: int,
        cursor: str | None,
        tag_filter: TagFilter | None = None,
    ) -> ArtifactRecordPage:
        async with self._runtime._scope_operation(self.scope_id):
            filters = {} if tag_filter is None else {"tag_filter": tag_filter}
            return await self._runtime._records().query_artifacts(
                self.scope_id,
                family,
                limit=limit,
                cursor=cursor,
                **filters,
            )

    async def get_tags(self, target: TagTarget) -> ArtifactTagSet:
        async with self._runtime._scope_operation(self.scope_id):
            return await self._runtime._records().get_tags(self.scope_id, target)

    async def replace_tags(self, target: TagTarget, tags: tuple[str, ...], *, expected_etag: str) -> ArtifactTagSet:
        async with self._runtime._scope_operation(self.scope_id), self._runtime._locked(self.scope_id):
            return await self._runtime._records().replace_tags(self.scope_id, target, tags, expected_etag=expected_etag)

    async def query_tags(self, query: TagQuery, *, caller: str = "runtime") -> TagQueryPage:
        async with self._runtime._scope_operation(self.scope_id):
            return await self._runtime._records().query_tags(self.scope_id, query, caller=caller)

    async def replace_artifact(
        self,
        family: str,
        artifact_id: str,
        expected_etag: str,
        write: ArtifactWrite,
        /,
    ) -> ArtifactRecord:
        async with self._runtime._scope_operation(self.scope_id), self._runtime._locked(self.scope_id):
            return await self._runtime._records().replace_artifact(
                self.scope_id,
                family,
                artifact_id,
                expected_etag,
                write,
            )


class RecordApplication:
    """Select scoped base access and list observable Scopes."""

    def __init__(self, runtime: BuiltinRuntime) -> None:
        self._runtime = runtime

    def for_scope(self, scope_id: str, /) -> ScopedRecordApplication:
        return ScopedRecordApplication(self._runtime, scope_id)

    async def list_scopes(self, *, limit: int, cursor: str | None) -> ScopeSummaryPage:
        async with self._runtime._operation():
            return await self._runtime._records().list_scopes(limit=limit, cursor=cursor)


class ScopedPromptApplication:
    """Read configuration and generate suggestions inside an existing Scope."""

    def __init__(self, runtime: BuiltinRuntime, scope_id: str) -> None:
        self._runtime = runtime
        self.scope_id = validate_scope_id(scope_id)

    async def read_configuration(self, key: str, /) -> PromptConfiguration:
        async with self._runtime._scope_operation(self.scope_id):
            service = self._runtime._prompt_service
            if service is None:
                raise PromptError("prompt_customization_unavailable")
            return await service.read_configuration(self.scope_id, key)

    async def generate_demonstrations(
        self, key: str, request: GeneratePromptDemonstrations, /
    ) -> PromptDemonstrationResult:
        async with self._runtime._scope_operation(self.scope_id):
            service = self._runtime._prompt_service
            if service is None:
                raise PromptError("prompt_customization_unavailable")
            return await service.generate_demonstrations(key, request)


class PromptApplication:
    def __init__(self, runtime: BuiltinRuntime) -> None:
        self._runtime = runtime

    def for_scope(self, scope_id: str, /) -> ScopedPromptApplication:
        return ScopedPromptApplication(self._runtime, scope_id)


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
        async with self._runtime._scope_operation(binding.scope_id):
            return await self._require_service().connector_checkpoint(binding)

    async def submit(self, request: SubmitSourceObservation, /) -> SourceReceipt:
        async with self._runtime._scope_operation(request.scope_id):
            return await self._require_service().submit_source_observation(request)

    async def commit(self, request: CommitConnectorCheckpoint, /) -> ConnectorCheckpointState:
        async with self._runtime._scope_operation(request.binding.scope_id):
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

    async def overview(
        self,
        selection: ScopeSelection,
        *,
        period: StatisticsPeriod = StatisticsPeriod.THIRTY_DAYS,
    ) -> Statistics:
        if self._runtime.scopes is None:
            raise _RuntimeStateError("statistics")
        async with self._runtime._operation():
            resolved = await self._runtime.scopes.resolve_selection(selection)
            captured_at = self._runtime._clock()
            snapshots = await overview_selection(
                tuple(self._runtime._statistics(scope.scope_id) for scope in resolved),
                period,
                captured_at,
            )
        return aggregate_statistics(
            selection,
            tuple(scope.scope_id for scope in resolved),
            snapshots,
            captured_at,
        )


class ScopedContextApplication:
    """Prepare final context for one scope using Runtime-owned source policy."""

    def __init__(self, runtime: BuiltinRuntime, scope_id: str) -> None:
        self._runtime = runtime
        self.scope_id = validate_scope_id(scope_id)

    async def prepare(
        self,
        request: PrepareContextRequest,
        /,
        *,
        authorize_scopes: Callable[[tuple[str, ...]], Awaitable[None]] | None = None,
    ) -> PreparedContext:
        if (
            request.assembly is not None
            and sum(section.limit for section in request.assembly.sections) > self._runtime.context_assembly_max_entries
        ):
            raise InvalidRuntimeRequestError("context-assembly-entry-limit")
        async with self._runtime._scope_operation(self.scope_id) as scope:
            if request.assembly is not None and not request.assembly.sections and not request.include_code:
                return PreparedContextBuilder().empty()
            if authorize_scopes is not None:
                await authorize_scopes((self.scope_id, *scope.context_references))
            return await self._prepare(request, scope)

    async def _prepare(self, request: PrepareContextRequest, scope: ScopeDescriptor, /) -> PreparedContext:
        build, effort = await self._prepare_build(request, scope)
        if effort is not None and self._runtime._recall_effort_sink is not None:
            try:
                await self._runtime._recall_effort_sink(effort)
            except Exception as error:
                log_safely(
                    logger,
                    logging.ERROR,
                    "Recall effort sink failed",
                    exc_info=error,
                    extra={
                        "event": "context.recall_gate.sink_failed",
                        "outcome": "failure",
                        "unit": "context",
                    },
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

    async def _prepare_build(
        self,
        request: PrepareContextRequest,
        scope: ScopeDescriptor,
        /,
    ) -> tuple[PreparedContextBuild, RecallEffort | None]:
        """Recall the participating families, optionally expand, then build the context.

        The controlled expansion loop lives here. It runs at most ``policy.max_rounds`` extra
        searches, each of which only lowers the admission floor for the families the caller
        already selected — never a new family, a larger ``limit``, or a different ``mode``.
        Every gate or expansion error degrades to the round-zero candidate set.

        Returns ``(build, effort)``. The RFC 1560 trace is **not** a field of the build:
        ``_prepare`` returns ``build.context`` and discards the rest, so a field there would
        have no production observer. The trace is returned alongside the build and delivered
        by ``_prepare`` to the Runtime's optional sink. ``effort`` is ``None`` whenever the
        policy is not configured, so the default-off path allocates nothing.
        """

        builder = PreparedContextBuilder()
        if request.include_code:
            builder.entry_limit = self._runtime.context_assembly_max_entries
        scope_ids = [self.scope_id, *scope.context_references]
        families: set[str] = (
            {section.family for section in request.assembly.sections}
            if request.assembly is not None
            else {MEMORY_FAMILY, EXPERIENCE_FAMILY, TOPIC_MEMORY_FAMILY}
        )
        # Caller-owned cache of the query vectors round 0 already paid for, keyed by scope.
        # Expansion rounds read it so a repeat search does not re-embed; round 0 fills it.
        reuse: dict[str, MemoryQueryEmbedding] = {}
        topic_reuse: dict[str, MemoryQueryEmbedding | None] = {}

        round_zero = await self._recall_round(
            request,
            scope_ids,
            families,
            builder,
            admission=None,
            reuse=reuse,
            topic_reuse=topic_reuse,
        )
        memory_candidates = list(round_zero.memory)
        experience_candidates = list(round_zero.experience)
        topic_memory_hits = round_zero.topic_memory.hits
        profile_candidates: list[PreparedProfileCandidate] = []
        profiles = self._runtime.profiles
        if "profile" in families and profiles is not None:
            async with profiles.database.transaction() as connection:
                for scope_id in scope_ids:
                    profile = await profiles.latest(connection, scope_id)
                    if profile is not None:
                        profile_candidates.append(PreparedProfileCandidate(scope_id=scope_id, profile=profile))

        policy = self._runtime.recall_sufficiency_policy
        recall_effort: RecallEffort | None = None
        if policy is not None:
            (
                memory_candidates,
                experience_candidates,
                topic_memory_hits,
                recall_effort,
            ) = await self._gated_recall_effort(
                request=request,
                scope_ids=scope_ids,
                families=families,
                builder=builder,
                policy=policy,
                memory_candidates=memory_candidates,
                experience_candidates=experience_candidates,
                topic_memory_hits=topic_memory_hits,
                profile_candidates=profile_candidates,
                reuse=reuse,
                topic_reuse=topic_reuse,
                round_zero=round_zero,
            )
        code = await self._code_candidates(request) if request.include_code else ()
        with self._runtime._stage(
            "context.build",
            attributes={
                "powercontext.context.build.scope_count": len(scope_ids),
                "powercontext.context.build.memory_candidate_count": sum(
                    len(candidates.hits) for candidates in memory_candidates
                ),
                "powercontext.context.build.topic_memory_candidate_count": len(topic_memory_hits),
                "powercontext.context.build.experience_candidate_count": sum(
                    len(candidates.hits) for candidates in experience_candidates
                ),
                "powercontext.context.build.profile_candidate_count": len(profile_candidates),
                "powercontext.context.build.code_candidate_count": len(code),
            },
        ) as span:
            build = builder.build_scopes_result(
                request=request,
                current_scope_id=self.scope_id,
                memory_candidates=memory_candidates,
                topic_memory_hits=topic_memory_hits,
                experience_candidates=experience_candidates,
                profile_candidates=profile_candidates,
                code_candidates=code,
            )
            if recall_effort is not None:
                recall_effort = replace(
                    recall_effort,
                    truncated_items=build.omissions.truncated_items,
                    dropped_items=build.omissions.dropped_items,
                    dropped_below_min_bytes=build.omissions.dropped_below_min_bytes,
                    dropped_no_fitting_truncation=build.omissions.dropped_no_fitting_truncation,
                )
            if span is not None:
                span.set_attributes({
                    "powercontext.context.build.selected_count": len(build.origins) + len(build.code_origins),
                    "powercontext.context.build.code_selected_count": len(build.code_origins),
                    "powercontext.context.build.code_injected_count": len(build.code_origins),
                    "powercontext.context.build.code_omitted_count": max(0, len(code) - len(build.code_origins)),
                    "powercontext.context.build.status": build.context.status,
                    "powercontext.context.build.content_bytes": build.context.content_bytes,
                })
                if recall_effort is not None:
                    # RFC 0028 permits "internal search mode and aggregate selection counts".
                    # These four are aggregates only: no query text, no entry id, no per-entry
                    # attribution, and nothing is written to a table.
                    span.set_attributes({
                        "powercontext.context.build.recall.rounds": recall_effort.rounds,
                        "powercontext.context.build.recall.assessment": recall_effort.assessment,
                        "powercontext.context.build.recall.truncated_items": recall_effort.truncated_items,
                        "powercontext.context.build.recall.dropped_items": recall_effort.dropped_items,
                    })
        return build, recall_effort

    async def _code_candidates(self, request: PrepareContextRequest) -> tuple[PreparedCodeCandidate, ...]:
        try:
            result = await self._runtime.code.for_scope(self.scope_id).query(
                CodeQueryRequest.model_validate({
                    "operation": {"kind": "explore", "query": request.query},
                    "max_bytes": 16000,
                })
            )
        except CodeError as error:
            log_safely(
                logger,
                logging.INFO,
                "Code context unavailable",
                extra={
                    "event": "context.code.unavailable",
                    "reason": error.code,
                },
            )
            return ()
        candidates = code_candidates(result) if isinstance(result, CodeQueryResult) else ()
        log_safely(
            logger,
            logging.INFO,
            "Code context retrieved",
            extra={
                "event": "context.code.retrieved",
                "candidate_count": len(candidates),
            },
        )
        return candidates

    async def _gated_recall_effort(  # noqa: C901 - the bounded expansion loop is intentionally explicit
        self,
        *,
        request: PrepareContextRequest,
        scope_ids: Sequence[str],
        families: set[str],
        builder: PreparedContextBuilder,
        policy: RecallSufficiencyPolicy,
        memory_candidates: list[PreparedMemoryCandidates],
        experience_candidates: list[PreparedExperienceCandidates],
        topic_memory_hits: tuple[TopicMemorySearchHit, ...],
        profile_candidates: Sequence[PreparedProfileCandidate],
        reuse: dict[str, MemoryQueryEmbedding],
        topic_reuse: dict[str, MemoryQueryEmbedding | None],
        round_zero: _RecallRoundOutcome,
    ) -> tuple[
        list[PreparedMemoryCandidates],
        list[PreparedExperienceCandidates],
        tuple[TopicMemorySearchHit, ...],
        RecallEffort,
    ]:
        """Run the bounded expansion loop and return the winning candidates plus the trace.

        Candidates accumulate across rounds; only new identities are ever added, so an earlier
        round's candidate is never removed. Any error returns the round-zero candidates unchanged
        and records ``expansion-failed`` — the gate can never turn a successful prepare into a
        failure. For a consistent signal basis, ``candidates_by_round`` counts the *un-truncated*
        accumulated pool (no family is clamped while the gate is consulting it); the Builder
        ceilings are applied once, on the returned candidates only.
        """

        gate = RecallSufficiencyGate()
        expander = RecallExpander()
        families_expected = _families_with_retrieved_candidates(families, round_zero.admissions)
        families_recoverable = _families_with_recoverable_candidates(families, round_zero.admissions)
        memory_hits_by_scope = {group.scope_id: list(group.hits) for group in memory_candidates}
        memory_ref_by_scope = {group.scope_id: group.memory_ref for group in memory_candidates}
        seen_memory = {_memory_identity(group.scope_id, hit) for group in memory_candidates for hit in group.hits}
        experience_hits_by_scope = {group.scope_id: list(group.hits) for group in experience_candidates}
        seen_experience = {
            _experience_identity(group.scope_id, hit) for group in experience_candidates for hit in group.hits
        }
        accumulated_topic = list(topic_memory_hits)
        seen_topic = {_topic_identity(hit) for hit in topic_memory_hits}
        candidates = build_recall_candidates(
            memory_hits=_flatten_memory_hits(memory_candidates),
            topic_memory_hits=topic_memory_hits,
            experience_hits=_flatten_experience_hits(experience_candidates),
        )
        round_zero_count = len(candidates)
        candidates_by_round = [round_zero_count]
        expansions: list[str] = []
        added_embeddings = 0
        added_generation_calls = 0
        admission_by_family = list(round_zero.admissions)
        try:
            budget = builder.probe_budget(
                request=request,
                current_scope_id=self.scope_id,
                memory_candidates=memory_candidates,
                topic_memory_hits=topic_memory_hits,
                experience_candidates=experience_candidates,
                profile_candidates=profile_candidates,
            )
            assessment = gate.assess(
                candidates,
                request.query,
                policy,
                scope_has_content=bool(candidates) or families_expected > 0,
                budget=budget,
                families_expected=families_expected,
            )
            while not assessment.sufficient and families_recoverable > 0 and len(expansions) < policy.max_rounds:
                plan = expander.plan(len(expansions) + 1, policy)
                issued = await self._recall_round(
                    request,
                    scope_ids,
                    families,
                    builder,
                    admission=plan.admission,
                    reuse=reuse,
                    topic_reuse=topic_reuse,
                )
                for group in issued.memory:
                    _ensure_memory_head_stable(
                        group.scope_id, group.memory_ref, memory_ref_by_scope.get(group.scope_id)
                    )
                    bucket = memory_hits_by_scope.setdefault(group.scope_id, [])
                    for hit in group.hits:
                        identity = _memory_identity(group.scope_id, hit)
                        if identity in seen_memory:
                            continue
                        seen_memory.add(identity)
                        bucket.append(hit)
                for group in issued.experience:
                    bucket = experience_hits_by_scope.setdefault(group.scope_id, [])
                    for hit in group.hits:
                        identity = _experience_identity(group.scope_id, hit)
                        if identity in seen_experience:
                            continue
                        seen_experience.add(identity)
                        bucket.append(hit)
                for hit in issued.topic_memory.hits:
                    identity = _topic_identity(hit)
                    if identity in seen_topic:
                        continue
                    seen_topic.add(identity)
                    accumulated_topic.append(hit)
                expansions.append(plan.action)
                added_embeddings += issued.embedding_calls
                added_generation_calls += issued.generation_calls
                admission_by_family = list(issued.admissions)
                families_recoverable = _families_with_recoverable_candidates(families, issued.admissions)
                candidates = build_recall_candidates(
                    memory_hits=_flatten_scope_memory(memory_hits_by_scope, scope_ids),
                    topic_memory_hits=tuple(accumulated_topic),
                    experience_hits=_flatten_scope_experience(experience_hits_by_scope, scope_ids),
                )
                candidates_by_round.append(len(candidates))
                budget = builder.probe_budget(
                    request=request,
                    current_scope_id=self.scope_id,
                    memory_candidates=_limit_expanded_memory_candidates(
                        [
                            PreparedMemoryCandidates(
                                scope_id=scope_id,
                                memory_ref=memory_ref_by_scope.get(scope_id),
                                hits=tuple(memory_hits_by_scope.get(scope_id, ())),
                            )
                            for scope_id in scope_ids
                        ],
                        memory_candidates,
                        builder.memory_candidate_limit,
                    ),
                    topic_memory_hits=tuple(accumulated_topic[: builder.topic_memory_candidate_limit]),
                    experience_candidates=_limit_expanded_experience_candidates(
                        [
                            PreparedExperienceCandidates(
                                scope_id=scope_id,
                                hits=tuple(experience_hits_by_scope.get(scope_id, ())),
                            )
                            for scope_id in scope_ids
                        ],
                        experience_candidates,
                        builder.experience_candidate_limit,
                    ),
                    profile_candidates=profile_candidates,
                )
                assessment = gate.assess(
                    candidates,
                    request.query,
                    policy,
                    scope_has_content=bool(candidates) or families_expected > 0,
                    budget=budget,
                    families_expected=families_expected,
                )
            if not assessment.sufficient and families_recoverable > 0 and len(expansions) >= policy.max_rounds:
                assessment = replace(assessment, reason=REASON_AT_MAX_ROUNDS)
        except Exception as error:
            log_safely(
                logger,
                logging.ERROR,
                "Recall sufficiency expansion failed; keeping the round-zero candidates",
                exc_info=error,
                extra={
                    "event": "context.recall_gate.expansion_failed",
                    "outcome": "failure",
                    "unit": "context",
                },
            )
            return (
                memory_candidates,
                experience_candidates,
                topic_memory_hits,
                recall_effort(
                    policy=policy,
                    assessment=REASON_EXPANSION_FAILED,
                    expansion_actions=expansions,
                    candidates_by_round=candidates_by_round,
                    admission_by_family=admission_by_family,
                    added_embeddings=added_embeddings,
                    added_generation_calls=added_generation_calls,
                ),
            )
        capped_topic = tuple(accumulated_topic[: builder.topic_memory_candidate_limit])
        return (
            _limit_expanded_memory_candidates(
                [
                    PreparedMemoryCandidates(
                        scope_id=scope_id,
                        memory_ref=memory_ref_by_scope.get(scope_id),
                        hits=tuple(memory_hits_by_scope.get(scope_id, ())),
                    )
                    for scope_id in scope_ids
                ],
                memory_candidates,
                builder.memory_candidate_limit,
            ),
            _limit_expanded_experience_candidates(
                [
                    PreparedExperienceCandidates(
                        scope_id=scope_id,
                        hits=tuple(experience_hits_by_scope.get(scope_id, ())),
                    )
                    for scope_id in scope_ids
                ],
                experience_candidates,
                builder.experience_candidate_limit,
            ),
            capped_topic,
            recall_effort(
                policy=policy,
                assessment=assessment.reason,
                expansion_actions=expansions,
                candidates_by_round=candidates_by_round,
                admission_by_family=admission_by_family,
                added_embeddings=added_embeddings,
                added_generation_calls=added_generation_calls,
            ),
        )

    async def _recall_round(
        self,
        request: PrepareContextRequest,
        scope_ids: Sequence[str],
        families: set[str],
        builder: PreparedContextBuilder,
        *,
        admission: AdmissionFloor | None,
        reuse: dict[str, MemoryQueryEmbedding],
        topic_reuse: dict[str, MemoryQueryEmbedding | None],
    ) -> _RecallRoundOutcome:
        memory_candidates: list[PreparedMemoryCandidates] = []
        experience_candidates: list[PreparedExperienceCandidates] = []
        admissions: list[AdmissionCounts] = []
        embedding_calls = 0
        generation_calls = 0
        for scope_id in scope_ids:
            outcome = await self._recall_scope(
                scope_id,
                request,
                memory_limit=builder.memory_candidate_limit if MEMORY_FAMILY in families else 0,
                experience_limit=builder.experience_candidate_limit if EXPERIENCE_FAMILY in families else 0,
                admission=admission,
                reuse=reuse.get(scope_id),
            )
            memory_candidates.append(outcome.memory)
            experience_candidates.append(outcome.experience)
            if outcome.memory_query_embedding is not None:
                reuse[scope_id] = outcome.memory_query_embedding
            admissions.extend(
                count for count in (outcome.memory_admission, outcome.experience_admission) if count is not None
            )
            embedding_calls += outcome.embedding_calls
            generation_calls += outcome.generation_calls
        memory_candidates = _limit_memory_candidates(memory_candidates, builder.memory_candidate_limit)
        experience_candidates = _limit_experience_candidates(
            experience_candidates,
            builder.experience_candidate_limit,
        )
        topic_outcome = (
            await self._topic_memory_hits(
                request.query.strip(),
                builder.topic_memory_candidate_limit,
                admission=admission,
                reuse=topic_reuse.get(self.scope_id),
                allow_embedding=self.scope_id not in topic_reuse or topic_reuse[self.scope_id] is not None,
            )
            if TOPIC_MEMORY_FAMILY in families
            else TopicMemoryRecallOutcome()
        )
        if TOPIC_MEMORY_FAMILY in families:
            topic_reuse[self.scope_id] = topic_outcome.query_embedding
        return _RecallRoundOutcome(
            memory=tuple(memory_candidates),
            experience=tuple(experience_candidates),
            topic_memory=topic_outcome,
            admissions=tuple(admissions) + (() if topic_outcome.admission is None else (topic_outcome.admission,)),
            embedding_calls=embedding_calls + topic_outcome.embedding_calls,
            generation_calls=generation_calls,
        )

    async def _recall_scope(
        self,
        scope_id: str,
        request: PrepareContextRequest,
        *,
        memory_limit: int,
        experience_limit: int,
        admission: AdmissionFloor | None,
        reuse: MemoryQueryEmbedding | None,
    ) -> _ScopeRecallOutcome:
        context_manager = (
            self._runtime._context(scope_id, embedding_purpose=ModelUsagePurpose.MEMORY_RECALL)
            if memory_limit > 0
            else self._runtime._scoped_operation(scope_id, embedding_purpose=ModelUsagePurpose.MEMORY_RECALL)
        )
        async with (
            context_manager as context,
            self._runtime._locked(scope_id),
        ):
            with self._runtime._stage(
                _MEMORY_SEARCH_STAGE,
                attributes={
                    _MEMORY_SEARCH_REQUESTED_MODE: "auto",
                    _MEMORY_SEARCH_LIMIT: memory_limit,
                },
            ) as span:
                current = None
                memory_hits = ()
                search_mode: str | None = None
                if context is not None:
                    service = context.artifacts.memory
                    current = await _head_or_none(service, context.artifacts.memory_artifact_id)
                    if current is not None:
                        result = await service.search(
                            request.query,
                            memories=(current,),
                            limit=memory_limit,
                            mode="auto",
                            admission=admission,
                            query_embedding=reuse,
                        )
                        memory_hits = result.hits
                        search_mode = result.mode
                        memory_admission = (
                            None if result.admission is None else replace(result.admission, scope_id=scope_id)
                        )
                        if result.query_embedding is not None:
                            reuse = result.query_embedding
                        memory_embedding_calls = result.embedding_calls
                        memory_generation_calls = result.generation_calls
                    else:
                        memory_admission = None
                        memory_embedding_calls = 0
                        memory_generation_calls = 0
                else:
                    memory_admission = None
                    memory_embedding_calls = 0
                    memory_generation_calls = 0
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
                    "powercontext.experience.search.limit": experience_limit,
                },
            ) as span:
                if experience_recall is None or experience_limit == 0:
                    experience_outcome = ExperienceSearchOutcome()
                elif admission is None:
                    experience_outcome = await experience_recall(scope_id, request.query, experience_limit)
                else:
                    experience_outcome = await experience_recall(
                        scope_id,
                        request.query,
                        experience_limit,
                        admission=admission,
                    )
                experience_hits = experience_outcome.hits
                if span is not None:
                    span.set_attributes({"powercontext.experience.search.result_count": len(experience_hits)})
        return _ScopeRecallOutcome(
            memory=PreparedMemoryCandidates(
                scope_id=scope_id,
                memory_ref=None if current is None else current.as_ref(),
                hits=memory_hits,
            ),
            experience=PreparedExperienceCandidates(scope_id=scope_id, hits=experience_hits),
            memory_admission=memory_admission,
            experience_admission=(
                None
                if experience_outcome.admission is None
                else replace(experience_outcome.admission, scope_id=scope_id)
            ),
            memory_query_embedding=reuse,
            embedding_calls=memory_embedding_calls,
            generation_calls=memory_generation_calls,
        )

    async def _topic_memory_hits(
        self,
        query: str,
        limit: int,
        *,
        admission: AdmissionFloor | None,
        reuse: MemoryQueryEmbedding | None,
        allow_embedding: bool,
    ) -> TopicMemoryRecallOutcome:
        configured = self._runtime._topic_memory_search is not None
        bounded_query = _bounded_topic_memory_recall_query(query)
        with self._runtime._stage(
            "topic_memory.search",
            attributes={
                "powercontext.topic_memory.search.configured": configured,
                "powercontext.topic_memory.search.limit": limit,
            },
        ) as span:
            result = (
                TopicMemorySearchResult(mode="fts")
                if not configured
                else (
                    await self._runtime.topic_memory.for_scope(self.scope_id).search(
                        SearchTopicMemoryRequest(query=bounded_query, limit=limit),
                        admission=admission,
                        query_embedding=reuse,
                        embedding_timeout_seconds=_CONTEXT_TOPIC_EMBEDDING_TIMEOUT_SECONDS,
                        allow_embedding=allow_embedding,
                    )
                )
            )
            if span is not None:
                span.set_attributes({"powercontext.topic_memory.search.result_count": len(result.hits)})
            return TopicMemoryRecallOutcome(
                hits=result.hits,
                admission=result.admission,
                query_embedding=result.query_embedding,
                embedding_calls=result.embedding_calls,
            )


def _limit_memory_candidates(
    candidates: list[PreparedMemoryCandidates],
    limit: int,
) -> list[PreparedMemoryCandidates]:
    counts = _round_robin_counts(tuple(len(group.hits) for group in candidates), limit)
    return [
        PreparedMemoryCandidates(
            scope_id=group.scope_id,
            memory_ref=group.memory_ref,
            hits=group.hits[:count],
        )
        for group, count in zip(candidates, counts, strict=True)
    ]


def _ensure_memory_head_stable(
    scope_id: str,
    actual: ArtifactRef | None,
    expected: ArtifactRef | None,
) -> None:
    if actual != expected:
        raise RuntimeError(f"Memory head changed during recall expansion for scope {scope_id}")  # noqa: TRY003


def _limit_expanded_memory_candidates(
    candidates: list[PreparedMemoryCandidates],
    round_zero: list[PreparedMemoryCandidates],
    limit: int,
) -> list[PreparedMemoryCandidates]:
    counts = _prefix_preserving_counts(
        tuple(len(group.hits) for group in candidates),
        tuple(len(group.hits) for group in round_zero),
        limit,
    )
    return [
        PreparedMemoryCandidates(
            scope_id=group.scope_id,
            memory_ref=group.memory_ref,
            hits=group.hits[:count],
        )
        for group, count in zip(candidates, counts, strict=True)
    ]


def _limit_experience_candidates(
    candidates: list[PreparedExperienceCandidates],
    limit: int,
) -> list[PreparedExperienceCandidates]:
    counts = _round_robin_counts(tuple(len(group.hits) for group in candidates), limit)
    return [
        PreparedExperienceCandidates(scope_id=group.scope_id, hits=group.hits[:count])
        for group, count in zip(candidates, counts, strict=True)
    ]


def _limit_expanded_experience_candidates(
    candidates: list[PreparedExperienceCandidates],
    round_zero: list[PreparedExperienceCandidates],
    limit: int,
) -> list[PreparedExperienceCandidates]:
    counts = _prefix_preserving_counts(
        tuple(len(group.hits) for group in candidates),
        tuple(len(group.hits) for group in round_zero),
        limit,
    )
    return [
        PreparedExperienceCandidates(scope_id=group.scope_id, hits=group.hits[:count])
        for group, count in zip(candidates, counts, strict=True)
    ]


def _prefix_preserving_counts(
    sizes: tuple[int, ...],
    prefix_sizes: tuple[int, ...],
    limit: int,
) -> tuple[int, ...]:
    prefix_counts = tuple(min(size, prefix) for size, prefix in zip(sizes, prefix_sizes, strict=True))
    remaining = max(0, limit - sum(prefix_counts))
    suffix_counts = _round_robin_counts(
        tuple(size - prefix for size, prefix in zip(sizes, prefix_counts, strict=True)),
        remaining,
    )
    return tuple(prefix + suffix for prefix, suffix in zip(prefix_counts, suffix_counts, strict=True))


def _families_with_retrieved_candidates(
    families: set[str],
    admissions: Sequence[AdmissionCounts],
) -> int:
    """Count selected families that returned backend candidates in this recall pass."""

    return len({
        admission.family for admission in admissions if admission.family in families and admission.retrieved > 0
    })


def _families_with_recoverable_candidates(
    families: set[str],
    admissions: Sequence[AdmissionCounts],
) -> int:
    """Count selected families where a lower admission floor may recover candidates."""

    return len({
        admission.family
        for admission in admissions
        if admission.family in families
        and (admission.rejected if admission.rejected is not None else admission.retrieved - admission.admitted) > 0
    })


def _round_robin_counts(sizes: tuple[int, ...], limit: int) -> tuple[int, ...]:
    counts = [0] * len(sizes)
    remaining = limit
    while remaining > 0:
        advanced = False
        for index, size in enumerate(sizes):
            if counts[index] >= size:
                continue
            counts[index] += 1
            remaining -= 1
            advanced = True
            if remaining == 0:
                break
        if not advanced:
            break
    return tuple(counts)


def _flatten_memory_hits(
    candidates: Sequence[PreparedMemoryCandidates],
) -> tuple[MemoryHit, ...]:
    return tuple(hit for group in candidates for hit in group.hits)


def _flatten_experience_hits(
    candidates: Sequence[PreparedExperienceCandidates],
) -> tuple[ExperienceSearchHit, ...]:
    return tuple(hit for group in candidates for hit in group.hits)


def _flatten_scope_memory(
    hits_by_scope: Mapping[str, Sequence[MemoryHit]],
    scope_ids: Sequence[str],
) -> tuple[MemoryHit, ...]:
    return tuple(hit for scope_id in scope_ids for hit in hits_by_scope.get(scope_id, ()))


def _flatten_scope_experience(
    hits_by_scope: Mapping[str, Sequence[ExperienceSearchHit]],
    scope_ids: Sequence[str],
) -> tuple[ExperienceSearchHit, ...]:
    return tuple(hit for scope_id in scope_ids for hit in hits_by_scope.get(scope_id, ()))


def _memory_identity(scope_id: str, hit: MemoryHit) -> tuple[str, str, int, str, str]:
    return (scope_id, hit.memory_ref.artifact_id, hit.memory_ref.revision, hit.entry_id, hit.entry_version_id)


def _experience_identity(scope_id: str, hit: ExperienceSearchHit) -> tuple[str, str, int]:
    return (scope_id, hit.artifact_ref.artifact_id, hit.artifact_ref.revision)


def _topic_identity(hit: TopicMemorySearchHit) -> tuple[str, int]:
    return (hit.artifact_ref.artifact_id, hit.artifact_ref.revision)


def _admission_keyword(admission: AdmissionFloor | None) -> dict[str, Any]:
    """Forward ``admission`` only when set, keeping today's exact downstream calls otherwise."""

    return {} if admission is None else {"admission": admission}


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
                memory_citations=request.memory_citations,
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

    async def search(self, query: str, limit: int, /) -> tuple[SkillSearchHit, ...]:
        recall = self._runtime._skill_recall
        if recall is None:
            return ()
        async with self._runtime._scoped_operation(self.scope_id):
            return await recall(self.scope_id, query, limit)

    async def list(
        self,
        *,
        include_deprecated: bool = False,
        limit: int = 100,
    ) -> tuple[tuple[Skill, ArtifactGovernance], ...]:
        lister = self._runtime._skill_lister
        if lister is None:
            return ()
        async with self._runtime._scoped_operation(self.scope_id):
            return await lister(self.scope_id, include_deprecated, limit)

    async def origins(self, skills: tuple[Skill, ...], /) -> tuple[SkillOrigin, ...]:
        """Resolve display provenance for current Skill revisions in one bounded read."""

        reader = self._runtime._skill_origin_reader
        if reader is None:
            raise _RuntimeStateError("skill-provenance")
        async with self._runtime._scoped_operation(self.scope_id):
            return await reader(self.scope_id, skills)

    async def package(self, artifact: ArtifactRef, /) -> SkillPackageSnapshot:
        resolver = self._runtime._skill_package_resolver
        if resolver is None:
            raise _RuntimeStateError("skill-package")
        async with self._runtime._scoped_operation(self.scope_id):
            return await resolver(self.scope_id, artifact)

    async def package_snapshot(self, package: SkillPackageRef, /) -> SkillPackageSnapshot:
        resolver = self._runtime._package_snapshot_resolver
        if resolver is None:
            raise _RuntimeStateError("skill-package")
        async with self._runtime._scoped_operation(self.scope_id):
            return await resolver(self.scope_id, package)

    async def upload_package(
        self,
        archive_bytes: bytes,
        reason: str | None,
        target: ArtifactRef | None,
        /,
    ) -> SkillCandidate:
        uploader = self._runtime._skill_package_uploader
        if uploader is None:
            raise _RuntimeStateError("skill-package")
        async with self._runtime._scoped_operation(self.scope_id), self._runtime._locked(self.scope_id):
            return await uploader(self.scope_id, archive_bytes, reason, target)

    async def record_usage(self, observation: SkillUsageCapture, /) -> SourceReceipt:
        recorder = self._runtime._skill_usage_recorder
        if recorder is None:
            raise _RuntimeStateError("skill-usage")
        async with self._runtime._scoped_operation(self.scope_id), self._runtime._locked(self.scope_id):
            return await recorder(self.scope_id, observation)

    async def governance(self, artifact_id: str, /) -> ArtifactGovernance:
        reader = self._runtime._skill_governance_reader
        if reader is None:
            raise _RuntimeStateError("skill-governance")
        async with self._runtime._scoped_operation(self.scope_id):
            return await reader(self.scope_id, artifact_id)

    async def update_lifecycle(
        self,
        artifact_id: str,
        expected_generation: int,
        lifecycle_state: ArtifactLifecycleState,
        replacement_artifact_id: str | None,
        /,
    ) -> ArtifactGovernance:
        updater = self._runtime._skill_governance_updater
        if updater is None:
            raise _RuntimeStateError("skill-governance")
        async with self._runtime._scoped_operation(self.scope_id), self._runtime._locked(self.scope_id):
            return await updater(
                self.scope_id,
                artifact_id,
                expected_generation,
                lifecycle_state,
                replacement_artifact_id,
            )

    async def inspect_publication(
        self,
        artifact: ArtifactRef,
        target: AgentSkillTarget,
        /,
    ) -> ManagedSkillPublicationStatus:
        async with self._runtime._scoped_operation(self.scope_id):
            service = self._runtime._skill_publications(self.scope_id, target, artifact)
            return await service.inspect(artifact, target)

    async def publish(
        self,
        artifact: ArtifactRef,
        target: AgentSkillTarget,
        /,
        *,
        allow_deprecated: bool = False,
    ) -> ManagedSkillPublicationStatus:
        async with self._runtime._scoped_operation(self.scope_id):
            service = self._runtime._skill_publications(self.scope_id, target, artifact)
            return await service.publish(artifact, target, allow_deprecated=allow_deprecated)

    async def unpublish(
        self,
        artifact: ArtifactRef,
        target: AgentSkillTarget,
        /,
    ) -> ManagedSkillPublicationStatus:
        async with self._runtime._scoped_operation(self.scope_id):
            service = self._runtime._skill_publications(self.scope_id, target, artifact)
            return await service.unpublish(artifact, target)


class SkillApplication:
    """Select a scoped managed Skill application service."""

    def __init__(self, runtime: BuiltinRuntime) -> None:
        self._runtime = runtime

    def for_scope(self, scope_id: str, /) -> ScopedSkillApplication:
        return ScopedSkillApplication(self._runtime, scope_id)


class RemoteSkillApplication:
    """Expose remote target lifecycle and Receiver reconciliation through the Runtime boundary."""

    def __init__(self, runtime: BuiltinRuntime) -> None:
        self._runtime = runtime

    def _service(self) -> RemoteSkillDistributionService:
        service = self._runtime._remote_skill_distribution
        if service is None:
            raise _RuntimeStateError("remote-skill-distribution")
        return service

    async def list_targets(
        self,
        scope_id: str,
        /,
        *,
        target_id: str | None = None,
        limit: int = 100,
    ) -> tuple[RemoteSkillTargetStatus, ...]:
        async with self._runtime._scoped_operation(scope_id):
            return await self._service().list_targets(
                scope_id,
                target_id=target_id,
                limit=limit,
            )

    async def create_target(
        self,
        scope_id: str,
        agent_kind: AgentKind,
        display_name: str,
        /,
    ) -> RemoteTargetEnrollment:
        async with self._runtime._scoped_operation(scope_id):
            return await self._service().create_target(scope_id, agent_kind, display_name)

    async def enroll(
        self,
        enrollment_code: str,
        installation_id: str,
        receiver_version: str,
        environment_fingerprint: str | None,
        machine_hostname: str | None = None,
        workspace_name: str | None = None,
        /,
    ) -> RemoteTargetCredential:
        async with self._runtime._operation():
            return await self._service().enroll(
                enrollment_code,
                installation_id,
                receiver_version,
                environment_fingerprint,
                machine_hostname,
                workspace_name,
            )

    async def rename_target(
        self,
        scope_id: str,
        target_id: str,
        expected_generation: int,
        display_name: str,
        /,
    ) -> RemoteAgentSkillTarget:
        async with self._runtime._scoped_operation(scope_id):
            return await self._service().rename_target(scope_id, target_id, expected_generation, display_name)

    async def revoke_target(
        self,
        scope_id: str,
        target_id: str,
        expected_generation: int,
        /,
    ) -> RemoteAgentSkillTarget:
        async with self._runtime._scoped_operation(scope_id):
            return await self._service().revoke_target(scope_id, target_id, expected_generation)

    async def publish(
        self,
        scope_id: str,
        target_id: str,
        artifact: ArtifactRef,
        expected_generation: int | None,
        /,
        *,
        allow_deprecated: bool = False,
    ) -> SkillPublication:
        async with self._runtime._scoped_operation(scope_id):
            return await self._service().publish(
                scope_id,
                target_id,
                artifact,
                expected_generation,
                allow_deprecated=allow_deprecated,
            )

    async def unpublish(
        self,
        scope_id: str,
        target_id: str,
        artifact_id: str,
        expected_generation: int,
        /,
    ) -> SkillPublication:
        async with self._runtime._scoped_operation(scope_id):
            return await self._service().unpublish(scope_id, target_id, artifact_id, expected_generation)

    async def reconcile(
        self,
        credential: str,
        observations: tuple[RemoteSkillObservation, ...],
        receiver_version: str,
        environment_fingerprint: str | None,
        /,
    ) -> RemoteSkillReconcileResult:
        async with self._runtime._operation():
            return await self._service().reconcile(
                credential,
                observations,
                receiver_version,
                environment_fingerprint,
            )

    async def download(
        self,
        credential: str,
        generation: int,
        artifact: ArtifactRef,
        package: SkillPackageRef,
        /,
    ) -> SkillPackageSnapshot:
        async with self._runtime._operation():
            return await self._service().download(credential, generation, artifact, package)

    async def receipt(
        self,
        credential: str,
        receipt: RemoteSkillReceipt,
        /,
    ) -> RemoteSkillReceiptResult:
        async with self._runtime._operation():
            return await self._service().receipt(credential, receipt)


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
        *,
        evidence_authorizer: HandoffEvidenceAuthorizer | None = None,
    ) -> HandoffResolution:
        async with self._runtime._context(self.scope_id) as context:
            return await context.artifacts.handoff.continue_from(
                handoff,
                evidence_authorizer=evidence_authorizer,
            )

    async def continue_latest(
        self,
        *,
        evidence_authorizer: HandoffEvidenceAuthorizer | None = None,
    ) -> HandoffResolution:
        async with self._runtime._context(self.scope_id) as context:
            return await context.artifacts.handoff.continue_latest(evidence_authorizer=evidence_authorizer)

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
        receipt = await self._runtime.sources.for_scope(self.scope_id)._capture(
            CaptureSource(
                source_id=source_id,
                content=value.model_dump_json(by_alias=True, exclude_none=False, indent=2),
                metadata={"kind": kind, "schema": value.model_dump(by_alias=True)["schema"]},
            ),
            handoff_receipt=kind == HANDOFF_RECEIPT_SOURCE_KIND,
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

    async def inspect_evidence(self, candidate_id: str, expected_version: int):
        """Expand the selected Candidate version, including exact entry provenance."""

        async with self._runtime._scoped_operation(self.scope_id):
            return await self._runtime._review(self.scope_id).inspect_evidence(candidate_id, expected_version)

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
                memory_citations=request.memory_citations,
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
                            tag_filter=request.tag_filter,
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

    async def capacity(self) -> MemoryCapacity:
        """Read capacity of the Scope's current Memory, or raise when it does not exist."""

        async with self._runtime._context(self.scope_id) as context:
            service = context.artifacts.memory
            current = await service.head(context.artifacts.memory_artifact_id)
            _validate_memory_identity(context.artifacts.memory_artifact_id, current)
            return await service.capacity(current)

    async def list(self, *, include_inactive: bool = False, tag_filter: TagFilter | None = None) -> MemoryEntriesPage:
        async with self._runtime._context(self.scope_id) as context:
            service = context.artifacts.memory
            current = await _head_or_none(service, context.artifacts.memory_artifact_id)
            if current is None:
                return MemoryEntriesPage(memory_ref=None)
            entries = tuple(
                _entry_record(current, entry) for entry in await service.entries(current, tag_filter=tag_filter)
            )
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


class ScopedTopicMemoryApplication:
    """Search, exactly read, and request processing for Topic Memory in one scope."""

    def __init__(self, runtime: BuiltinRuntime, scope_id: str) -> None:
        self._runtime = runtime
        self.scope_id = validate_scope_id(scope_id)

    async def search(
        self,
        request: SearchTopicMemoryRequest,
        /,
        *,
        admission: AdmissionFloor | None = None,
        query_embedding: MemoryQueryEmbedding | None = None,
        embedding_timeout_seconds: float | None = None,
        allow_embedding: bool = True,
    ) -> TopicMemorySearchResult:
        search = self._runtime._topic_memory_search
        if search is None:
            raise _RuntimeStateError("topic-memory-search")
        query = request.query
        if query != query.strip() or not query or len(query) > MAX_TOPIC_MEMORY_QUERY_LENGTH:
            raise InvalidRuntimeRequestError("topic-memory-query")
        if not 1 <= request.limit <= MAX_TOPIC_MEMORY_SEARCH_LIMIT:
            raise InvalidRuntimeRequestError("topic-memory-limit")
        if len(set(analyze_text(query).split())) > MAX_TOPIC_MEMORY_QUERY_TERMS:
            raise InvalidRuntimeRequestError("topic-memory-query-terms")

        used_fallback = False
        async with self._runtime._scoped_operation(
            self.scope_id,
            embedding_purpose=ModelUsagePurpose.TOPIC_MEMORY_RECALL,
        ):
            embedding = self._runtime._topic_memory_embedding_model if allow_embedding else None
            browse = self._runtime._topic_memory_browse
            if embedding is not None and browse is not None and not await browse(self.scope_id, limit=1, after=None):
                embedding = None
            if embedding is None:
                result = await search(
                    self.scope_id,
                    query,
                    limit=request.limit,
                    mode="fts",
                    **_admission_keyword(admission),
                )
                result = result.model_copy(update={"embedding_calls": 0})
            else:
                result, used_fallback = await self._search_with_embedding(
                    request,
                    embedding,
                    search,
                    admission,
                    query_embedding,
                    embedding_timeout_seconds,
                )
        observer = self._runtime._topic_memory_search_observer
        if observer is not None:
            try:
                observer(result.mode, used_fallback)
            except Exception as error:
                log_safely(
                    logger,
                    logging.ERROR,
                    "Topic Memory search observation failed",
                    exc_info=error,
                    extra={
                        "event": "topic_memory.search.observation_failed",
                        "outcome": "failure",
                        "unit": "topic-memory",
                    },
                )
        return result

    async def _search_with_embedding(
        self,
        request: SearchTopicMemoryRequest,
        embedding: EmbeddingModel,
        search: TopicMemorySearch,
        admission: AdmissionFloor | None,
        query_embedding: MemoryQueryEmbedding | None,
        embedding_timeout_seconds: float | None,
    ) -> tuple[TopicMemorySearchResult, bool]:
        if query_embedding is not None and query_embedding.embedding_profile == embedding.profile:
            result = await search(
                self.scope_id,
                request.query,
                limit=request.limit,
                mode="hybrid",
                query_vector=query_embedding.query_vector,
                embedding_profile=query_embedding.embedding_profile,
                **_admission_keyword(admission),
            )
            return result.model_copy(update={"query_embedding": query_embedding, "embedding_calls": 0}), False
        try:
            async with asyncio.timeout(embedding_timeout_seconds):
                embedded = await embed_query(embedding, (request.query,))
            if len(embedded.vectors) != 1:
                raise InvalidInferenceOutputError("embed", "provider returned the wrong vector count")
        except (InferenceUnavailableError, InferenceTimeoutError, TimeoutError) as error:
            used_fallback = True
            log_safely(
                logger,
                logging.WARNING,
                "Topic Memory search fell back to FTS",
                extra={
                    "event": "topic_memory.search.embedding_fallback",
                    "outcome": "fallback",
                    "mode": "fts",
                    "error_code": (
                        "inference_timeout"
                        if isinstance(error, (InferenceTimeoutError, TimeoutError))
                        else "inference_unavailable"
                    ),
                    "unit": "topic-memory",
                },
            )
        else:
            result = await search(
                self.scope_id,
                request.query,
                limit=request.limit,
                mode="hybrid",
                query_vector=embedded.vectors[0],
                embedding_profile=embedding.profile,
                **_admission_keyword(admission),
            )
            return result.model_copy(
                update={
                    "query_embedding": MemoryQueryEmbedding(
                        query_vector=tuple(embedded.vectors[0]),
                        embedding_profile=embedding.profile,
                    ),
                    "embedding_calls": 1,
                }
            ), False

        result = await search(
            self.scope_id,
            request.query,
            limit=request.limit,
            mode="fts",
            **_admission_keyword(admission),
        )
        return result.model_copy(update={"embedding_calls": 1}), used_fallback

    async def get(self, request: GetTopicMemoryRequest, /) -> PublishedTopicMemory:
        if self._runtime._topic_memory_get is None:
            raise _RuntimeStateError("topic-memory-get")
        if request.artifact.family != TopicMemory.family:
            raise InvalidRuntimeRequestError("topic-memory-family")
        async with self._runtime._scoped_operation(self.scope_id):
            return await self._runtime._topic_memory_get(self.scope_id, request.artifact)

    async def browse(
        self,
        *,
        limit: int,
        after: TopicMemoryBrowseCursor | None = None,
    ) -> tuple[TopicMemoryCurrentItem, ...]:
        """Browse current Topic heads for a private management projection."""

        if self._runtime._topic_memory_browse is None:
            raise _RuntimeStateError("topic-memory-browse")
        if not 1 <= limit <= 100:
            raise InvalidRuntimeRequestError("topic-memory-browse-limit")
        async with self._runtime._scoped_operation(self.scope_id):
            return await self._runtime._topic_memory_browse(self.scope_id, limit=limit, after=after)

    async def flush(self) -> TopicMemoryFlushResult:
        if not self._runtime._topic_memory_processing_available or self._runtime._topic_memory_flush is None:
            raise TopicMemoryProcessingUnavailableError
        async with self._runtime._scoped_operation(self.scope_id), self._runtime._locked(self.scope_id):
            accepted = await self._runtime._topic_memory_flush(self.scope_id)
        if accepted and self._runtime.artifact_processing_supervisor is not None:
            try:
                self._runtime.artifact_processing_supervisor.wake(TOPIC_MEMORY_SOURCE_WINDOW_BINDING)
            except Exception as error:
                log_safely(
                    logger,
                    logging.WARNING,
                    "Topic Memory supervisor wake failed after flush acceptance",
                    exc_info=error,
                    extra={
                        "event": "topic_memory.flush.wake_failed",
                        "outcome": "accepted",
                        "unit": "topic-memory",
                    },
                )
        return TopicMemoryFlushResult(status="accepted" if accepted else "idle")


class TopicMemoryApplication:
    """Select the scoped Topic Memory application service."""

    def __init__(self, runtime: BuiltinRuntime) -> None:
        self._runtime = runtime

    def for_scope(self, scope_id: str, /) -> ScopedTopicMemoryApplication:
        return ScopedTopicMemoryApplication(self._runtime, scope_id)


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
                        runner = self._runtime._scheduled_source_runner
                        result = (
                            await self._runtime.memory.for_scope(scope_id).flush()
                            if runner is None
                            else await runner(scope_id, self._runtime)
                        )
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
                        runner = self._runtime._scheduled_experience_runner
                        result = (
                            await self._runtime.experience.for_scope(scope_id).incubate()
                            if runner is None
                            else await runner(scope_id, self._runtime)
                        )
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
        code_service: CodeService | None = None,
        source_window_limit: int = 100,
        context_assembly_max_entries: int = 8,
        recall_sufficiency_policy: RecallSufficiencyPolicy | None = None,
        scope_cache_size: int = DEFAULT_SCOPE_CACHE_SIZE,
        scope_evictor: ScopeEvictor | None = None,
        scope_cache_observer: ScopeCacheObserver | None = None,
        scope_ids: ScopeIds | None = None,
        review_service: ReviewServiceFactory | None = None,
        profiles: RelationalProfileService | None = None,
        subject_sources: SubjectSourceService | None = None,
        generation_service: GenerationServiceFactory | None = None,
        experience_recall: ExperienceRecall | None = None,
        skill_recall: SkillRecall | None = None,
        skill_lister: SkillLister | None = None,
        skill_origin_reader: SkillOriginReader | None = None,
        skill_governance_reader: SkillGovernanceReader | None = None,
        skill_governance_updater: SkillGovernanceUpdater | None = None,
        skill_package_resolver: SkillPackageResolver | None = None,
        package_snapshot_resolver: PackageSnapshotResolver | None = None,
        skill_package_uploader: SkillPackageUploader | None = None,
        skill_usage_recorder: SkillUsageRecorder | None = None,
        experience_incubator: ExperienceIncubator | None = None,
        topic_memory_search: TopicMemorySearch | None = None,
        topic_memory_get: TopicMemoryGet | None = None,
        topic_memory_browse: TopicMemoryBrowse | None = None,
        topic_memory_flush: TopicMemoryFlush | None = None,
        topic_memory_embedding_model: EmbeddingModel | None = None,
        topic_memory_processing_available: bool = False,
        topic_memory_search_observer: TopicMemorySearchObserver | None = None,
        external_skill_registry: ExternalSkillRegistryFactory | None = None,
        external_skill_importer: ExternalSkillImporter | None = None,
        skill_publication_service: SkillPublicationServiceFactory | None = None,
        remote_skill_distribution: RemoteSkillDistributionService | None = None,
        statistics_service: StatisticsServiceFactory | None = None,
        record_service: RecordService | None = None,
        prompt_service: PromptService | None = None,
        recall_token_estimator: RecallTokenEstimator | None = None,
        recall_effort_sink: RecallEffortSink | None = None,
        publication_application: ArtifactPublicationApplication | None = None,
        scope_application: ScopeApplication | None = None,
        readiness: RuntimeReadinessChecks | None = None,
        clock: Clock | None = None,
        tracing: RuntimeTracing | None = None,
        scheduled_source_runner: ScheduledSourceRunner | None = None,
        scheduled_experience_runner: ScheduledExperienceRunner | None = None,
        remote_ingestion: RemoteIngestion | None = None,
        dream_service: DreamService | None = None,
        generation_concurrency: int = 4,
    ) -> None:
        if source_window_limit < 1:
            raise _RuntimeConfigurationError("source_window_limit")
        if context_assembly_max_entries < 1:
            raise _RuntimeConfigurationError("context_assembly_max_entries")
        if scope_cache_size < 1:
            raise _RuntimeConfigurationError("scope_cache_size")
        self._provider = provider
        self._capabilities = capabilities
        self._review_service = review_service
        self.profiles = profiles
        self.subject_sources = subject_sources
        self._generation_service = generation_service
        self._dream_service = dream_service
        self._review_evidence_authorizer: ScopedEvidenceAuthorizer | None = None
        self._review_authorization_context: AuthorizationContext = nullcontext
        self._generation_slots = asyncio.Semaphore(generation_concurrency)
        self._generation_owners: set[asyncio.Task[Any]] = set()
        self._experience_recall = experience_recall
        self._skill_recall = skill_recall
        self._skill_lister = skill_lister
        self._skill_origin_reader = skill_origin_reader
        self._skill_governance_reader = skill_governance_reader
        self._skill_governance_updater = skill_governance_updater
        self._skill_package_resolver = skill_package_resolver
        self._package_snapshot_resolver = package_snapshot_resolver
        self._skill_package_uploader = skill_package_uploader
        self._skill_usage_recorder = skill_usage_recorder
        self._experience_incubator = experience_incubator
        self._topic_memory_search = topic_memory_search
        self._topic_memory_get = topic_memory_get
        self._topic_memory_browse = topic_memory_browse
        self._topic_memory_flush = topic_memory_flush
        self._topic_memory_embedding_model = topic_memory_embedding_model
        self._topic_memory_processing_available = topic_memory_processing_available
        self._topic_memory_search_observer = topic_memory_search_observer
        self._external_skill_registry = external_skill_registry
        self._external_skill_importer = external_skill_importer
        self._skill_publication_service = skill_publication_service
        self._remote_skill_distribution = remote_skill_distribution
        self._statistics_service = statistics_service
        self._record_service = record_service
        self._prompt_service = prompt_service
        self._recall_token_estimator = recall_token_estimator
        self._recall_effort_sink = recall_effort_sink
        self.publications = publication_application
        self.scopes = scope_application
        self._readiness = RuntimeReadinessChecks() if readiness is None else readiness
        self._clock = _utc_now if clock is None else clock
        self._tracing = tracing
        self._scheduled_source_runner = scheduled_source_runner
        self._scheduled_experience_runner = scheduled_experience_runner
        self.source_window_limit = source_window_limit
        self.context_assembly_max_entries = context_assembly_max_entries
        self.recall_sufficiency_policy = recall_sufficiency_policy
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
        self.code = CodeApplication(self, code_service or CodeService(CodeConfig()))
        self.context = ContextApplication(self)
        self.experience = ExperienceApplication(self)
        self.dream = DreamApplication(self)
        self.external_skills = ExternalSkillApplication(self)
        self.handoff = HandoffApplication(self)
        self.work = WorkApplication(self)
        self.memory = MemoryApplication(self)
        self.topic_memory = TopicMemoryApplication(self)
        self.records = RecordApplication(self)
        self.prompts = PromptApplication(self)
        self.review = ReviewApplication(self)
        self.skill = SkillApplication(self)
        self.remote_skills = RemoteSkillApplication(self)
        self.statistics = StatisticsApplication(self)
        self.handoff_report: HandoffReportApplication | None = None
        self.artifact_processing_supervisor: ArtifactProcessingSupervisors | None = None
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
        supervisor_status = (
            "disabled"
            if self.artifact_processing_supervisor is None
            else self.artifact_processing_supervisor.status.value
        )
        status = dependencies.status
        if supervisor_status == "degraded":
            status = RuntimeReadinessStatus.NOT_READY
        return RuntimeReadiness(
            status=status,
            checks={
                "runtime": ReadinessCheckStatus.READY,
                **dependencies.checks,
                "artifact_processing_supervisor": supervisor_status,
                **(
                    {}
                    if self.artifact_processing_supervisor is None
                    else {
                        f"artifact_processing.{family}": str(details["status"])
                        for family, details in self.artifact_processing_supervisor.family_status.items()
                    }
                ),
            },
        )

    def start_scheduler(  # noqa: C901
        self,
        scheduler_path: str | Path,
        schedule_seconds: float | None,
        *,
        experience_schedule_seconds: float | None = None,
        profile_cron: str | None = None,
        profile_timezone: str = "Asia/Shanghai",
        profile_max_concurrency: int = 4,
    ) -> None:
        """Start the APScheduler time adapter for this Runtime."""

        if schedule_seconds is None and experience_schedule_seconds is None and profile_cron is None:
            raise _RuntimeConfigurationError("schedule_seconds")
        if schedule_seconds is not None and schedule_seconds <= 0:
            raise _RuntimeConfigurationError("schedule_seconds")
        if experience_schedule_seconds is not None and experience_schedule_seconds <= 0:
            raise _RuntimeConfigurationError("experience_schedule_seconds")
        if schedule_seconds is not None and self.processor is None:
            raise _RuntimeConfigurationError("scope_ids")
        if experience_schedule_seconds is not None and self.experience_processor is None:
            raise _RuntimeStateError("experience-incubation")
        if self._scheduler is not None or self.artifact_processing_supervisor is not None:
            raise _RuntimeStateError("scheduler")
        from powercontext.builtin.runtime.scheduler import (
            configure_experience_incubation_job,
            configure_profile_job,
            configure_source_window_job,
            create_scheduler,
            register_processors,
            scheduler_runtime_key,
            unregister_processor,
        )

        runtime_key = scheduler_runtime_key(scheduler_path)
        scheduler: AsyncIOScheduler | None = None

        async def process_profiles():
            async with self._operation():
                if self.profiles is not None:
                    await self.profiles.scan(max_concurrency=profile_max_concurrency)

        register_processors(
            runtime_key,
            profile=process_profiles if profile_cron is not None else None,
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
            configure_profile_job(scheduler, runtime_key=runtime_key, cron=profile_cron, timezone=profile_timezone)
            scheduler.resume()
        except BaseException:
            if scheduler is not None and scheduler.running:
                scheduler.shutdown(wait=False)
            unregister_processor(runtime_key)
            self._scheduler_runtime_key = None
            self._scheduler = None
            raise

    def configure_evidence_authorization(
        self,
        *,
        dream: DreamAuthorizer,
        review: ScopedEvidenceAuthorizer,
        context: AuthorizationContext,
        attest_candidate: CandidateAttester,
    ) -> None:
        """Bind a trusted Server adapter's current authorization policy."""

        self._review_evidence_authorizer = review
        self._review_authorization_context = context
        if self._dream_service is not None:
            self._dream_service.authorize = dream
            self._dream_service.authorization_context = context
            self._dream_service.attest_candidate = attest_candidate

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
            if self.artifact_processing_supervisor is not None:
                await self.artifact_processing_supervisor.close()
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
    async def _scope_operation(self, scope_id: str) -> AsyncIterator[ScopeDescriptor]:
        scope = validate_scope_id(scope_id)
        async with self._operation():
            if self.scopes is None:
                raise _RuntimeStateError("scope")
            registered = await self.scopes.get(scope)
            with self._scope_cache.lease(scope):
                yield registered

    @asynccontextmanager
    async def _scoped_operation(
        self,
        scope_id: str,
        *,
        generation_purpose: ModelUsagePurpose | None = None,
        embedding_purpose: ModelUsagePurpose | None = None,
    ) -> AsyncIterator[None]:
        scope = validate_scope_id(scope_id)
        async with self._scope_operation(scope), self._generation_slot(generation_purpose is not None):
            with bind_usage_reporter(
                self.statistics.for_scope(scope).record_model_usage,
                generation_purpose=generation_purpose,
                embedding_purpose=embedding_purpose,
            ):
                yield

    @asynccontextmanager
    async def _generation_slot(self, required: bool) -> AsyncIterator[None]:
        task = asyncio.current_task()
        if not required or task is None or task in self._generation_owners:
            yield
            return
        async with self._generation_slots:
            self._generation_owners.add(task)
            try:
                yield
            finally:
                self._generation_owners.remove(task)

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
        scope = validate_scope_id(scope_id)
        service = self._review_service(scope)
        authorizer = self._review_evidence_authorizer
        if authorizer is not None:
            service.configure_authorization(lambda ref: authorizer(scope, ref), self._review_authorization_context)
        return service

    def _records(self) -> RecordService:
        if self._record_service is None:
            raise _RuntimeStateError("records")
        return self._record_service

    def _generation(self, scope_id: str) -> ReviewedGenerationService:
        if self._generation_service is None:
            raise _RuntimeStateError("review")
        return self._generation_service(validate_scope_id(scope_id))

    def _external_skills(self, scope_id: str) -> ExternalSkillRegistryService:
        if self._external_skill_registry is None:
            raise ExternalSkillRegistryUnavailableError
        return self._external_skill_registry(validate_scope_id(scope_id))

    def _skill_publications(
        self,
        scope_id: str,
        target: AgentSkillTarget,
        artifact: ArtifactRef,
    ) -> ManagedSkillPublicationService:
        if self._skill_publication_service is None:
            raise _RuntimeStateError("skill-publication")
        return self._skill_publication_service(validate_scope_id(scope_id), target.target_id, artifact.artifact_id)

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


def _bounded_topic_memory_recall_query(query: str) -> str:
    """Focus internal Prepared Context recall without weakening the public search contract."""

    trimmed = query.strip()
    terms = tuple(dict.fromkeys(analyze_text(trimmed).split()))
    if len(terms) <= MAX_TOPIC_MEMORY_QUERY_TERMS:
        return trimmed

    selected: list[str] = []
    characters = 0
    for term in terms[:MAX_TOPIC_MEMORY_QUERY_TERMS]:
        separator = int(bool(selected))
        available = MAX_TOPIC_MEMORY_QUERY_LENGTH - characters - separator
        if available < 1:
            break
        selected.append(term[:available])
        characters += separator + min(len(term), available)
    return " ".join(selected)


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
