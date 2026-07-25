"""Typed component groups for the standard built-in profile."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.memory import MemoryService
from powercontext.builtin.sources import (
    ContentCapture,
    ContentSource,
    SourceCursor,
    SourceJournal,
)
from powercontext.context import Sources


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


@runtime_checkable
class SourceWindowApplication(Protocol):
    """Atomically execute one built-in Source-window activation."""

    async def flush(self, *, limit: int) -> MemoryFlushResult: ...

    async def cursor(self) -> SourceCursor: ...


class BuiltinSources(Sources):
    """Expose built-in Source use cases over one scope-bound catalog."""

    journal: SourceJournal

    async def capture(self, value: ContentCapture, /) -> tuple[ContentSource, int]:
        """Resolve and persist one Content Source, returning its journal position."""

        resolved = await self.resolve(value)
        if type(resolved) is not ContentSource:
            raise TypeError("Content Source adapter returned an unexpected Source type")  # noqa: TRY003
        source = await self.add(resolved)
        if type(source) is not ContentSource:
            raise TypeError("Content Source adapter returned an unexpected Source type")  # noqa: TRY003
        return source, await self.journal.position(source)


class BuiltinArtifacts(BaseModel):
    """Expose built-in Artifact families for one scope."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    memory: MemoryService
    memory_artifact_id: str
