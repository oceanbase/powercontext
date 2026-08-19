"""Shared benchmark gates."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeVar

from powercontext_eval.errors import PowerContextEvalError

ResultT = TypeVar("ResultT")


class GoldCheckFailed(PowerContextEvalError):
    """The official evaluator rejected its own gold patch."""


@dataclass(frozen=True)
class GoldResult:
    """Official result for the benchmark gold patch."""

    instance_id: str
    resolved: bool


def run_after_gold(gold: GoldResult, arms: Callable[[], ResultT]) -> ResultT:
    """Run treatment arms only after a successful official gold check."""

    if gold.resolved is not True:
        raise GoldCheckFailed(f"Gold patch did not resolve {gold.instance_id}")
    return arms()
