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

import json
from dataclasses import replace
from types import SimpleNamespace

import pytest
from powercontext.builtin.artifacts.atomic_memory.extraction import AtomicMemoryEvidence, AtomicMemoryExtractionInput
from powercontext.builtin.inference import character_token_estimator
from powercontext.sources import SourceRef

from powercontext_eval_evidence_admission.admission import leaves
from powercontext_eval_evidence_admission.manifests import EvaluationError
from powercontext_eval_evidence_admission.models import Support, canonical, digest
from powercontext_eval_evidence_admission.v7.fidelity import cost_ledger, quote_inventory
from powercontext_eval_evidence_admission.v8.workflow import selection_manifest
from powercontext_eval_evidence_admission.v9.admission import pack
from powercontext_eval_evidence_admission.v9.codec import INSTRUCTIONS, TemplateOutput, prepare, render, restore


def supplied(content, metadata=None):
    evidence = AtomicMemoryEvidence(
        evidence_id="source:1",
        source_ref=SourceRef(source_type="content", source_id="source-a"),
        journal_position=1,
        content=content,
        source_metadata={} if metadata is None else metadata,
    )
    supports = []
    for pointer, value in leaves({"content": content, "source_metadata": evidence.source_metadata}):
        size = len(value.encode()) if isinstance(value, str) else len(canonical(value))
        supports.append(
            Support(
                evidence_id=evidence.evidence_id,
                source_hash=digest("source-a"),
                materialization_hash=digest("material"),
                projection_hash=digest(evidence.model_dump(mode="json")),
                field_hash=digest(pointer),
                byte_start=0,
                byte_end=size,
                char_start=0,
                char_end=len(value) if isinstance(value, str) else size,
            )
        )
    return AtomicMemoryExtractionInput(evidence=(evidence,)), tuple(supports)


def template(quote="q0", evidence="e0", start=0, end=4):
    return TemplateOutput.model_validate(
        {
            "candidates": [
                {
                    "kind": "fact",
                    "evidence_ids": [evidence],
                    "parts": [{"type": "copy", "quote_id": quote, "byte_start": start, "byte_end": end}],
                }
            ]
        }
    )


def test_compact_payload_roundtrip_keeps_types_paths_times_containers_and_hashlike_facts():
    content = {"body": "e\u0301\r\n👩🏽‍💻", "a/b~": [17, True, None, {}, [], ""], "numeric": {"0": "object key"}}
    metadata = {"event_time": "2026-10-27T12:00:00Z", "owner": "张三", "sha256": "a" * 64}
    value, supports = supplied(content, metadata)
    request = prepare(value, supports)
    wire = json.loads(canonical(request.payload))
    decoded = restore(request, wire, request_hash=request.binding_hash)
    assert decoded == value
    assert decoded.evidence[0].content["a/b~"] == [17, True, None, {}, [], ""]
    assert wire["evidence"][0]["evidence_id"] == "e0"
    assert isinstance(wire["evidence"][0]["data"]["content"]["a/b~"], list)
    assert isinstance(wire["evidence"][0]["data"]["content"]["numeric"], dict)
    assert wire["evidence"][0]["data"]["source_metadata"]["sha256"]["v"] == "a" * 64
    assert selection_manifest([decoded], supports) == selection_manifest([value], supports)


def test_inline_quotes_render_exact_bytes_and_restore_full_evidence_identity():
    text = "e\u0301\r\n下一行👩🏽‍💻"
    value, supports = supplied(text)
    request = prepare(value, supports)
    wire = json.loads(canonical(request.payload))
    assert "quote_index" not in wire
    assert wire["evidence"][0]["data"]["content"]["q"] == "q0"
    output = TemplateOutput.model_validate_json(template(end=len(text.encode())).model_dump_json())
    rendered = render(output, request, request_hash=request.binding_hash)
    assert rendered.candidates[0].text.encode() == text.encode()
    assert rendered.candidates[0].evidence_ids == ("source:1",)
    assert render(TemplateOutput(), request, request_hash=request.binding_hash).candidates == ()


@pytest.mark.parametrize("change", ["value", "field", "range", "quote", "source", "journal", "container"])
def test_tampered_wire_cannot_reuse_originating_binding(change):
    value, supports = supplied({"body": "original"})
    request = prepare(value, supports)
    wire = json.loads(canonical(request.payload))
    row = wire["evidence"][0]["data"]["content"]["body"]
    if change == "value":
        row["v"] = "forged"
    elif change == "field":
        wire["evidence"][0]["data"]["content"]["wrong"] = wire["evidence"][0]["data"]["content"].pop("body")
    elif change == "range":
        row["copies"] = [{"q": "q0", "b": [1, 4]}]
    elif change == "quote":
        row["q"] = "q9"
    elif change == "source":
        wire["evidence"][0]["source_ref"]["source_id"] = "source-b"
    elif change == "journal":
        wire["evidence"][0]["journal_position"] = 2
    else:
        wire["evidence"][0]["data"]["content"] = [wire["evidence"][0]["data"]["content"]]
    with pytest.raises(EvaluationError, match="v9_request_binding_mismatch"):
        restore(request, wire, request_hash=request.binding_hash)


def test_short_aliases_cannot_cross_requests_versions_or_internal_maps():
    value, supports = supplied("text")
    request = prepare(value, supports)
    changed = prepare(value, (supports[0].model_copy(update={"materialization_hash": digest("new")}), *supports[1:]))
    with pytest.raises(EvaluationError, match="v9_request_binding_mismatch"):
        render(template(), changed, request_hash=request.binding_hash)
    changed = replace(request, aliases=("source:other",))
    with pytest.raises(EvaluationError, match="v9_request_binding_mismatch"):
        render(template(), changed, request_hash=request.binding_hash)
    with pytest.raises(EvaluationError, match="invalid_evidence_reference"):
        render(template(evidence="source:1"), request, request_hash=request.binding_hash)
    with pytest.raises(EvaluationError, match="copy_without_candidate_support"):
        render(template(quote="q9"), request, request_hash=request.binding_hash)


def test_valid_alias_does_not_authorize_another_evidences_quote():
    value, supports = supplied("text")
    other = value.evidence[0].model_copy(update={"evidence_id": "source:2", "journal_position": 2})
    value = value.model_copy(update={"evidence": (*value.evidence, other)})
    supports += tuple(s.model_copy(update={"evidence_id": "source:2"}) for s in supports)
    request = prepare(value, supports)
    with pytest.raises(EvaluationError, match="copy_without_candidate_support"):
        render(template(quote="q1", evidence="e0"), request, request_hash=request.binding_hash)


@pytest.mark.parametrize(
    ("text", "start", "end"), [("中文", 1, 3), ("e\u0301", 0, 1), ("👩🏽‍💻", 0, 4), ("\r\nX", 0, 1)]
)
def test_compact_render_preserves_protected_sequence_boundaries(text, start, end):
    value, supports = supplied(text)
    request = prepare(value, supports)
    with pytest.raises(EvaluationError, match="copy_splits"):
        render(template(start=start, end=end), request, request_hash=request.binding_hash)


def test_gapped_support_is_explicit_and_does_not_copy_unadmitted_middle():
    value, supports = supplied("head GAP tail")
    source = supports[0]
    supports = (
        source.model_copy(update={"byte_end": 4, "char_end": 4}),
        source.model_copy(update={"byte_start": 9, "char_start": 9}),
        supports[1],
    )
    request = prepare(value, supports)
    row = request.payload["evidence"][0]["data"]["content"]
    assert "q" not in row
    assert row["copies"] == [{"q": "q0", "b": [0, 4]}, {"q": "q1", "b": [9, 13]}]
    assert restore(request, json.loads(canonical(request.payload)), request_hash=request.binding_hash) == value
    with pytest.raises(EvaluationError, match="copy_span_out_of_bounds"):
        render(template(end=8), request, request_hash=request.binding_hash)


def test_scalar_only_evidence_remains_citable_without_a_string_copy():
    value, supports = supplied(17)
    request = prepare(value, supports)
    assert request.quotes == ()
    output = TemplateOutput.model_validate(
        {"candidates": [{"kind": "fact", "evidence_ids": ["e0"], "parts": [{"type": "text", "text": "Count is 17."}]}]}
    )
    assert render(output, request, request_hash=request.binding_hash).candidates[0].evidence_ids == ("source:1",)


def test_ledger_charges_exact_serialized_request_and_reduces_same_input_cost():
    value, supports = supplied("deployctl --region cn --retries 03 " + "x" * 200)
    request = prepare(value, supports)
    encoded = canonical(
        {"instructions": INSTRUCTIONS, "input": request.payload, "output_schema": TemplateOutput.model_json_schema()}
    )
    assert request.cost["single_estimated_input_tokens"] == character_token_estimator().estimate(encoded.decode())
    assert request.cost["serialized_request_bytes"] == len(encoded)
    assert sum(request.cost["components"].values()) == request.cost["single_estimated_input_tokens"]
    assert (
        request.cost["single_estimated_input_tokens"]
        < cost_ledger(value, quote_inventory(value, supports))["single_estimated_input_tokens"]
    )


def test_resource_caps_reject_whole_sample_without_dropping_diagnostic_content():
    value, supports = supplied("selected " + "x" * 500)
    request = prepare(value, supports)
    cap = SimpleNamespace(max_calls_per_sample=1, total_input_budget_multiplier=1)
    plan = pack([value, value], supports, request.cost["single_estimated_input_tokens"] + 10, cap)
    assert not plan.accepted and plan.overflow == {"per_call": False, "cumulative": True, "call_count": True}
    assert len(plan.requests) == 2
    assert selection_manifest([r.value for r in plan.requests], supports) == selection_manifest(
        [value, value], supports
    )


def test_indivisible_failure_retains_full_diagnostic_plan():
    value, supports = supplied("whole selected value")
    plan = pack([value], supports, 1, SimpleNamespace(max_calls_per_sample=16, total_input_budget_multiplier=8))
    assert not plan.accepted and plan.reason == "indivisible_range_over_budget"
    assert selection_manifest([value], supports) == selection_manifest(
        [r.value for r in plan.requests], tuple(s for r in plan.requests for s in r.supports)
    )


def test_duplicate_evidence_id_is_rejected_before_short_alias_assignment():
    value, supports = supplied("text")
    with pytest.raises(EvaluationError, match="v9_duplicate_evidence_id"):
        prepare(value.model_copy(update={"evidence": value.evidence * 2}), supports)


def test_source_keys_that_resemble_wire_keys_remain_ordinary_source_data():
    value, supports = supplied({"v": {"q": "q0", "copies": [{"b": [0, 4]}]}, "q": "e0"})
    request = prepare(value, supports)
    wire = json.loads(canonical(request.payload))
    assert wire["evidence"][0]["data"]["content"]["v"]["q"]["v"] == "q0"
    assert restore(request, wire, request_hash=request.binding_hash) == value


@pytest.mark.parametrize("unknown", [True, False])
def test_fragment_envelope_is_not_allowed_to_silently_hide_metadata(unknown):
    value, supports = supplied("text")
    parent = value.evidence[0]
    metadata = {
        "admission_policy": "bounded-fragments-v3",
        "source_declaration_changed": False,
        "materialization_hash": supports[0].materialization_hash,
        "projection_hash": supports[0].projection_hash,
    }
    if unknown:
        metadata["owner_fact"] = "must not disappear"
    else:
        metadata["projection_hash"] = "wrong-version"
    parent = parent.model_copy(
        update={
            "evidence_id": "fragment-bundle:parent",
            "source_metadata": metadata,
            "content": {
                "incomplete": True,
                "ranges": [{"field": "/content", "value": "text", "bytes": [0, 4], "characters": [0, 4]}],
            },
        }
    )
    value = value.model_copy(update={"evidence": (parent,)})
    supports = tuple(s.model_copy(update={"evidence_id": parent.evidence_id}) for s in supports)
    with pytest.raises(EvaluationError, match="v9_unrecognized_fragment_envelope|v9_fragment_provenance_mismatch"):
        prepare(value, supports)
