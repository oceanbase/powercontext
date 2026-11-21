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

"""Offline-only preparation and immutable historical replay; no credential/provider initialization."""

from __future__ import annotations

import hashlib
import json
import subprocess
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from powercontext.builtin.artifacts.atomic_memory.extraction import AtomicMemoryCandidate, AtomicMemoryExtractionOutput
from powercontext.builtin.inference import GenerationResult, character_token_estimator

from ..cli import audit_command
from ..live_evaluation import verify_audit_derivation
from ..manifests import PACKAGE_ROOT, EvaluationError, load_suite, read_json
from ..models import canonical, digest
from ..report import code_identity, load_run
from ..runner import instructions_for, run_sample
from .contract import ROOT, identity, protocol
from .fidelity import quote_inventory, request_cost
from .scoring import score_literal_atoms

INPUT_KEYS = (
    "case_hash",
    "arm",
    "budget",
    "repetition",
    "completeness",
    "input_hashes",
    "materializations",
    "projections",
    "source_hashes",
    "definitions",
    "projection_versions",
    "metrics",
    "preparation",
    "supports",
    "omissions",
    "window_attempts",
)


def write_result(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


class InputProbe:
    """The empty local response lets the existing preparation boundary expose its inputs."""

    def __init__(self):
        self.inputs = []

    async def generate(self, value, /):
        self.inputs.append(value)
        return GenerationResult(output=AtomicMemoryExtractionOutput())


def cost_budget_status(costs: list[dict[str, Any]], budget: int, policy) -> dict[str, bool]:
    total = sum(c["estimated_input_tokens"] for c in costs)
    return {
        "per_call": any(c["estimated_input_tokens"] > budget for c in costs),
        "cumulative": bool(policy and total > policy.total_input_budget_multiplier * budget),
        "call_count": bool(policy and len(costs) > policy.max_calls_per_sample),
    }


async def prepare(dataset: str, reference_dir: Path, output_dir: Path) -> dict[str, Any]:
    if output_dir.exists():
        raise EvaluationError("v6_output_already_exists")
    frozen = identity()
    suite = load_suite(experiment_version="v5", dataset=dataset)
    manifest, references, _ = load_run(reference_dir)
    if (
        manifest["mode"] != "prepare"
        or manifest["dataset"] != dataset
        or manifest["code"]
        != {
            **code_identity(suite),
            "revision": manifest["code"]["revision"],
            "frozen_committed": manifest["code"]["frozen_committed"],
        }
    ):
        raise EvaluationError("v6_reference_freeze_mismatch")
    cases = {digest(c.model_dump(mode="json")): c for c in suite.cases}
    counters = defaultdict(Counter)
    rows = []
    for index, reference in enumerate(references):
        case = cases[reference.case_hash]
        probe = InputProbe()
        sample = await run_sample(suite, case, reference.arm, reference.budget, reference.repetition, generator=probe)
        if any(getattr(reference, key) != getattr(sample, key) for key in INPUT_KEYS):
            raise EvaluationError("v6_reference_preparation_changed")
        costs = [request_cost(value, quote_inventory(value, sample.supports)) for value in probe.inputs]
        comparable_v5 = [
            character_token_estimator().estimate(
                canonical(
                    {
                        "instructions": instructions_for(suite),
                        "input": value.model_dump(mode="json"),
                        "output_schema": AtomicMemoryExtractionOutput.model_json_schema(),
                    }
                ).decode()
            )
            for value in probe.inputs
        ]
        budget_status = cost_budget_status(costs, reference.budget, suite.experiment.policy_for(reference.arm))
        overflow = any(budget_status.values())
        eligible = bool(sample.preparation and sample.preparation.required_content_fits)
        missing = sample.preparation.missing_claim_hashes if sample.preparation else ()
        count = counters[reference.arm]
        count["samples"] += 1
        count["prepared_reference_calls"] += len(costs)
        count["v6_estimated_input_tokens"] += sum(c["estimated_input_tokens"] for c in costs)
        count["reference_estimated_input_tokens"] += reference.metrics.prepared_tokens_total
        count["v5_same_basis_estimated_input_tokens"] += sum(comparable_v5)
        count["cost_overflow_samples"] += overflow
        for key, exceeded in budget_status.items():
            count[key + "_overflow_samples"] += exceeded
        if eligible:
            count["required_claims"] += sample.preparation.required_count
            count["reference_retained_claims"] += len(sample.preparation.retained_claim_hashes)
            count["cost_accepted_retained_claims"] += 0 if overflow else len(sample.preparation.retained_claim_hashes)
        rows.append(
            {
                "sample_id": sample.sample_id,
                "case": case.case_id,
                "arm": sample.arm,
                "budget": sample.budget,
                "repetition": sample.repetition,
                "reference_status": reference.status,
                "reference_input_hashes": list(reference.input_hashes),
                "local_probe_calls": len(probe.inputs),
                "provider_requests": 0,
                "jointly_fits_reference_contract": eligible,
                "reference_missing_claim_hashes": list(missing),
                "request_costs": costs,
                "v5_same_basis_estimated_input_tokens": sum(comparable_v5),
                "budget_overflow": budget_status,
                "v6_fixed_selection_cost_accepted": not overflow,
                "new_model_generation": "not_run",
            }
        )
        if (index + 1) % 90 == 0:
            print(json.dumps({"dataset": dataset, "completed": index + 1, "total": len(references)}), flush=True)
    if identity() != frozen:
        raise EvaluationError("v6_code_changed_during_preparation")
    result = {
        "schema": "evidence-admission.v6.preparation.v1",
        "dataset": dataset,
        "v6_identity": frozen,
        "reference_manifest_hash": digest(manifest),
        "reference_code_identity": code_identity(suite),
        "mode": "offline_input_probe_no_model_generation",
        "provider_requests": 0,
        "reference_inputs_unchanged": True,
        "selection_policy_changed": False,
        "cost_scope": "fixed_v5_selected_ranges_with_v6_instructions_input_index_and_output_schema",
        "provider_wire_tokens": None,
        "cost_overflow_rejects_without_silent_reselection": True,
        "arms": dict(counters),
        "samples": rows,
        "production_authorized": False,
    }
    output_dir.mkdir(parents=True)
    write_result(output_dir / "preparation.json", result)
    write_result(output_dir / "COMPLETE.json", {"result_hash": digest(result)})
    return {"dataset": dataset, "samples": len(rows), "provider_requests": 0, "arms": dict(counters)}


def replay_history(session_path: Path, output_dir: Path, legacy_replay_dir: Path | None = None) -> dict[str, Any]:
    if output_dir.exists():
        raise EvaluationError("v6_output_already_exists")
    frozen = identity()
    session = read_json(session_path)
    public, private = Path(session["public_root"]), Path(session["private_root"])
    raw, samples, _ = load_run(public / "primary")
    reviewed, reviewed_samples, _ = load_run(public / "audited-primary")
    if code_identity(load_suite(experiment_version="v5")) != raw["code"]:
        raise EvaluationError("historical_v5_freeze_changed")
    outputs = read_json(private / "primary/outputs.json")
    atom_reviews = read_json(ROOT / "recorded-atom-reviews.json")
    if outputs["manifest_hash"] != digest(raw) or atom_reviews["original_manifest_hash"] != digest(raw):
        raise EvaluationError("historical_output_identity_mismatch")
    output_dir.mkdir(parents=True)
    if legacy_replay_dir is None:
        legacy_replay_dir = output_dir / "legacy-replayed"
        audit_command(
            run_dir=public / "primary",
            output_dir=legacy_replay_dir,
            private_outputs=private / "primary/outputs.json",
            reviews=private / "primary/reviews.json",
            suite_root=PACKAGE_ROOT,
            private_manifest=None,
            experiment_version="v5",
            dataset="synthetic-v1",
        )
    replay_manifest, replayed, replay_report = load_run(legacy_replay_dir)
    verify_audit_derivation(raw, samples, replay_manifest, replayed, mode="audited_live")
    if replay_manifest != reviewed or replayed != reviewed_samples:
        raise EvaluationError("historical_replay_changed_results")
    by_sample = defaultdict(list)
    for output in outputs["outputs"]:
        by_sample[output["sample_id"]].extend(
            AtomicMemoryCandidate.model_validate(c) for c in output["output"]["candidates"]
        )
    suite = load_suite(experiment_version="v5")
    cases = {digest(c.model_dump(mode="json")): c for c in suite.cases}
    target_cases = set(protocol()["failure_cases"])
    target_rows, protection_rows, diagnostics = [], [], []
    for sample in replayed:
        case = cases[sample.case_hash]
        row = {
            "sample_id": sample.sample_id,
            "case": case.case_id,
            "arm": sample.arm,
            "budget": sample.budget,
            "repetition": sample.repetition,
            "status": sample.status,
            "historical_response_evidence": "recorded_validated"
            if sample.generation
            else "missing_failed_response"
            if sample.status == "failed"
            else "no_generation_expected",
        }
        if case.case_id in target_cases:
            target_rows.append(row)
        if case.case_id in protocol()["protection_cases"]:
            protection_rows.append(row)
        if case.case_id == "exact-literals" and sample.generation:
            diagnostics.append(
                {
                    **row,
                    **score_literal_atoms(case, sample, tuple(by_sample[sample.sample_id]), atom_reviews["reviews"]),
                }
            )
    candidate = replay_report["arms"][suite.experiment.candidate_arm]
    # Generation totals in the legacy report include impossible identities; calculate eligible values explicitly.
    eligible = [
        s
        for s in replayed
        if s.arm == suite.experiment.candidate_arm and s.preparation and s.preparation.required_content_fits
    ]
    claims = sum(len(s.generation.retained_claim_hashes) for s in eligible if s.generation)
    literals = sum(len(s.generation.retained_literal_hashes) for s in eligible if s.generation)
    if (claims, literals, len(target_rows)) != (198, 4, 180):
        raise EvaluationError("historical_v5_baseline_not_reproduced")
    if identity() != frozen:
        raise EvaluationError("v6_code_changed_during_replay")
    result = {
        "schema": "evidence-admission.v6.recorded-replay.v1",
        "v6_identity": frozen,
        "original_manifest_hash": digest(raw),
        "verified_legacy_replay_manifest_hash": digest(replay_manifest),
        "audited_manifest_hash": digest(reviewed),
        "all_900_sample_records_identical": True,
        "legacy_candidate_claims": [claims, 216],
        "legacy_candidate_literals": [literals, 63],
        "legacy_candidate_summary": candidate,
        "historical_valid_returns": len(outputs["outputs"]),
        "historical_unrecoverable_failed_responses": sum(s.status == "failed" for s in samples),
        "historical_failed_response_root_cause": "unknown_not_recoverable",
        "failure_matrix": target_rows,
        "protection_matrix": protection_rows,
        "independent_literal_atom_diagnostics": diagnostics,
        "reviewer": "assistant",
        "independent_human_review": False,
        "changed_model_outputs": 0,
        "provider_requests": 0,
        "new_protocol_model_generation": "not_run",
        "production_authorized": False,
    }
    write_result(output_dir / "replay.json", result)
    write_result(output_dir / "COMPLETE.json", {"result_hash": digest(result)})
    return {
        k: result[k]
        for k in (
            "all_900_sample_records_identical",
            "legacy_candidate_claims",
            "legacy_candidate_literals",
            "historical_valid_returns",
            "historical_unrecoverable_failed_responses",
            "provider_requests",
        )
    }


def preservation(session_path: Path, before_path: Path) -> dict[str, Any]:
    session, before = read_json(session_path), read_json(before_path)
    for key in ("public", "private"):
        directory = Path(session[f"{key}_root"])
        current = {
            str(p.relative_to(directory)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in directory.rglob("*")
            if p.is_file()
        }
        if current != before[key]:
            raise EvaluationError("historical_artifacts_changed")
    for name, expected in before["staged_files"].items():
        indexed = subprocess.run(["git", "show", ":" + name], capture_output=True, check=True).stdout
        if (
            hashlib.sha256(Path(name).read_bytes()).hexdigest() != expected
            or hashlib.sha256(indexed).hexdigest() != expected
        ):
            raise EvaluationError("original_staged_files_changed")
    names = subprocess.run(["git", "diff", "--cached", "--name-only", "-z"], capture_output=True, check=True).stdout
    if set(names.decode().split("\0")[:-1]) != set(before["staged_files"]):
        raise EvaluationError("staged_file_set_changed")
    if code_identity(load_suite(experiment_version="v5")) != session["code"]:
        raise EvaluationError("historical_v5_code_changed")
    return {
        "v5_public_artifacts_unchanged": True,
        "v5_private_artifacts_unchanged": True,
        "v5_code_identity_unchanged": True,
        "original_staged_files_unchanged": len(before["staged_files"]),
        "provider_requests_this_phase": 0,
    }


def summarize(run_root: Path, session_path: Path) -> dict[str, Any]:
    results = {}
    for name, dataset in (
        ("primary", "synthetic-v1"),
        ("validation", "validation-v2"),
        ("additional", "validation-v3"),
    ):
        directory = run_root / f"preparation-{name}"
        result = read_json(directory / "preparation.json")
        if (
            digest(result) != read_json(directory / "COMPLETE.json")["result_hash"]
            or result["dataset"] != dataset
            or result["v6_identity"] != identity()
        ):
            raise EvaluationError("v6_preparation_integrity_mismatch")
        results[dataset] = result
    replay = read_json(run_root / "replay/replay.json")
    if (
        digest(replay) != read_json(run_root / "replay/COMPLETE.json")["result_hash"]
        or replay["v6_identity"] != identity()
    ):
        raise EvaluationError("v6_replay_integrity_mismatch")
    totals = defaultdict(Counter)
    for result in results.values():
        for arm, counts in result["arms"].items():
            totals[arm].update(counts)
    candidate = totals["bounded-fragments-v3"]
    cost_failures = sum(a["cost_overflow_samples"] for a in totals.values())
    # Proposed paired canary, never an executable run or authorization.
    proposed = [
        r
        for r in results["synthetic-v1"]["samples"]
        if r["arm"] == "bounded-fragments-v3"
        and (
            r["case"] in protocol()["failure_cases"]
            or (r["case"] in {"small-complete", "negative-control", "forged-role-marker"} and r["budget"] == 4096)
        )
    ]
    potential_calls = 2 * sum(r["local_probe_calls"] for r in proposed)
    potential_input = sum(
        r["v5_same_basis_estimated_input_tokens"] + sum(c["estimated_input_tokens"] for c in r["request_costs"])
        for r in proposed
    )
    atom_totals = defaultdict(Counter)
    for row in replay["independent_literal_atom_diagnostics"]:
        c = atom_totals[row["arm"]]
        c["scorable_recorded_samples"] += 1
        c["retained_atoms_in_scorable_samples"] += len(row["retained_atom_ids"])
        c["unresolved_candidates"] += row["unresolved_candidates"]
        c["composite_candidates"] += row["composite_candidates"]
    result = {
        "schema": "evidence-admission.v6.offline-summary.v1",
        "v6_identity": identity(),
        "samples": sum(len(r["samples"]) for r in results.values()),
        "provider_requests": 0,
        "reference_candidate_claims": [candidate["reference_retained_claims"], candidate["required_claims"]],
        "cost_accepted_candidate_claims": [candidate["cost_accepted_retained_claims"], candidate["required_claims"]],
        "cost_overflow_samples_all_arms": cost_failures,
        "arms": dict(totals),
        "recorded_literal_atoms": dict(atom_totals),
        "atom_comparison_scope": "contract_rescoring_only; failed/rejected_samples_deliver_zero_retained_atoms; all_arms_require_36_atoms",
        "historical_replay": {
            k: replay[k]
            for k in (
                "all_900_sample_records_identical",
                "legacy_candidate_claims",
                "legacy_candidate_literals",
                "historical_valid_returns",
                "historical_unrecoverable_failed_responses",
            )
        },
        "preservation": preservation(session_path, run_root / "history-preservation-before.json"),
        "new_protocol_model_generation": "not_run",
        "production_authorized": False,
        "offline_cost_gate_passed": cost_failures == 0
        and candidate["reference_retained_claims"] == candidate["required_claims"] == 495,
        "online_budget_draft": {
            "status": "blocked_by_offline_cost_gate" if cost_failures else "requires_separate_decision",
            "paired_protocols": ["v5", "v6"],
            "samples": 2 * len(proposed),
            "potential_provider_request_cap": potential_calls,
            "potential_estimated_input_tokens": potential_input,
            "potential_maximum_output_tokens": potential_calls * 2048,
            "v6_budget_violating_sample_identities": [
                r["sample_id"] for r in proposed if not r["v6_fixed_selection_cost_accepted"]
            ],
            "failed_calls_must_not_be_retried_or_replaced": True,
            "currency_cost": None,
            "authorized": False,
        },
    }
    write_result(run_root / "summary.json", result)
    return result
