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

"""Commands and results exposed by the built-in Runtime."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from enum import StrEnum
from hashlib import sha256
from typing import Annotated, Any, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator

from powercontext.artifacts import ArtifactRef
from powercontext.artifacts.models import _reject_legacy_memory_citations
from powercontext.builtin.artifacts.atomic_memory.models import AtomicMemoryContent
from powercontext.builtin.artifacts.experience import ExperienceContent
from powercontext.builtin.artifacts.handoff.models import HandoffContent
from powercontext.builtin.artifacts.memory.models import (
    MemoryEntryInput,
    MemoryHit,
    MemoryRerankTrace,
    MemorySearchMode,
    MemoryUsedSearchMode,
)
from powercontext.builtin.artifacts.profile.models import ProfileCandidateProposal, ProfileWriteContent
from powercontext.builtin.artifacts.prompt import PromptCapability, PromptContent
from powercontext.builtin.artifacts.skill import (
    ExternalSkillProviderScan,
    ExternalSkillResolution,
    SkillContent,
)
from powercontext.builtin.artifacts.topic_memory.models import TopicMemoryContent
from powercontext.builtin.catalog_changes.models import CatalogChangeCandidate, CatalogChangeProposal
from powercontext.builtin.review import (
    DEFAULT_CANDIDATE_PAGE_SIZE,
    MAX_CANDIDATE_EVIDENCE,
    MAX_CANDIDATE_PAGE_SIZE,
    Candidate,
    CandidatePage,
    CandidateStatus,
)
from powercontext.builtin.review.generation import SkillGenerationOrigin
from powercontext.builtin.sources import ExternalSkillImportMode
from powercontext.builtin.tags import TagFilter
from powercontext.sources import ConnectorBinding, SourceObservation, SourceRef

PreparedContextSchema: TypeAlias = Literal["powercontext.prepared-context.v1"]
PreparedContextStatus: TypeAlias = Literal["ready", "empty"]
PreparedContextReason: TypeAlias = Literal["inference_timeout", "inference_unavailable"]
ReviewedProposal: TypeAlias = (
    ExperienceContent
    | SkillContent
    | ProfileCandidateProposal
    | AtomicMemoryContent
    | TopicMemoryContent
    | HandoffContent
    | PromptContent
)
BootstrapContextSchema: TypeAlias = Literal["powercontext.bootstrap-context.v1"]
BootstrapContextProfile: TypeAlias = Literal["powercontext.scope-bootstrap.v1"]
BootstrapContextLifecycle: TypeAlias = Literal["startup", "resume", "clear", "compact", "restore", "fork"]
BootstrapContextStatus: TypeAlias = Literal["ready", "empty", "skipped"]
BootstrapReceiptState: TypeAlias = Literal["pending", "injected", "skipped", "failed"]
BootstrapSkipReason: TypeAlias = Literal[
    "disabled",
    "no_eligible_context",
    "already_delivered",
    "preparation_failed",
]

PREPARED_CONTEXT_SCHEMA: PreparedContextSchema = "powercontext.prepared-context.v1"
BOOTSTRAP_CONTEXT_SCHEMA: BootstrapContextSchema = "powercontext.bootstrap-context.v1"
BOOTSTRAP_CONTEXT_PROFILE: BootstrapContextProfile = "powercontext.scope-bootstrap.v1"


class _PreparedContextModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class CaptureSource(BaseModel):
    """Transport-neutral command for the built-in captured-content route."""

    source_id: str
    content: str
    metadata: Mapping[str, JsonValue]


class WorkingNoteKind(StrEnum):
    """Classify one bounded kind of session-local work state."""

    INSPECTION = "inspection"
    ABANDONED_APPROACH = "abandoned_approach"
    VALIDATION = "validation"
    UNRESOLVED = "unresolved"
    HOST_CONTEXT = "host_context"


class CaptureWorkingNote(BaseModel):
    """Explicitly capture short-lived, non-Memory work state."""

    source_id: str
    content: str
    session_id: str
    note_kind: WorkingNoteKind
    expires_at: datetime | None = None
    metadata: Mapping[str, JsonValue] = Field(default_factory=dict)


class SourceReceipt(BaseModel):
    """A canonical captured Source and its stable journal position."""

    source_ref: SourceRef
    sequence: int


class SubmitSourceObservation(BaseModel):
    """Submit one worker-materialized observation for durable acceptance."""

    scope_id: str
    observation: SourceObservation


class ConnectorCheckpointState(BaseModel):
    """Current opaque checkpoint for one exact Connector binding."""

    binding: ConnectorBinding
    checkpoint: JsonValue | None


class CommitConnectorCheckpoint(BaseModel):
    """Compare and replace one binding checkpoint after durable submissions."""

    binding: ConnectorBinding
    expected: JsonValue | None
    checkpoint: JsonValue | None


class DreamOperationCapability(BaseModel):
    operation: str
    output_kind: Literal["candidate", "tag_candidate"] = "candidate"
    effect: Literal[
        "review_then_publish",
        "review_then_publish_configuration",
        "review_then_replace_tags",
    ] = "review_then_publish"


class ExtractionBackground(BaseModel):
    """Placement, leadership, and lifecycle of the Memory Supervisor."""

    location: Literal["local", "external", "none"]
    role: Literal["leader", "standby"] | None = None
    state: Literal["running", "degraded", "stopped", "unknown"]
    automatic_processing_enabled: bool | None = None


class ExtractionFailure(BaseModel):
    """A historical failure observation, not an unresolved incident."""

    model_config = ConfigDict(frozen=True)

    code: str
    stage: Literal["inference", "flush", "worker", "supervisor", "lease_renewal", "scope_discovery"]
    occurred_at: datetime


class ExtractionObservation(BaseModel):
    """Independent success and failure evidence within this Runtime's lifetime."""

    status: Literal["unverified", "observed"]
    since: datetime
    last_success_at: datetime | None = None
    last_failure: ExtractionFailure | None = None


class ExtractionStatus(BaseModel):
    """Read-only facts about Memory extraction, without an overall health verdict."""

    configuration: Literal["configured", "unconfigured", "unknown"]
    background: ExtractionBackground
    observation: ExtractionObservation


class RuntimeCapabilities(BaseModel):
    """Behavior available from the assembled Source-to-Memory Runtime."""

    memory_extraction: bool
    experience_generation: bool = False
    managed_skill_generation: bool = False
    artifact_dreaming: bool = False
    artifact_dreaming_operations: tuple[DreamOperationCapability, ...] = ()
    external_skill_registry: bool = False
    memory_search_modes: tuple[MemorySearchMode, ...]
    handoff_generation: bool = False
    prompts: dict[str, PromptCapability] = Field(default_factory=dict)
    context_versions: tuple[PreparedContextSchema, ...] = (PREPARED_CONTEXT_SCHEMA,)
    context_reuse_versions: tuple[str, ...] = ("powercontext.context-reuse.v1",)
    context_reuse_families: tuple[str, ...] = ("atomic-memory",)


class MemoryFlushResult(BaseModel):
    """Result of processing one scoped Source window.

    ``held_count`` and ``hold_codes`` report a write-gate ``HOLD``. A held window commits nothing,
    yet it still advances the cursor past its Sources, so those Sources are consumed rather than
    retried; ``held_count`` is 0 or 1 and ``hold_codes`` carries the gate rejection codes.
    """

    previous_cursor: int
    high_watermark: int
    current_cursor: int
    source_count: int
    memory_ref: ArtifactRef | None
    remaining_work: bool = False
    held_count: int = 0
    hold_codes: tuple[str, ...] = ()

    @property
    def processed(self) -> bool:
        return self.current_cursor > self.previous_cursor


class TopicMemoryFlushResult(BaseModel):
    """Durable acceptance result for one scoped Topic Memory flush request."""

    status: Literal["accepted", "idle"]


class SearchTopicMemoryRequest(BaseModel):
    """Caller-neutral Topic Memory search request."""

    query: str
    limit: int = 10


class GetTopicMemoryRequest(BaseModel):
    """Read one exact immutable Topic Memory revision."""

    artifact: ArtifactRef


class ExperienceIncubationResult(BaseModel):
    """Result of incubating one scoped Task Outcome Source window."""

    previous_cursor: int = Field(ge=0)
    high_watermark: int = Field(ge=0)
    current_cursor: int = Field(ge=0)
    source_count: int = Field(ge=0)
    candidate_count: int = Field(ge=0)
    candidate_ids: tuple[str, ...] = ()

    @property
    def processed(self) -> bool:
        return self.current_cursor > self.previous_cursor


class RememberMemoryRequest(BaseModel):
    """Append explicit entries, optionally against an expected head."""

    entries: tuple[MemoryEntryInput, ...]
    expected_revision: int | None = None


class SearchMemoryRequest(BaseModel):
    """Search one scoped Memory head."""

    query: str
    limit: int = 10
    mode: MemorySearchMode = "auto"
    tag_filter: TagFilter | None = None


class MemorySearchPage(BaseModel):
    """Search results that can represent a scope with no Memory."""

    memory_ref: ArtifactRef | None
    mode: MemoryUsedSearchMode | None
    hits: tuple[MemoryHit, ...] = ()
    rerank: MemoryRerankTrace | None = None


class ContextAssemblySection(_PreparedContextModel):
    """One selected Artifact family and its maximum output count."""

    family: Literal["memory", "experience", "profile", "topic-memory"]
    limit: Annotated[int, Field(ge=1, le=8)]

    @model_validator(mode="after")
    def validate_limit(self) -> ContextAssemblySection:
        if self.family == "experience" and self.limit > 2:
            raise ValueError("Experience sections cannot include more than two entries")  # noqa: TRY003
        return self


class ContextAssembly(_PreparedContextModel):
    """Request-local selection and presentation of historical context."""

    format: Literal["markdown"] = "markdown"
    sections: Annotated[tuple[ContextAssemblySection, ...], Field(max_length=4, strict=False)] = (
        ContextAssemblySection(family="memory", limit=6),
        ContextAssemblySection(family="experience", limit=2),
    )
    show: Annotated[tuple[Literal["confidence", "recall_rank"], ...], Field(max_length=2, strict=False)] = ()

    @model_validator(mode="after")
    def validate_selection(self) -> ContextAssembly:
        if len({section.family for section in self.sections}) != len(self.sections):
            raise ValueError("Assembly families must be unique")  # noqa: TRY003
        if len(set(self.show)) != len(self.show):
            raise ValueError("Assembly metadata fields must be unique")  # noqa: TRY003
        return self


class PrepareContextRequest(_PreparedContextModel):
    """Prepare bounded context for one Agent turn."""

    query: Annotated[str, Field(min_length=1, max_length=8192)]
    max_bytes: Annotated[int, Field(ge=512, le=32768)] = 8000
    assembly: ContextAssembly | None = None
    bootstrap_receipt_id: Annotated[str, Field(min_length=1, max_length=64)] | None = None
    include_code: bool = Field(default=False, strict=True)
    include_reuse: bool = Field(default=False, strict=True)
    #: Opt into ``reason`` on the response. Off by default: a preparation a Host injects has to keep
    #: the four fields its validator knows, and a lost channel is not part of the injected content.
    explain: bool = Field(default=False, strict=True)

    @model_validator(mode="before")
    @classmethod
    def reject_null_assembly(cls, value: Any) -> Any:
        if isinstance(value, Mapping) and "assembly" in value and value["assembly"] is None:
            raise ValueError("assembly must be omitted or contain an object")  # noqa: TRY003
        return value

    @field_validator("query")
    @classmethod
    def validate_query(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("query must contain non-whitespace content")  # noqa: TRY003
        return value

    @field_validator("bootstrap_receipt_id")
    @classmethod
    def validate_bootstrap_receipt_id(cls, value: str | None) -> str | None:
        if value is not None and not all("\x21" <= character <= "\x7e" for character in value):
            raise ValueError("bootstrap_receipt_id must contain only visible ASCII")  # noqa: TRY003
        return value


class PreparedContext(_PreparedContextModel):
    """Ephemeral context ready for direct injection into one Agent turn."""

    schema_version: PreparedContextSchema = Field(default=PREPARED_CONTEXT_SCHEMA, alias="schema")
    status: PreparedContextStatus
    content: str | None
    content_bytes: Annotated[int, Field(ge=0)]
    #: Why a retrieval channel was lost, mirroring ``BootstrapContext.reason``. Filled only when the
    #: request asked with ``explain``: a Host injects ``content`` and validates the four fields it
    #: knows, so an unrequested reason would break every Host for a fact none of them reads.
    reason: PreparedContextReason | None = None

    @model_validator(mode="after")
    def validate_content(self) -> PreparedContext:
        if self.status == "empty":
            if self.content is not None or self.content_bytes != 0:
                raise ValueError("empty prepared context must not contain content")  # noqa: TRY003
            return self
        if self.content is None or not self.content.strip():
            raise ValueError("ready prepared context must contain content")  # noqa: TRY003
        if len(self.content.encode("utf-8")) != self.content_bytes:
            raise ValueError("prepared context byte count does not match content")  # noqa: TRY003
        return self


class ContextReuseConstraints(_PreparedContextModel):
    """Conditions for preserving one prepared value, independent of provider KV settings."""

    preserve: tuple[Literal["content", "citations", "trust", "order"], ...] = (
        "content",
        "citations",
        "trust",
        "order",
    )
    refresh_on: tuple[Literal["permission_change", "source_ineligible", "context_boundary"], ...] = (
        "permission_change",
        "source_ineligible",
        "context_boundary",
    )


class ContextReuse(_PreparedContextModel):
    """Short-lived authenticated private provenance for an exact prepared value."""

    schema_version: Literal["powercontext.context-reuse.v1"] = Field(
        default="powercontext.context-reuse.v1",
        alias="schema",
    )
    constraints: ContextReuseConstraints = Field(default_factory=ContextReuseConstraints)
    expires_at: datetime
    binding: Annotated[str, Field(min_length=1, max_length=65536)]


class PreparedContextWithReuse(_PreparedContextModel):
    """Explicit opt-in envelope; the normal preparation shape remains unchanged."""

    schema_version: Literal["powercontext.prepared-context-with-reuse.v1"] = Field(
        default="powercontext.prepared-context-with-reuse.v1",
        alias="schema",
    )
    context: PreparedContext
    reuse: ContextReuse


PrepareContextResult: TypeAlias = PreparedContext | PreparedContextWithReuse


class BootstrapContextRequest(_PreparedContextModel):
    """Prepare curated context for one host lifecycle boundary without a fake query."""

    enabled: bool = False
    profile: BootstrapContextProfile = BOOTSTRAP_CONTEXT_PROFILE
    lifecycle: BootstrapContextLifecycle
    integration: Annotated[str, Field(min_length=1, max_length=64)]
    event_id: Annotated[str, Field(min_length=1, max_length=512)] | None = None
    max_bytes: Annotated[int, Field(ge=512, le=8192)] = 4096
    handoff: ArtifactRef | None = None

    @field_validator("integration")
    @classmethod
    def validate_integration(cls, value: str) -> str:
        if not all("\x21" <= character <= "\x7e" for character in value):
            raise ValueError("integration must contain only visible ASCII")  # noqa: TRY003
        return value

    @field_validator("event_id")
    @classmethod
    def validate_event_id(cls, value: str | None) -> str | None:
        if value is not None and not all("\x20" <= character <= "\x7e" for character in value):
            raise ValueError("event_id must contain only printable ASCII")  # noqa: TRY003
        return value

    @field_validator("handoff")
    @classmethod
    def validate_handoff(cls, value: ArtifactRef | None) -> ArtifactRef | None:
        if value is not None and value.family != "handoff":
            raise ValueError("bootstrap handoff must use the handoff family")  # noqa: TRY003
        return value


class BootstrapContextItem(_PreparedContextModel):
    """One delivered item pinned to exact authoritative content."""

    kind: Literal["atomic-memory", "handoff"]
    scope_id: Annotated[str, Field(min_length=1, max_length=256)]
    artifact: ArtifactRef
    content_digest: Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
    truncated: bool = False

    @model_validator(mode="after")
    def validate_reference(self) -> BootstrapContextItem:
        if self.artifact.family != self.kind:
            raise ValueError("bootstrap item kind must match its Artifact family")  # noqa: TRY003
        return self


class BootstrapDeliveryReceipt(_PreparedContextModel):
    """Content-free durable delivery state exposed to a host integration."""

    receipt_id: Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[\x21-\x7E]+$")]
    state: BootstrapReceiptState


class BootstrapContext(_PreparedContextModel):
    """A final bounded bootstrap package or a normal non-delivery result."""

    schema_version: BootstrapContextSchema = Field(default=BOOTSTRAP_CONTEXT_SCHEMA, alias="schema")
    status: BootstrapContextStatus
    reason: BootstrapSkipReason | None = None
    profile: BootstrapContextProfile = BOOTSTRAP_CONTEXT_PROFILE
    content: str | None
    content_bytes: Annotated[int, Field(ge=0)]
    package_digest: Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")] | None = None
    items: Annotated[tuple[BootstrapContextItem, ...], Field(max_length=7, strict=False)] = ()
    truncated: bool = False
    receipt: BootstrapDeliveryReceipt

    @model_validator(mode="after")
    def validate_result(self) -> BootstrapContext:
        if self.status == "ready":
            if (
                self.reason is not None
                or self.receipt.state != "pending"
                or self.content is None
                or not self.content.strip()
                or self.package_digest is None
                or not self.items
                or len(self.content.encode("utf-8")) != self.content_bytes
                or self.package_digest != f"sha256:{sha256(self.content.encode('utf-8')).hexdigest()}"
            ):
                raise ValueError("ready bootstrap context is incomplete")  # noqa: TRY003
            return self
        if self.content is not None or self.content_bytes != 0 or self.package_digest is not None or self.items:
            raise ValueError("non-ready bootstrap context must not contain package data")  # noqa: TRY003
        if self.status == "empty" and self.reason != "no_eligible_context":
            raise ValueError("empty bootstrap context requires the no-content reason")  # noqa: TRY003
        if self.status == "empty" and self.receipt.state != "skipped":
            raise ValueError("empty bootstrap context requires a skipped receipt")  # noqa: TRY003
        if self.status == "skipped" and self.reason is None:
            raise ValueError("skipped bootstrap context requires a reason")  # noqa: TRY003
        if self.status == "skipped" and (
            self.reason == "no_eligible_context"
            or (self.reason == "disabled" and self.receipt.state != "skipped")
            or (self.reason == "preparation_failed" and self.receipt.state != "failed")
            or (self.reason == "already_delivered" and self.receipt.state == "pending")
        ):
            raise ValueError("skipped bootstrap context has an inconsistent receipt")  # noqa: TRY003
        return self


class RecordBootstrapDeliveryRequest(_PreparedContextModel):
    """Finalize one pending delivery receipt without accepting content or error text."""

    receipt_id: Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[\x21-\x7E]+$")]
    outcome: Literal["injected", "failed"]


class ProposeExperienceRequest(BaseModel):
    """Submit a complete Experience proposal with exact evidence."""

    _reject_legacy_citations = model_validator(mode="before")(_reject_legacy_memory_citations)

    proposal: ExperienceContent
    sources: tuple[SourceRef, ...] = ()
    artifacts: tuple[ArtifactRef, ...] = ()
    target: ArtifactRef | None = None
    reason: str | None = None


class GenerateExperienceRequest(BaseModel):
    """Generate a reviewed Experience Candidate from exact evidence."""

    _reject_legacy_citations = model_validator(mode="before")(_reject_legacy_memory_citations)

    sources: tuple[SourceRef, ...] = Field(default=(), max_length=MAX_CANDIDATE_EVIDENCE)
    artifacts: tuple[ArtifactRef, ...] = Field(default=(), max_length=MAX_CANDIDATE_EVIDENCE)
    target: ArtifactRef | None = None
    reason: str | None = None

    @model_validator(mode="after")
    def validate_evidence_bound(self):
        if len(self.sources) + len(self.artifacts) > MAX_CANDIDATE_EVIDENCE:
            raise ValueError("generation evidence exceeds the combined reference bound")  # noqa: TRY003
        return self


class GetExperienceRequest(BaseModel):
    """Read one exact approved Experience revision."""

    artifact: ArtifactRef


class ProposeSkillRequest(BaseModel):
    """Submit a complete managed Skill proposal with exact evidence."""

    _reject_legacy_citations = model_validator(mode="before")(_reject_legacy_memory_citations)

    proposal: SkillContent
    sources: tuple[SourceRef, ...] = ()
    artifacts: tuple[ArtifactRef, ...] = ()
    target: ArtifactRef | None = None
    reason: str | None = None


class GenerateSkillRequest(BaseModel):
    """Generate a reviewed managed Skill Candidate from an explicit lineage shape."""

    _reject_legacy_citations = model_validator(mode="before")(_reject_legacy_memory_citations)

    origin: SkillGenerationOrigin
    sources: tuple[SourceRef, ...] = Field(default=(), max_length=MAX_CANDIDATE_EVIDENCE)
    artifacts: tuple[ArtifactRef, ...] = Field(default=(), max_length=MAX_CANDIDATE_EVIDENCE)
    target: ArtifactRef | None = None
    reason: str | None = None

    @model_validator(mode="after")
    def validate_evidence_bound(self):
        if len(self.sources) + len(self.artifacts) > MAX_CANDIDATE_EVIDENCE:
            raise ValueError("generation evidence exceeds the combined reference bound")  # noqa: TRY003
        return self


class GetSkillRequest(BaseModel):
    """Read one exact approved managed Skill revision."""

    artifact: ArtifactRef


class ListExternalSkillsRequest(BaseModel):
    """Discover registrations currently available in this host environment."""

    include_unavailable: bool = False


class ResolveExternalSkillRequest(BaseModel):
    """Resolve one exact external Skill fingerprint on the current host."""

    external_skill_id: str
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")


class ImportExternalSkillRequest(ResolveExternalSkillRequest):
    """Explicitly snapshot and propose an external Skill as a new managed Candidate."""

    mode: ExternalSkillImportMode
    reason: str | None = Field(default=None, min_length=1, max_length=2_000)


ExternalSkillScanResult = ExternalSkillProviderScan
ExternalSkillList = tuple[ExternalSkillResolution, ...]


class ListCandidatesRequest(BaseModel):
    """Filter and page the current Review Inbox."""

    candidate_kind: Literal["artifact", "tag"] | None = None
    status: CandidateStatus = CandidateStatus.PENDING
    family: Literal["experience", "skill", "profile", "atomic-memory", "topic-memory", "handoff", "prompt"] | None = (
        None
    )
    cursor: str | None = None
    limit: Annotated[int, Field(ge=1, le=MAX_CANDIDATE_PAGE_SIZE)] = DEFAULT_CANDIDATE_PAGE_SIZE


class GetCandidateRequest(BaseModel):
    candidate_id: str


class ApproveCandidateRequest(BaseModel):
    candidate_id: str
    expected_version: Annotated[int, Field(ge=1)]


class RejectCandidateRequest(ApproveCandidateRequest):
    reason: Annotated[str, Field(min_length=1, max_length=2_000)]


class ReviseCandidateRequest(ApproveCandidateRequest):
    _reject_legacy_citations = model_validator(mode="before")(_reject_legacy_memory_citations)

    proposal: (
        CatalogChangeProposal
        | ExperienceContent
        | SkillContent
        | ProfileWriteContent
        | AtomicMemoryContent
        | TopicMemoryContent
        | HandoffContent
        | PromptContent
    )
    sources: tuple[SourceRef, ...] | None = None
    artifacts: tuple[ArtifactRef, ...] | None = None

    target: ArtifactRef | None = None
    reason: str | None = None


ExperienceCandidate = Candidate[ExperienceContent]
ExperienceCandidatePage = CandidatePage[ExperienceContent]
SkillCandidate = Candidate[SkillContent]
ReviewedCandidate = Candidate[ReviewedProposal] | CatalogChangeCandidate


class ReviewedCandidatePage(BaseModel):
    candidates: tuple[ReviewedCandidate, ...]
    next_cursor: str | None = None
