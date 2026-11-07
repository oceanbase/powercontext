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

"""v14 offline comparison: identical packing, cell-based encoding cost, budget gate."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from types import SimpleNamespace

from ..manifests import EvaluationError
from ..models import digest
from ..runner import run_sample
from ..v6.workflow import INPUT_KEYS, InputProbe
from ..v7.workflow import DATASETS, reference_run, write_result
from .admission import pack
from .contract import identity

MAX_CALLS = 16
TOTAL_MULTIPLIER = 8
CANDIDATE_REQUIRED_CLAIMS = 495


def compare_sample(case, sample, reference, inputs, policy):
    if any(getattr(reference, key) != getattr(sample, key) for key in INPUT_KEYS):
        raise EvaluationError("v14_reference_preparation_changed")
    plan = pack(inputs, sample.supports, sample.budget, policy)
    return {
        "sample_id": sample.sample_id,
        "case": case.case_id,
        "arm": sample.arm,
        "budget": sample.budget,
        "repetition": sample.repetition,
        "reference_status": reference.status,
        "plan_accepted": plan.accepted,
        "plan_reason": plan.reason,
        "budget_overflow": plan.overflow,
        "diagnostic_plan_calls": len(plan.requests),
        "diagnostic_plan_estimated_input_tokens": sum(r.cost["single_estimated_input_tokens"] for r in plan.requests),
        "cell_count": sum(r.cost["cell_count"] for r in plan.requests),
        "requests": [{"cost": r.cost, "request_binding_hash": r.binding_hash} for r in plan.requests],
        "provider_requests": 0,
        "model_generation": "not_run",
    }


async def compare(dataset, reference_dir, output_dir):
    if output_dir.exists():
        raise EvaluationError("v14_output_already_exists")
    frozen = identity()
    suite, manifest, references = reference_run(dataset, reference_dir)
    cases = {digest(c.model_dump(mode="json")): c for c in suite.cases}
    rows = []
    for index, reference in enumerate(references):
        case = cases[reference.case_hash]
        probe = InputProbe()
        sample = await run_sample(suite, case, reference.arm, reference.budget, reference.repetition, generator=probe)
        policy = suite.experiment.policy_for(reference.arm) or SimpleNamespace(
            max_calls_per_sample=MAX_CALLS, total_input_budget_multiplier=TOTAL_MULTIPLIER
        )
        rows.append(compare_sample(case, sample, reference, probe.inputs, policy))
        if (index + 1) % 90 == 0:
            print(json.dumps({"dataset": dataset, "completed": index + 1, "total": len(references)}), flush=True)
    if identity() != frozen:
        raise EvaluationError("v14_code_changed_during_comparison")
    result = {
        "schema": "evidence-admission.v14.offline-comparison.v1",
        "v14_identity": frozen,
        "dataset": dataset,
        "reference_manifest_hash": digest(manifest),
        "mode": "full_comparison",
        "samples": rows,
        "provider_requests": 0,
        "model_generation": "not_run",
        "production_authorized": False,
    }
    output_dir.mkdir(parents=True)
    write_result(output_dir / "comparison.json", result)
    write_result(output_dir / "COMPLETE.json", {"result_hash": digest(result)})
    return {"dataset": dataset, "samples": len(rows), "rejected": sum(not r["plan_accepted"] for r in rows)}


def summarize(run_root):
    frozen = identity()
    results = {}
    for name, dataset in DATASETS.items():
        directory = run_root / f"comparison-{name}"
        result = json.loads((directory / "comparison.json").read_text())
        if digest(result) != json.loads((directory / "COMPLETE.json").read_text())["result_hash"]:
            raise EvaluationError("v14_comparison_integrity_mismatch")
        results[dataset] = result
    totals = defaultdict(Counter)
    failures = []
    for dataset, result in results.items():
        for row in result["samples"]:
            count = totals[row["arm"]]
            count["samples"] += 1
            count["rejected_samples"] += not row["plan_accepted"]
            count["cell_count"] += row["cell_count"]
            count["diagnostic_plan_calls"] += row["diagnostic_plan_calls"]
            count["diagnostic_plan_estimated_input_tokens"] += row["diagnostic_plan_estimated_input_tokens"]
            for key, flag in row["budget_overflow"].items():
                count[key + "_overflow_samples"] += flag
            if not row["plan_accepted"]:
                failures.append(
                    {
                        "dataset": dataset,
                        **{k: row[k] for k in ("sample_id", "case", "arm", "budget", "repetition", "plan_reason")},
                    }
                )
    candidate = totals["bounded-fragments-v3"]
    candidate_gate = (
        candidate["rejected_samples"] == 0
        and candidate["samples"] == 324
        and not any(f["arm"] == "bounded-fragments-v3" for f in failures)
    )
    all_gate = candidate_gate and not failures
    result = {
        "schema": "evidence-admission.v14.offline-summary.v1",
        "v14_identity": frozen,
        "samples": sum(len(r["samples"]) for r in results.values()),
        "arms": dict(totals),
        "failures": failures,
        "candidate_required_claims": CANDIDATE_REQUIRED_CLAIMS,
        "candidate_offline_gate_passed": candidate_gate,
        "all_arms_offline_gate_passed": all_gate,
        "provider_requests": 0,
        "model_generation": "not_run",
        "production_authorized": False,
    }
    write_result(run_root / "summary.json", result)
    return result
