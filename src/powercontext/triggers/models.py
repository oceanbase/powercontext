"""Immutable values produced by Triggers."""

from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel

StateT = TypeVar("StateT")
ActionT_co = TypeVar("ActionT_co", covariant=True)


class PolicyTransition(BaseModel, Generic[StateT, ActionT_co]):
    """The complete result of one pure Trigger activation."""

    state: StateT
    actions: tuple[ActionT_co, ...] = ()
