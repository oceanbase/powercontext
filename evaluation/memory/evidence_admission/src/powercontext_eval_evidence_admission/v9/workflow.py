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

"""Immutable v7/v8/v9 offline comparisons, scored from decoded wire payloads."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from types import SimpleNamespace

from ..manifests import EvaluationError, read_json
from ..models import canonical, digest
from ..runner import run_sample
from ..v6.workflow import INPUT_KEYS, InputProbe
from ..v7.contract import identity as v7_identity
from ..v7.fidelity import cost_ledger as v7_ledger
from ..v7.fidelity import quote_inventory
from ..v7.workflow import DATASETS, reference_run, verified_results, write_result
from ..v8.contract import identity as v8_identity
from ..v8.workflow import coverage, selection_manifest
from .admission import pack
from .codec import restore
from .contract import identity

FAILURE_CASES = {"mixed-scaffolding", "exact-literals", "unicode-boundaries", "impossible-completeness"}
PROTECTION_CASES = {"small-complete", "negative-control", "forged-role-marker"}


def baseline(directory, dataset, filename, identity_key, expected_identity, manifest, references):
    result = read_json(directory / filename)
    if (
        result["dataset"] != dataset
        or result[identity_key] != expected_identity
        or digest(result) != read_json(directory / "COMPLETE.json")["result_hash"]
        or result["reference_manifest_hash"] != digest(manifest)
    ):
        raise EvaluationError("v9_baseline_identity_mismatch")
    rows = result["samples"]
    if len(rows) != len(references) or {r["sample_id"] for r in rows} != {r.sample_id for r in references}:
        raise EvaluationError("v9_baseline_sample_set_mismatch")
    return {r["sample_id"]: r for r in rows}, digest(result)


def minimal_case(dataset, case, reference):
    if reference.arm != "bounded-fragments-v3":
        return False
    if dataset == "synthetic-v1":
        return (
            case.case_id in FAILURE_CASES
            or case.case_id in PROTECTION_CASES
            and reference.budget == 4096
            or case.case_id == "many-source-overflow"
            and reference.budget == 2048
        )
    return dataset == "validation-v2" and case.case_id == "validation-reordered-sources" and reference.budget == 2048


def compare_sample(case, sample, reference, inputs, policy, old7, old8):
    if any(getattr(reference, key) != getattr(sample, key) for key in INPUT_KEYS):
        raise EvaluationError("v9_reference_preparation_changed")
    costs7 = [v7_ledger(v, quote_inventory(v, sample.supports)) for v in inputs]
    if costs7 != old7["v7_request_costs"]:
        raise EvaluationError("v9_v7_request_cost_not_reproduced")
    before = selection_manifest(inputs, sample.supports)
    if digest(before) != old8["selected_inventory_hash"]:
        raise EvaluationError("v9_v8_selection_not_reproduced")
    plan = pack(inputs, sample.supports, sample.budget, policy)
    decoded = []
    for request in plan.requests:
        payload = json.loads(canonical(request.payload))
        value = restore(request, payload, request_hash=request.binding_hash)
        if value != request.value:
            raise EvaluationError("v9_wire_roundtrip_changed_input")
        decoded.append(SimpleNamespace(value=value, supports=request.supports))
    after = selection_manifest([r.value for r in decoded], tuple(s for r in decoded for s in r.supports))
    if before != after:
        raise EvaluationError("v9_selected_content_changed")
    score = coverage(case, decoded)
    original = coverage(case, [SimpleNamespace(value=v, supports=sample.supports) for v in inputs])
    if set(original["retained_claim_hashes"]) != set(old8["reference_retained_claim_hashes"]):
        raise EvaluationError("v9_reference_coverage_not_reproduced")
    diagnostic_calls = len(plan.requests)
    diagnostic_input = sum(r.cost["single_estimated_input_tokens"] for r in plan.requests)
    return {
        "sample_id": sample.sample_id,
        "case": case.case_id,
        "arm": sample.arm,
        "budget": sample.budget,
        "repetition": sample.repetition,
        "reference_status": reference.status,
        "reference_required_content_fits": old8["reference_required_content_fits"],
        "reference_required_claims": old8["reference_required_claims"],
        "reference_retained_claim_hashes": old8["reference_retained_claim_hashes"],
        "reference_coverage": original,
        "v7_calls": old7["calls"],
        "v7_estimated_input_tokens": old7["v7_estimated_input_tokens"],
        "v7_cost_accepted": old7["cost_accepted"],
        "v8_diagnostic_plan_calls": old8["diagnostic_plan_calls"],
        "v8_diagnostic_plan_estimated_input_tokens": old8["diagnostic_plan_estimated_input_tokens"],
        "v8_plan_accepted": old8["plan_accepted"],
        "plan_accepted": plan.accepted,
        "plan_reason": plan.reason,
        "budget_overflow": plan.overflow,
        "budget_caps": {
            "single_input": sample.budget,
            "cumulative_input": sample.budget * policy.total_input_budget_multiplier,
            "calls": policy.max_calls_per_sample,
        },
        "diagnostic_plan_calls": diagnostic_calls,
        "diagnostic_plan_estimated_input_tokens": diagnostic_input,
        "executable_calls": diagnostic_calls if plan.accepted else 0,
        "executable_estimated_input_tokens": diagnostic_input if plan.accepted else 0,
        "selected_inventory_hash": digest(before),
        "decoded_inventory_hash": digest(after),
        "selection_preserved": True,
        "wire_roundtrip_exact": True,
        "coverage": score,
        "requests": [{"cost": r.cost, "request_binding_hash": r.binding_hash} for r in plan.requests],
        "provider_requests": 0,
        "model_generation": "not_run",
    }


async def compare(dataset, reference_dir, output_dir, v7_root, v8_root, *, minimal=False):
    if output_dir.exists():
        raise EvaluationError("v9_output_already_exists")
    frozen = identity()
    suite, manifest, references = reference_run(dataset, reference_dir)
    cases = {digest(c.model_dump(mode="json")): c for c in suite.cases}
    name = next(k for k, v in DATASETS.items() if v == dataset)
    old7, hash7 = baseline(
        v7_root / f"comparison-{name}",
        dataset,
        "cost-comparison.json",
        "v7_identity",
        v7_identity(),
        manifest,
        references,
    )
    old8, hash8 = baseline(
        v8_root / f"comparison-{name}", dataset, "comparison.json", "v8_identity", v8_identity(), manifest, references
    )
    selected = [r for r in references if not minimal or minimal_case(dataset, cases[r.case_hash], r)]
    rows = []
    for index, reference in enumerate(selected):
        case = cases[reference.case_hash]
        probe = InputProbe()
        sample = await run_sample(suite, case, reference.arm, reference.budget, reference.repetition, generator=probe)
        policy = suite.experiment.policy_for(reference.arm) or SimpleNamespace(
            max_calls_per_sample=frozen["contract"]["max_calls_per_sample"],
            total_input_budget_multiplier=frozen["contract"]["total_input_budget_multiplier"],
        )
        rows.append(
            compare_sample(
                case, sample, reference, probe.inputs, policy, old7[reference.sample_id], old8[reference.sample_id]
            )
        )
        if (index + 1) % (3 if minimal else 90) == 0:
            print(json.dumps({"dataset": dataset, "completed": index + 1, "total": len(selected)}), flush=True)
    if identity() != frozen:
        raise EvaluationError("v9_code_changed_during_comparison")
    result = {
        "schema": "evidence-admission.v9.offline-comparison.v1",
        "v9_identity": frozen,
        "dataset": dataset,
        "reference_dir": str(reference_dir.resolve()),
        "reference_manifest_hash": digest(manifest),
        "baseline_dirs": {
            "v7": str((v7_root / f"comparison-{name}").resolve()),
            "v8": str((v8_root / f"comparison-{name}").resolve()),
        },
        "baseline_hashes": {"v7": hash7, "v8": hash8},
        "mode": "minimal_replay" if minimal else "full_comparison",
        "samples": rows,
        "provider_requests": 0,
        "model_generation": "not_run",
        "production_authorized": False,
    }
    write_result(output_dir / "comparison.json", result)
    write_result(output_dir / "COMPLETE.json", {"result_hash": digest(result)})
    return {
        "dataset": dataset,
        "samples": len(rows),
        "rejected": sum(not r["plan_accepted"] for r in rows),
        "provider_requests": 0,
    }


def regressions(row):
    return {
        key: sorted(set(row["reference_coverage"][key]) - set(row["coverage"][key]))
        for key in (
            "retained_claim_hashes",
            "same_request_claim_hashes",
            "retained_literal_hashes",
            "same_request_literal_hashes",
        )
    }


def verified_comparisons(run_root, frozen):
    results = verified_results(run_root, "comparison", "comparison.json", "v9_identity", frozen)
    for dataset, result in results.items():
        if result["mode"] != "full_comparison":
            raise EvaluationError("v9_full_comparison_required")
        _, manifest, references = reference_run(dataset, Path(result["reference_dir"]))
        for version, filename, key, expected in (
            ("v7", "cost-comparison.json", "v7_identity", v7_identity()),
            ("v8", "comparison.json", "v8_identity", v8_identity()),
        ):
            _, result_hash = baseline(
                Path(result["baseline_dirs"][version]), dataset, filename, key, expected, manifest, references
            )
            if result_hash != result["baseline_hashes"][version]:
                raise EvaluationError("v9_baseline_changed_after_comparison")
    return results


def summarize(run_root):
    frozen = identity()
    results = verified_comparisons(run_root, frozen)
    totals, failures, residuals = defaultdict(Counter), [], []
    for dataset, result in results.items():
        for row in result["samples"]:
            count = totals[row["arm"]]
            count["samples"] += 1
            count["rejected_samples"] += not row["plan_accepted"]
            count["selection_changed_samples"] += not row["selection_preserved"]
            count["wire_roundtrip_failed_samples"] += not row["wire_roundtrip_exact"]
            for key in (
                "v7_calls",
                "v7_estimated_input_tokens",
                "v8_diagnostic_plan_calls",
                "v8_diagnostic_plan_estimated_input_tokens",
                "diagnostic_plan_calls",
                "diagnostic_plan_estimated_input_tokens",
                "executable_calls",
                "executable_estimated_input_tokens",
            ):
                count[key] += row[key]
            for key, flag in row["budget_overflow"].items():
                count[key + "_overflow_samples"] += flag
            lost = regressions(row)
            for key, values in lost.items():
                count["regressed_" + key] += len(values)
            if row["reference_required_content_fits"]:
                count["required_claims"] += row["reference_required_claims"]
                count["reference_retained_claims"] += len(row["reference_retained_claim_hashes"])
                count["planned_union_claims"] += len(row["coverage"]["retained_claim_hashes"])
                if row["plan_accepted"]:
                    count["cost_accepted_claims"] += len(row["coverage"]["retained_claim_hashes"])
                    count["cost_accepted_same_request_claims"] += len(row["coverage"]["same_request_claim_hashes"])
            if not row["plan_accepted"] or any(lost.values()):
                failures.append(
                    {
                        "dataset": dataset,
                        **{k: row[k] for k in ("sample_id", "case", "arm", "budget", "repetition", "plan_reason")},
                        "regressions": lost,
                    }
                )
            if (
                row["arm"] == "bounded-fragments-v3"
                and row["budget"] == 2048
                and row["case"] in {"many-source-overflow", "validation-reordered-sources"}
            ):
                residuals.append(
                    {
                        "dataset": dataset,
                        **{
                            k: row[k]
                            for k in (
                                "sample_id",
                                "case",
                                "budget",
                                "repetition",
                                "v7_calls",
                                "v7_estimated_input_tokens",
                                "v8_diagnostic_plan_calls",
                                "v8_diagnostic_plan_estimated_input_tokens",
                                "diagnostic_plan_calls",
                                "diagnostic_plan_estimated_input_tokens",
                                "plan_accepted",
                            )
                        },
                    }
                )
    candidate = totals["bounded-fragments-v3"]
    candidate_gate = (
        candidate["rejected_samples"]
        == candidate["selection_changed_samples"]
        == candidate["wire_roundtrip_failed_samples"]
        == 0
        and candidate["required_claims"]
        == candidate["reference_retained_claims"]
        == candidate["planned_union_claims"]
        == candidate["cost_accepted_claims"]
        == candidate["cost_accepted_same_request_claims"]
        == frozen["contract"]["candidate_required_claims"]
        and not any(r["arm"] == "bounded-fragments-v3" for r in failures)
    )
    all_gate = candidate_gate and not failures
    proposed = [
        r
        for r in results["synthetic-v1"]["samples"]
        if r["arm"] == "bounded-fragments-v3"
        and (r["case"] in FAILURE_CASES or r["case"] in PROTECTION_CASES and r["budget"] == 4096)
    ]
    result = {
        "schema": "evidence-admission.v9.offline-summary.v1",
        "v9_identity": frozen,
        "samples": sum(len(r["samples"]) for r in results.values()),
        "arms": dict(totals),
        "failures": failures,
        "previous_residual_failure_samples": residuals,
        "candidate_offline_gate_passed": candidate_gate,
        "all_arms_offline_gate_passed": all_gate,
        "provider_requests": 0,
        "model_generation": "not_run",
        "production_authorized": False,
        "online_preflight_draft": {
            "status": "requires_separate_provider_preflight" if all_gate else "blocked_by_offline_gate",
            "authorized": False,
            "paired_sample_identities": 2 * len(proposed),
            "v9_potential_call_cap": sum(r["executable_calls"] for r in proposed),
            "v9_estimated_input_tokens": sum(r["executable_estimated_input_tokens"] for r in proposed),
            "v9_maximum_output_tokens": sum(r["executable_calls"] for r in proposed) * 2048,
            "baseline_and_provider_wire_usage": "not_preflighted",
            "currency_cost": None,
        },
    }
    write_result(run_root / "summary.json", result)
    return result
