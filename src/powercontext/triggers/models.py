"""Immutable values produced by Triggers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, TypeVar

StateT = TypeVar("StateT")
ActionT_co = TypeVar("ActionT_co", covariant=True)


@dataclass(frozen=True, slots=True)
class PolicyTransition(Generic[StateT, ActionT_co]):
    """The complete result of one pure Trigger activation."""

    state: StateT
    actions: tuple[ActionT_co, ...] = ()
