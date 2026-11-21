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

"""Score semantic parent coverage without changing historical whole-sentence scores."""

from __future__ import annotations

from collections import defaultdict
from typing import Literal

from powercontext.builtin.artifacts.atomic_memory.extraction import AtomicMemoryExtractionOutput
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from ..manifests import EvaluationError, case_catalog
from ..models import digest
from ..v7.fidelity import quote_inventory
from ..v8.workflow import covers
from ..v9_live.scoring import atoms


class Judgement(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    supported: StrictBool
    atomic: StrictBool
    false_attribution: StrictBool
    entailed_atom_ids: list[str]


class Review(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    reviewer: str = Field(min_length=1)
    reviewer_kind: Literal["human", "assistant"]
    output_hash: str
    context_hash: str
    judgements: dict[str, Judgement]


def ground_truth(case):
    parents = {p["claim_hash"]: p for p in case_catalog(case)["claims"] if p["required"]}
    children = []
    for atom in atoms(case):
        matching = [
            key
            for key, p in parents.items()
            if p["source_hash"] == atom["source_hash"]
            and p["field_hash"] == atom["field_hash"]
            and p["byte_start"] <= atom["byte_start"] < atom["byte_end"] <= p["byte_end"]
        ]
        if len(matching) != 1:
            raise EvaluationError("v10_atom_parent_not_unique")
        children.append({**atom, "parent_claim_hash": matching[0]})
    if len({a["id"] for a in children}) != len(children):
        raise EvaluationError("v10_duplicate_atom_id")
    if set(parents) != {a["parent_claim_hash"] for a in children}:
        raise EvaluationError("v10_parent_without_children")
    return children


def candidate_key(candidate, index):
    return digest({"index": index, "candidate": candidate.model_dump(mode="json")})


def review_binding(case, value, supports, output):
    return digest(
        {
            "contract": "evidence-admission.v10.parent-atom-review.v1",
            "case": case.model_dump(mode="json"),
            "input": value.model_dump(mode="json"),
            "supports": [s.model_dump(mode="json") for s in supports],
            "output": output.model_dump(mode="json"),
        }
    )


def supported_atom(atom, quotes):
    groups = defaultdict(list)
    for quote in quotes:
        groups[(quote.support.materialization_hash, quote.support.projection_hash)].append(quote)
    return any(covers(group, atom, atom["text"]) for group in groups.values())


def score(case, value, supports, output, raw_review):
    """Unknown meanings remain unresolved. No response rewriting or automatic semantic approval."""
    output = AtomicMemoryExtractionOutput.model_validate(output)
    review = Review.model_validate(raw_review)
    if review.output_hash != digest(output.model_dump(mode="json")):
        raise EvaluationError("v10_review_output_mismatch")
    if review.context_hash != review_binding(case, value, supports, output):
        raise EvaluationError("v10_review_context_mismatch")
    known_keys = {candidate_key(c, i) for i, c in enumerate(output.candidates)}
    if not set(review.judgements) <= known_keys:
        raise EvaluationError("v10_unknown_review_candidate")
    expected = {a["id"]: a for a in ground_truth(case)}
    grouped = defaultdict(set)
    for a in expected.values():
        grouped[a["parent_claim_hash"]].add(a["id"])
    supplied = {e.evidence_id for e in value.evidence}
    quotes = quote_inventory(value, supports)
    retained, literals = set(), set()
    unsupported = false_attribution = nonatomic = literal_loss = invalid_support = unresolved = 0
    for index, candidate in enumerate(output.candidates):
        cited = set(candidate.evidence_ids)
        local = tuple(q for q in quotes if q.evidence_id in cited)
        invalid = not cited <= supplied or any(not any(q.evidence_id == e for q in local) for e in cited)
        invalid_support += invalid
        forbidden = [c for c in case.forbidden if candidate.text in (c.text, *c.accepted_candidates)]
        judgement = review.judgements.get(candidate_key(candidate, index))
        if judgement is None:
            unresolved += 1
            unsupported += bool(forbidden)
            false_attribution += any(c.current_user for c in forbidden)
            continue
        ids = judgement.entailed_atom_ids
        if len(ids) != len(set(ids)) or not set(ids) <= expected.keys():
            raise EvaluationError("v10_invalid_review_atoms")
        bad_attribution = judgement.false_attribution or any(c.current_user for c in forbidden)
        supported = judgement.supported and not invalid and not forbidden and not bad_attribution
        supported = supported and all(supported_atom(expected[a], local) for a in ids)
        unsupported += not supported
        false_attribution += bad_attribution
        nonatomic += not judgement.atomic or len(ids) > 1
        if not supported:
            continue
        for atom_id in ids:
            required = expected[atom_id]["literals"]
            matching = [i for i, literal in enumerate(required) if literal.encode() in candidate.text.encode()]
            literals.update((atom_id, i) for i in matching)
            literal_loss += len(required) - len(matching)
            if len(required) == len(matching):
                retained.add(atom_id)
    retained_parents = {p for p, children in grouped.items() if children <= retained}
    structural_pass = not (unsupported or false_attribution or invalid_support or unresolved or literal_loss)
    semantic_complete = retained == set(expected) and structural_pass and (bool(expected) or not output.candidates)
    return {
        "required_parent_claims": len(grouped),
        "retained_parent_claims": len(retained_parents),
        "retained_parent_hashes": sorted(retained_parents),
        "required_atoms": len(expected),
        "retained_atoms": len(retained),
        "retained_atom_hashes": sorted(digest(a) for a in retained),
        "required_literals": sum(len(a["literals"]) for a in expected.values()),
        "retained_literals": len(literals),
        "unsupported_candidates": unsupported,
        "false_attribution_candidates": false_attribution,
        "invalid_support_candidates": invalid_support,
        "unresolved_candidates": unresolved,
        "nonatomic_candidates": nonatomic,
        "literal_mismatches": literal_loss,
        "semantic_complete": semantic_complete,
        "atomicity_passed": nonatomic == 0,
        "diagnostic_success": semantic_complete and nonatomic == 0,
        "reviewer_kind": review.reviewer_kind,
        "formal_acceptance": False,
        "formal_blockers": ["offline_only", "live_coverage_and_committed_freeze_not_established"],
    }
