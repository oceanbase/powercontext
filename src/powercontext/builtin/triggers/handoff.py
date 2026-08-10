"""Pure provider-boundary policy for Handoff generation."""

from __future__ import annotations

from pydantic import BaseModel, Field

from powercontext.builtin.artifacts.handoff import ActivateHandoff, PrepareHandoff
from powercontext.builtin.sources import SourceCursor
from powercontext.triggers import PolicyTransition

HANDOFF_BOUNDARY_TRIGGER_NAME = "handoff-participant-boundary"


class HandoffBoundary(BaseModel):
    """One provider boundary anchored to a durable Source journal position."""

    position: int = Field(gt=0)
    activation: ActivateHandoff


class HandoffTrigger:
    """Generate at most one Action as Source boundaries advance."""

    def initial_state(self) -> SourceCursor:
        return SourceCursor()

    def activate(
        self,
        signal: HandoffBoundary,
        state: SourceCursor,
        /,
    ) -> PolicyTransition[SourceCursor, PrepareHandoff]:
        if signal.position <= state.sequence:
            return PolicyTransition(state=state)
        activation = signal.activation
        return PolicyTransition(
            state=SourceCursor(sequence=signal.position),
            actions=(
                PrepareHandoff(
                    objective=activation.objective,
                    evidence=activation.action_evidence(),
                    max_bytes=activation.max_bytes,
                ),
            ),
        )
