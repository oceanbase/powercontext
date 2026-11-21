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

"""v14 full-request budget planning; identical packing logic, cell-based encoding cost."""

from dataclasses import dataclass

from ..v7.workflow import budget_status
from ..v8.admission import range_units, unit_input
from .wire import Request, prepare


@dataclass(frozen=True)
class Plan:
    requests: tuple[Request, ...]
    accepted: bool
    reason: str
    overflow: dict[str, bool]


def pack(inputs, supports, budget, policy):
    requests = []
    indivisible = False
    for value in inputs:
        original = prepare(value, supports)
        if original.cost["single_estimated_input_tokens"] <= budget:
            requests.append(original)
            continue
        pending = []
        for evidence in value.evidence:
            for unit in range_units(evidence, supports):
                candidate = prepare(*unit_input([*pending, unit]))
                if candidate.cost["single_estimated_input_tokens"] <= budget:
                    pending.append(unit)
                    continue
                if pending:
                    requests.append(prepare(*unit_input(pending)))
                pending = []
                single = prepare(*unit_input([unit]))
                if single.cost["single_estimated_input_tokens"] > budget:
                    indivisible = True
                    requests.append(single)
                else:
                    pending.append(unit)
        if pending:
            requests.append(prepare(*unit_input(pending)))
    overflow = budget_status([r.cost for r in requests], budget, policy)
    accepted = not any(overflow.values())
    reason = "indivisible_range_over_budget" if indivisible else "accepted" if accepted else "resource_budget"
    return Plan(tuple(requests), accepted, reason, overflow)
