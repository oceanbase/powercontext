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
