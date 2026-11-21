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

"""Contract diagnostics cannot repair model output or override legacy v5 scores."""

from typing import Any

from powercontext.builtin.artifacts.atomic_memory.extraction import AtomicMemoryCandidate

from ..manifests import case_catalog
from ..models import Case, Sample, digest

ATOM_IDS = ("deployment-command", "deployment-config-path", "deployment-job", "deployment-limit")


def cited_parent_supported(case: Case, sample: Sample, candidate: AtomicMemoryCandidate) -> bool:
    if sample.case_hash != digest(case.model_dump(mode="json")) or not set(candidate.evidence_ids) <= {
        s.evidence_id for s in sample.supports
    }:
        return False
    span = next(row for row in case_catalog(case)["claims"] if row["required"])
    ranges = sorted(
        (s.byte_start, s.byte_end)
        for s in sample.supports
        if s.evidence_id in candidate.evidence_ids
        and s.source_hash == span["source_hash"]
        and s.field_hash == span["field_hash"]
    )
    merged = []
    for start, end in ranges:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    return any(start <= span["byte_start"] and span["byte_end"] <= end for start, end in merged)


def score_literal_atoms(
    case: Case, sample: Sample, candidates: tuple[AtomicMemoryCandidate, ...], reviews: dict[str, list[int]]
) -> dict[str, Any]:
    """Only explicit semantic reviews permit child matching; raw string presence is insufficient."""
    if case.case_id != "exact-literals":
        raise ValueError("literal atom contract applies only to exact-literals")
    literals = case.required[0].exact_literals
    retained = set()
    unresolved = invalid_support = literal_mismatch = 0
    composite = 0
    for candidate in candidates:
        key = digest({"case": case.case_id, "kind": candidate.kind, "text": candidate.text})
        reviewed = reviews.get(key)
        if candidate.text in (case.required[0].text, *case.required[0].accepted_candidates):
            reviewed = list(range(4))
        if reviewed is None:
            unresolved += 1
            continue
        if not cited_parent_supported(case, sample, candidate):
            invalid_support += 1
            continue
        if any(type(index) is not int or index not in range(4) for index in reviewed):
            raise ValueError("invalid atom review")
        kept = [index for index in reviewed if literals[index] in candidate.text]
        literal_mismatch += len(set(reviewed) - set(kept))
        composite += len(kept) > 1
        retained.update(kept)
    return {
        "required_atom_ids": list(ATOM_IDS),
        "retained_atom_ids": [ATOM_IDS[i] for i in sorted(retained)],
        "unresolved_candidates": unresolved,
        "invalid_support_candidates": invalid_support,
        "literal_mismatches": literal_mismatch,
        "composite_candidates": composite,
        "scope": "rescoring_recorded_v5_output_not_new_model_generation",
        "production_authorized": False,
    }
