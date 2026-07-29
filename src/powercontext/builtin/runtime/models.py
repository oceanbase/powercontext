"""Commands and results exposed by the built-in Runtime."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator

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

PreparedContextSchema: TypeAlias = Literal["powercontext.prepared-context.v1"]
PreparedContextStatus: TypeAlias = Literal["ready", "empty"]

PREPARED_CONTEXT_SCHEMA: PreparedContextSchema = "powercontext.prepared-context.v1"


class _PreparedContextModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


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
    context_versions: tuple[PreparedContextSchema, ...] = (PREPARED_CONTEXT_SCHEMA,)


class MemoryFlushResult(BaseModel):
    """Result of processing one scoped Source window."""

    previous_cursor: int
    high_watermark: int
    current_cursor: int
    source_count: int
    memory_ref: ArtifactRef | None

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


class MemorySearchPage(BaseModel):
    """Search results that can represent a scope with no Memory."""

    memory_ref: ArtifactRef | None
    mode: MemoryUsedSearchMode | None
    hits: tuple[MemoryHit, ...] = ()


class PrepareContextRequest(_PreparedContextModel):
    """Prepare bounded context for one Agent turn."""

    query: Annotated[str, Field(min_length=1, max_length=8192)]
    max_bytes: Annotated[int, Field(ge=512, le=32768)] = 8000

    @field_validator("query")
    @classmethod
    def validate_query(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("query must contain non-whitespace content")  # noqa: TRY003
        return value


class PreparedContext(_PreparedContextModel):
    """Ephemeral context ready for direct injection into one Agent turn."""

    schema_version: PreparedContextSchema = Field(default=PREPARED_CONTEXT_SCHEMA, alias="schema")
    status: PreparedContextStatus
    content: str | None
    content_bytes: Annotated[int, Field(ge=0)]

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
    """Selected current-head entries for one scope, or an absent Memory."""

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
