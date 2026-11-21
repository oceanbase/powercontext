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

"""Explicit semantic reviews coexist with the unchanged legacy generation scorer."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from powercontext.builtin.artifacts.atomic_memory.extraction import (
    AtomicMemoryExtractionInput,
    AtomicMemoryExtractionOutput,
)
from pydantic import BaseModel, ConfigDict, StrictBool

from ..admission import Admission, Fragment, leaves
from ..manifests import EvaluationError, case_catalog
from ..models import Case, Sample, Support, digest
from ..scoring import score_generation
from .contract import contract


class CandidateReview(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    supported: StrictBool
    atomic: StrictBool
    entailed_atom_ids: list[str]


class Review(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    packet_hash: str
    reviewer: str
    independent_human_review: StrictBool
    candidates: dict[str, CandidateReview]


def atoms(case):
    result = []
    catalog = {c["claim_hash"]: c for c in case_catalog(case)["claims"] if c["required"]}
    for claim in case.required:
        span = catalog[digest(claim.claim_id)]
        pieces: list[dict[str, Any]]
        if case.case_id == "exact-literals":
            pieces = [
                {"id": a["id"], "text": a["text"], "literals": [claim.exact_literals[a["literal_index"]]]}
                for a in contract()["literal_atoms"]
            ]
        elif case.case_id == "unicode-boundaries":
            pieces = [
                {"id": a["id"], "text": a["text"], "literals": [claim.exact_literals[i] for i in a["literal_indexes"]]}
                for a in contract()["unicode_atoms"]
            ]
        else:
            pieces = [{"id": claim.claim_id, "text": claim.text, "literals": list(claim.exact_literals)}]
        for atom in pieces:
            start = span["byte_start"] + len(claim.text[: claim.text.index(atom["text"])].encode())
            result.append(
                {
                    **atom,
                    "source_hash": span["source_hash"],
                    "field_hash": span["field_hash"],
                    "byte_start": start,
                    "byte_end": start + len(atom["text"].encode()),
                }
            )
    return result


def atom_supported(atom, candidate, supports):
    groups = defaultdict(list)
    for support in supports:
        if (
            support.evidence_id in candidate.evidence_ids
            and support.source_hash == atom["source_hash"]
            and support.field_hash == atom["field_hash"]
        ):
            groups[(support.materialization_hash, support.projection_hash)].append(
                (support.byte_start, support.byte_end)
            )
    for ranges in groups.values():
        merged = []
        for start, end in sorted(ranges):
            if merged and start <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
            else:
                merged.append((start, end))
        if any(start <= atom["byte_start"] and atom["byte_end"] <= end for start, end in merged):
            return True
    return False


def candidate_rows(pair, outputs):
    for call in pair["calls"]:
        output = outputs[call["call_id"]]
        parsed = AtomicMemoryExtractionOutput.model_validate(output)
        for index, candidate in enumerate(parsed.candidates):
            key = digest(
                {
                    "case": digest(pair["case"]),
                    "sample": pair["pair_id"],
                    "call": call["call_id"],
                    "output_hash": digest(output),
                    "index": index,
                    "candidate": candidate.model_dump(mode="json"),
                }
            )
            yield key, call, candidate


def review_packet(pair, outputs):
    case = Case.model_validate(pair["case"])
    rows = list(candidate_rows(pair, outputs))
    return {
        "schema": "evidence-admission.v9-live.review-packet.v1",
        "pair_id": pair["pair_id"],
        "case_hash": digest(pair["case"]),
        "atoms": atoms(case),
        "candidate_groups": [
            {
                "review_id": key,
                "candidate": candidate.model_dump(mode="json"),
                "supplied_evidence": call["value"],
                "supplied_supports": call["supports"],
            }
            for key, call, candidate in sorted(rows, key=lambda r: r[0])
        ],
        "output_inventory_hash": digest({c["call_id"]: digest(outputs[c["call_id"]]) for c in pair["calls"]}),
        "contract_hash": digest(contract()["review"]),
        "review_instructions": "Check actual assertion, attribution, cited local evidence and independent atomicity. "
        "Do not approve from string presence alone. Mark unentailed atoms absent; preserve unknowns. "
        "Protocol labels are omitted; grouping and wording may still reveal them, so this is not guaranteed blind review.",
    }


def legacy_score(pair, outputs, full):
    """Rebuild the selected-span view and invoke the original scorer without semantic overrides."""
    case = Case.model_validate(pair["case"])
    sample = Sample.model_validate(pair["sample"])
    original = AtomicMemoryExtractionInput.model_validate(full)
    results = {}
    for protocol in ("v5", "v9"):
        calls = [c for c in pair["calls"] if c["protocol"] == protocol]
        supports = tuple(dict.fromkeys(Support.model_validate(s) for c in calls for s in c["supports"]))
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
        admission = Admission((), admitted, admitted, supports, (), sample.metrics, "bounded", True)
        candidates = tuple(
            candidate
            for c in calls
            for candidate in AtomicMemoryExtractionOutput.model_validate(outputs[c["call_id"]]).candidates
        )
        results[protocol] = score_generation(case, original, admission, candidates).model_dump(mode="json")
    return results


def score_pair(pair, outputs, raw_review, *, reviewer, full):
    packet = review_packet(pair, outputs)
    review = Review.model_validate(raw_review)
    rows = list(candidate_rows(pair, outputs))
    if (
        review.packet_hash != digest(packet)
        or review.reviewer != reviewer
        or review.independent_human_review
        or set(review.candidates) != {r[0] for r in rows}
    ):
        raise EvaluationError("review_identity_or_completeness_mismatch")
    case = Case.model_validate(pair["case"])
    sample = Sample.model_validate(pair["sample"])
    expected = {a["id"]: a for a in atoms(case)}
    results = {}
    for protocol in ("v5", "v9"):
        retained, literals = set(), set()
        unsupported = nonatomic = literal_loss = 0
        selected = [(key, call, candidate) for key, call, candidate in rows if call["protocol"] == protocol]
        for key, call, candidate in selected:
            judgement = review.candidates[key]
            ids = judgement.entailed_atom_ids
            if len(ids) != len(set(ids)) or not set(ids) <= expected.keys():
                raise EvaluationError("invalid_review_atom_ids")
            supports = tuple(Support.model_validate(s) for s in call["supports"])
            forbidden = any(candidate.text in (c.text, *c.accepted_candidates) for c in case.forbidden)
            supported = (
                judgement.supported
                and not forbidden
                and all(atom_supported(expected[i], candidate, supports) for i in ids)
            )
            unsupported += not supported
            nonatomic += not judgement.atomic or len(ids) > 1
            if not supported:
                continue
            for key_id in ids:
                required = expected[key_id]["literals"]
                matching = [i for i, text in enumerate(required) if text.encode() in candidate.text.encode()]
                literals.update((key_id, i) for i in matching)
                literal_loss += len(required) - len(matching)
                if len(required) == len(matching):
                    retained.add(key_id)
        eligible = bool(sample.preparation and sample.preparation.required_content_fits)
        passed = (
            eligible
            and retained == set(expected)
            and not unsupported
            and not nonatomic
            and not literal_loss
            and (bool(expected) or not selected)
        )
        results[protocol] = {
            "required_atoms": len(expected),
            "retained_atoms": len(retained),
            "required_literals": sum(len(a["literals"]) for a in expected.values()),
            "retained_literals": len(literals),
            "unsupported_candidates": unsupported,
            "nonatomic_candidates": nonatomic,
            "literal_mismatches": literal_loss,
            "complete_eligible": eligible,
            "complete_success": passed,
            "unresolved_candidates": 0,
        }
    legacy = legacy_score(pair, outputs, full)
    # Ineligible cases remain partial diagnostics; they cannot count as complete successes.
    quality = results["v9"]
    safe_partial = not (
        quality["unsupported_candidates"] or quality["nonatomic_candidates"] or quality["literal_mismatches"]
    )
    advance = quality["complete_success"] if quality["complete_eligible"] else safe_partial
    return {
        "pair_id": pair["pair_id"],
        "reviewer": review.reviewer,
        "independent_human_review": False,
        "packet_hash": digest(packet),
        "scores": results,
        "legacy_v5_scorer": legacy,
        "candidate_gate_passed": advance,
        "production_authorized": False,
    }
