"""Pure Trigger policies used by the local runtime."""

from __future__ import annotations

from powercontext.runtime.models import ProcessSourceWindow, SourceHighWatermark
from powercontext.sources.journal import SourceCursor
from powercontext.triggers import PolicyTransition


class _InvalidSourceWindowError(ValueError):
    def __init__(self) -> None:
        super().__init__("source window limit must be positive")


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
        if signal.limit < 1:
            raise _InvalidSourceWindowError
        if signal.sequence <= state.sequence:
            return PolicyTransition(state=state)

        through = min(signal.sequence, state.sequence + signal.limit)
        return PolicyTransition(
            state=SourceCursor(through),
            actions=(ProcessSourceWindow(after=state.sequence, through=through),),
        )
