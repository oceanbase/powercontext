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

"""Sans-I/O Trigger contract."""

from __future__ import annotations

from typing import Protocol, TypeVar

from powercontext.triggers.models import PolicyTransition

SignalT_contra = TypeVar("SignalT_contra", contravariant=True)
StateT = TypeVar("StateT")
ActionT_co = TypeVar("ActionT_co", covariant=True)


class Trigger(Protocol[SignalT_contra, StateT, ActionT_co]):
    """Map one signal and activation state to a pure transition."""

    def initial_state(self) -> StateT:
        """Return the state used before the first activation."""

        ...

    def activate(
        self,
        signal: SignalT_contra,
        state: StateT,
        /,
    ) -> PolicyTransition[StateT, ActionT_co]:
        """Evaluate a signal without persisting state or executing actions."""

        ...
