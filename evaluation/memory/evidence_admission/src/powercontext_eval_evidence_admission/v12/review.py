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

"""Explicit semantic reviews and unchanged legacy scores; no automatic live judgements."""

from __future__ import annotations

from powercontext.builtin.artifacts.atomic_memory.extraction import (
    AtomicMemoryExtractionInput,
    AtomicMemoryExtractionOutput,
)

from ..admission import Admission, Fragment, leaves
from ..manifests import EvaluationError
from ..models import Case, Sample, Support, digest
from ..scoring import score_generation
from ..v10.scoring import candidate_key, ground_truth, review_binding, score
from .session import call_request


def packet(call, output):
    request = call_request(call)
    case = Case.model_validate(call["case"])
    value = AtomicMemoryExtractionOutput.model_validate(output)
    return {
        "sample_id": call["sample_id"],
        "case": call["case"],
        "input": call["value"],
        "supports": call["supports"],
        "output": output,
        "ground_truth": ground_truth(case),
        "review_template": {
            "reviewer": "assistant",
            "reviewer_kind": "assistant",
            "output_hash": digest(output),
            "context_hash": review_binding(case, request.encoded.value, request.encoded.supports, value),
            "judgements": {
                candidate_key(c, i): {
                    "supported": None,
                    "atomic": None,
                    "false_attribution": None,
                    "entailed_atom_ids": [],
                }
                for i, c in enumerate(value.candidates)
            },
        },
    }


def legacy_score(call, output):
    original = AtomicMemoryExtractionInput.model_validate(call["full"])
    supports = tuple(Support.model_validate(s) for s in call["supports"])
    fragments = []
    for evidence in original.evidence:
        for pointer, leaf in leaves({"content": evidence.content, "source_metadata": evidence.source_metadata}):
            for support in supports:
                if support.source_hash != digest(evidence.source_ref.source_id) or support.field_hash != digest(
                    pointer
                ):
                    continue
                value = leaf[support.char_start : support.char_end] if isinstance(leaf, str) else leaf
                fragments.append(
                    Fragment(
                        evidence,
                        support.materialization_hash,
                        support.projection_hash,
                        pointer,
                        0,
                        value,
                        support.byte_start,
                        support.byte_end,
                        support.char_start,
                        support.char_end,
                    )
                )
    admitted = tuple(fragments)
    sample = Sample.model_validate(call["sample"])
    admission = Admission((), admitted, admitted, supports, (), sample.metrics, "bounded", True)
    return score_generation(
        Case.model_validate(call["case"]),
        original,
        admission,
        AtomicMemoryExtractionOutput.model_validate(output).candidates,
    ).model_dump(mode="json")


def evaluate(call, output, review, *, copy_parts):
    if not review.get("reviewer", "").strip():
        raise EvaluationError("v12_reviewer_required")
    request = call_request(call)
    scored = score(Case.model_validate(call["case"]), request.encoded.value, request.encoded.supports, output, review)
    return {
        "v10_score": scored,
        "legacy_score": legacy_score(call, output),
        "copy_parts": copy_parts,
        "copy_path_observed": copy_parts > 0,
        "candidate_gate_passed": scored["diagnostic_success"] and copy_parts > 0,
        "independent_human_review": review["reviewer_kind"] == "human",
        "formal_acceptance": False,
        "production_authorized": False,
    }
