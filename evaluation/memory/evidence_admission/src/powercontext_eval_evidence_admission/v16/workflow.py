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

"""Reexecute the complete immutable selection matrix, roundtrip wire, then score admission."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from types import SimpleNamespace

from ..manifests import EvaluationError, file_hash, load_suite, read_json
from ..models import digest
from ..runner import run_sample
from ..v6.workflow import INPUT_KEYS, InputProbe
from ..v7.workflow import DATASETS, reference_run, write_result
from ..v8.workflow import coverage, selection_manifest
from .admission import pack
from .freeze import committed_freeze, identity
from .wire import restore


def compare_sample(case, sample, reference, inputs, policy):
    if any(getattr(reference, key) != getattr(sample, key) for key in INPUT_KEYS):
        raise EvaluationError("v16_reference_preparation_changed")
    plan = pack(inputs, sample.supports, sample.budget, policy)
    for request in plan.requests:
        restored = restore(request, json.loads(json.dumps(request.payload)), request_hash=request.binding_hash)
        if restored != request.value:
            raise EvaluationError("v16_wire_roundtrip_failed")
    selected = selection_manifest(inputs, sample.supports)
    planned = selection_manifest([r.value for r in plan.requests], tuple(s for r in plan.requests for s in r.supports))
    if selected != planned:
        raise EvaluationError("v16_packing_changed_admitted_ranges")
    covered = coverage(case, plan.requests)
    preparation = sample.preparation
    expected = set(preparation.retained_claim_hashes) if preparation else set()
    if expected - set(covered["retained_claim_hashes"]):
        raise EvaluationError("v16_input_coverage_regression")
    return {
        "sample_id": sample.sample_id,
        "case": case.case_id,
        "arm": sample.arm,
        "budget": sample.budget,
        "repetition": sample.repetition,
        "reference_status": reference.status,
        "completeness": sample.completeness,
        "plan_accepted": plan.accepted,
        "plan_reason": plan.reason,
        "budget_overflow": plan.overflow,
        "diagnostic_plan_calls": len(plan.requests),
        "diagnostic_plan_estimated_input_tokens": sum(r.cost["single_estimated_input_tokens"] for r in plan.requests),
        "cell_count": sum(len(r.cells) for r in plan.requests),
        "requests": [{"cost": r.cost, "request_binding_hash": r.binding_hash} for r in plan.requests],
        "reference_required_content_fits": bool(preparation and preparation.required_content_fits),
        "reference_required_claims": preparation.required_count if preparation else 0,
        "coverage": covered,
        "selected_ranges_hash": digest(selected),
        "selection_unchanged": True,
        "wire_roundtrip_passed": True,
        "provider_requests": 0,
        "model_generation": "not_run",
    }


async def compare(dataset, reference_dir, output_dir, historical_dir):
    if output_dir.exists():
        raise EvaluationError("v16_output_already_exists")
    frozen = identity()
    execution_freeze = committed_freeze()
    suite, manifest, references = reference_run(dataset, reference_dir)
    historical_file = historical_dir / "comparison.json"
    historical = read_json(historical_file)
    if (
        file_hash(historical_file) != frozen["contract"]["historical_comparison_sha256"][dataset]
        or digest(historical["v15_identity"]) != frozen["contract"]["historical_v15_identity_hash"]
        or digest(historical) != read_json(historical_dir / "COMPLETE.json")["result_hash"]
        or historical["dataset"] != dataset
    ):
        raise EvaluationError("v16_historical_comparison_changed")
    prior = {r["sample_id"]: r for r in historical["samples"]}
    if len(prior) != len(historical["samples"]) or set(prior) != {r.sample_id for r in references}:
        raise EvaluationError("v16_historical_sample_set_changed")
    cases = {digest(c.model_dump(mode="json")): c for c in suite.cases}
    rows = []
    for index, reference in enumerate(references):
        case = cases[reference.case_hash]
        probe = InputProbe()
        sample = await run_sample(suite, case, reference.arm, reference.budget, reference.repetition, generator=probe)
        policy = suite.experiment.policy_for(reference.arm) or SimpleNamespace(
            max_calls_per_sample=16, total_input_budget_multiplier=8
        )
        row = compare_sample(case, sample, reference, probe.inputs, policy)
        previous = prior[row["sample_id"]]
        unchanged = (
            "case",
            "arm",
            "budget",
            "repetition",
            "reference_status",
            "completeness",
            "reference_required_content_fits",
            "reference_required_claims",
            "selected_ranges_hash",
        )
        coverage_losses = {
            key: sorted(set(previous["coverage"][key]) - set(row["coverage"][key])) for key in previous["coverage"]
        }
        row.update(
            historical_v15_input_unchanged=all(row[key] == previous[key] for key in unchanged),
            historical_v15_coverage_losses=coverage_losses,
            historical_v15_executable_regression=previous["plan_accepted"] and not row["plan_accepted"],
            historical_v15_calls=previous["diagnostic_plan_calls"],
            historical_v15_estimated_input_tokens=previous["diagnostic_plan_estimated_input_tokens"],
        )
        rows.append(row)
        if (index + 1) % 90 == 0:
            print(json.dumps({"dataset": dataset, "completed": index + 1, "total": len(references)}), flush=True)
    if identity() != frozen or committed_freeze() != execution_freeze:
        raise EvaluationError("v16_code_changed_during_comparison")
    result = {
        "schema": "evidence-admission.v16.offline-comparison.v1",
        "v16_identity": frozen,
        "execution_freeze": execution_freeze,
        "dataset": dataset,
        "reference_dir": str(reference_dir.resolve()),
        "reference_manifest_hash": digest(manifest),
        "historical_v15_comparison_sha256": file_hash(historical_file),
        "samples": rows,
        "provider_requests": 0,
        "model_generation": "not_run",
        "production_authorized": False,
    }
    write_result(output_dir / "comparison.json", result)
    write_result(output_dir / "COMPLETE.json", {"result_hash": digest(result)})
    return {"dataset": dataset, "samples": len(rows), "rejected": sum(not r["plan_accepted"] for r in rows)}


def verified_comparisons(run_root, frozen):
    results = {}
    for name, dataset in DATASETS.items():
        directory = run_root / f"comparison-{name}"
        result = read_json(directory / "comparison.json")
        if (
            digest(result) != read_json(directory / "COMPLETE.json")["result_hash"]
            or result["dataset"] != dataset
            or result["v16_identity"] != frozen
            or result["historical_v15_comparison_sha256"] != frozen["contract"]["historical_comparison_sha256"][dataset]
        ):
            raise EvaluationError("v16_comparison_integrity_or_identity_mismatch")
        _, manifest, references = reference_run(dataset, Path(result["reference_dir"]))
        ids = [r["sample_id"] for r in result["samples"]]
        if (
            len(ids) != len(set(ids))
            or set(ids) != {r.sample_id for r in references}
            or digest(manifest) != result["reference_manifest_hash"]
        ):
            raise EvaluationError("v16_comparison_sample_set_mismatch")
        results[dataset] = result
    return results


def summarize(run_root, *, persist=True):
    frozen = identity()
    results = verified_comparisons(run_root, frozen)
    totals = defaultdict(Counter)
    failures, repetitions = [], defaultdict(set)
    for dataset, result in results.items():
        for row in result["samples"]:
            count = totals[row["arm"]]
            count["samples"] += 1
            count["historical_v15_input_regressions"] += not row["historical_v15_input_unchanged"]
            count["historical_v15_coverage_losses"] += sum(
                len(values) for values in row["historical_v15_coverage_losses"].values()
            )
            count["historical_v15_calls"] += row["historical_v15_calls"]
            count["historical_v15_estimated_input_tokens"] += row["historical_v15_estimated_input_tokens"]
            count["historical_v15_executable_regressions"] += row["historical_v15_executable_regression"]
            count["rejected_samples"] += not row["plan_accepted"]
            count["diagnostic_plan_calls"] += row["diagnostic_plan_calls"]
            count["diagnostic_plan_estimated_input_tokens"] += row["diagnostic_plan_estimated_input_tokens"]
            for key, flag in row["budget_overflow"].items():
                count[key + "_overflow_samples"] += flag
            if row["reference_required_content_fits"]:
                count["required_claims"] += row["reference_required_claims"]
                retained = len(row["coverage"]["retained_claim_hashes"])
                count["retained_claims"] += retained
                count["executable_retained_claims"] += retained if row["plan_accepted"] else 0
            reasons = []
            if not row["plan_accepted"]:
                reasons.append("protocol_budget_rejection")
            if not row["historical_v15_input_unchanged"]:
                reasons.append("historical_input_or_status_changed")
            if any(row["historical_v15_coverage_losses"].values()):
                reasons.append("historical_claim_or_literal_coverage_loss")
            if row["historical_v15_executable_regression"]:
                reasons.append("historical_executable_loss")
            if reasons:
                failures.append(
                    {
                        "dataset": dataset,
                        "reasons": reasons,
                        **{
                            k: row[k] for k in ("case", "arm", "budget", "repetition", "plan_reason", "budget_overflow")
                        },
                    }
                )
            repeated = {k: v for k, v in row.items() if k not in ("sample_id", "repetition")}
            repetitions[dataset, row["case"], row["arm"], row["budget"]].add(digest(repeated))
    candidate = totals["bounded-fragments-v3"]
    nondeterministic = [list(key) for key, variants in repetitions.items() if len(variants) != 1]
    passed = (
        candidate["samples"] == 324
        and candidate["rejected_samples"] == 0
        and candidate["executable_retained_claims"] == candidate["required_claims"] == 495
        and not failures
        and not nondeterministic
        and not any(
            c["historical_v15_executable_regressions"]
            or c["historical_v15_input_regressions"]
            or c["historical_v15_coverage_losses"]
            for c in totals.values()
        )
    )
    result = {
        "schema": "evidence-admission.v16.offline-summary.v1",
        "identity_hash": digest(frozen),
        "samples": sum(len(r["samples"]) for r in results.values()),
        "arms": dict(totals),
        "failures": failures,
        "nondeterministic_groups": nondeterministic,
        "offline_gate_passed": passed,
        "decision": "pending_minimal_live_validation" if passed else "insufficient_evidence",
        "stop_required": not passed,
        "provider_requests": 0,
        "model_generation": "not_run",
        "production_authorized": False,
    }
    if persist:
        write_result(run_root / "summary.json", result)
    return result


def minimal_case_record(case, sample, plan):
    """Keep ordinary packing acceptance separate from the frozen one-request gate."""
    literals = [literal for claim in case.required for literal in claim.exact_literals]
    selectable = all(
        any(literal.encode() in c.value.encode() for r in plan.requests for c in r.cells) for literal in literals
    )
    covered = coverage(case, plan.requests)
    complete = len(covered["same_request_claim_hashes"]) == len(case.required)
    return {
        "case": case.case_id,
        "sample_id": sample.sample_id,
        "budget": sample.budget,
        "accepted": plan.accepted,
        "requests": len(plan.requests),
        "estimated_input_tokens": [r.cost["single_estimated_input_tokens"] for r in plan.requests],
        "cell_count": sum(len(r.cells) for r in plan.requests),
        "request_hashes": [r.binding_hash for r in plan.requests],
        "required_literals_copy_selectable": selectable,
        "coverage": covered,
        "one_request_gate_passed": plan.accepted and len(plan.requests) == 1 and selectable and complete,
    }


async def minimal_plan(run_root):
    """Rebuild the frozen two-case manifest only after complete offline acceptance."""
    output = run_root / "minimal-readiness.json"
    if output.exists():
        raise EvaluationError("v16_minimal_manifest_already_exists")
    frozen = identity()
    summary = summarize(run_root, persist=False)
    if read_json(run_root / "summary.json") != summary:
        raise EvaluationError("v16_offline_summary_changed")
    if not summary["offline_gate_passed"]:
        raise EvaluationError("v16_offline_gate_required_before_minimal_manifest")
    suite = load_suite(experiment_version="v5", dataset="synthetic-v1")
    contract = frozen["contract"]["online"]
    rows = []
    for label in contract["minimal_cases"]:
        case = next(c for c in suite.cases if c.case_id == label)
        probe = InputProbe()
        sample = await run_sample(suite, case, contract["arm"], contract["budget"], 0, generator=probe)
        plan = pack(probe.inputs, sample.supports, sample.budget, suite.experiment.v3)
        rows.append(minimal_case_record(case, sample, plan))
    if identity() != frozen:
        raise EvaluationError("v16_identity_changed_during_minimal_manifest")
    passed = all(row["one_request_gate_passed"] for row in rows)
    result = {
        "schema": "evidence-admission.v16.minimal-readiness.v1",
        "identity_hash": digest(frozen),
        "offline_summary_hash": digest(summary),
        "cases": rows,
        "minimal_manifest_gate_passed": passed,
        "sdk_preflight": "not_run",
        "provider_requests": 0,
        "decision": "pending_sdk_preflight" if passed else "insufficient_evidence",
        "stop_required": not passed,
        "production_authorized": False,
    }
    write_result(output, result)
    return result
