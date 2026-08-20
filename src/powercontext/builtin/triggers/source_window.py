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
