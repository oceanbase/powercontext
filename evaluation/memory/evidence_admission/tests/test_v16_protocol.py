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

"""Regressions for lossless request encoding and unchanged semantic quality gates."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace

import pytest
from powercontext.builtin.artifacts.atomic_memory.extraction import (
    AtomicMemoryExtractionInput,
    AtomicMemoryExtractionOutput,
)
from powercontext.builtin.inference import GenerationResult

from powercontext_eval_evidence_admission.manifests import EvaluationError
from powercontext_eval_evidence_admission.models import digest
from powercontext_eval_evidence_admission.v10.scoring import candidate_key, ground_truth, review_binding, score
from powercontext_eval_evidence_admission.v16.codec import TemplateOutput, cell_binding, render, split_cells
from powercontext_eval_evidence_admission.v16.live import Extractor, pipeline
from powercontext_eval_evidence_admission.v16.wire import prepare, restore

from .test_v10_pipeline import prepared
from .test_v14_codec import supplied


def response(parts, evidence_ids=("e0",)):
    return TemplateOutput.model_validate(
        {
            "candidates": [
                {
                    "kind": "fact",
                    "description": "Synthetic protocol candidate",
                    "evidence_ids": evidence_ids,
                    "parts": parts,
                }
            ]
        }
    )


def copied(cell):
    return {"type": "copy", "quote_id": cell.cell_id}


def test_zero_cell_evidence_remains_citable():
    value, supports = supplied("A service uses an ordinary ASCII region.")
    request = prepare(value, supports)
    output = response([{"type": "text", "text": "The service uses an ordinary ASCII region."}])
    restored = render(output, request.cells, request.aliases, request_hash=cell_binding(request.cells))
    assert restored.candidates[0].evidence_ids == ("source:1",)
    assert restore(request, request.payload, request_hash=request.binding_hash) == value


def test_mixed_evidence_can_cite_a_source_without_copy_units():
    a, sa = supplied("a command `alpha`")
    b, sb = supplied("plain ASCII fact")
    evidence = b.evidence[0].model_copy(update={"evidence_id": "source:2"})
    support = sb[0].model_copy(update={"evidence_id": "source:2"})
    value = AtomicMemoryExtractionInput(evidence=(*a.evidence, evidence))
    request = prepare(value, (*sa, support))
    output = response([{"type": "text", "text": "plain ASCII fact"}], ("e1",))
    restored = render(output, request.cells, request.aliases, request_hash=cell_binding(request.cells))
    assert restored.candidates[0].evidence_ids == ("source:2",)
    with pytest.raises(EvaluationError, match="copy_without_candidate_support"):
        render(
            response([copied(request.cells[0])], ("e1",)),
            request.cells,
            request.aliases,
            request_hash=cell_binding(request.cells),
        )


@pytest.mark.parametrize(
    "text",
    [
        "`command --tries 03` with 中文 and e\u0301 and 👩🏽‍💻\r\nnext",
        "unclosed ` followed by e\u0301 and 中文",
        "ordinary ASCII without any copy units",
        "a `quoted` value with emoji 🇨🇳 and CR\rnext",
        "中" * 190,
    ],
)
def test_inline_encoding_preserves_original_bytes_without_a_duplicate_table(text):
    value, supports = supplied(text)
    request = prepare(value, supports)
    restored = restore(request, json.loads(json.dumps(request.payload)), request_hash=request.binding_hash)
    assert restored.model_dump(mode="json") == value.model_dump(mode="json")
    assert "cells" not in request.payload
    for cell in request.cells:
        raw = json.dumps(request.payload, ensure_ascii=False)
        assert raw.count('"c": "' + cell.cell_id + '"') == 1


@pytest.mark.parametrize("protected", ["e\u0301", "👩🏽‍💻", "\r\n", "🇨🇳", "✈️"])
def test_protected_sequences_are_selectable_without_whole_leaf_copy(protected):
    text = "普通中文 before " + protected + " after `code`"
    value, supports = supplied(text)
    request = prepare(value, supports)
    cell = next(c for c in request.cells if c.value == protected)
    output = render(
        response([{"type": "text", "text": "before "}, copied(cell), {"type": "text", "text": " after"}]),
        request.cells,
        request.aliases,
        request_hash=cell_binding(request.cells),
    )
    assert output.candidates[0].text.encode() == ("before " + protected + " after").encode()
    assert all(c.value != text for c in request.cells)


def test_one_fact_may_use_multiple_copy_parts():
    value, supports = supplied("service `north` listens on `8080`")
    request = prepare(value, supports)
    output = response([copied(request.cells[0]), {"type": "text", "text": " listens on "}, copied(request.cells[1])])
    restored = render(output, request.cells, request.aliases, request_hash=cell_binding(request.cells))
    assert len(restored.candidates) == 1
    assert restored.candidates[0].text == "north listens on 8080"


@pytest.mark.parametrize("change", ["payload", "alias", "cell_id", "projection", "materialization"])
def test_request_tampering_rejected_before_generation(change):
    value, supports = supplied("a `literal`")
    request = prepare(value, supports)
    expected = request.binding_hash
    if change == "payload":
        request.payload["evidence"][0]["data"]["content"]["content"] = "tampered"
    elif change == "alias":
        request = replace(request, aliases=("other",))
    elif change == "cell_id":
        request = replace(request, cells=(replace(request.cells[0], cell_id="c999"),))
    else:
        changed = supports[0].model_copy(update={change + "_hash": digest("different")})
        request = replace(request, supports=(changed,))
    with pytest.raises(EvaluationError, match="v16_request_binding_mismatch"):
        restore(request, request.payload, request_hash=expected)


def test_unknown_cell_and_evidence_are_rejected():
    value, supports = supplied("`known`")
    request = prepare(value, supports)
    for output, message in [
        (response([{"type": "copy", "quote_id": "c999"}]), "copy_without_candidate_support"),
        (response([copied(request.cells[0])], ("e99",)), "invalid_evidence_reference"),
    ]:
        with pytest.raises(EvaluationError, match=message):
            render(output, request.cells, request.aliases, request_hash=cell_binding(request.cells))


def test_original_unicode_case_roundtrips_and_can_render_exact_literals():
    async def scenario():
        case, sample, inputs = await prepared("unicode-boundaries")
        request = prepare(inputs[0], sample.supports)
        for literal in case.required[0].exact_literals:
            assert any(c.value == literal for c in request.cells)
        parts = [{"type": "text", "text": "原文需要保留："}]
        ids = []
        for literal in case.required[0].exact_literals:
            cell = next(c for c in request.cells if c.value == literal)
            parts.append(copied(cell))
            parts.append({"type": "text", "text": "；"})
            ids.append(f"e{cell.evidence_index}")
        output = response(parts, tuple(dict.fromkeys(ids)))

        class Controlled:
            async def generate(self, value, /):
                return GenerationResult(output=output)

        extractor = Extractor(request, Controlled())
        candidates = await pipeline(extractor).extract(request.value)
        text = "".join(c.text for c in candidates)
        assert all(literal.encode() in text.encode() for literal in case.required[0].exact_literals)
        assert extractor.raw_template == output
        assert restore(request, request.payload, request_hash=request.binding_hash) == request.value

    asyncio.run(scenario())


def test_compound_recorded_output_is_not_split_or_promoted_to_atomic_success():
    async def scenario():
        case, sample, inputs = await prepared()
        request = prepare(inputs[0], sample.supports)
        output = response([{"type": "text", "text": "Deployment uses "}, *[copied(c) for c in request.cells]])

        class Recorded:
            async def generate(self, value, /):
                return GenerationResult(output=output)

        restored = AtomicMemoryExtractionOutput(
            candidates=await pipeline(Extractor(request, Recorded())).extract(request.value)
        )
        assert len(restored.candidates) == 1
        atoms = ground_truth(case)
        candidate = restored.candidates[0]
        review = {
            "reviewer": "controlled_offline_fixture_not_live_review",
            "reviewer_kind": "assistant",
            "output_hash": digest(restored.model_dump(mode="json")),
            "context_hash": review_binding(case, request.value, request.supports, restored),
            "judgements": {
                candidate_key(candidate, 0): {
                    "supported": True,
                    "atomic": False,
                    "false_attribution": False,
                    "entailed_atom_ids": [a["id"] for a in atoms],
                }
            },
        }
        result = score(case, request.value, request.supports, restored, review)
        assert result["retained_literals"] == 4
        assert result["nonatomic_candidates"] == 1
        assert not result["diagnostic_success"]

    asyncio.run(scenario())


def test_protected_content_is_not_normalized_or_added_to_ascii_text():
    assert split_cells("plain ASCII") == []
    assert split_cells("x e\u0301 y") == [("e\u0301", 2, 4)]
    assert split_cells("x\r\ny") == [("\r\n", 1, 3)]


def test_unicode_prose_has_no_copy_wrappers_but_code_and_protected_clusters_do():
    assert split_cells("普通中文，标点。 café Ελληνικά") == []
    assert split_cells("中文 `中文命令` 文本") == [("中文命令", 4, 8)]
    assert [value for value, _, _ in split_cells("中🙂文👩🏽‍💻 e\u0301\r\n")] == ["🙂", "👩🏽‍💻", "e\u0301", "\r\n"]


def test_v16_isolates_cell_policy_from_instructions_and_output_schema():
    from powercontext_eval_evidence_admission.v15.codec import INSTRUCTIONS as old_instructions
    from powercontext_eval_evidence_admission.v15.codec import TemplateOutput as OldOutput
    from powercontext_eval_evidence_admission.v16.codec import INSTRUCTIONS

    assert INSTRUCTIONS == old_instructions
    assert TemplateOutput.model_json_schema() == OldOutput.model_json_schema()


def test_original_unicode_manifest_rejects_multiple_requests_without_changing_evidence():
    async def scenario():
        from pathlib import Path

        from powercontext_eval_evidence_admission.manifests import load_suite, read_json
        from powercontext_eval_evidence_admission.v15.admission import pack as old_pack
        from powercontext_eval_evidence_admission.v16 import freeze
        from powercontext_eval_evidence_admission.v16.admission import pack
        from powercontext_eval_evidence_admission.v16.workflow import minimal_case_record

        case, sample, inputs = await prepared("unicode-boundaries")
        policy = load_suite(experiment_version="v5").experiment.v3
        previous = old_pack(inputs, sample.supports, 4096, policy)
        plan = pack(inputs, sample.supports, 4096, policy)
        assert len(previous.requests) == 4
        contract = read_json(Path(freeze.__file__).with_name("contract.json"))
        assert contract["online"]["per_case_requests"] == 1
        assert plan.accepted and 1 < len(plan.requests) < len(previous.requests)
        row = minimal_case_record(case, sample, plan)
        assert not row["one_request_gate_passed"]
        assert row["required_literals_copy_selectable"]
        for request in plan.requests:
            assert request.cost["single_estimated_input_tokens"] <= 4096
            assert restore(request, request.payload, request_hash=request.binding_hash) == request.value
            assert all(c.identity()["protocol"] == "v16-protected-inline-cell" for c in request.cells)

    asyncio.run(scenario())


def test_many_source_2k_reports_current_runtime_indivisible_range():
    async def scenario():
        from powercontext_eval_evidence_admission.manifests import load_suite
        from powercontext_eval_evidence_admission.v16.admission import pack

        _, sample, inputs = await prepared("many-source-overflow", budget=2048)
        plan = pack(inputs, sample.supports, 2048, load_suite(experiment_version="v5").experiment.v3)
        assert not plan.accepted
        assert plan.reason == "indivisible_range_over_budget"
        assert plan.overflow == {"per_call": True, "cumulative": True, "call_count": True}
        assert len(plan.requests) > 16
        assert any(r.cost["single_estimated_input_tokens"] > 2048 for r in plan.requests)
        assert sum(r.cost["single_estimated_input_tokens"] for r in plan.requests) > 16384

    asyncio.run(scenario())


@pytest.mark.parametrize("defect", ["input", "literal", "execution"])
def test_offline_gate_rejects_one_historical_regression_despite_full_aggregate_coverage(tmp_path, monkeypatch, defect):
    from powercontext_eval_evidence_admission.v16 import workflow

    # Controlled counters exercise the verdict, not Source replay or bundle validation.
    rows = []
    for index in range(324):
        claims = [f"claim-{index}-a"] + ([f"claim-{index}-b"] if index < 171 else [])
        rows.append(
            {
                "sample_id": f"sample-{index}",
                "case": f"case-{index}",
                "arm": "bounded-fragments-v3",
                "budget": 4096,
                "repetition": 0,
                "plan_accepted": True,
                "plan_reason": "accepted",
                "budget_overflow": {},
                "diagnostic_plan_calls": 1,
                "diagnostic_plan_estimated_input_tokens": 1,
                "reference_required_content_fits": True,
                "reference_required_claims": len(claims),
                "coverage": {"retained_claim_hashes": claims},
                "historical_v15_input_unchanged": True,
                "historical_v15_coverage_losses": {"retained_literal_hashes": []},
                "historical_v15_executable_regression": False,
                "historical_v15_calls": 1,
                "historical_v15_estimated_input_tokens": 1,
            }
        )
    monkeypatch.setattr(workflow, "verified_comparisons", lambda *_: {"controlled": {"samples": rows}})
    monkeypatch.setattr(workflow, "identity", lambda: {"controlled_test": "offline_gate"})
    assert workflow.summarize(tmp_path, persist=False)["offline_gate_passed"]
    if defect == "input":
        rows[0]["historical_v15_input_unchanged"] = False
    elif defect == "literal":
        rows[0]["historical_v15_coverage_losses"]["retained_literal_hashes"] = ["lost-literal-identity"]
    else:
        rows[0]["historical_v15_executable_regression"] = True
    result = workflow.summarize(tmp_path, persist=False)
    assert result["arms"]["bounded-fragments-v3"]["executable_retained_claims"] == 495
    assert not result["offline_gate_passed"] and result["stop_required"]
    assert result["failures"] and result["decision"] == "insufficient_evidence"
