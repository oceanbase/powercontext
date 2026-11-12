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

"""Offline cost and provenance validation over immutable reference selections."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from powercontext.builtin.inference import character_token_estimator

from ..admission import leaves
from ..manifests import EvaluationError, load_suite, read_json
from ..models import canonical, digest
from ..report import code_identity, load_run
from ..runner import run_sample
from ..v6.fidelity import quote_inventory as v6_quote_inventory
from ..v6.fidelity import request_cost as v6_request_cost
from ..v6.workflow import INPUT_KEYS, InputProbe, cost_budget_status
from .contract import identity
from .fidelity import cost_ledger, quote_inventory

DATASETS = {"primary": "synthetic-v1", "validation": "validation-v2", "additional": "validation-v3"}


def write_result(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def budget_status(costs, budget, policy):
    return cost_budget_status(
        [{"estimated_input_tokens": c["single_estimated_input_tokens"]} for c in costs], budget, policy
    )


def reference_run(dataset, reference_dir):
    suite = load_suite(experiment_version="v5", dataset=dataset)
    manifest, references, _ = load_run(reference_dir)
    expected = code_identity(suite)
    expected.update({k: manifest["code"][k] for k in ("revision", "frozen_committed")})
    expected_ids = {
        digest({"case_hash": digest(c.model_dump(mode="json")), "arm": a, "budget": b, "repetition": r})
        for c in suite.cases
        for a in suite.experiment.arms
        for b in suite.experiment.budgets
        for r in range(suite.experiment.repetitions)
    }
    if (
        manifest["mode"] != "prepare"
        or manifest["dataset"] != dataset
        or manifest["code"] != expected
        or len(references) != len(expected_ids)
        or {r.sample_id for r in references} != expected_ids
    ):
        raise EvaluationError("v7_reference_freeze_mismatch")
    return suite, manifest, references


def compare_sample(case, sample, reference, inputs, policy):
    if any(getattr(reference, key) != getattr(sample, key) for key in INPUT_KEYS):
        raise EvaluationError("v7_reference_preparation_changed")
    v6_costs = [v6_request_cost(v, v6_quote_inventory(v, sample.supports)) for v in inputs]
    costs = [cost_ledger(v, quote_inventory(v, sample.supports)) for v in inputs]
    status = budget_status(costs, sample.budget, policy)
    diagnostics = []
    for index, c in enumerate(costs):
        components = c["components"]
        values = []
        for evidence in inputs[index].evidence:
            if evidence.evidence_id.startswith("fragment-bundle:"):
                values.extend(r["value"] for r in evidence.content["ranges"])
            elif evidence.evidence_id.startswith("fragment:"):
                values.append(evidence.content["value"])
            else:
                values.extend(
                    v for _, v in leaves({"content": evidence.content, "source_metadata": evidence.source_metadata})
                )
        value_tokens = sum(character_token_estimator().estimate(canonical(v).decode()) for v in values)
        diagnostics.append(
            {
                "call_index": index,
                "selected_values_estimated_tokens": value_tokens,
                "selected_values_alone_over_budget": value_tokens > sample.budget,
                "evidence_wrapper_and_framing_tokens": components["evidence_body"] - value_tokens,
                "over_budget": c["single_estimated_input_tokens"] > sample.budget,
                "evidence_envelope_alone_over_budget": components["evidence_body"] > sample.budget,
                "fixed_protocol_tokens": components["instructions"] + components["output_schema"],
                "components": components,
                "estimated_input_tokens": c["single_estimated_input_tokens"],
                "excess_tokens": max(0, c["single_estimated_input_tokens"] - sample.budget),
            }
        )
    preparation = sample.preparation
    return {
        "sample_id": sample.sample_id,
        "case": case.case_id,
        "arm": sample.arm,
        "budget": sample.budget,
        "repetition": sample.repetition,
        "reference_status": reference.status,
        "calls": len(inputs),
        "reference_inputs_unchanged": True,
        "v6_estimated_input_tokens": sum(c["estimated_input_tokens"] for c in v6_costs),
        "v7_estimated_input_tokens": sum(c["single_estimated_input_tokens"] for c in costs),
        "v7_request_costs": costs,
        "request_diagnostics": diagnostics,
        "budget_overflow": status,
        "cost_accepted": not any(status.values()),
        "reference_required_content_fits": bool(preparation and preparation.required_content_fits),
        "reference_required_claims": preparation.required_count if preparation else 0,
        "reference_retained_claim_hashes": list(preparation.retained_claim_hashes) if preparation else [],
        "model_generation": "not_run",
        "provider_requests": 0,
    }


def finish_comparison(dataset, reference_dir, manifest, rows, frozen, output_dir):
    if identity() != frozen:
        raise EvaluationError("v7_code_changed_during_comparison")
    result = {
        "schema": "evidence-admission.v7.verified-cost-comparison.v2",
        "dataset": dataset,
        "v7_identity": frozen,
        "reference_manifest_hash": digest(manifest),
        "reference_dir": str(reference_dir.resolve()),
        "mode": "offline_no_model_generation",
        "provider_requests": 0,
        "fact_selection_policy_changed": False,
        "samples": rows,
        "production_authorized": False,
    }
    write_result(output_dir / "cost-comparison.json", result)
    write_result(output_dir / "COMPLETE.json", {"result_hash": digest(result)})
    return result


async def cost_compare(dataset: str, reference_dir: Path, output_dir: Path) -> dict[str, Any]:
    if output_dir.exists():
        raise EvaluationError("v7_output_already_exists")
    frozen = identity()
    suite, manifest, references = reference_run(dataset, reference_dir)
    cases = {digest(c.model_dump(mode="json")): c for c in suite.cases}
    rows = []
    for index, reference in enumerate(references):
        case = cases[reference.case_hash]
        probe = InputProbe()
        sample = await run_sample(suite, case, reference.arm, reference.budget, reference.repetition, generator=probe)
        rows.append(compare_sample(case, sample, reference, probe.inputs, suite.experiment.policy_for(reference.arm)))
        if (index + 1) % 90 == 0:
            print(json.dumps({"dataset": dataset, "completed": index + 1, "total": len(references)}), flush=True)
    finish_comparison(dataset, reference_dir, manifest, rows, frozen, output_dir)
    return {"dataset": dataset, "samples": len(rows), "provider_requests": 0}


def verified_results(run_root, prefix, filename, identity_key, expected_identity):
    results = {}
    for name, dataset in DATASETS.items():
        directory = run_root / f"{prefix}-{name}"
        result = read_json(directory / filename)
        if (
            digest(result) != read_json(directory / "COMPLETE.json")["result_hash"]
            or result["dataset"] != dataset
            or result[identity_key] != expected_identity
        ):
            raise EvaluationError("comparison_integrity_or_identity_mismatch")
        _, manifest, references = reference_run(dataset, Path(result["reference_dir"]))
        ids = [r["sample_id"] for r in result["samples"]]
        if (
            len(ids) != len(set(ids))
            or set(ids) != {r.sample_id for r in references}
            or result["reference_manifest_hash"] != digest(manifest)
        ):
            raise EvaluationError("comparison_sample_set_mismatch")
        results[dataset] = result
    return results


def summarize(run_root: Path) -> dict[str, Any]:
    frozen = identity()
    results = verified_results(run_root, "comparison", "cost-comparison.json", "v7_identity", frozen)
    totals = defaultdict(Counter)
    grouped = defaultdict(Counter)
    for dataset, result in results.items():
        for row in result["samples"]:
            count = totals[row["arm"]]
            count["samples"] += 1
            count["calls"] += row["calls"]
            for key in ("v6_estimated_input_tokens", "v7_estimated_input_tokens"):
                count[key] += row[key]
            count["overflow_samples"] += not row["cost_accepted"]
            for key, flag in row["budget_overflow"].items():
                count[key + "_overflow_samples"] += flag
            if row["reference_required_content_fits"]:
                count["reference_required_claims"] += row["reference_required_claims"]
                retained = len(row["reference_retained_claim_hashes"])
                count["reference_retained_claims"] += retained
                count["cost_accepted_claims"] += retained if row["cost_accepted"] else 0
            for request in row["request_diagnostics"]:
                if request["over_budget"]:
                    group = grouped[f"{dataset}/{row['arm']}/{row['budget']}/{row['case']}"]
                    group["over_budget_calls"] += 1
                    group["evidence_envelope_alone_over_budget_calls"] += request["evidence_envelope_alone_over_budget"]
                    group["selected_values_alone_over_budget_calls"] += request["selected_values_alone_over_budget"]
                    group["excess_estimated_tokens"] += request["excess_tokens"]
    candidate = totals["bounded-fragments-v3"]
    result = {
        "schema": "evidence-admission.v7.verified-cost-summary.v2",
        "v7_identity": frozen,
        "samples": sum(len(r["samples"]) for r in results.values()),
        "provider_requests": 0,
        "arms": dict(totals),
        "overflow_attribution_by_dataset_arm_budget_case": dict(grouped),
        "candidate_claims_reference": [candidate["reference_retained_claims"], candidate["reference_required_claims"]],
        "candidate_claims_cost_accepted": [candidate["cost_accepted_claims"], candidate["reference_required_claims"]],
        "candidate_cost_gate_passed": candidate["overflow_samples"] == 0,
        "all_arms_cost_gate_passed": all(c["overflow_samples"] == 0 for c in totals.values()),
        "model_generation": "not_run",
        "provider_attempt_audit": "not_exercised_offline",
        "production_authorized": False,
    }
    write_result(run_root / "summary.json", result)
    return result
