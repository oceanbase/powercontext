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

"""Parent meaning is complete only when every child survives at its actual source range."""

from __future__ import annotations

import asyncio
import json

import pytest
from powercontext.builtin.artifacts.atomic_memory.extraction import AtomicMemoryCandidate, AtomicMemoryExtractionOutput

from powercontext_eval_evidence_admission.manifests import EvaluationError
from powercontext_eval_evidence_admission.models import digest
from powercontext_eval_evidence_admission.v10.scoring import (
    candidate_key,
    ground_truth,
    review_binding,
    score,
    supported_atom,
)

from .test_v10_pipeline import prepared

TEXTS = [
    "Deployment command is `deployctl --region cn-2 --retries 03`.",
    "Deployment configuration path is `/srv/synthetic/app.toml`.",
    "Deployment job is `batch_A-17`.",
    "Deployment limit is `0.075`.",
]


def data(*, compound=False, missing=False):
    case, sample, inputs = asyncio.run(prepared())
    expected = ground_truth(case)
    texts = TEXTS[:2] + ["Deployment job is `batch_A-17` with limit `0.075`."] if compound else list(TEXTS)
    if missing:
        texts.pop()
    value = inputs[0]
    output = AtomicMemoryExtractionOutput(
        candidates=tuple(
            AtomicMemoryCandidate(
                kind="fact", text=text, description=(text)[:200], evidence_ids=(value.evidence[0].evidence_id,)
            )
            for text in texts
        )
    )
    judgements = {}
    for index, candidate in enumerate(output.candidates):
        ids = [a["id"] for a in expected[index:]] if compound and index == 2 else [expected[index]["id"]]
        judgements[candidate_key(candidate, index)] = {
            "supported": True,
            "atomic": len(ids) == 1,
            "false_attribution": False,
            "entailed_atom_ids": ids,
        }
    review = {
        "reviewer": "offline-fixture",
        "reviewer_kind": "assistant",
        "output_hash": digest(output.model_dump(mode="json")),
        "context_hash": review_binding(case, value, sample.supports, output),
        "judgements": judgements,
    }
    return case, value, sample.supports, output, review


def test_split_parent_semantics_complete_without_changing_old_denominator():
    result = score(*data())
    assert result["required_parent_claims"] == result["retained_parent_claims"] == 1
    assert result["required_atoms"] == result["retained_atoms"] == 4
    assert result["retained_literals"] == 4 and result["diagnostic_success"]
    assert not result["formal_acceptance"]


def test_known_compound_failure_keeps_semantics_but_fails_atomicity():
    result = score(*data(compound=True))
    assert result["retained_parent_claims"] == 1 and result["retained_atoms"] == 4
    assert result["semantic_complete"] and result["nonatomic_candidates"] == 1
    assert not result["diagnostic_success"]


def test_parent_requires_every_child_not_any_child():
    result = score(*data(missing=True))
    assert result["retained_atoms"] == 3 and result["retained_parent_claims"] == 0
    assert not result["semantic_complete"]


@pytest.mark.parametrize("mode", ["unknown", "literal", "false-attribution", "forged-evidence", "source-bytes"])
def test_critical_losses_cannot_be_hidden_by_semantic_review(mode):
    case, value, supports, output, review = data()
    if mode == "unknown":
        review["judgements"].pop(next(iter(review["judgements"])))
    elif mode == "false-attribution":
        next(iter(review["judgements"].values()))["false_attribution"] = True
    elif mode == "source-bytes":
        content = value.evidence[0].content
        assert "batch_A-17" in str(content)
        raw = value.model_dump(mode="json")
        raw["evidence"][0]["content"] = json.loads(json.dumps(content).replace("batch_A-17", "batch_B-99"))
        value = type(value).model_validate(raw)
    else:
        old = output.candidates[0]
        changed = old.model_copy(
            update={"text": old.text.replace("03", "3")} if mode == "literal" else {"evidence_ids": ("unsupplied",)}
        )
        output = output.model_copy(update={"candidates": (changed, *output.candidates[1:])})
        judgement = review["judgements"].pop(candidate_key(old, 0))
        review["judgements"][candidate_key(changed, 0)] = judgement
        review["output_hash"] = digest(output.model_dump(mode="json"))
    review["context_hash"] = review_binding(case, value, supports, output)
    result = score(case, value, supports, output, review)
    assert not result["diagnostic_success"]
    assert result["retained_parent_claims"] == 0


def test_review_is_bound_to_exact_output_and_candidate_identity():
    items = list(data())
    items[-1]["output_hash"] = "different"
    with pytest.raises(EvaluationError, match="v10_review_output_mismatch"):
        score(*items)


def test_unreviewed_negative_control_is_not_treated_as_supported():
    case, sample, inputs = asyncio.run(prepared("negative-control"))
    value = inputs[0]
    output = AtomicMemoryExtractionOutput(
        candidates=(
            AtomicMemoryCandidate(
                kind="fact",
                text="Invented durable user fact.",
                description=("Invented durable user fact.")[:200],
                evidence_ids=(value.evidence[0].evidence_id,),
            ),
        )
    )
    result = score(
        case,
        value,
        sample.supports,
        output,
        {
            "reviewer": "offline-fixture",
            "reviewer_kind": "assistant",
            "output_hash": digest(output.model_dump(mode="json")),
            "context_hash": review_binding(case, value, sample.supports, output),
            "judgements": {},
        },
    )
    assert result["unresolved_candidates"] == 1 and not result["diagnostic_success"]


def test_review_cannot_be_reused_with_different_admitted_ranges():
    case, value, supports, output, review = data()
    changed = tuple(s.model_copy(update={"materialization_hash": digest("another-materialization")}) for s in supports)
    with pytest.raises(EvaluationError, match="v10_review_context_mismatch"):
        score(case, value, changed, output, review)


def test_atom_ranges_from_different_materializations_cannot_be_joined():
    from dataclasses import replace

    from powercontext_eval_evidence_admission.v7.fidelity import quote_inventory

    case, value, supports, _, _ = data()
    atom = ground_truth(case)[0]
    original = next(q for q in quote_inventory(value, supports) if atom["text"] in q.value)
    middle = len(atom["text"].encode()) // 2
    left = replace(original, source_byte_start=atom["byte_start"], value=atom["text"][:middle])
    right = replace(
        original,
        source_byte_start=atom["byte_start"] + middle,
        value=atom["text"][middle:],
        support=original.support.model_copy(update={"materialization_hash": digest("different")}),
    )
    assert not supported_atom(atom, [left, right])
    assert supported_atom(atom, [left, replace(right, support=left.support)])
