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

"""Paired offline v7/v8 comparison with independent, post-packing coverage scoring."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from types import SimpleNamespace

from ..manifests import EvaluationError, case_catalog
from ..models import digest
from ..runner import run_sample
from ..v6.workflow import InputProbe
from ..v7.contract import identity as v7_identity
from ..v7.fidelity import quote_inventory
from ..v7.workflow import (
    compare_sample,
    finish_comparison,
    reference_run,
    verified_results,
    write_result,
)
from .admission import pack, range_units
from .contract import identity


def selection_manifest(inputs, supports):
    """Compare selected values with provenance; evidence ids may change after deriving a projection."""
    rows = []
    for value in inputs:
        for evidence in value.evidence:
            for parent, row, matched, _ in range_units(evidence, supports):
                rows.append(
                    digest(
                        {
                            "source_ref": parent.source_ref.model_dump(mode="json"),
                            "journal_position": parent.journal_position,
                            "via_artifact": parent.via_artifact.model_dump(mode="json")
                            if parent.via_artifact
                            else None,
                            "range": row,
                            "supports": sorted({digest(s.model_dump(exclude={"evidence_id"})) for s in matched}),
                        }
                    )
                )
    return sorted(rows)


def covers(quotes, claim, text):
    """Match exact bytes at the frozen claim offset, including adjacent selected ranges."""
    start, end = claim["byte_start"], claim["byte_end"]
    raw = text.encode()
    pieces = []
    for q in quotes:
        s = q.support
        if s.source_hash == claim["source_hash"] and s.field_hash == claim["field_hash"]:
            left = max(start, q.source_byte_start)
            right = min(end, q.source_byte_start + len(q.value.encode()))
            if left < right:
                actual = q.value.encode()[left - q.source_byte_start : right - q.source_byte_start]
                if actual != raw[left - start : right - start]:
                    return False
                pieces.append((left, right))
    reached = start
    for left, right in sorted(pieces):
        if left > reached:
            break
        reached = max(reached, right)
    return reached >= end


def coverage(case, requests):
    """Labels are used here only, after the label-free packing decision is complete."""
    catalog = {c["claim_hash"]: c for c in case_catalog(case)["claims"] if c["required"]}
    quote_sets = [quote_inventory(p.value, p.supports) for p in requests]
    all_quotes = tuple(q for quotes in quote_sets for q in quotes)
    union, visible, literals, visible_literals = [], [], [], []
    for claim in case.required:
        key = digest(claim.claim_id)
        target = catalog[key]
        if covers(all_quotes, target, claim.text):
            union.append(key)
        if any(covers(qs, target, claim.text) for qs in quote_sets):
            visible.append(key)
        for index, literal in enumerate(claim.exact_literals):
            left = target["byte_start"] + len(claim.text[: claim.text.index(literal)].encode())
            span = {**target, "byte_start": left, "byte_end": left + len(literal.encode())}
            literal_key = digest({"claim": claim.claim_id, "literal": index})
            if covers(all_quotes, span, literal):
                literals.append(literal_key)
            if any(covers(qs, span, literal) for qs in quote_sets):
                visible_literals.append(literal_key)
    return {
        "retained_claim_hashes": union,
        "same_request_claim_hashes": visible,
        "retained_literal_hashes": literals,
        "same_request_literal_hashes": visible_literals,
    }


async def compare(dataset, reference_dir, output_dir, v7_output_dir):
    if output_dir.exists() or v7_output_dir.exists():
        raise EvaluationError("comparison_output_already_exists")
    frozen, v7_frozen = identity(), v7_identity()
    suite, manifest, references = reference_run(dataset, reference_dir)
    cases = {digest(c.model_dump(mode="json")): c for c in suite.cases}
    rows, v7_rows = [], []
    contract = frozen["contract"]
    for index, reference in enumerate(references):
        case = cases[reference.case_hash]
        probe = InputProbe()
        sample = await run_sample(suite, case, reference.arm, reference.budget, reference.repetition, generator=probe)
        old_policy = suite.experiment.policy_for(reference.arm)
        old = compare_sample(case, sample, reference, probe.inputs, old_policy)
        v7_rows.append(old)
        policy = old_policy or SimpleNamespace(
            max_calls_per_sample=contract["default_max_calls_per_sample"],
            total_input_budget_multiplier=contract["default_total_input_budget_multiplier"],
        )
        plan = pack(probe.inputs, sample.supports, sample.budget, policy)
        inputs = [r.value for r in plan.requests]
        supports = tuple(s for r in plan.requests for s in r.supports)
        before = selection_manifest(probe.inputs, sample.supports)
        after = selection_manifest(inputs, supports)
        preserved = before == after
        if plan.accepted and not preserved:
            raise EvaluationError("v8_selected_content_changed")
        score = coverage(case, plan.requests)
        old_score = coverage(case, [SimpleNamespace(value=v, supports=sample.supports) for v in probe.inputs])
        old_retained = set(old["reference_retained_claim_hashes"])
        if old_retained != set(old_score["retained_claim_hashes"]):
            raise EvaluationError("v8_reference_coverage_not_reproduced")
        calls = len(plan.requests) if plan.accepted else 0
        rows.append(
            {
                "sample_id": sample.sample_id,
                "case": case.case_id,
                "arm": sample.arm,
                "budget": sample.budget,
                "repetition": sample.repetition,
                "reference_status": reference.status,
                "reference_required_content_fits": old["reference_required_content_fits"],
                "reference_required_claims": old["reference_required_claims"],
                "reference_retained_claim_hashes": old["reference_retained_claim_hashes"],
                "reference_retained_literal_hashes": old_score["retained_literal_hashes"],
                "reference_same_request_claim_hashes": old_score["same_request_claim_hashes"],
                "v7_cost_accepted": old["cost_accepted"],
                "v7_calls": old["calls"],
                "v7_estimated_input_tokens": old["v7_estimated_input_tokens"],
                "plan_accepted": plan.accepted,
                "plan_reason": plan.reason,
                "selection_preserved": preserved,
                "selected_inventory_hash": digest(before),
                "planned_inventory_hash": digest(after),
                "budget_overflow": plan.overflow,
                "budget_caps": {
                    "single_input": sample.budget,
                    "cumulative_input": sample.budget * policy.total_input_budget_multiplier,
                    "calls": policy.max_calls_per_sample,
                },
                "diagnostic_plan_calls": len(plan.requests),
                "executable_calls": calls,
                "diagnostic_plan_estimated_input_tokens": sum(
                    p.cost["single_estimated_input_tokens"] for p in plan.requests
                ),
                "executable_estimated_input_tokens": sum(p.cost["single_estimated_input_tokens"] for p in plan.requests)
                if plan.accepted
                else 0,
                "executable_maximum_output_tokens": calls * suite.experiment.max_output_tokens,
                "requests": [{"cost": p.cost, "request_binding_hash": p.binding_hash} for p in plan.requests],
                "coverage": score,
                "provider_requests": 0,
                "model_generation": "not_run",
            }
        )
        if (index + 1) % 90 == 0:
            print(json.dumps({"dataset": dataset, "completed": index + 1, "total": len(references)}), flush=True)
    if identity() != frozen:
        raise EvaluationError("v8_code_changed_during_comparison")
    finish_comparison(dataset, reference_dir, manifest, v7_rows, v7_frozen, v7_output_dir)
    result = {
        "schema": "evidence-admission.v8.offline-comparison.v1",
        "dataset": dataset,
        "v8_identity": frozen,
        "reference_manifest_hash": digest(manifest),
        "reference_dir": str(reference_dir.resolve()),
        "samples": rows,
        "provider_requests": 0,
        "model_generation": "not_run",
        "production_authorized": False,
    }
    write_result(output_dir / "comparison.json", result)
    write_result(output_dir / "COMPLETE.json", {"result_hash": digest(result)})
    return {"dataset": dataset, "samples": len(rows), "provider_requests": 0}


def summarize(run_root):
    frozen = identity()
    results = verified_results(run_root, "comparison", "comparison.json", "v8_identity", frozen)
    totals, failures = defaultdict(Counter), []
    for dataset, result in results.items():
        for row in result["samples"]:
            count = totals[row["arm"]]
            count["samples"] += 1
            count["plan_rejected_samples"] += not row["plan_accepted"]
            count["selection_changed_samples"] += not row["selection_preserved"]
            for key in (
                "v7_calls",
                "v7_estimated_input_tokens",
                "executable_calls",
                "diagnostic_plan_calls",
                "diagnostic_plan_estimated_input_tokens",
                "executable_estimated_input_tokens",
                "executable_maximum_output_tokens",
            ):
                count[key] += row[key]
            for key, flag in row["budget_overflow"].items():
                count[key + "_overflow_samples"] += flag
            same_request_lost = set(row["reference_same_request_claim_hashes"]) - set(
                row["coverage"]["same_request_claim_hashes"]
            )
            literal_lost = set(row["reference_retained_literal_hashes"]) - set(
                row["coverage"]["retained_literal_hashes"]
            )
            count["same_request_regressed_claims"] += len(same_request_lost)
            count["union_regressed_literals"] += len(literal_lost)
            if row["reference_required_content_fits"]:
                count["required_claims"] += row["reference_required_claims"]
                count["reference_retained_claims"] += len(row["reference_retained_claim_hashes"])
                count["planned_union_claims"] += len(row["coverage"]["retained_claim_hashes"])
                if row["plan_accepted"]:
                    count["cost_accepted_claims"] += len(row["coverage"]["retained_claim_hashes"])
                    count["cost_accepted_same_request_claims"] += len(row["coverage"]["same_request_claim_hashes"])
            if not row["plan_accepted"] or same_request_lost or literal_lost:
                failures.append(
                    {
                        "dataset": dataset,
                        **{k: row[k] for k in ("sample_id", "case", "arm", "budget", "repetition", "plan_reason")},
                        "same_request_regressed_claims": sorted(same_request_lost),
                        "union_regressed_literals": sorted(literal_lost),
                    }
                )
    candidate = totals["bounded-fragments-v3"]
    candidate_gate = (
        candidate["plan_rejected_samples"] == 0
        and candidate["cost_accepted_claims"]
        == candidate["cost_accepted_same_request_claims"]
        == candidate["reference_retained_claims"]
        == candidate["required_claims"]
        == 495
        and candidate["same_request_regressed_claims"] == candidate["union_regressed_literals"] == 0
    )
    proposed = [
        r
        for r in results["synthetic-v1"]["samples"]
        if r["arm"] == "bounded-fragments-v3"
        and (
            r["case"] in {"mixed-scaffolding", "exact-literals", "unicode-boundaries", "impossible-completeness"}
            or r["case"] in {"small-complete", "negative-control", "forged-role-marker"}
            and r["budget"] == 4096
        )
    ]
    result = {
        "schema": "evidence-admission.v8.offline-summary.v1",
        "v8_identity": frozen,
        "samples": sum(len(r["samples"]) for r in results.values()),
        "arms": dict(totals),
        "failures": failures,
        "candidate_offline_gate_passed": candidate_gate,
        "all_arms_offline_gate_passed": candidate_gate and not failures,
        "provider_requests": 0,
        "model_generation": "not_run",
        "production_authorized": False,
        "online_budget_draft": {
            "authorized": False,
            "status": "requires_separate_decision" if candidate_gate and not failures else "blocked_by_offline_gate",
            "paired_sample_identities": 2 * len(proposed),
            "candidate_protocol_executable_call_cap": sum(r["executable_calls"] for r in proposed),
            "candidate_protocol_estimated_input_tokens": sum(r["executable_estimated_input_tokens"] for r in proposed),
            "candidate_protocol_maximum_output_tokens": sum(r["executable_maximum_output_tokens"] for r in proposed),
            "baseline_wire_budget_and_provider_overhead": "requires_separate_provider_preflight",
            "currency_cost": None,
        },
    }
    write_result(run_root / "summary.json", result)
    return result
