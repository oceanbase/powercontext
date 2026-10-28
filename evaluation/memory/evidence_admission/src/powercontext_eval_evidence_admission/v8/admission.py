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

"""Repack selected evidence without labels, omitted Source reads or byte truncation."""

from __future__ import annotations

from dataclasses import dataclass

from powercontext.builtin.artifacts.atomic_memory.extraction import AtomicMemoryExtractionInput

from ..admission import leaves
from ..manifests import EvaluationError
from ..models import Support, canonical, digest
from ..v7.fidelity import binding, cost_ledger, quote_inventory
from ..v7.workflow import budget_status


@dataclass(frozen=True)
class PreparedRequest:
    value: AtomicMemoryExtractionInput
    supports: tuple[Support, ...]
    cost: dict
    binding_hash: str


def request(value, supports):
    quotes = quote_inventory(value, supports)
    return PreparedRequest(value, supports, cost_ledger(value, quotes), binding(value, quotes))


def range_units(evidence, supports):
    """Keep each admitted range whole; scalar JSON and field paths remain explicit."""
    content = evidence.content
    if evidence.evidence_id.startswith("fragment-bundle:"):
        rows = content["ranges"]
        metadata = evidence.source_metadata
    elif evidence.evidence_id.startswith("fragment:"):
        rows = [
            {
                "field": content["field"],
                "value": content["value"],
                "bytes": content["byte_range"],
                "characters": content["character_range"],
            }
        ]
        metadata = evidence.source_metadata
    else:
        rows = []
        for pointer, leaf in leaves({"content": content, "source_metadata": evidence.source_metadata}):
            size = len(leaf.encode()) if isinstance(leaf, str) else len(canonical(leaf))
            rows.append(
                {
                    "field": pointer,
                    "value": leaf,
                    "bytes": [0, size],
                    "characters": [0, len(leaf) if isinstance(leaf, str) else size],
                }
            )
        metadata = {"admission_policy": "request-budget-v8", "source_declaration_changed": False}
    for row in rows:
        left, right = row["bytes"]
        matched = tuple(
            s
            for s in supports
            if s.evidence_id == evidence.evidence_id
            and s.field_hash == digest(row["field"])
            and s.source_hash == digest(evidence.source_ref.source_id)
            and (s.byte_start < right and left < s.byte_end or left == right == s.byte_start == s.byte_end)
        )
        if not matched:
            raise EvaluationError("v8_range_without_selected_support")
        yield evidence, row, matched, metadata


def unit_input(units):
    """Group adjacent units of the same parent into a new explicitly identified projection."""
    evidence, supports = [], []
    groups = []
    for unit in units:
        if groups and groups[-1][0][0].evidence_id == unit[0].evidence_id:
            groups[-1].append(unit)
        else:
            groups.append([unit])
    for group in groups:
        parent, _, _, metadata = group[0]
        ranges = [u[1] for u in group]
        new_id = "fragment-bundle:v8:" + digest({"parent": parent.model_dump(mode="json"), "ranges": ranges})
        item = parent.model_copy(
            update={
                "evidence_id": new_id,
                "content": {"incomplete": True, "ranges": ranges},
                "source_metadata": metadata,
            }
        )
        evidence.append(item)
        supports.extend(s.model_copy(update={"evidence_id": new_id}) for u in group for s in u[2])
    return AtomicMemoryExtractionInput(evidence=tuple(evidence)), tuple(dict.fromkeys(supports))


@dataclass(frozen=True)
class Plan:
    requests: tuple[PreparedRequest, ...]
    accepted: bool
    reason: str
    overflow: dict[str, bool]


def pack(inputs, supports, budget, policy):
    """Charge the final wire representation before accepting the complete sample.

    No labels enter this function. Failed plans retain diagnostic requests but authorize
    zero calls; neither a partial prefix nor a silently truncated range is executable.
    """
    prepared = []
    for value in inputs:
        original = request(value, supports)
        if original.cost["single_estimated_input_tokens"] <= budget:
            prepared.append(original)
            continue
        pending = []
        for evidence in value.evidence:
            for unit in range_units(evidence, supports):
                candidate = request(*unit_input([*pending, unit]))
                if candidate.cost["single_estimated_input_tokens"] <= budget:
                    pending.append(unit)
                    continue
                if pending:
                    prepared.append(request(*unit_input(pending)))
                    pending = []
                single = request(*unit_input([unit]))
                if single.cost["single_estimated_input_tokens"] > budget:
                    return Plan(
                        tuple(prepared),
                        False,
                        "indivisible_range_over_budget",
                        {"per_call": True, "cumulative": False, "call_count": False},
                    )
                pending.append(unit)
        if pending:
            prepared.append(request(*unit_input(pending)))
    overflow = budget_status([p.cost for p in prepared], budget, policy)
    return Plan(
        tuple(prepared),
        not any(overflow.values()),
        "resource_budget" if any(overflow.values()) else "accepted",
        overflow,
    )
