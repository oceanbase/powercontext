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

from types import SimpleNamespace

import pytest

from powercontext_eval_evidence_admission.manifests import EvaluationError
from powercontext_eval_evidence_admission.models import digest
from powercontext_eval_evidence_admission.v7 import workflow


@pytest.mark.parametrize("corruption", ["identity", "dataset", "duplicate", "manifest", "hash"])
def test_summary_rejects_stale_mixed_or_incomplete_comparison(tmp_path, monkeypatch, corruption):
    frozen = {"code": "current"}
    monkeypatch.setattr(
        workflow, "reference_run", lambda *a: (None, {"frozen": True}, [SimpleNamespace(sample_id="sample")])
    )
    for name, dataset in workflow.DATASETS.items():
        result = {
            "dataset": dataset,
            "v7_identity": frozen,
            "reference_dir": "/unused",
            "reference_manifest_hash": digest({"frozen": True}),
            "samples": [{"sample_id": "sample"}],
        }
        if name == "primary":
            if corruption == "identity":
                result["v7_identity"] = {"code": "stale"}
            elif corruption == "dataset":
                result["dataset"] = "wrong"
            elif corruption == "duplicate":
                result["samples"] *= 2
            elif corruption == "manifest":
                result["reference_manifest_hash"] = "wrong"
        directory = tmp_path / f"comparison-{name}"
        workflow.write_result(directory / "cost-comparison.json", result)
        workflow.write_result(
            directory / "COMPLETE.json",
            {"result_hash": "wrong" if corruption == "hash" and name == "primary" else digest(result)},
        )
    with pytest.raises(EvaluationError):
        workflow.verified_results(tmp_path, "comparison", "cost-comparison.json", "v7_identity", frozen)


def test_budget_status_checks_cumulative_and_calls_independently():
    costs = [{"single_estimated_input_tokens": 90}] * 3
    caps = SimpleNamespace(max_calls_per_sample=2, total_input_budget_multiplier=2)
    assert workflow.budget_status(costs, 100, caps) == {"per_call": False, "cumulative": True, "call_count": True}
