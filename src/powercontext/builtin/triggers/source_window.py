"""Pure Trigger policies used by the local runtime."""

from __future__ import annotations

from pydantic import BaseModel, Field

from powercontext.builtin.sources import SourceCursor
from powercontext.triggers import PolicyTransition

SOURCE_WINDOW_TRIGGER_NAME = "memory-source-window"


class SourceHighWatermark(BaseModel):
    """The bounded journal position visible to one built-in activation."""

    sequence: int = Field(ge=0)
    limit: int = Field(gt=0)


class ProcessSourceWindow(BaseModel):
    """Consume one fixed, non-empty built-in Source journal window."""

    after: int = Field(ge=0)
    through: int = Field(gt=0)


class SourceWindowTrigger:
    """Select the next bounded Source window from a monotonic journal."""

    def initial_state(self) -> SourceCursor:
        return SourceCursor()

    def activate(
        self,
        signal: SourceHighWatermark,
        state: SourceCursor,
        /,
    ) -> PolicyTransition[SourceCursor, ProcessSourceWindow]:
        if signal.sequence <= state.sequence:
            return PolicyTransition(state=state)

        through = min(signal.sequence, state.sequence + signal.limit)
        return PolicyTransition(
            state=SourceCursor(sequence=through),
            actions=(ProcessSourceWindow(after=state.sequence, through=through),),
        )
