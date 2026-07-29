"""Map HTTP transport values to the runtime application boundary."""

from __future__ import annotations

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.runtime import (
    CaptureSource,
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
    GetMemoryEntryRequest as RuntimeGetMemoryEntryRequest,
)
from powercontext.builtin.runtime import (
    MemoryCitation as RuntimeMemoryCitation,
)
from powercontext.builtin.runtime import (
    MemoryRevisionChanges as RuntimeMemoryRevisionChanges,
)
from powercontext.builtin.runtime import (
    RetireMemoryEntryRequest as RuntimeRetireMemoryEntryRequest,
)
from powercontext.builtin.runtime import (
    ReviseMemoryEntryRequest as RuntimeReviseMemoryEntryRequest,
)
from powercontext.builtin.runtime import (
    SearchMemoryRequest as RuntimeSearchMemoryRequest,
)
from powercontext.http import (
    ArtifactReference,
    CaptureContentSourceRequest,
    CaptureContentSourceResponse,
    CaptureStatus,
    EntryChange,
    EntryChangeOperation,
    FlushMemoryResponse,
    FlushStatus,
    GetMemoryEntryRequest,
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
    RetireMemoryEntryRequest,
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


def artifact_reference(value: ArtifactRef) -> ArtifactReference:
    return ArtifactReference(family=value.family, artifact_id=value.artifact_id, revision=value.revision)


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
