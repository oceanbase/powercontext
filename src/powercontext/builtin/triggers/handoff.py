# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

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
