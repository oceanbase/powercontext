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

"""Paired fact/literal comparisons use the unchanged v1 feasibility reference."""

from __future__ import annotations

from collections import defaultdict

from .manifests import Suite
from .models import Arm, Sample, digest


def compare_preparation(
    suite: Suite,
    samples: tuple[Sample, ...],
    repetitions: int,
    *,
    baseline: Arm = "bounded-fragments-v1",
    candidate: Arm = "bounded-fragments-v2",
) -> dict:
    arms = (baseline, candidate)
    paired = defaultdict(dict)
    for sample in samples:
        if sample.arm in arms:
            paired[(sample.case_hash, sample.budget, sample.repetition)][sample.arm] = sample
    regressions = []
    reference_mismatches = []
    failures = defaultdict(list)
    cases = {digest(c.model_dump(mode="json")): c.case_id for c in suite.cases}
    for key, pair in paired.items():
        if set(pair) != set(arms):
            continue
        before, after = (pair[arm] for arm in arms)
        left, right = before.preparation, after.preparation
        reasons = []
        if left and right:
            if left.required_content_fits != right.required_content_fits or left.required_count != right.required_count:
                reference_mismatches.append(after.sample_id)
            if not set(left.retained_claim_hashes) <= set(right.retained_claim_hashes):
                reasons.append("required_fact_lost")
            if not set(left.retained_literal_hashes) <= set(right.retained_literal_hashes):
                reasons.append("exact_literal_lost")
            if left.required_content_fits and (
                left.missing_claim_hashes or left.exact_literals_retained != left.exact_literals_total
            ):
                failures[key[:2]].append((before, after))
        elif left and not right:
            reasons.append("preparation_missing")
        if before.status == "prepared" and after.status != "prepared":
            reasons.append("preparation_status_regressed")
        if before.completeness == "complete" and after.completeness != "complete":
            reasons.append("complete_evidence_regressed")
        if before.status in {"cancelled", "unavailable"} and (
            before.status != after.status or before.completeness != after.completeness
        ):
            reasons.append("failure_semantics_regressed")
        if after.memory_mutations or after.published_cursor:
            reasons.append("published_state")
        if reasons:
            regressions.append(
                {
                    "sample_id": after.sample_id,
                    "label": cases[key[0]],
                    "budget": key[1],
                    "repetition": key[2],
                    "reasons": reasons,
                }
            )
    rows = []
    for (case_hash, budget), pairs in sorted(failures.items(), key=lambda item: (cases[item[0][0]], item[0][1])):
        before, after = pairs[0]
        fixed = all(
            right.preparation is not None
            and not right.preparation.missing_claim_hashes
            and right.preparation.exact_literals_retained == right.preparation.exact_literals_total
            and right.status in {"prepared", "generated"}
            for _, right in pairs
        )
        rows.append(
            {
                "label": cases[case_hash],
                "budget": budget,
                "repetitions": len(pairs),
                "required": before.preparation.required_count,
                f"{baseline[-2:]}_retained": len(before.preparation.retained_claim_hashes),
                f"{candidate[-2:]}_retained": len(after.preparation.retained_claim_hashes)
                if after.preparation
                else None,
                "baseline_retained": len(before.preparation.retained_claim_hashes),
                "candidate_retained": len(after.preparation.retained_claim_hashes) if after.preparation else None,
                "fully_fixed": fixed,
            }
        )
    expected = len(suite.cases) * len(suite.experiment.budgets) * repetitions
    complete = (
        all(set(pair) == set(arms) for pair in paired.values())
        and len(paired) == expected
        and sum(1 for sample in samples if sample.arm in arms) == 2 * expected
    )
    return {
        "scope": "preparation_only_not_model_quality",
        "baseline_arm": baseline,
        "candidate_arm": candidate,
        "reference_encoding": "v1-claim-fragments",
        "paired_samples": len(paired),
        "paired_coverage_complete": complete,
        f"{baseline[-2:]}_failing_combinations": rows,
        "failing_combinations": rows,
        "fully_fixed_combinations": sum(1 for row in rows if row["fully_fixed"]),
        "regressions": regressions,
        "reference_mismatches": reference_mismatches,
        "no_observed_regression": complete and not regressions and not reference_mismatches,
    }
