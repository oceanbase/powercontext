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

from __future__ import annotations

from types import SimpleNamespace

import pytest
from powercontext.builtin.artifacts.atomic_memory.extraction import AtomicMemoryEvidence, AtomicMemoryExtractionInput
from powercontext.sources import SourceRef

from powercontext_eval_evidence_admission.models import Case, Claim, Piece, SourceFixture, Support, digest
from powercontext_eval_evidence_admission.v7.fidelity import cost_ledger, quote_inventory
from powercontext_eval_evidence_admission.v8.admission import pack
from powercontext_eval_evidence_admission.v8.workflow import coverage, selection_manifest


def selected(parts):
    rows, supports, offset = [], [], 0
    for value in parts:
        size = len(value.encode())
        rows.append(
            {
                "field": "/content/content",
                "value": value,
                "bytes": [offset, offset + size],
                "characters": [offset, offset + len(value)],
            }
        )
        supports.append(
            Support(
                evidence_id="fragment-bundle:parent",
                source_hash=digest("source-a"),
                materialization_hash=digest("material"),
                projection_hash=digest("projection"),
                field_hash=digest("/content/content"),
                byte_start=offset,
                byte_end=offset + size,
                char_start=offset,
                char_end=offset + len(value),
            )
        )
        offset += size
    evidence = AtomicMemoryEvidence(
        evidence_id="fragment-bundle:parent",
        source_ref=SourceRef(source_type="content", source_id="source-a"),
        journal_position=1,
        content={"ranges": rows, "incomplete": True},
        source_metadata={
            "materialization_hash": digest("material"),
            "projection_hash": digest("projection"),
            "admission_policy": "bounded-fragments-v3",
        },
    )
    return AtomicMemoryExtractionInput(evidence=(evidence,)), tuple(supports)


def policy(calls=16, multiplier=8):
    return SimpleNamespace(max_calls_per_sample=calls, total_input_budget_multiplier=multiplier)


def split_budget(value, supports):
    # Enough for one range including the complete protocol, insufficient for the bundle.
    costs = []
    for row in value.evidence[0].content["ranges"]:
        single = value.model_copy(
            update={
                "evidence": (value.evidence[0].model_copy(update={"content": {"ranges": [row], "incomplete": True}}),)
            }
        )
        costs.append(cost_ledger(single, quote_inventory(single, supports))["single_estimated_input_tokens"])
    return max(costs) + 80


def test_repacking_preserves_selected_bytes_provenance_and_full_request_budget():
    value, supports = selected(["first " + "x" * 1000, "second " + "y" * 1000])
    budget = split_budget(value, supports)
    plan = pack([value], supports, budget, policy())
    assert plan.accepted and len(plan.requests) == 2
    assert all(r.cost["single_estimated_input_tokens"] <= budget for r in plan.requests)
    assert selection_manifest([value], supports) == selection_manifest(
        [r.value for r in plan.requests], tuple(s for r in plan.requests for s in r.supports)
    )
    assert len({r.binding_hash for r in plan.requests}) == 2


@pytest.mark.parametrize(("caps", "key"), [(policy(calls=1), "call_count"), (policy(multiplier=1), "cumulative")])
def test_sample_caps_reject_whole_plan_without_authorizing_partial_prefix(caps, key):
    value, supports = selected(["x" * 1000, "y" * 1000])
    plan = pack([value], supports, split_budget(value, supports), caps)
    assert not plan.accepted and plan.reason == "resource_budget"
    assert plan.overflow[key]
    assert len(plan.requests) == 2  # Diagnostic only; rejected sample has no executable calls.


def test_indivisible_overflow_is_explicit_and_never_truncated():
    value, supports = selected(["required " + "x" * 4000])
    plan = pack([value], supports, 100, policy())
    assert not plan.accepted and plan.reason == "indivisible_range_over_budget"
    assert plan.requests == ()


def test_coverage_separates_union_from_same_request_visibility():
    parts = ["a" * 1000, "b" * 1000]
    value, supports = selected(parts)
    plan = pack([value], supports, split_budget(value, supports), policy())
    case = Case(
        case_id="spanning-fact",
        categories=("regression",),
        sources=(SourceFixture(source_id="source-a", pieces=(Piece(text="".join(parts)),)),),
        required=(Claim(claim_id="spans-both", source_id="source-a", text="a" * 10 + "b" * 10),),
        completeness_ceiling="complete",
        reducibility="fragments",
    )
    score = coverage(case, plan.requests)
    assert score["retained_claim_hashes"] == [digest("spans-both")]
    assert score["same_request_claim_hashes"] == []


def test_original_requests_are_not_merged_and_no_missing_content_is_backfilled():
    value, supports = selected(["one"])
    plan = pack([value, value], supports, 8192, policy())
    assert len(plan.requests) == 2
    assert plan.requests[0].value == plan.requests[1].value == value
    assert pack([], supports, 2048, policy()).requests == ()


def test_multifield_scalar_and_unicode_survive_repacking():
    value, supports = selected(["e\u0301\r\n👩🏽‍💻" * 20, "tail" * 200])
    evidence = value.evidence[0]
    scalar = {"field": "/content/metadata/count", "value": 17, "bytes": [0, 2], "characters": [0, 2]}
    supports += (
        supports[0].model_copy(
            update={
                "field_hash": digest(scalar["field"]),
                "byte_start": 0,
                "byte_end": 2,
                "char_start": 0,
                "char_end": 2,
            }
        ),
    )
    value = value.model_copy(
        update={
            "evidence": (
                evidence.model_copy(
                    update={"content": {"incomplete": True, "ranges": [*evidence.content["ranges"], scalar]}}
                ),
            )
        }
    )
    plan = pack([value], supports, split_budget(value, supports), policy())
    assert plan.accepted
    assert selection_manifest([value], supports) == selection_manifest(
        [r.value for r in plan.requests], tuple(s for r in plan.requests for s in r.supports)
    )
    assert any(r["value"] == 17 for p in plan.requests for e in p.value.evidence for r in e.content["ranges"])
