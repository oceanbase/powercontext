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

import pytest

from powercontext_eval_evidence_admission.manifests import EvaluationError
from powercontext_eval_evidence_admission.models import digest
from powercontext_eval_evidence_admission.v7.workflow import write_result
from powercontext_eval_evidence_admission.v9.workflow import baseline, regressions


@pytest.mark.parametrize("corruption", ["identity", "dataset", "hash", "manifest", "duplicate"])
def test_baseline_verification_rejects_stale_or_changed_evidence(tmp_path, corruption):
    from types import SimpleNamespace

    row = {"sample_id": "sample"}
    value = {
        "dataset": "synthetic-v1",
        "identity": {"code": "current"},
        "reference_manifest_hash": digest({"frozen": True}),
        "samples": [row],
    }
    if corruption == "identity":
        value["identity"] = {"code": "stale"}
    elif corruption == "dataset":
        value["dataset"] = "wrong"
    elif corruption == "manifest":
        value["reference_manifest_hash"] = "wrong"
    elif corruption == "duplicate":
        value["samples"] *= 2
    write_result(tmp_path / "comparison.json", value)
    write_result(tmp_path / "COMPLETE.json", {"result_hash": "wrong" if corruption == "hash" else digest(value)})
    with pytest.raises(EvaluationError):
        baseline(
            tmp_path,
            "synthetic-v1",
            "comparison.json",
            "identity",
            {"code": "current"},
            {"frozen": True},
            [SimpleNamespace(sample_id="sample")],
        )


def test_regressions_keep_union_and_same_request_literal_failures_distinct():
    keys = (
        "retained_claim_hashes",
        "same_request_claim_hashes",
        "retained_literal_hashes",
        "same_request_literal_hashes",
    )
    row = {"reference_coverage": {k: ["required"] for k in keys}, "coverage": {k: ["required"] for k in keys}}
    row["coverage"]["same_request_literal_hashes"] = []
    result = regressions(row)
    assert result["same_request_literal_hashes"] == ["required"]
    assert result["retained_literal_hashes"] == []
