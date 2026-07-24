"""Transport-independent values used by the local runtime."""

from __future__ import annotations

from dataclasses import dataclass

from powercontext.artifacts import ArtifactRef
from powercontext.memory import (
    MemoryCitation,
    MemoryEntryInput,
    MemoryEntryState,
    MemoryEntryVersion,
    MemoryHit,
    MemoryRevisionChanges,
    MemorySearchMode,
    MemoryUsedSearchMode,
)
from powercontext.sources import Source


@dataclass(frozen=True, slots=True)
class SourceHighWatermark:
    """The bounded journal position visible to one activation."""

    sequence: int
    limit: int


@dataclass(frozen=True, slots=True)
class ProcessSourceWindow:
    """Consume one fixed, non-empty Source journal window."""

    after: int
    through: int


@dataclass(frozen=True, slots=True)
class SourceReceipt:
    """A canonical captured Source and its stable journal position."""

    source: Source
    sequence: int


@dataclass(frozen=True, slots=True)
class MemoryFlushResult:
    """Result of processing one scoped Source window."""

    previous_cursor: int
    high_watermark: int
    current_cursor: int
    source_count: int
    memory_ref: ArtifactRef | None

    @property
    def processed(self) -> bool:
        return self.current_cursor > self.previous_cursor


@dataclass(frozen=True, slots=True)
class RememberMemoryRequest:
    """Append explicit entries, optionally against an expected head."""

    entries: tuple[MemoryEntryInput, ...]
    expected_revision: int | None = None


@dataclass(frozen=True, slots=True)
class SearchMemoryRequest:
    """Search one scoped Memory head."""

    query: str
    limit: int = 10
    mode: MemorySearchMode = "auto"


@dataclass(frozen=True, slots=True)
class MemorySearchPage:
    """Search results that can represent a scope with no Memory."""

    memory_ref: ArtifactRef | None
    mode: MemoryUsedSearchMode | None
    hits: tuple[MemoryHit, ...] = ()


@dataclass(frozen=True, slots=True)
class MemoryEntryRecord:
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


@dataclass(frozen=True, slots=True)
class MemoryEntriesPage:
    """Current entries for one scope, or an absent Memory."""

    memory_ref: ArtifactRef | None
    entries: tuple[MemoryEntryRecord, ...] = ()


@dataclass(frozen=True, slots=True)
class GetMemoryEntryRequest:
    citation: MemoryCitation


@dataclass(frozen=True, slots=True)
class ReviseMemoryEntryRequest:
    citation: MemoryCitation
    kind: str
    text: str
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class RetireMemoryEntryRequest:
    citation: MemoryCitation
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class MemoryMutationResult:
    previous_revision: int | None
    memory_ref: ArtifactRef
    entry: MemoryEntryRecord | None = None


@dataclass(frozen=True, slots=True)
class MemoryChangesPage:
    memory_ref: ArtifactRef | None
    revisions: tuple[MemoryRevisionChanges, ...] = ()
