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

"""Publish only content-free diagnostic facts, preserving failure and review boundaries."""

from __future__ import annotations

from ..manifests import EvaluationError
from ..models import digest
from ..v7.workflow import write_result
from ..v9_live.ledger import money
from .runner import verified_state
from .session import load_session


def report(session_dir, output_dir):
    if output_dir.exists():
        raise EvaluationError("v13_report_output_exists")
    public, data, private = load_session(session_dir)
    state = verified_state(public, data, private)
    rows = []
    for call in data["calls"]:
        settled = state["settled"].get(call["call_id"])
        terminal = state["terminals"].get(settled["attempt_id"], {}) if settled else {}
        review = state["reviews"].get(call["call_id"])
        reviewed = None
        if review:
            from ..manifests import read_json

            reviewed = read_json(private / f"{call['call_id']}.review.json")["result"]
        rows.append(
            {
                "case": call["case_label"],
                "sample_id": call["sample_id"],
                "request_hash": call["request_hash"],
                "budget": call["budget"],
                "planned_estimated_input_tokens": call["cost"]["single_estimated_input_tokens"],
                "outcome": terminal.get("outcome", "not_run"),
                **{
                    k: terminal.get(k)
                    for k in (
                        "http_request_hash",
                        "response_hash",
                        "response_capture_complete",
                        "failure_stage",
                        "error_category",
                        "http_status",
                        "provider_finish_reason",
                        "response_model",
                        "system_fingerprint_hash",
                        "thinking_disabled_verified",
                        "provider_request_started",
                        "provider_response_received",
                        "pipeline_entered",
                        "pipeline_returned_successfully",
                        "structured_output_validated",
                        "protocol_output_validated",
                        "input_tokens",
                        "output_tokens",
                        "reasoning_tokens",
                        "final_content_characters",
                        "generation_elapsed_seconds",
                        "copy_parts",
                    )
                },
                "output_hash": settled["output_hash"] if settled else None,
                "review": reviewed,
                "quality_scored": reviewed is not None,
            }
        )
    diagnostic_passed = (
        not state["stop_required"]
        and len(state["reviews"]) == len(rows)
        and all(r["candidate_gate_passed"] for r in state["reviews"].values())
    )
    carry = public["budget_carry_forward"]
    combined = money(carry["prior_known_upper_charge_cny"]) + money(state["known_upper_charge_cny"])
    result = {
        "schema": "evidence-admission.v13.live-diagnostic.v1",
        "decision": "insufficient_evidence",
        "execution_mode": public["execution_mode"],
        "freeze": public["freeze"],
        "identity_hash": digest(public["identity"]),
        "plan_hash": digest(public),
        "provider": public["provider"],
        "thinking": {"type": "disabled"},
        "provider_requests": state["audit"]["http_requests"] if public["execution_mode"] == "live" else 0,
        "mock_http_requests": state["audit"]["http_requests"] if public["execution_mode"] != "live" else 0,
        "cases": rows,
        "diagnostic_passed": diagnostic_passed,
        "stop_required": state["stop_required"],
        "known_upper_charge_cny": state["known_upper_charge_cny"] if public["execution_mode"] == "live" else None,
        "combined_known_upper_charge_cny": str(combined) if public["execution_mode"] == "live" else None,
        "remaining_conditional_budget_cny": str(money(carry["approved_total_cny"]) - combined)
        if public["execution_mode"] == "live"
        else None,
        "held_reservation_cny": state["held_reservation_cny"],
        "unknown_usage_attempts": len(state["unknown_usage_call_ids"]),
        "actual_invoice_fee_cny": None,
        "formal_acceptance": False,
        "production_authorized": False,
        "formal_blockers": [
            "diagnostic_single_arm_only",
            "formal_all_arm_matrix_not_run",
            "independent_critical_review_not_completed",
        ],
    }
    write_result(output_dir / "summary.json", result)
    write_result(
        output_dir / "COMPLETE.json",
        {
            "schema": "evidence-admission.v13.diagnostic-artifact.v1",
            "result_hash": digest(result),
            "artifact_complete": True,
            "diagnostic_passed": diagnostic_passed,
            "formal_acceptance": False,
            "production_authorized": False,
        },
    )
    return {
        "diagnostic_passed": diagnostic_passed,
        "decision": "insufficient_evidence",
        "provider_requests": result["provider_requests"],
        "formal_acceptance": False,
    }
