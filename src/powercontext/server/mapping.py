"""Map HTTP transport values to the runtime application boundary."""

from __future__ import annotations

from powercontext.api import (
    ArtifactReference,
    CaptureContentSourceRequest,
    CaptureContentSourceResponse,
    CaptureStatus,
    EntryChange,
    FlushMemoryResponse,
    FlushStatus,
    GetMemoryEntryRequest,
    ListMemoryChangesResponse,
    ListMemoryEntriesResponse,
    MemoryEntry,
    MemoryMutationResponse,
    MemoryRevisionChanges,
    RetireMemoryEntryRequest,
    ReviseMemoryEntryRequest,
    SearchMemoryHit,
    SearchMemoryRequest,
    SearchMemoryResponse,
    SourceReference,
)
from powercontext.api import (
    MemoryCitation as TransportMemoryCitation,
)
from powercontext.api import (
    RememberMemoryRequest as TransportRememberMemoryRequest,
)
from powercontext.artifacts import ArtifactRef
from powercontext.memory import (
    MemoryChange,
    MemoryEntryInput,
    MemoryHit,
)
from powercontext.memory import (
    MemoryCitation as CoreMemoryCitation,
)
from powercontext.memory import (
    MemoryRevisionChanges as CoreMemoryRevisionChanges,
)
from powercontext.memory.canonical import normalize_kind, normalize_reason, normalize_text
from powercontext.runtime import (
    GetMemoryEntryRequest as RuntimeGetMemoryEntryRequest,
)
from powercontext.runtime import (
    MemoryChangesPage,
    MemoryEntriesPage,
    MemoryEntryRecord,
    MemoryFlushResult,
    MemoryMutationResult,
    MemorySearchPage,
    RememberMemoryRequest,
    SourceReceipt,
)
from powercontext.runtime import (
    RetireMemoryEntryRequest as RuntimeRetireMemoryEntryRequest,
)
from powercontext.runtime import (
    ReviseMemoryEntryRequest as RuntimeReviseMemoryEntryRequest,
)
from powercontext.runtime import (
    SearchMemoryRequest as RuntimeSearchMemoryRequest,
)
from powercontext.server.errors import InvalidServerRequestError
from powercontext.sources import CONTENT_SOURCE_NAME, ContentCapture


def capture_request(value: CaptureContentSourceRequest) -> ContentCapture:
    try:
        return ContentCapture(
            source_id=value.source_id,
            content=value.content,
            metadata={} if value.metadata is None else value.metadata,
        )
    except (TypeError, ValueError) as error:
        raise InvalidServerRequestError from error


def capture_response(value: SourceReceipt) -> CaptureContentSourceResponse:
    return CaptureContentSourceResponse(
        status=CaptureStatus.ACCEPTED,
        source=SourceReference(name=CONTENT_SOURCE_NAME, source_id=value.source.name),
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
    try:
        return RememberMemoryRequest(
            entries=(
                MemoryEntryInput(
                    kind=normalize_kind(value.kind),
                    text=normalize_text(value.text),
                    reason=normalize_reason(value.reason),
                ),
            ),
            expected_revision=value.expected_revision,
        )
    except (TypeError, ValueError) as error:
        raise InvalidServerRequestError from error


def search_request(value: SearchMemoryRequest) -> RuntimeSearchMemoryRequest:
    try:
        return RuntimeSearchMemoryRequest(
            query=normalize_text(value.query),
            limit=value.limit,
            mode=value.mode,
        )
    except (TypeError, ValueError) as error:
        raise InvalidServerRequestError from error


def get_request(value: GetMemoryEntryRequest) -> RuntimeGetMemoryEntryRequest:
    return RuntimeGetMemoryEntryRequest(citation=core_citation(value.citation))


def revise_request(value: ReviseMemoryEntryRequest) -> RuntimeReviseMemoryEntryRequest:
    try:
        return RuntimeReviseMemoryEntryRequest(
            citation=core_citation(value.citation),
            kind=normalize_kind(value.kind),
            text=normalize_text(value.text),
            reason=normalize_reason(value.reason),
        )
    except (TypeError, ValueError) as error:
        raise InvalidServerRequestError from error


def retire_request(value: RetireMemoryEntryRequest) -> RuntimeRetireMemoryEntryRequest:
    try:
        return RuntimeRetireMemoryEntryRequest(
            citation=core_citation(value.citation),
            reason=normalize_reason(value.reason),
        )
    except (TypeError, ValueError) as error:
        raise InvalidServerRequestError from error


def search_response(value: MemorySearchPage) -> SearchMemoryResponse:
    return SearchMemoryResponse(
        memory=None if value.memory_ref is None else artifact_reference(value.memory_ref),
        mode=value.mode,
        hits=[search_hit(hit) for hit in value.hits],
    )


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
    return ArtifactReference(artifact_id=value.artifact_id, revision=value.revision)


def core_citation(value: TransportMemoryCitation) -> CoreMemoryCitation:
    return CoreMemoryCitation(
        memory_ref=ArtifactRef(value.memory_ref.artifact_id, value.memory_ref.revision),
        entry_id=value.entry_id,
        entry_version_id=value.entry_version_id,
    )


def transport_citation(value: CoreMemoryCitation) -> TransportMemoryCitation:
    return TransportMemoryCitation(
        memory_ref=artifact_reference(value.memory_ref),
        entry_id=value.entry_id,
        entry_version_id=value.entry_version_id,
    )


def entry_change(value: MemoryChange) -> EntryChange:
    return EntryChange(
        op=value.op,
        entry_id=value.entry_id,
        from_entry_version_id=value.from_entry_version_id,
        to_entry_version_id=value.to_entry_version_id,
        reason=value.reason,
    )


def revision_changes(value: CoreMemoryRevisionChanges) -> MemoryRevisionChanges:
    return MemoryRevisionChanges(
        memory_ref=artifact_reference(value.memory_ref),
        changes=[entry_change(change) for change in value.changes],
    )


def search_hit(value: MemoryHit) -> SearchMemoryHit:
    return SearchMemoryHit(
        citation=transport_citation(
            CoreMemoryCitation(
                memory_ref=value.memory_ref,
                entry_id=value.entry_id,
                entry_version_id=value.entry_version_id,
            )
        ),
        text=value.text,
        score=value.score,
        matched_by=list(value.matched_by),
    )


def memory_entry(value: MemoryEntryRecord) -> MemoryEntry:
    entry = value.entry
    return MemoryEntry(
        citation=transport_citation(value.citation),
        version=entry.version,
        kind=entry.kind,
        text=entry.text,
        state=value.state,
        source_refs=[SourceReference(name=CONTENT_SOURCE_NAME, source_id=source.name) for source in entry.sources],
        artifact_refs=[artifact_reference(reference) for reference in entry.artifacts],
    )
