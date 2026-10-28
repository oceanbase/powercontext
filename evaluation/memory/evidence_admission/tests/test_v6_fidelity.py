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
from powercontext.builtin.artifacts.memory.canonical import normalize_text
from powercontext.sources import SourceRef

from powercontext_eval_evidence_admission.manifests import EvaluationError, load_suite
from powercontext_eval_evidence_admission.models import Support, digest
from powercontext_eval_evidence_admission.v6.fidelity import (
    TemplateOutput,
    copy_span,
    quote_inventory,
    render,
    request_cost,
)
from powercontext_eval_evidence_admission.v6.workflow import cost_budget_status


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


@pytest.mark.parametrize(
    "text",
    [
        "e\u0301",
        "👩🏽‍💻",
        "说明\r\n下一行",
        "  前后空白\r\n",
        "deployctl --retries 03",
        "/srv/synthetic/app.toml",
        "0.075",
    ],
)
def test_explicit_copy_preserves_bytes_through_json_roundtrip(text):
    value, supports = supplied(text)
    quotes = quote_inventory(value, supports)
    output = TemplateOutput.model_validate_json(template(quotes[0]).model_dump_json())
    rendered = render(output, quotes)
    assert rendered.candidates[0].text.encode() == b"Exact value: " + text.encode()
    assert json.loads(rendered.model_dump_json())["candidates"][0]["text"].encode().endswith(text.encode())


@pytest.mark.parametrize(
    ("text", "start", "end"), [("中文", 1, 3), ("e\u0301", 0, 1), ("👩🏽‍💻", 0, 4), ("👩🏽‍💻", 0, 8), ("\r\nX", 0, 1)]
)
def test_copy_rejects_utf8_combining_emoji_and_crlf_splits(text, start, end):
    value, supports = supplied(text)
    with pytest.raises(EvaluationError, match="copy_splits"):
        copy_span(quote_inventory(value, supports)[0], start, end)


def test_quotes_bind_input_version_field_and_source_not_identical_string():
    value, supports = supplied("same")
    old = quote_inventory(value, supports)[0]
    changed = quote_inventory(value, (supports[0].model_copy(update={"materialization_hash": digest("material-2")}),))
    with pytest.raises(EvaluationError, match="copy_without_candidate_support"):
        render(template(old), changed)
    wrong = value.model_copy(
        update={
            "evidence": (
                value.evidence[0].model_copy(
                    update={"source_ref": SourceRef(source_type="content", source_id="other")}
                ),
            )
        }
    )
    with pytest.raises(EvaluationError, match="unique_admitted_support"):
        quote_inventory(wrong, supports)


def test_copy_does_not_backfill_missing_parts_or_approve_whole_source():
    value, supports = supplied("old value; new value")
    quote = quote_inventory(value, supports)[0]
    assert render(template(quote, 0, 9), (quote,)).candidates[0].text == "Exact value: old value"
    assert render(TemplateOutput(), (quote,)).candidates == ()
    large, large_supports = supplied("X" * 1025)
    q = quote_inventory(large, large_supports)[0]
    with pytest.raises(EvaluationError, match="too_large"):
        render(template(q), (q,))


def test_copy_must_be_cited_by_its_own_candidate_and_schema_cost_is_included():
    value, supports = supplied("a")
    quote = quote_inventory(value, supports)[0]
    changed = replace(quote, evidence_id="source:other", quote_id="other")
    data = template(quote).model_dump(mode="json")
    data["candidates"][0]["parts"][1]["quote_id"] = "other"
    with pytest.raises(EvaluationError, match="copy_without_candidate_support"):
        render(TemplateOutput.model_validate(data), (quote, changed))
    cost = request_cost(value, (quote,))
    assert cost["serialized_request_bytes"] > len(value.model_dump_json().encode())
    assert cost["schema_hash"] == digest(TemplateOutput.model_json_schema())


def test_persistence_normalization_remains_a_different_contract():
    raw = "  e\u0301\r\ninner\r\n"
    value, supports = supplied(raw)
    rendered = render(template(quote_inventory(value, supports)[0]), quote_inventory(value, supports))
    assert rendered.candidates[0].text.endswith(raw)
    assert normalize_text(raw) == "é\r\ninner"  # Internal CRLF is preserved; outer whitespace is stripped.


def test_full_field_index_is_limited_to_its_admitted_support_ranges():
    value, supports = supplied("head-middle-tail")
    first = supports[0].model_copy(update={"byte_end": 4, "char_end": 4})
    last = supports[0].model_copy(update={"byte_start": 12, "char_start": 12})
    quotes = quote_inventory(value, (first, last))
    assert [q.value for q in quotes] == ["head", "tail"]
    assert quotes[1].index()["value_bytes"] == [12, 16]
    with pytest.raises(EvaluationError, match="out_of_bounds"):
        copy_span(quotes[0], 0, len("head-middle-tail"))


def test_dry_run_enforces_cumulative_budget_even_when_every_call_fits():
    policy = load_suite(experiment_version="v5").experiment.v3
    equal = [{"estimated_input_tokens": 512}] * 16
    assert not any(cost_budget_status(equal, 1024, policy).values())
    excess = [{"estimated_input_tokens": 513}] * 16
    assert cost_budget_status(excess, 1024, policy) == {"per_call": False, "cumulative": True, "call_count": False}
    assert cost_budget_status(equal + equal[:1], 1024, policy)["call_count"]
