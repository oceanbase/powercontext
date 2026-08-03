"""Typed context groups for the standard built-in profile."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from powercontext.builtin.artifacts.handoff import HandoffService
from powercontext.builtin.artifacts.memory import MemoryService
from powercontext.builtin.sources import (
    ContentCapture,
    ContentSource,
    SourceJournal,
)
from powercontext.context import Sources


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

    handoff: HandoffService
    handoff_artifact_id: str
    memory: MemoryService
    memory_artifact_id: str
