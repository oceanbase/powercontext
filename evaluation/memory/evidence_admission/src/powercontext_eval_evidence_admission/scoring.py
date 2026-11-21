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

"""Ground-truth scoring is separate from admission; unresolved semantics cannot pass."""

from __future__ import annotations

from collections import defaultdict

from powercontext.builtin.artifacts.atomic_memory.extraction import AtomicMemoryCandidate, AtomicMemoryExtractionInput

from .admission import Admission, Fragment, token_count
from .manifests import EvaluationError, field_value
from .models import Case, Claim, GenerationScore, PreparationScore, digest


def admitted_text(items: tuple[Fragment, ...]) -> dict[tuple[str, str], tuple[tuple[int, int, str], ...]]:
    groups = defaultdict(list)
    for item in items:
        if isinstance(item.value, str):
            groups[(item.source.source_ref.source_id, item.field)].append(item)
    result = {}
    for key, group in groups.items():
        ranges: list[tuple[int, int, str]] = []
        for item in sorted(group, key=lambda part: part.byte_start):
            if ranges and ranges[-1][1] == item.byte_start:
                start, _, text = ranges.pop()
                ranges.append((start, item.byte_end, text + item.value))
            else:
                ranges.append((item.byte_start, item.byte_end, item.value))
        result[key] = tuple(ranges)
    return result


def _supported_span(source_id: str, field: str, start: int, text: str, items: tuple[Fragment, ...]) -> bool:
    end = start + len(text.encode())
    return any(
        left <= start and end <= right and admitted.encode()[start - left : end - left] == text.encode()
        for left, right, admitted in admitted_text(items).get((source_id, field), ())
    )


def _claim_start(claim: Claim, full: AtomicMemoryExtractionInput) -> int:
    evidence = next(item for item in full.evidence if item.source_ref.source_id == claim.source_id)
    original = field_value({"content": evidence.content, "source_metadata": evidence.source_metadata}, claim.field)
    return len(original[: original.index(claim.text)].encode())


def supported_claim(claim: Claim, items: tuple[Fragment, ...], full: AtomicMemoryExtractionInput) -> bool:
    return _supported_span(claim.source_id, claim.field, _claim_start(claim, full), claim.text, items)


def score_preparation(
    case: Case, full: AtomicMemoryExtractionInput, admission: Admission, budget: int, instructions: str
) -> PreparationScore:
    retained = tuple(claim for claim in case.required if supported_claim(claim, admission.admitted, full))
    missing = tuple(claim for claim in case.required if claim not in retained)
    literal_retained = 0
    retained_literals = []
    for claim in case.required:
        claim_start = _claim_start(claim, full)
        for index, literal in enumerate(claim.exact_literals):
            # Anchor the literal inside the labeled claim, never to an unrelated repeated occurrence.
            start = claim_start + len(claim.text[: claim.text.index(literal)].encode())
            if _supported_span(claim.source_id, claim.field, start, literal, admission.admitted):
                literal_retained += 1
                retained_literals.append(digest({"claim": claim.claim_id, "literal": index}))
    # An oracle reference input measures joint feasibility only; it is never passed to admission.
    # Each claim carries the same provenance/offset envelope as a selected fragment.
    required_evidence = []
    for claim in case.required:
        original = next(
            item
            for item in admission.inventory
            if item.source.source_ref.source_id == claim.source_id and item.field == claim.field
        )
        text = field_value(
            {"content": original.source.content, "source_metadata": original.source.source_metadata}, claim.field
        )
        start = text.index(claim.text)
        byte_start = len(text[:start].encode())
        required_evidence.append(
            Fragment(
                original.source,
                original.materialization,
                original.projection_hash,
                claim.field,
                original.field_order,
                claim.text,
                byte_start,
                byte_start + len(claim.text.encode()),
                start,
                start + len(claim.text),
            ).evidence()
        )
    ceiling = token_count(AtomicMemoryExtractionInput(evidence=tuple(required_evidence)), instructions) <= budget
    return PreparationScore(
        required_count=len(case.required),
        retained_claim_hashes=tuple(digest(c.claim_id) for c in retained),
        missing_claim_hashes=tuple(digest(c.claim_id) for c in missing),
        exact_literals_retained=literal_retained,
        exact_literals_total=sum(len(c.exact_literals) for c in case.required),
        retained_literal_hashes=tuple(retained_literals),
        required_content_fits=ceiling,
    )


def _bigrams(text: str) -> set[str]:
    return {text[index : index + 2] for index in range(max(1, len(text) - 1))}


def score_generation(
    case: Case,
    full: AtomicMemoryExtractionInput,
    admission: Admission,
    candidates: tuple[AtomicMemoryCandidate, ...],
    *,
    audit: dict[str, dict] | None = None,
) -> GenerationScore:
    supported = set()
    unsupported = false_attribution = invalid_support = unresolved = duplicates = near = 0
    hashes = []
    references = []
    previous: list[str] = []
    for candidate in candidates:
        candidate_hash = digest(candidate.model_dump(mode="json"))
        hashes.append(candidate_hash)
        refs = tuple(support for support in admission.supports if support.evidence_id in candidate.evidence_ids)
        references.append(refs)
        valid_ids = {support.evidence_id for support in refs}
        if not set(candidate.evidence_ids) <= valid_ids:
            invalid_support += 1
        items = tuple(
            item
            for item in admission.admitted
            if any(
                support.source_hash == digest(item.source.source_ref.source_id)
                and support.materialization_hash == item.materialization
                and support.projection_hash == item.projection_hash
                and support.field_hash == digest(item.field)
                and support.byte_start <= item.byte_start
                and item.byte_end <= support.byte_end
                for support in refs
            )
        )
        matched = [claim for claim in case.required if candidate.text in (claim.text, *claim.accepted_candidates)]
        forbidden = [claim for claim in case.forbidden if candidate.text in (claim.text, *claim.accepted_candidates)]
        supported_matches = [claim for claim in matched if supported_claim(claim, items, full)]
        if forbidden:
            unsupported += 1
            false_attribution += int(any(claim.current_user for claim in forbidden))
        elif matched and (
            not supported_matches
            or any(literal not in candidate.text for claim in supported_matches for literal in claim.exact_literals)
        ):
            unsupported += 1
        elif matched and candidate.description == candidate.text:
            supported.update(digest(claim.claim_id) for claim in supported_matches)
        elif audit and candidate_hash in audit:
            review = audit[candidate_hash]
            if set(review) != {"verdict", "claim_hashes"}:
                raise EvaluationError("invalid_audit_record")
            allowed = {digest(claim.claim_id): claim for claim in case.required}
            if review["verdict"] == "supported":
                reviewed = review["claim_hashes"]
                if not reviewed or any(
                    key not in allowed or not supported_claim(allowed[key], items, full) for key in reviewed
                ):
                    raise EvaluationError("audit_claim_without_admitted_support")
                if any(literal not in candidate.text for key in reviewed for literal in allowed[key].exact_literals):
                    raise EvaluationError("audit_cannot_override_exact_literal")
                supported.update(reviewed)
            elif review["verdict"] == "false_attribution":
                false_attribution += 1
                unsupported += 1
            elif review["verdict"] == "unsupported":
                unsupported += 1
            else:
                raise EvaluationError("invalid_audit_verdict")
        else:
            unresolved += 1
        duplicates += int(candidate.text in previous)
        pairs = _bigrams(candidate.text)
        near += int(
            any(
                text != candidate.text and len(pairs & _bigrams(text)) / max(1, len(pairs | _bigrams(text))) >= 0.85
                for text in previous
            )
        )
        previous.append(candidate.text)
    return GenerationScore(
        candidates=len(candidates),
        retained_claim_hashes=tuple(sorted(supported)),
        retained_literal_hashes=tuple(
            sorted(
                digest({"claim": claim.claim_id, "literal": index})
                for claim in case.required
                if digest(claim.claim_id) in supported
                for index, _ in enumerate(claim.exact_literals)
            )
        ),
        unsupported=unsupported,
        false_attribution=false_attribution,
        invalid_support=invalid_support,
        unresolved=unresolved,
        exact_duplicates=duplicates,
        near_duplicates=near,
        candidate_hashes=tuple(hashes),
        support=tuple(references),
    )
