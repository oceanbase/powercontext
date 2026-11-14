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

"""Whitelisted public reports and paired end-to-end results, including rejected controls."""

from __future__ import annotations

from collections import Counter, defaultdict
from decimal import Decimal

from ..manifests import EvaluationError, read_json
from ..models import digest
from .core import ARMS, contract, dump, offline_summary
from .workflow import calls_for, charge, load, reservation


def summarize(directory):
    manifest = load(directory, committed=False)
    state = read_json(directory / "state.json")
    records = state["records"]
    expected = {digest(c): c for c in calls_for(manifest)}
    if len({r["call_id"] for r in records}) != len(records) or any(r["call_id"] not in expected for r in records):
        raise EvaluationError("v18_duplicate_or_unplanned_result")
    by_row = defaultdict(list)
    counters = defaultdict(Counter)
    public_records = []
    audit_unavailable = 0
    unscored_required = 0
    for record in records:
        if {k: record[k] for k in ("row_id", "input_index", "input_hash")} != expected[record["call_id"]]:
            raise EvaluationError("v18_call_result_binding_changed")
        derivative = directory / "reviews" / (record["call_id"] + ".json")
        score = record.get("score")
        if derivative.exists():
            reviewed = read_json(derivative)
            if reviewed["original_record_hash"] != digest(record):
                raise EvaluationError("v18_review_derivation_changed")
            score = reviewed["score"]
        raw = directory / "audit" / (record["call_id"] + ".response-bytes.json")
        if not raw.exists() or digest(raw.read_bytes().hex()) != record.get("response_hash"):
            audit_unavailable += 1
        else:
            try:
                response = read_json(raw)
            except EvaluationError:
                response = {}
            usage = response.get("usage") if isinstance(response, dict) else None
            if charge(usage) != (
                Decimal(record["known_charge_cny"]) if record.get("known_charge_cny") is not None else None
            ):
                raise EvaluationError("v18_usage_receipt_changed")
        score_required = record.get("score_required", record["outcome"] == "returned")
        if score_required and score is None:
            unscored_required += 1
        effective = {**record, "score": score}
        by_row[record["row_id"]].append(effective)
        usage = record.get("provider_usage") or {}
        public = {
            **effective,
            "provider_usage": {
                k: usage[k] for k in ("prompt_tokens", "completion_tokens", "total_tokens") if type(usage.get(k)) is int
            },
        }
        public_records.append(public)
    rows = []
    grouped = defaultdict(dict)
    quality_failures = unresolved = 0
    for row in manifest["rows"]:
        actual = by_row[row["row_id"]]
        c = counters[row["arm"]]
        c["rows"] += 1
        c["planned_calls"] += row["planned_calls"]
        c["executed_calls"] += len(actual)
        missing = row["planned_calls"] - len(actual)
        c["unrun_calls"] += missing
        scoring_incomplete = sum(
            r.get("score_required", r["outcome"] == "returned") and r.get("score") is None for r in actual
        )
        c["scoring_incomplete_calls"] += scoring_incomplete
        retained = {
            h
            for r in actual
            if r["outcome"] == "returned"
            for h in (r.get("score") or {}).get("retained_atom_hashes", [])
        }
        critical = 0
        unreviewed = 0
        for record in actual:
            c[record["outcome"]] += 1
            usage = record.get("provider_usage") or {}
            for key in ("prompt_tokens", "completion_tokens"):
                if type(usage.get(key)) is int:
                    c[key] += usage[key]
            scored = record.get("score")
            if scored:
                if record["outcome"] == "returned":
                    c["qualified_retained_literals"] += scored["retained_literals"]
                c["raw_literal_occurrences_descriptive"] += scored["raw_literal_occurrences"]
                for key in (
                    "unsupported_candidates",
                    "false_attribution_candidates",
                    "invalid_support_candidates",
                    "unresolved_candidates",
                    "nonatomic_candidates",
                    "literal_mismatches",
                    "exact_duplicates",
                    "near_duplicates",
                ):
                    c[key] += scored[key]
                critical += sum(
                    scored[k]
                    for k in ("unsupported_candidates", "false_attribution_candidates", "invalid_support_candidates")
                )
                unreviewed += scored["unresolved_candidates"]
        prep = row["sample"]["preparation"]
        eligible = bool(prep and prep["required_content_fits"])
        required = set(row["required_atom_hashes"])
        if eligible:
            c["eligible_required_atoms"] += len(required)
            c["eligible_proven_delivered_atoms"] += len(retained & required)
            c["eligible_required_literals"] += prep["exact_literals_total"]
            c["eligible_qualified_retained_literals"] += sum(
                (r.get("score") or {}).get("retained_literals", 0) for r in actual if r["outcome"] == "returned"
            )
        required_literals = set(row["required_literal_hashes"])
        raw_literals = {
            h
            for r in actual
            if r["outcome"] == "returned"
            for h in (r.get("score") or {}).get("raw_present_literal_hashes", [])
        }
        literal_measured = row["planned_calls"] > 0 and missing == 0 and scoring_incomplete == 0
        known_literal_absence = eligible and literal_measured and bool(required_literals - raw_literals)
        failed_output = any(r["outcome"] != "returned" for r in actual)
        failure = (
            critical > 0
            or failed_output
            or known_literal_absence
            or eligible
            and missing == 0
            and retained != required
            and unreviewed == 0
        )
        quality_failures += bool(failure and row["arm"] == ARMS[-1])
        unresolved += unreviewed
        outcome = {
            "row_id": row["row_id"],
            "dataset": row["dataset"],
            "case": row["case"],
            "arm": row["arm"],
            "budget": row["budget"],
            "repetition": row["repetition"],
            "admission_status": row["sample"]["status"],
            "completeness": row["sample"]["completeness"],
            "planned_calls": row["planned_calls"],
            "executed_calls": len(actual),
            "unrun_calls": missing,
            "scoring_incomplete_calls": scoring_incomplete,
            "zero_call_reason": row["sample"]["reason"] if row["planned_calls"] == 0 else None,
            "eligible": eligible,
            "required_atom_hashes": sorted(required),
            "proven_delivered_atom_hashes": sorted(retained),
            "semantic_unresolved": unreviewed,
            "known_quality_failure": bool(failure),
            "failed_output": failed_output,
            "known_literal_absence": bool(known_literal_absence),
            "literal_output_measured": literal_measured,
            "literal_presence_basis": "Pipeline_accepted_candidates; rejected_output_occurrences_are_separate_diagnostics",
            "required_literal_hashes": sorted(required_literals),
            "raw_present_literal_hashes": sorted(raw_literals),
            "measurement_complete": missing == 0 and scoring_incomplete == 0,
            "preparation": {
                k: row["sample"][k]
                for k in (
                    "input_hashes",
                    "source_hashes",
                    "materializations",
                    "projections",
                    "projection_versions",
                    "metrics",
                    "omissions",
                    "supports",
                    "window_attempts",
                    "preparation",
                    "materialization_seconds",
                    "admission_seconds",
                    "cancellation_seconds",
                    "memory_mutations",
                    "published_cursor",
                )
            },
        }
        rows.append(outcome)
        grouped[(row["dataset"], row["case"], row["budget"], row["repetition"])][row["arm"]] = outcome
    pairs = []
    for key, group in grouped.items():
        candidate = group[ARMS[-1]]
        for control in ARMS[:-1]:
            baseline = group[control]
            a, b = set(candidate["proven_delivered_atom_hashes"]), set(baseline["proven_delivered_atom_hashes"])
            pairs.append(
                {
                    "dataset": key[0],
                    "case": key[1],
                    "budget": key[2],
                    "repetition": key[3],
                    "control": control,
                    "both_measured": candidate["measurement_complete"] and baseline["measurement_complete"],
                    "both_executed": candidate["executed_calls"] > 0 and baseline["executed_calls"] > 0,
                    "both_semantics_resolved": candidate["semantic_unresolved"] == baseline["semantic_unresolved"] == 0,
                    "candidate_additional_proven_atoms": sorted(a - b),
                    "candidate_missing_relative_proven_atoms": sorted(b - a),
                    "proven_set_difference_is_not_unresolved_semantic_loss": True,
                }
            )
    unknown_usage = sum(r.get("known_charge_cny") is None for r in records)
    complete = (
        state["state"] == "finished"
        and len(records) == len(expected)
        and unknown_usage == 0
        and audit_unavailable == 0
        and unscored_required == 0
    )
    blockers = []
    if not complete:
        blockers.append("live_coverage_or_execution_incomplete")
    if unscored_required:
        blockers.append("scoring_incomplete")
    if unresolved:
        blockers.append("critical_semantics_unresolved")
    if audit_unavailable:
        blockers.append("raw_audit_unavailable_or_hash_mismatch")
    # No positive claim without established per-row gates and reproducible window-control improvement.
    candidate_rows = [r for r in rows if r["arm"] == ARMS[-1] and r["eligible"]]
    input_loss = any(
        set(r["required_atom_hashes"]) - set(r["admitted_atom_hashes"])
        for r in manifest["rows"]
        if r["arm"] == ARMS[-1] and r["sample"]["preparation"] and r["sample"]["preparation"]["required_content_fits"]
    )
    window_floor_rejections = {
        r["row_id"]
        for r in manifest["rows"]
        if r["arm"] == "window-reduction-v1"
        and r["sample"]["status"] == "budget_rejected"
        and any(end - start == 1 and not fits for start, end, _, fits in r["sample"]["window_attempts"])
    }
    improvement = any(
        p["control"] == "window-reduction-v1"
        and p["both_measured"]
        and p["both_semantics_resolved"]
        and p["candidate_additional_proven_atoms"]
        and p["case"] in {"single-source-overflow", "mixed-scaffolding", "oversized-tool-field"}
        and any(
            r["row_id"] in window_floor_rejections
            and r["dataset"] == p["dataset"]
            and r["case"] == p["case"]
            and r["budget"] == p["budget"]
            and r["repetition"] == p["repetition"]
            for r in manifest["rows"]
        )
        for p in pairs
    )
    live_complete = complete and state.get("mode") == "live"
    if state.get("mode") != "live":
        blockers.append("not_a_live_execution")
    passed = live_complete and not unresolved and not input_loss and quality_failures == 0 and improvement
    passed = passed and all(not r["known_quality_failure"] for r in candidate_rows)
    passed = (
        passed and counters[ARMS[-1]]["nonatomic_candidates"] == 0 and counters[ARMS[-1]]["literal_mismatches"] == 0
    )
    decision = "insufficient_evidence"
    if live_complete and not unresolved:
        decision = "bounded_projection_follow_up_required" if passed else "no_production_change"
    return {
        "schema": "evidence-admission.v18.report.v1",
        "decision": decision,
        "identity_hash": manifest["identity_hash"],
        "freeze": state.get("freeze"),
        "state": state["state"],
        "preparation": offline_summary(manifest),
        "execution_complete": complete,
        "execution_mode": state.get("mode", "not_started"),
        "live_execution_complete": live_complete,
        "quality_gate_passed": bool(passed),
        "semantic_unresolved_candidates": unresolved,
        "candidate_known_quality_failure_rows": quality_failures,
        "candidate_input_loss": input_loss,
        "reproducible_window_improvement": improvement,
        "arms": dict(counters),
        "rows": rows,
        "pairs": pairs,
        "records": public_records,
        "unknown_usage_calls": unknown_usage,
        "scoring_incomplete_calls": unscored_required,
        "raw_audit_unavailable_calls": audit_unavailable,
        "formal_blockers": blockers,
        "budget": {
            "scope": "v9_and_later",
            "approved_cny": contract()["budget_total_cny"],
            "prior_known_upper_cny": contract()["prior_known_upper_cny"],
            "new_known_upper_cny": state["known_charge_cny"],
            "cumulative_known_upper_cny": str(
                Decimal(contract()["prior_known_upper_cny"]) + Decimal(state["known_charge_cny"])
            ),
            "unknown_usage_is_zero": False,
            "full_context_reservation_cny": str(reservation()),
            "unrecorded_in_flight_call": state["state"] == "in_flight"
            and state.get("in_flight") not in {r["call_id"] for r in records},
            "unknown_reserved_upper_cny": str(
                reservation()
                * (
                    unknown_usage
                    + int(
                        state["state"] == "in_flight" and state.get("in_flight") not in {r["call_id"] for r in records}
                    )
                )
            ),
            "account_invoice": False,
        },
        "limitations": [
            "known_synthetic_regression_sets_not_unseen_holdout",
            "local_SQLite_not_remote_backend_acceptance",
            "native_character_estimator_not_provider_token_hard_bound",
            "citations_do_not_prove_model_use",
            "extraction_output_only_no_Memory_publication",
            "model_alias_not_physical_snapshot",
            "semantic_paraphrases_need_bound_review_not_automatic_approval",
        ],
        "production_authorized": False,
        "follow_up_contract_required": decision == "bounded_projection_follow_up_required",
    }


def export(directory, output):
    result = summarize(directory)
    dump(output, result, new=True)
    return {
        "decision": result["decision"],
        "state": result["state"],
        "execution_complete": result["execution_complete"],
        "report_hash": digest(result),
    }
