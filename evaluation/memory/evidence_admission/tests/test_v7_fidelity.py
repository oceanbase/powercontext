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

import pytest
from powercontext.builtin.artifacts.atomic_memory.extraction import AtomicMemoryEvidence, AtomicMemoryExtractionInput
from powercontext.sources import SourceRef

from powercontext_eval_evidence_admission.manifests import EvaluationError
from powercontext_eval_evidence_admission.models import Support, digest
from powercontext_eval_evidence_admission.v6.fidelity import quote_inventory as v6_quote_inventory
from powercontext_eval_evidence_admission.v6.fidelity import request_cost as v6_request_cost
from powercontext_eval_evidence_admission.v7.fidelity import (
    TemplateOutput,
    binding,
    copy_span,
    cost_ledger,
    quote_inventory,
    render,
)


def supplied(text):
    evidence = AtomicMemoryEvidence(
        evidence_id="source:1",
        source_ref=SourceRef(source_type="content", source_id="source-a"),
        journal_position=1,
        content={"content": text},
        source_metadata={},
    )
    value = AtomicMemoryExtractionInput(evidence=(evidence,))
    support = Support(
        evidence_id=evidence.evidence_id,
        source_hash=digest("source-a"),
        field_hash=digest("/content/content"),
        materialization_hash=digest("material-1"),
        projection_hash=digest(evidence.model_dump(mode="json")),
        byte_start=0,
        byte_end=len(text.encode()),
        char_start=0,
        char_end=len(text),
    )
    return value, (support,)


def template(quote, start=0, end=None):
    return TemplateOutput.model_validate(
        {
            "candidates": [
                {
                    "kind": "fact",
                    "description": "Synthetic protocol candidate",
                    "evidence_ids": [quote.evidence_id],
                    "parts": [
                        {"type": "text", "text": "Exact value: "},
                        {
                            "type": "copy",
                            "quote_id": quote.quote_id,
                            "byte_start": start,
                            "byte_end": len(quote.value.encode()) if end is None else end,
                        },
                    ],
                }
            ]
        }
    )


def test_short_quote_ids_and_minimal_model_facing_index():
    value, supports = supplied("alpha beta gamma")
    quotes = quote_inventory(value, supports)
    assert [q.quote_id for q in quotes] == ["q0"]
    index = quotes[0].index()
    assert set(index) == {"q", "e", "p", "b"}
    assert index["q"] == "q0" and index["e"] == 0
    # Provenance hashes stay internal; a pointer unambiguously locates the value.
    serialized = json.dumps(index)
    assert "sha256" not in serialized and "source" not in serialized and "pointer" not in serialized
    # Full identity stays internal only.
    internal = quotes[0].identity()
    assert internal["evidence_id"] == "source:1"
    assert len(internal["support"]["field_hash"]) == 64


def test_v7_cost_ledger_is_cheaper_than_v6_for_the_same_input():
    value, supports = supplied("deployctl --region cn --retries 03 " + "x" * 200)
    v6_cost = v6_request_cost(value, v6_quote_inventory(value, supports))
    v7_cost = cost_ledger(value, quote_inventory(value, supports))
    assert set(v7_cost["components"]) == {"instructions", "evidence_body", "quote_index", "output_schema"}
    assert sum(v7_cost["components"].values()) == v7_cost["single_estimated_input_tokens"]
    assert v7_cost["components"]["quote_index"] > 0
    assert v7_cost["single_estimated_input_tokens"] < v6_cost["estimated_input_tokens"]


def test_render_resolves_short_ids_and_preserves_bytes():
    text = "e\u0301\r\n下一行"
    value, supports = supplied(text)
    quotes = quote_inventory(value, supports)
    output = TemplateOutput.model_validate_json(template(quotes[0]).model_dump_json())
    rendered = render(output, value, quotes, request_hash=binding(value, quotes))
    assert rendered.candidates[0].text.encode() == b"Exact value: " + text.encode()


def test_short_ids_are_request_local_and_do_not_alias_across_requests():
    a_value, a_supports = supplied("first")
    b_value, b_supports = supplied("second")
    a_quote = quote_inventory(a_value, a_supports)[0]
    b_quote = quote_inventory(b_value, b_supports)[0]
    assert a_quote.quote_id == b_quote.quote_id == "q0"
    # Two requests share the short id but carry different full identities.
    assert binding(a_value, (a_quote,)) != binding(b_value, (b_quote,))
    # Rendering request A's output against request B's quotes must fail the request binding.
    data = template(a_quote)
    with pytest.raises(EvaluationError, match="quote_request_binding_mismatch"):
        render(data, b_value, (b_quote,), request_hash=binding(a_value, (a_quote,)))
    # Same-request rendering resolves the short id to the correct span.
    assert (
        render(data, a_value, (a_quote,), request_hash=binding(a_value, (a_quote,))).candidates[0].text
        == "Exact value: first"
    )
    # Cross-source/version reuse of the same short id is also rejected.
    changed = replace(b_quote, evidence_id="source:other", quote_id="q9")
    data2 = template(a_quote).model_dump(mode="json")
    data2["candidates"][0]["parts"][1]["quote_id"] = "q9"
    with pytest.raises(EvaluationError, match="copy_without_candidate_support"):
        render(
            TemplateOutput.model_validate(data2),
            a_value,
            (a_quote, changed),
            request_hash=binding(a_value, (a_quote, changed)),
        )


@pytest.mark.parametrize(
    ("text", "start", "end"), [("中文", 1, 3), ("e\u0301", 0, 1), ("👩🏽‍💻", 0, 4), ("\r\nX", 0, 1)]
)
def test_copy_rejects_utf8_combining_emoji_and_crlf_splits(text, start, end):
    value, supports = supplied(text)
    with pytest.raises(EvaluationError, match="copy_splits"):
        copy_span(quote_inventory(value, supports)[0], start, end)


def test_copy_does_not_backfill_and_missing_span_stays_missing():
    value, supports = supplied("old value; new value")
    quote = quote_inventory(value, supports)[0]
    assert (
        render(template(quote, 0, 9), value, (quote,), request_hash=binding(value, (quote,))).candidates[0].text
        == "Exact value: old value"
    )
    assert render(TemplateOutput(), value, (quote,), request_hash=binding(value, (quote,))).candidates == ()


def test_pointer_disambiguates_metadata_and_body_and_escaped_keys():
    from powercontext_eval_evidence_admission.manifests import field_value

    value, supports = supplied("body")
    evidence = value.evidence[0].model_copy(update={"content": {"content": "body", "a/b~": "tail"}})
    value = value.model_copy(update={"evidence": (evidence,)})
    supports += (supports[0].model_copy(update={"field_hash": digest("/content/a~1b~0"), "byte_end": 4}),)
    quotes = quote_inventory(value, supports)
    assert len(quotes) == 2
    for quote in quotes:
        index = quote.index()
        raw = field_value(value.evidence[index["e"]].model_dump(mode="json"), index["p"]).encode()
        assert raw[slice(*index["b"])] == quote.value.encode()


def test_binding_includes_full_request_context_and_mapping():
    value, supports = supplied("first")
    quotes = quote_inventory(value, supports)
    changed = value.model_copy(update={"evidence": (value.evidence[0].model_copy(update={"journal_position": 2}),)})
    with pytest.raises(EvaluationError, match="quote_request_binding_mismatch"):
        render(template(quotes[0]), changed, quotes, request_hash=binding(value, quotes))
    renamed = (replace(quotes[0], quote_id="q9"),)
    with pytest.raises(EvaluationError, match="quote_request_binding_mismatch"):
        render(TemplateOutput(), value, renamed, request_hash=binding(value, quotes))


def test_canonical_full_request_is_the_ledger_total():
    from powercontext.builtin.inference import character_token_estimator

    from powercontext_eval_evidence_admission.models import canonical
    from powercontext_eval_evidence_admission.v7.fidelity import INSTRUCTIONS

    value, supports = supplied('a\\b\n"quoted"')
    quotes = quote_inventory(value, supports)
    encoded = canonical(
        {
            "instructions": INSTRUCTIONS,
            "input": {
                "evidence": value.model_dump(mode="json")["evidence"],
                "quote_index": [q.index() for q in quotes],
            },
            "output_schema": TemplateOutput.model_json_schema(),
        }
    )
    cost = cost_ledger(value, quotes)
    assert cost["single_estimated_input_tokens"] == character_token_estimator().estimate(encoded.decode())
    assert cost["serialized_request_bytes"] == len(encoded)
