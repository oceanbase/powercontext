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

from dataclasses import replace

from powercontext_eval_evidence_admission.comparison import compare_preparation
from powercontext_eval_evidence_admission.models import PreparationScore, Sample, digest


def test_comparison_detects_identity_regression_even_when_retention_counts_improve(suite):
    case = next(c for c in suite.cases if c.case_id == "impossible-completeness")
    minimal = replace(suite, cases=(case,), experiment=suite.experiment.model_copy(update={"budgets": (2048,)}))
    before = PreparationScore(
        required_count=96,
        retained_claim_hashes=("old-fact",),
        missing_claim_hashes=("omitted",),
        exact_literals_retained=1,
        exact_literals_total=96,
        retained_literal_hashes=("old-literal",),
        required_content_fits=False,
    )
    after = before.model_copy(
        update={
            "retained_claim_hashes": ("new-fact-a", "new-fact-b"),
            "exact_literals_retained": 2,
            "retained_literal_hashes": ("new-literal-a", "new-literal-b"),
        }
    )
    rows = tuple(
        Sample(
            sample_id=digest((arm, repetition)),
            case_hash=digest(case.model_dump(mode="json")),
            arm=arm,
            budget=2048,
            repetition=repetition,
            completeness="bounded",
            status="prepared",
            preparation=score,
        )
        for arm, score in (("bounded-fragments-v1", before), ("bounded-fragments-v2", after))
        for repetition in range(2)
    )
    comparison = compare_preparation(minimal, rows, 2)
    assert comparison["paired_coverage_complete"] and not comparison["no_observed_regression"]
    assert len(comparison["regressions"]) == 2
    assert all(set(row["reasons"]) == {"required_fact_lost", "exact_literal_lost"} for row in comparison["regressions"])
    assert not compare_preparation(minimal, rows[:-1], 2)["paired_coverage_complete"]
    assert not compare_preparation(minimal, (*rows, rows[0]), 2)["paired_coverage_complete"]
