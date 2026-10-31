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

"""One bounded SDK request per step, with durable accounting and explicit review gates."""

from __future__ import annotations

import asyncio
import json
import os
import stat

import httpx
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from ..manifests import EvaluationError, read_json
from ..models import digest
from ..v9.codec import TemplateOutput, render
from ..v9_live.ledger import Ledger, exclusive, money, reservation, settlement_facts, write_private
from ..v9_live.runner import expected_attempt
from ..v9_preflight.adapter import Config
from ..v9_preflight.adapter import summary as audit_summary
from .adapter import ResumableJournal, extract
from .freeze import committed_freeze
from .review import evaluate, packet
from .session import call_request, load_session


class Approval(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    plan_hash: str
    live_authorized: StrictBool
    approved_by: str = Field(min_length=1)
    currency: str
    limit_cny: str
    max_cases: int = Field(ge=1, le=2)
    accept_conditional_billing_envelope: StrictBool
    accept_current_model_alias_route: StrictBool


def validate_approval(public, raw):
    approval = Approval.model_validate(raw)
    if (
        approval.plan_hash != digest(public)
        or approval.live_authorized != (public["execution_mode"] == "live")
        or approval.currency != "CNY"
        or not approval.approved_by.strip()
        or not approval.accept_conditional_billing_envelope
        or not approval.accept_current_model_alias_route
        or money(approval.limit_cny) <= 0
        or money(approval.limit_cny) > money(public["budget_carry_forward"]["remaining_conditional_cny"])
    ):
        raise EvaluationError("v13_plan_bound_authorization_required")
    return approval


def verified_state(public, data, private):
    for path in [private, *private.rglob("*")]:
        expected_mode = 0o700 if path.is_dir() else 0o600
        if path.is_symlink() or stat.S_IMODE(path.stat().st_mode) != expected_mode:
            raise EvaluationError("v13_private_permissions_changed")
    ledger = Ledger(private)
    if ledger.rows()[0]["plan_hash"] != digest(public) or ledger.rows()[0]["mode"] != public["execution_mode"]:
        raise EvaluationError("v13_ledger_plan_changed")
    state = ledger.state()
    audit = audit_summary(private / "audit")
    events = [json.loads(line) for line in (private / "audit/attempts.jsonl").read_bytes().splitlines()]
    starts = {e["attempt_id"]: e for e in events if e["kind"] == "start"}
    terminals = {e["attempt_id"]: e for e in events if e["kind"] == "terminal"}
    calls = {c["call_id"]: c for c in data["calls"]}
    if not set(state["reserved"]) <= calls.keys() or not set(starts) <= {
        r["attempt_id"] for r in state["reserved"].values()
    }:
        raise EvaluationError("v13_unplanned_attempt")
    for call_id, reserved in state["reserved"].items():
        call = calls[call_id]
        if reserved["request_hash"] != call["request_hash"]:
            raise EvaluationError("v13_reserved_request_changed")
        start = starts.get(reserved["attempt_id"])
        if start and start["input_hash"] != call["request_hash"]:
            raise EvaluationError("v13_audit_binding_changed")
    for call_id, settled in state["settled"].items():
        terminal = terminals.get(settled["attempt_id"])
        if terminal is None:
            raise EvaluationError("v13_settlement_without_terminal")
        expected = settlement_facts(terminal, settled["output_hash"], calls[call_id]["budget"])
        if any(settled[k] != v for k, v in expected.items()):
            raise EvaluationError("v13_settlement_changed")
        if settled["output_hash"] is not None:
            output = read_json(private / f"{call_id}.output.json")
            if digest(output) != settled["output_hash"]:
                raise EvaluationError("v13_output_changed")
            response = read_json(private / "audit" / f"{settled['attempt_id']}.response.bin")
            template = TemplateOutput.model_validate_json(response["choices"][0]["message"]["content"])
            request = call_request(calls[call_id])
            restored = render(template, request.encoded, request_hash=request.encoded.binding_hash).model_dump(
                mode="json"
            )
            copy_parts = sum(p.type == "copy" for c in template.candidates for p in c.parts)
            if (
                restored != output
                or terminal.get("copy_parts") != copy_parts
                or not terminal.get("pipeline_returned_successfully")
            ):
                raise EvaluationError("v13_pipeline_output_or_copy_binding_changed")
    for call_id, record in state["reviews"].items():
        if call_id not in state["settled"]:
            raise EvaluationError("v13_review_without_output")
        receipt = read_json(private / f"{call_id}.review.json")
        terminal = terminals[state["settled"][call_id]["attempt_id"]]
        computed = evaluate(
            calls[call_id],
            read_json(private / f"{call_id}.output.json"),
            receipt["review"],
            copy_parts=terminal["copy_parts"],
        )
        if (
            digest(receipt) != record["receipt_hash"]
            or computed != receipt["result"]
            or (computed["candidate_gate_passed"] != record["candidate_gate_passed"])
        ):
            raise EvaluationError("v13_review_receipt_changed")
    metadata = {
        (e.get("response_model"), e.get("system_fingerprint_hash"))
        for e in terminals.values()
        if e.get("http_status") == 200
    }
    state["stop_required"] = bool(
        state["stop_required"]
        or not audit["attempt_coverage_complete"]
        or len(metadata) > 1
        or any(not r["candidate_gate_passed"] for r in state["reviews"].values())
    )
    state["audit"] = audit
    state["terminals"] = terminals
    return state


def status(directory):
    public, data, private = load_session(directory)
    state = verified_state(public, data, private)
    live = public["execution_mode"] == "live"
    return {
        "execution_mode": public["execution_mode"],
        "plan_hash": digest(public),
        "planned_calls": len(data["calls"]),
        "settled_calls": len(state["settled"]),
        "reviewed_cases": len(state["reviews"]),
        "stop_required": state["stop_required"],
        "known_upper_charge_cny": state["known_upper_charge_cny"],
        "held_reservation_cny": state["held_reservation_cny"],
        "unknown_usage_call_ids": state["unknown_usage_call_ids"],
        "provider_requests": state["audit"]["http_requests"] if live else 0,
        "mock_http_requests": 0 if live else state["audit"]["http_requests"],
        "usage_values_are_simulated": not live,
        "actual_invoice_fee": None,
        "formal_acceptance": False,
        "production_authorized": False,
    }


def review_paths(private, call, output):
    value = packet(call, output)
    for suffix, content in (("packet", value), ("template", value["review_template"])):
        path = private / f"{call['call_id']}.{suffix}.json"
        if path.exists():
            if read_json(path) != content:
                raise EvaluationError("v13_review_packet_changed")
        else:
            write_private(path, content)
    return {
        "private_review_packet": str(private / f"{call['call_id']}.packet.json"),
        "private_review_template": str(private / f"{call['call_id']}.template.json"),
    }


async def run_next(directory, raw_approval, *, transport=None, api_key=None):
    public, data, private = load_session(directory)
    approval = validate_approval(public, raw_approval)
    live = public["execution_mode"] == "live"
    if (live and transport is not None) or (not live and not isinstance(transport, httpx.MockTransport)):
        raise EvaluationError("v13_transport_mode_mismatch")
    if live and committed_freeze() != public["freeze"]:
        raise EvaluationError("v13_committed_freeze_changed")
    with exclusive(private):
        state = verified_state(public, data, private)
        if state["stop_required"]:
            raise EvaluationError("v13_session_stopped_never_retry")
        call = next((c for c in data["calls"] if c["call_id"] not in state["reviews"]), None)
        if call is None:
            return {"state": "all_cases_reviewed", **status(directory)}
        if data["calls"].index(call) >= approval.max_cases:
            raise EvaluationError("v13_approved_case_scope_exhausted")
        if call["call_id"] not in state["settled"]:
            if money(state["known_upper_charge_cny"]) + reservation() > money(approval.limit_cny):
                raise EvaluationError("insufficient_currency_reservation")
            key = api_key if api_key is not None else os.environ.get("DEEPSEEK_API_KEY") if live else None
            if not key or not key.strip():
                raise EvaluationError("configured_api_key_unavailable")
            prior = state["authorization"]
            if prior is None or prior["approval_hash"] != digest(raw_approval):
                Ledger(private).append(
                    "authorization",
                    approval_hash=digest(raw_approval),
                    approval=raw_approval,
                    previous_approval_hash=prior["approval_hash"] if prior else None,
                )
            journal = ResumableJournal(private / "audit")
            attempt_id = expected_attempt(call, journal.sequence + 1)
            ledger = Ledger(private)
            ledger.reserve(call, attempt_id, approval.limit_cny)
            try:
                output = await extract(
                    call_request(call),
                    Config(**public["provider"]),
                    journal,
                    httpx.AsyncHTTPTransport(retries=0) if live else transport,
                    api_key=key,
                    sample_id=call["sample_id"],
                    expected_http_request_hash=public["sdk_http_request_hashes"].get(call["call_id"]),
                )
            except asyncio.CancelledError:
                # The reservation remains held; no status read or settlement may mask cancellation.
                raise
            except Exception:
                terminal = [
                    json.loads(line)
                    for line in journal.events.read_bytes().splitlines()
                    if json.loads(line)["kind"] == "terminal" and json.loads(line)["attempt_id"] == attempt_id
                ]
                if len(terminal) == 1:
                    ledger.settle(call["call_id"], terminal[0], input_budget=call["budget"])
                raise
            raw = output.model_dump(mode="json")
            write_private(private / f"{call['call_id']}.output.json", raw)
            terminal = json.loads(journal.events.read_bytes().splitlines()[-1])
            ledger.settle(call["call_id"], terminal, output_hash=digest(raw), input_budget=call["budget"])
        output = read_json(private / f"{call['call_id']}.output.json")
        checked = status(directory)
        return {
            "state": "stopped_after_response" if checked["stop_required"] else "awaiting_review",
            "case": call["case_label"],
            **review_paths(private, call, output),
            **checked,
        }


def import_review(directory, raw_review):
    public, data, private = load_session(directory)
    with exclusive(private):
        state = verified_state(public, data, private)
        for call_id in state["reviews"]:
            receipt = read_json(private / f"{call_id}.review.json")
            if receipt["review"] == raw_review:
                return receipt["result"]
        if state["stop_required"]:
            raise EvaluationError("v13_cannot_review_stopped_session")
        call = next((c for c in data["calls"] if c["call_id"] not in state["reviews"]), None)
        if call is None or call["call_id"] not in state["settled"]:
            raise EvaluationError("v13_no_pending_output")
        terminal = state["terminals"][state["settled"][call["call_id"]]["attempt_id"]]
        output = read_json(private / f"{call['call_id']}.output.json")
        result = evaluate(call, output, raw_review, copy_parts=terminal["copy_parts"])
        receipt = {"review": raw_review, "result": result}
        path = private / f"{call['call_id']}.review.json"
        if path.exists():
            if read_json(path) != receipt:
                raise EvaluationError("v13_pending_review_conflict")
        else:
            write_private(path, receipt)
        Ledger(private).append(
            "review",
            pair_id=call["call_id"],
            receipt_hash=digest(receipt),
            candidate_gate_passed=result["candidate_gate_passed"],
        )
        return result
