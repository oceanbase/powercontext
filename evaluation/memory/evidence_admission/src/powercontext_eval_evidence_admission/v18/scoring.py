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

"""Exact ground-truth checks first; unknown semantics are never automatically approved."""

from __future__ import annotations

from collections import Counter

from ..models import digest
from ..v7.fidelity import quote_inventory
from ..v10.scoring import candidate_key, ground_truth, review_binding, score, supported_atom


def automatic_review(case, value, supports, output):
    atoms = ground_truth(case)
    quotes = quote_inventory(value, supports)
    judgements = {}
    for index, candidate in enumerate(output.candidates):
        local = tuple(q for q in quotes if q.evidence_id in candidate.evidence_ids)
        matches = [a for a in atoms if candidate.text == a["text"] and supported_atom(a, local)]
        # Only exact complete ground-truth assertions are automatically established.
        # Paraphrases/substrings and all extra assertions require a bound review.
        if len(matches) == 1:
            judgements[candidate_key(candidate, index)] = {
                "supported": True,
                "atomic": True,
                "false_attribution": False,
                "entailed_atom_ids": [matches[0]["id"]],
            }
        elif any(candidate.text in (f.text, *f.accepted_candidates) for f in case.forbidden):
            judgements[candidate_key(candidate, index)] = {
                "supported": False,
                "atomic": True,
                "false_attribution": any(
                    f.current_user for f in case.forbidden if candidate.text in (f.text, *f.accepted_candidates)
                ),
                "entailed_atom_ids": [],
            }
    return {
        "reviewer": "deterministic-exact-ground-truth",
        "reviewer_kind": "assistant",  # conservative v10 vocabulary; not an independent human claim
        "output_hash": digest(output.model_dump(mode="json")),
        "context_hash": review_binding(case, value, supports, output),
        "judgements": judgements,
    }


def score_output(case, value, supports, output, review=None):
    review = review or automatic_review(case, value, supports, output)
    result = score(case, value, supports, output.model_dump(mode="json"), review)
    # v10's historical execution flags do not describe a v18 run; only its bound scores are inherited.
    result.pop("formal_acceptance")
    result.pop("formal_blockers")
    texts = [c.text for c in output.candidates]
    grams = [{t[i : i + 2] for i in range(max(1, len(t) - 1))} for t in texts]
    result.update(
        candidates=len(texts),
        exact_duplicates=sum(n - 1 for n in Counter(texts).values()),
        near_duplicates=sum(
            any(texts[i] != texts[j] and len(g & grams[j]) / max(1, len(g | grams[j])) >= 0.8 for j in range(i))
            for i, g in enumerate(grams)
        ),
        candidate_hashes=[digest(c.model_dump(mode="json")) for c in output.candidates],
        retained_atom_ids=[a["id"] for a in ground_truth(case) if digest(a["id"]) in result["retained_atom_hashes"]],
        review_hash=digest(review),
        score_kind=(
            "deterministic_exact_ground_truth"
            if review == automatic_review(case, value, supports, output)
            else "human_bound_review"
            if review["reviewer_kind"] == "human"
            else "assistant_diagnostic"
        ),
    )
    # Literal occurrence alone is descriptive; it does not establish an entailed/supported claim.
    result["raw_literal_occurrences"] = sum(
        any(literal.encode() in text.encode() for text in texts)
        for atom in ground_truth(case)
        for literal in atom["literals"]
    )
    literal_ids = [
        (digest({"atom": atom["id"], "literal_index": index}), literal)
        for atom in ground_truth(case)
        for index, literal in enumerate(atom["literals"])
    ]
    result["required_literal_hashes"] = sorted(key for key, _ in literal_ids)
    result["raw_present_literal_hashes"] = sorted(
        key for key, literal in literal_ids if any(literal.encode() in text.encode() for text in texts)
    )
    return result, review
