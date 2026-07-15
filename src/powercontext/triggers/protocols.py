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
