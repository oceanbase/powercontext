"""Map HTTP transport values to the runtime application boundary."""

from __future__ import annotations

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.experience import Experience, ExperienceContent
from powercontext.builtin.review import CandidateStatus as RuntimeCandidateStatus
from powercontext.builtin.runtime import (
    ApproveArtifactCandidateRequest as RuntimeApproveArtifactCandidateRequest,
)
from powercontext.builtin.runtime import (
    CaptureSource,
    ExperienceCandidate,
    ExperienceCandidatePage,
    MemoryChange,
    MemoryChangesPage,
    MemoryEntriesPage,
    MemoryEntryInput,
    MemoryEntryRecord,
    MemoryFlushResult,
    MemoryHit,
    MemoryMutationResult,
    MemorySearchPage,
    PrepareContextRequest,
    PreparedContext,
    RememberMemoryRequest,
    SourceReceipt,
)
from powercontext.builtin.runtime import (
    GetArtifactCandidateRequest as RuntimeGetArtifactCandidateRequest,
)
from powercontext.builtin.runtime import (
    GetExperienceRequest as RuntimeGetExperienceRequest,
)
from powercontext.builtin.runtime import (
    GetMemoryEntryRequest as RuntimeGetMemoryEntryRequest,
)
from powercontext.builtin.runtime import (
    ListArtifactCandidatesRequest as RuntimeListArtifactCandidatesRequest,
)
from powercontext.builtin.runtime import (
    MemoryCitation as RuntimeMemoryCitation,
)
from powercontext.builtin.runtime import (
    MemoryRevisionChanges as RuntimeMemoryRevisionChanges,
)
from powercontext.builtin.runtime import (
    ProposeExperienceRequest as RuntimeProposeExperienceRequest,
)
from powercontext.builtin.runtime import (
    RejectArtifactCandidateRequest as RuntimeRejectArtifactCandidateRequest,
)
from powercontext.builtin.runtime import (
    RetireMemoryEntryRequest as RuntimeRetireMemoryEntryRequest,
)
from powercontext.builtin.runtime import (
    ReviseArtifactCandidateRequest as RuntimeReviseArtifactCandidateRequest,
)
from powercontext.builtin.runtime import (
    ReviseMemoryEntryRequest as RuntimeReviseMemoryEntryRequest,
)
from powercontext.builtin.runtime import (
    SearchMemoryRequest as RuntimeSearchMemoryRequest,
)
from powercontext.http import (
    ApproveArtifactCandidateRequest,
    ArtifactCandidate,
    ArtifactCandidatePage,
    ArtifactReference,
    CandidateFamily,
    CandidateStatus,
    CaptureContentSourceRequest,
    CaptureContentSourceResponse,
    CaptureStatus,
    EntryChange,
    EntryChangeOperation,
    ExperienceArtifact,
    ExperienceProposal,
    FlushMemoryResponse,
    FlushStatus,
    GetArtifactCandidateRequest,
    GetExperienceRequest,
    GetMemoryEntryRequest,
    ListArtifactCandidatesRequest,
    ListMemoryChangesResponse,
    ListMemoryEntriesResponse,
    MemoryEntry,
    MemoryEntryState,
    MemoryMatchedBy,
    MemoryMutationResponse,
    MemoryRevisionChanges,
    MemoryUsedSearchMode,
    PreparedContextSchema,
    PreparedContextStatus,
    ProposeExperienceRequest,
    RejectArtifactCandidateRequest,
    RetireMemoryEntryRequest,
    ReviseArtifactCandidateRequest,
    ReviseMemoryEntryRequest,
    SearchMemoryHit,
    SearchMemoryRequest,
    SearchMemoryResponse,
    SourceReference,
)
from powercontext.http import (
    MemoryCitation as TransportMemoryCitation,
)
from powercontext.http import (
    PrepareContextRequest as TransportPrepareContextRequest,
)
from powercontext.http import (
    PreparedContext as TransportPreparedContext,
)
from powercontext.http import (
    RememberMemoryRequest as TransportRememberMemoryRequest,
)
from powercontext.sources import SourceRef


def capture_request(value: CaptureContentSourceRequest) -> CaptureSource:
    return CaptureSource(
        source_id=value.source_id,
        content=value.content,
        metadata={} if value.metadata is None else value.metadata,
    )


def capture_response(value: SourceReceipt) -> CaptureContentSourceResponse:
    return CaptureContentSourceResponse(
        status=CaptureStatus.ACCEPTED,
        source=SourceReference(name=value.source_ref.source_type, source_id=value.source_ref.source_id),
        position=value.sequence,
    )


def flush_response(value: MemoryFlushResult) -> FlushMemoryResponse:
    return FlushMemoryResponse(
        status=FlushStatus.PROCESSED if value.processed else FlushStatus.IDLE,
        previous_cursor=value.previous_cursor,
        current_cursor=value.current_cursor,
        high_watermark=value.high_watermark,
        processed_source_count=value.source_count,
        memory=None if value.memory_ref is None else artifact_reference(value.memory_ref),
    )


def remember_request(value: TransportRememberMemoryRequest) -> RememberMemoryRequest:
    return RememberMemoryRequest(
        entries=(MemoryEntryInput(kind=value.kind, text=value.text, reason=value.reason),),
        expected_revision=value.expected_revision,
    )


def propose_experience_request(value: ProposeExperienceRequest) -> RuntimeProposeExperienceRequest:
    return RuntimeProposeExperienceRequest(
        proposal=experience_content(value.proposal),
        sources=tuple(runtime_source_reference(source) for source in value.source_refs),
        artifacts=tuple(runtime_artifact_reference(artifact) for artifact in value.artifact_refs),
        target=None if value.target is None else runtime_artifact_reference(value.target),
        reason=value.reason,
    )


def get_experience_request(value: GetExperienceRequest) -> RuntimeGetExperienceRequest:
    return RuntimeGetExperienceRequest(artifact=runtime_artifact_reference(value.artifact))


def list_candidates_request(value: ListArtifactCandidatesRequest) -> RuntimeListArtifactCandidatesRequest:
    return RuntimeListArtifactCandidatesRequest(
        status=RuntimeCandidateStatus(value.status.value),
        family=None if value.family is None else value.family.value,
        cursor=value.cursor,
        limit=value.limit,
    )


def get_candidate_request(value: GetArtifactCandidateRequest) -> RuntimeGetArtifactCandidateRequest:
    return RuntimeGetArtifactCandidateRequest(candidate_id=value.candidate_id)


def approve_candidate_request(value: ApproveArtifactCandidateRequest) -> RuntimeApproveArtifactCandidateRequest:
    return RuntimeApproveArtifactCandidateRequest(
        candidate_id=value.candidate_id,
        expected_version=value.expected_version,
    )


def reject_candidate_request(value: RejectArtifactCandidateRequest) -> RuntimeRejectArtifactCandidateRequest:
    return RuntimeRejectArtifactCandidateRequest(
        candidate_id=value.candidate_id,
        expected_version=value.expected_version,
        reason=value.reason,
    )


def revise_candidate_request(value: ReviseArtifactCandidateRequest) -> RuntimeReviseArtifactCandidateRequest:
    return RuntimeReviseArtifactCandidateRequest(
        candidate_id=value.candidate_id,
        expected_version=value.expected_version,
        proposal=experience_content(value.proposal),
        sources=tuple(runtime_source_reference(source) for source in value.source_refs),
        artifacts=tuple(runtime_artifact_reference(artifact) for artifact in value.artifact_refs),
        target=None if value.target is None else runtime_artifact_reference(value.target),
        reason=value.reason,
    )


def search_request(value: SearchMemoryRequest) -> RuntimeSearchMemoryRequest:
    return RuntimeSearchMemoryRequest(query=value.query, limit=value.limit, mode=value.mode.value)


def prepare_context_request(value: TransportPrepareContextRequest) -> PrepareContextRequest:
    return PrepareContextRequest(query=value.query, max_bytes=value.max_bytes)


def get_request(value: GetMemoryEntryRequest) -> RuntimeGetMemoryEntryRequest:
    return RuntimeGetMemoryEntryRequest(citation=runtime_citation(value.citation))


def revise_request(value: ReviseMemoryEntryRequest) -> RuntimeReviseMemoryEntryRequest:
    return RuntimeReviseMemoryEntryRequest(
        citation=runtime_citation(value.citation),
        kind=value.kind,
        text=value.text,
        reason=value.reason,
    )


def retire_request(value: RetireMemoryEntryRequest) -> RuntimeRetireMemoryEntryRequest:
    return RuntimeRetireMemoryEntryRequest(citation=runtime_citation(value.citation), reason=value.reason)


def search_response(value: MemorySearchPage) -> SearchMemoryResponse:
    return SearchMemoryResponse(
        memory=None if value.memory_ref is None else artifact_reference(value.memory_ref),
        mode=None if value.mode is None else MemoryUsedSearchMode(value.mode),
        hits=[search_hit(hit) for hit in value.hits],
    )


def prepared_context_response(value: PreparedContext) -> TransportPreparedContext:
    return TransportPreparedContext.model_validate({
        "schema": PreparedContextSchema(value.schema_version),
        "status": PreparedContextStatus(value.status),
        "content": value.content,
        "content_bytes": value.content_bytes,
    })


def entries_response(value: MemoryEntriesPage) -> ListMemoryEntriesResponse:
    return ListMemoryEntriesResponse(
        memory=None if value.memory_ref is None else artifact_reference(value.memory_ref),
        entries=[memory_entry(item) for item in value.entries],
    )


def mutation_response(value: MemoryMutationResult) -> MemoryMutationResponse:
    return MemoryMutationResponse(
        memory=artifact_reference(value.memory_ref),
        entry=None if value.entry is None else memory_entry(value.entry),
    )


def changes_response(value: MemoryChangesPage) -> ListMemoryChangesResponse:
    return ListMemoryChangesResponse(
        memory=None if value.memory_ref is None else artifact_reference(value.memory_ref),
        revisions=[revision_changes(revision) for revision in value.revisions],
    )


def candidate_response(value: ExperienceCandidate) -> ArtifactCandidate:
    return ArtifactCandidate(
        candidate_id=value.candidate_id,
        version=value.version,
        family=CandidateFamily(value.family),
        status=CandidateStatus(value.status.value),
        proposal=experience_proposal(value.proposal),
        source_refs=[source_reference(source) for source in value.sources],
        artifact_refs=[artifact_reference(artifact) for artifact in value.artifacts],
        target=None if value.target is None else artifact_reference(value.target),
        reason=value.reason,
        result_artifact=None if value.result_artifact is None else artifact_reference(value.result_artifact),
        decision_reason=value.decision_reason,
    )


def candidate_page_response(value: ExperienceCandidatePage) -> ArtifactCandidatePage:
    return ArtifactCandidatePage(
        candidates=[candidate_response(candidate) for candidate in value.candidates],
        next_cursor=value.next_cursor,
    )


def experience_response(value: Experience) -> ExperienceArtifact:
    return ExperienceArtifact(
        artifact=artifact_reference(value.as_ref()),
        content=experience_proposal(value.content),
        source_refs=[source_reference(source) for source in value.lineage.sources],
        artifact_refs=[artifact_reference(artifact) for artifact in value.lineage.artifacts],
    )


def experience_content(value: ExperienceProposal) -> ExperienceContent:
    return ExperienceContent(
        situation=value.situation,
        action=value.action,
        outcome=value.outcome,
        lesson=value.lesson,
    )


def experience_proposal(value: ExperienceContent) -> ExperienceProposal:
    return ExperienceProposal(
        situation=value.situation,
        action=value.action,
        outcome=value.outcome,
        lesson=value.lesson,
    )


def artifact_reference(value: ArtifactRef) -> ArtifactReference:
    return ArtifactReference(family=value.family, artifact_id=value.artifact_id, revision=value.revision)


def runtime_artifact_reference(value: ArtifactReference) -> ArtifactRef:
    return ArtifactRef(family=value.family, artifact_id=value.artifact_id, revision=value.revision)


def source_reference(value: SourceRef) -> SourceReference:
    return SourceReference(name=value.source_type, source_id=value.source_id)


def runtime_source_reference(value: SourceReference) -> SourceRef:
    return SourceRef(source_type=value.name, source_id=value.source_id)


def runtime_citation(value: TransportMemoryCitation) -> RuntimeMemoryCitation:
    return RuntimeMemoryCitation(
        memory_ref=ArtifactRef(
            family=value.memory_ref.family,
            artifact_id=value.memory_ref.artifact_id,
            revision=value.memory_ref.revision,
        ),
        entry_id=value.entry_id,
        entry_version_id=value.entry_version_id,
    )


def transport_citation(value: RuntimeMemoryCitation) -> TransportMemoryCitation:
    return TransportMemoryCitation(
        memory_ref=artifact_reference(value.memory_ref),
        entry_id=value.entry_id,
        entry_version_id=value.entry_version_id,
    )


def entry_change(value: MemoryChange) -> EntryChange:
    return EntryChange(
        op=EntryChangeOperation(value.op),
        entry_id=value.entry_id,
        from_entry_version_id=value.from_entry_version_id,
        to_entry_version_id=value.to_entry_version_id,
        reason=value.reason,
    )


def revision_changes(value: RuntimeMemoryRevisionChanges) -> MemoryRevisionChanges:
    return MemoryRevisionChanges(
        memory_ref=artifact_reference(value.memory_ref),
        changes=[entry_change(change) for change in value.changes],
    )


def search_hit(value: MemoryHit) -> SearchMemoryHit:
    return SearchMemoryHit(
        citation=transport_citation(
            RuntimeMemoryCitation(
                memory_ref=value.memory_ref,
                entry_id=value.entry_id,
                entry_version_id=value.entry_version_id,
            )
        ),
        text=value.text,
        score=value.score,
        matched_by=[MemoryMatchedBy(channel) for channel in value.matched_by],
    )


def memory_entry(value: MemoryEntryRecord) -> MemoryEntry:
    entry = value.entry
    return MemoryEntry(
        citation=transport_citation(value.citation),
        version=entry.version,
        kind=entry.kind,
        text=entry.text,
        state=MemoryEntryState(value.state),
        source_refs=[SourceReference(name=source.source_type, source_id=source.source_id) for source in entry.sources],
        artifact_refs=[artifact_reference(reference) for reference in entry.artifacts],
    )
