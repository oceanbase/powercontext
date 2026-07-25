"""Commands and results exposed by the built-in Runtime."""

from __future__ import annotations

from collections.abc import Mapping

from pydantic import BaseModel, JsonValue

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.memory.models import (
    MemoryCitation,
    MemoryEntryInput,
    MemoryEntryState,
    MemoryEntryVersion,
    MemoryHit,
    MemoryRevisionChanges,
    MemorySearchMode,
    MemoryUsedSearchMode,
)
from powercontext.sources import SourceRef


class CaptureSource(BaseModel):
    """Transport-neutral command for the built-in captured-content route."""

    source_id: str
    content: str
    metadata: Mapping[str, JsonValue]


class SourceReceipt(BaseModel):
    """A canonical captured Source and its stable journal position."""

    source_ref: SourceRef
    sequence: int


class RuntimeCapabilities(BaseModel):
    """Behavior available from the assembled Source-to-Memory Runtime."""

    memory_extraction: bool
    memory_search_modes: tuple[MemorySearchMode, ...]


class RememberMemoryRequest(BaseModel):
    """Append explicit entries, optionally against an expected head."""

    entries: tuple[MemoryEntryInput, ...]
    expected_revision: int | None = None


class SearchMemoryRequest(BaseModel):
    """Search one scoped Memory head."""

    query: str
    limit: int = 10
    mode: MemorySearchMode = "auto"


class MemorySearchPage(BaseModel):
    """Search results that can represent a scope with no Memory."""

    memory_ref: ArtifactRef | None
    mode: MemoryUsedSearchMode | None
    hits: tuple[MemoryHit, ...] = ()


class MemoryEntryRecord(BaseModel):
    """An exact entry version together with its state in one Revision."""

    memory_ref: ArtifactRef
    state: MemoryEntryState
    entry: MemoryEntryVersion

    @property
    def citation(self) -> MemoryCitation:
        return MemoryCitation(
            memory_ref=self.memory_ref,
            entry_id=self.entry.entry_id,
            entry_version_id=self.entry.entry_version_id,
        )


class MemoryEntriesPage(BaseModel):
    """Current entries for one scope, or an absent Memory."""

    memory_ref: ArtifactRef | None
    entries: tuple[MemoryEntryRecord, ...] = ()


class GetMemoryEntryRequest(BaseModel):
    citation: MemoryCitation


class ReviseMemoryEntryRequest(BaseModel):
    citation: MemoryCitation
    kind: str
    text: str
    reason: str | None = None


class RetireMemoryEntryRequest(BaseModel):
    citation: MemoryCitation
    reason: str | None = None


class MemoryMutationResult(BaseModel):
    previous_revision: int | None
    memory_ref: ArtifactRef
    entry: MemoryEntryRecord | None = None


class MemoryChangesPage(BaseModel):
    memory_ref: ArtifactRef | None
    revisions: tuple[MemoryRevisionChanges, ...] = ()
