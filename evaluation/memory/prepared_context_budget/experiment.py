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

"""Per-case measurement and the preregistered decision rule.

Every formula and threshold here is read from, or documented in, the versioned manifest. Nothing is tuned after
inspecting results: changing a rule means publishing a new manifest version.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from math import fsum, log2
from typing import Any, Literal

from .arms import (
    Allocation,
    Candidate,
    Form,
    allocation_checks,
    baseline_allocation,
    baseline_checks,
    frozen_candidates,
    run_baseline,
    waterfill,
)
from .manifest import Case, Manifest

Outcome = Literal["invalid", "improvement_established", "improvement_not_established"]
_ELLIPSIS_BYTES = 3


@dataclass(frozen=True)
class ItemResult:
    """One frozen candidate under both arms. Bodies are never copied into results."""

    position: int
    kind: str
    family: str
    artifact_id: str
    rank: int
    weight: float
    display_bytes: int
    baseline_disposition: str
    baseline_delivered_bytes: int
    baseline_retained_fraction: float
    candidate_disposition: str
    candidate_delivered_bytes: int
    candidate_retained_fraction: float


@dataclass(frozen=True)
class CaseResult:
    case_id: str
    role: str
    critical: bool
    sensitivity_of: str | None
    path: str
    max_bytes: int
    input_sha256: str
    baseline_content_bytes: int
    candidate_content_bytes: int
    baseline_coverage: float
    candidate_coverage: float
    coverage_delta: float
    top_item_truncation_loss: float
    facts_declared: int
    baseline_facts_retained: int
    candidate_facts_retained: int
    #: Facts the baseline delivers that the candidate arm does not, as ``family/artifact_id: fact``.
    fact_regressions: tuple[str, ...]
    checks: dict[str, bool]
    items: tuple[ItemResult, ...]

    @property
    def failed_checks(self) -> tuple[str, ...]:
        return tuple(name for name, passed in self.checks.items() if not passed)


@dataclass(frozen=True)
class Decision:
    outcome: Outcome
    primary_cases: int
    mean_coverage_delta: float
    min_mean_coverage_delta: float
    max_top_item_truncation_loss: float
    top_item_loss_failures: tuple[str, ...]
    critical_fact_regressions: tuple[str, ...]
    invalid_checks: tuple[str, ...]
    reasons: tuple[str, ...]


def weight(position: int) -> float:
    return 1.0 / log2(position + 1)


def retained_fraction(candidate: Candidate, form: Form | None) -> float:
    if form is None:
        return 0.0
    if not form.truncated:
        return 1.0
    return (form.size - _ELLIPSIS_BYTES) / candidate.forms.display_bytes


def value(candidate: Candidate, form: Form | None, truncated_value_factor: float) -> float:
    if form is None:
        return 0.0
    if not form.truncated:
        return 1.0
    return truncated_value_factor * retained_fraction(candidate, form)


def coverage(candidates: Sequence[Candidate], allocation: Allocation, truncated_value_factor: float) -> float:
    total = fsum(weight(candidate.position) for candidate in candidates)
    if total == 0:
        return 0.0
    earned = fsum(
        weight(candidate.position) * value(candidate, form, truncated_value_factor)
        for candidate, form in zip(candidates, allocation.forms, strict=True)
    )
    return earned / total


def retained_facts(case: Case, candidates: Sequence[Candidate], allocation: Allocation) -> frozenset[str]:
    retained: set[str] = set()
    for candidate, form in zip(candidates, allocation.forms, strict=True):
        if form is None:
            continue
        for fact in case.facts.get((candidate.family, candidate.artifact_id), ()):
            if fact in form.content:
                retained.add(f"{candidate.family}/{candidate.artifact_id}: {fact}")
    return frozenset(retained)


def evaluate_case(manifest: Manifest, case: Case) -> CaseResult:
    """Measure one case twice; the second run is the replay check."""

    first = _measure(manifest, case)
    replayed = _measure(manifest, case)
    return replace(first, checks={**first.checks, "replay_identical": first == replayed})


def decide(manifest: Manifest, results: Sequence[CaseResult]) -> Decision:
    """Apply the preregistered decision rule of the manifest."""

    rules = manifest.decision
    min_delta = float(rules["min_mean_coverage_delta"])
    max_loss = float(rules["max_top_item_truncation_loss"])
    allowed_regressions = int(rules["critical_fact_regressions_allowed"])
    primary = [result for result in results if result.role == "primary"]
    mean_delta = fsum(result.coverage_delta for result in primary) / len(primary) if primary else 0.0
    invalid = tuple(f"{result.case_id}: {name}" for result in results for name in result.failed_checks)
    loss_failures = tuple(result.case_id for result in primary if result.top_item_truncation_loss > max_loss)
    regressions = tuple(
        f"{result.case_id}: {fact}" for result in primary if result.critical for fact in result.fact_regressions
    )
    reasons: list[str] = []
    if invalid:
        reasons.append(f"{len(invalid)} validity check(s) failed")
    if not mean_delta > min_delta:
        reasons.append(f"mean coverage delta {mean_delta:.6f} is not greater than {min_delta}")
    if loss_failures:
        reasons.append(f"top-item truncation loss above {max_loss} in {len(loss_failures)} primary case(s)")
    if len(regressions) > allowed_regressions:
        reasons.append(f"{len(regressions)} critical fact regression(s); {allowed_regressions} allowed")
    outcome: Outcome
    if invalid:
        outcome = "invalid"
    elif reasons:
        outcome = "improvement_not_established"
    else:
        outcome = "improvement_established"
    return Decision(
        outcome=outcome,
        primary_cases=len(primary),
        mean_coverage_delta=mean_delta,
        min_mean_coverage_delta=min_delta,
        max_top_item_truncation_loss=max_loss,
        top_item_loss_failures=loss_failures,
        critical_fact_regressions=regressions,
        invalid_checks=invalid,
        reasons=tuple(reasons),
    )


def sensitivity(results: Sequence[CaseResult]) -> list[dict[str, Any]]:
    """Compare each preregistered reordering case with the case it reorders."""

    by_id = {result.case_id: result for result in results}
    rows = []
    for result in results:
        if result.role != "sensitivity" or result.sensitivity_of not in by_id:
            continue
        reference = by_id[result.sensitivity_of]
        rows.append({
            "case_id": result.case_id,
            "reorders": reference.case_id,
            "reference_coverage_delta": reference.coverage_delta,
            "reordered_coverage_delta": result.coverage_delta,
            "reference_top_item_truncation_loss": reference.top_item_truncation_loss,
            "reordered_top_item_truncation_loss": result.top_item_truncation_loss,
            "reference_fact_regressions": len(reference.fact_regressions),
            "reordered_fact_regressions": len(result.fact_regressions),
        })
    return rows


def _measure(manifest: Manifest, case: Case) -> CaseResult:
    factor = float(manifest.scoring["truncated_value_factor"])
    baseline = run_baseline(case)
    candidates = frozen_candidates(case, baseline.trace)
    base = baseline_allocation(case, candidates)
    cand = waterfill(case, candidates)
    checks = {
        "trace_forms_reconstructed": all(
            (form is not None) == (candidate.record.disposition in {"full", "truncated"})
            for candidate, form in zip(candidates, base.forms, strict=True)
        ),
        **baseline_checks(case, baseline, candidates, base),
        **{f"baseline_{name}": passed for name, passed in allocation_checks(case, candidates, base).items()},
        **{f"candidate_{name}": passed for name, passed in allocation_checks(case, candidates, cand).items()},
    }
    base_facts = retained_facts(case, candidates, base)
    cand_facts = retained_facts(case, candidates, cand)
    base_coverage = coverage(candidates, base, factor)
    cand_coverage = coverage(candidates, cand, factor)
    top_loss = 0.0
    if candidates:
        top_loss = max(
            0.0,
            retained_fraction(candidates[0], base.forms[0]) - retained_fraction(candidates[0], cand.forms[0]),
        )
    return CaseResult(
        case_id=case.id,
        role=case.role,
        critical=case.critical,
        sensitivity_of=case.sensitivity_of,
        path=case.path,
        max_bytes=case.request.max_bytes,
        input_sha256=case.input_sha256,
        baseline_content_bytes=base.content_bytes,
        candidate_content_bytes=cand.content_bytes,
        baseline_coverage=base_coverage,
        candidate_coverage=cand_coverage,
        coverage_delta=cand_coverage - base_coverage,
        top_item_truncation_loss=top_loss,
        facts_declared=sum(len(facts) for facts in case.facts.values()),
        baseline_facts_retained=len(base_facts),
        candidate_facts_retained=len(cand_facts),
        fact_regressions=tuple(sorted(base_facts - cand_facts)),
        checks=checks,
        items=tuple(_item(candidate, base, cand, index) for index, candidate in enumerate(candidates)),
    )


def _item(candidate: Candidate, base: Allocation, cand: Allocation, index: int) -> ItemResult:
    base_form, cand_form = base.forms[index], cand.forms[index]
    return ItemResult(
        position=candidate.position,
        kind=candidate.record.kind,
        family=candidate.family,
        artifact_id=candidate.artifact_id,
        rank=candidate.record.rank,
        weight=weight(candidate.position),
        display_bytes=candidate.forms.display_bytes,
        baseline_disposition=base.dispositions[index],
        baseline_delivered_bytes=0 if base_form is None else base_form.size,
        baseline_retained_fraction=retained_fraction(candidate, base_form),
        candidate_disposition=cand.dispositions[index],
        candidate_delivered_bytes=0 if cand_form is None else cand_form.size,
        candidate_retained_fraction=retained_fraction(candidate, cand_form),
    )
