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

"""One paired group per invocation, with durable no-retry accounting and review gates."""

from __future__ import annotations

import asyncio
import json
import os
import stat

import httpx
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from ..manifests import EvaluationError, read_json
from ..models import digest
from ..v9_preflight.adapter import Config, Journal, open_adapter
from ..v9_preflight.adapter import summary as audit_summary
from .ledger import Ledger, exclusive, money, reservation, settlement_facts, write_private
from .plan import call_instructions, call_value, load_session
from .scoring import review_packet, score_pair


class Approval(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    plan_hash: str
    live_authorized: StrictBool
    approved_by: str = Field(min_length=1)
    currency: str
    limit_cny: str
    max_pairs: int = Field(ge=1, le=45)
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
    ):
        raise EvaluationError("execution_authorization_or_billing_contract_missing")
    return approval


class ResumableJournal(Journal):
    def __init__(self, directory):
        self.directory = directory
        self.events = directory / "attempts.jsonl"
        rows = [json.loads(line) for line in self.events.read_bytes().splitlines()]
        self.sequence = max((r["sequence"] for r in rows if r["kind"] == "start"), default=0)


def expected_attempt(call, sequence):
    sample = digest({"sample_id": call["sample_id"], "protocol": call["protocol"], "call_index": call["index"]})
    return digest({"sample_id": sample, "input_hash": call["request_hash"], "sequence": sequence})


def outputs_for(pair, state, private):
    result = {}
    for call in pair["calls"]:
        settled = state["settled"].get(call["call_id"])
        if settled is None or settled["output_hash"] is None:
            raise EvaluationError("pair_outputs_not_complete")
        value = read_json(private / f"{call['call_id']}.output.json")
        if digest(value) != settled["output_hash"]:
            raise EvaluationError("durable_output_hash_mismatch")
        result[call["call_id"]] = value
    return result


def verified_state(public, data, private):
    if stat.S_IMODE(private.stat().st_mode) != 0o700:
        raise EvaluationError("private_session_permissions_changed")
    for p in private.iterdir():
        if p.is_file() and (p.is_symlink() or stat.S_IMODE(p.stat().st_mode) != 0o600):
            raise EvaluationError("private_session_permissions_changed")
    ledger = Ledger(private)
    if ledger.rows()[0]["plan_hash"] != digest(public) or ledger.rows()[0]["mode"] != public["execution_mode"]:
        raise EvaluationError("execution_ledger_session_mismatch")
    state = ledger.state()
    audit = audit_summary(private / "audit")
    events = [json.loads(line) for line in (private / "audit/attempts.jsonl").read_bytes().splitlines()]
    starts = {r["attempt_id"]: r for r in events if r["kind"] == "start"}
    terminals = {r["attempt_id"]: r for r in events if r["kind"] == "terminal"}
    calls = {c["call_id"]: c for p in data["pairs"] for c in p["calls"]}
    if not set(state["reserved"]) <= calls.keys() or not set(starts) <= {
        r["attempt_id"] for r in state["reserved"].values()
    }:
        raise EvaluationError("unplanned_execution_attempt")
    for call_id, row in state["reserved"].items():
        if row["request_hash"] != calls[call_id]["request_hash"]:
            raise EvaluationError("reserved_request_hash_mismatch")
        if row["attempt_id"] in starts and starts[row["attempt_id"]]["input_hash"] != row["request_hash"]:
            raise EvaluationError("audit_input_hash_mismatch")
    for call_id, row in state["settled"].items():
        terminal = terminals.get(row["attempt_id"])
        if terminal is None:
            raise EvaluationError("settlement_without_audit_terminal")
        if any(row[k] != terminal.get(k) for k in ("outcome", "error_category")):
            raise EvaluationError("settlement_terminal_mismatch")
        expected = settlement_facts(terminal, row["output_hash"], calls[call_id]["budget"])
        if any(row[k] != v for k, v in expected.items()):
            raise EvaluationError("settlement_usage_mismatch")
        if row["output_hash"] is not None:
            output = read_json(private / f"{call_id}.output.json")
            if digest(output) != row["output_hash"]:
                raise EvaluationError("durable_output_hash_mismatch")
    pairs = {p["pair_id"]: p for p in data["pairs"]}
    for pair_id, record in state["reviews"].items():
        if pair_id not in pairs:
            raise EvaluationError("unplanned_pair_review")
        receipt = read_json(private / f"{pair_id}.review.json")
        pair = pairs[pair_id]
        computed = score_pair(
            pair,
            outputs_for(pair, state, private),
            receipt["review"],
            reviewer=public["reviewer"],
            full=data["full"][pair["case"]["case_id"]],
        )
        if (
            digest(receipt) != record["receipt_hash"]
            or computed != receipt["result"]
            or record["candidate_gate_passed"] != computed["candidate_gate_passed"]
        ):
            raise EvaluationError("review_receipt_mismatch")
    if not audit["attempt_coverage_complete"]:
        state["stop_required"] = True
    metadata = {
        (r.get("response_model"), r.get("system_fingerprint_hash"))
        for r in terminals.values()
        if r.get("http_status") == 200
    }
    state["provider_metadata_consistent"] = len(metadata) <= 1
    if not state["provider_metadata_consistent"]:
        state["stop_required"] = True
    state["audit"] = audit
    return state


def status(output_dir):
    public, data, private = load_session(output_dir)
    state = verified_state(public, data, private)
    failed_review = any(not r["candidate_gate_passed"] for r in state["reviews"].values())
    return {
        "execution_mode": public["execution_mode"],
        "plan_hash": digest(public),
        "reserved_calls": len(state["reserved"]),
        "settled_calls": len(state["settled"]),
        "reviewed_pairs": len(state["reviews"]),
        "planned_pairs": len(data["pairs"]),
        "pending_call_ids": state["pending_call_ids"],
        "unknown_usage_call_ids": state["unknown_usage_call_ids"],
        "known_upper_charge_cny": state["known_upper_charge_cny"],
        "held_reservation_cny": state["held_reservation_cny"],
        "actual_invoice_fee": None,
        "stop_required": state["stop_required"] or failed_review,
        "audit_attempt_coverage_complete": state["audit"]["attempt_coverage_complete"],
        "provider_metadata_consistent": state["provider_metadata_consistent"],
        "provider_requests": state["audit"]["http_requests"] if public["execution_mode"] == "live" else 0,
        "mock_http_requests": state["audit"]["http_requests"] if public["execution_mode"] == "offline_mock" else 0,
        "production_authorized": False,
    }


async def run_next(output_dir, raw_approval, *, transport=None, api_key=None):
    public, data, private = load_session(output_dir)
    approval = validate_approval(public, raw_approval)
    mode = public["execution_mode"]
    if mode == "offline_mock" and not isinstance(transport, httpx.MockTransport):
        raise EvaluationError("offline_session_requires_mock_transport")
    if mode == "live" and transport is not None:
        raise EvaluationError("live_session_requires_default_transport")
    with exclusive(private):
        state = verified_state(public, data, private)
        if state["stop_required"] or any(not r["candidate_gate_passed"] for r in state["reviews"].values()):
            raise EvaluationError("session_stopped_no_automatic_retry")
        pair = next((p for p in data["pairs"] if p["pair_id"] not in state["reviews"]), None)
        if pair is None:
            return {"state": "all_pairs_reviewed", **status(output_dir)}
        pair_index = public["pair_order"].index(pair["pair_id"])
        if pair_index >= approval.max_pairs:
            raise EvaluationError("approved_pair_scope_exhausted")
        remaining = [c for c in pair["calls"] if c["call_id"] not in state["settled"]]
        ledger = Ledger(private)
        if remaining:
            if money(state["known_upper_charge_cny"]) + reservation() > money(approval.limit_cny):
                raise EvaluationError("insufficient_currency_reservation")
            prior = state["authorization"]
            if prior is None or prior["approval_hash"] != digest(raw_approval):
                if prior and approval.max_pairs > prior["approval"]["max_pairs"] and not state["reviews"]:
                    raise EvaluationError("cannot_expand_pairs_before_first_pair_review")
                ledger.append(
                    "authorization",
                    approval_hash=digest(raw_approval),
                    approval=raw_approval,
                    previous_approval_hash=prior["approval_hash"] if prior else None,
                )
            key = api_key
            if key is None and mode == "live":
                key = os.environ.get("DEEPSEEK_API_KEY")
            if not key or not key.strip():
                raise EvaluationError("configured_api_key_unavailable")
            journal = ResumableJournal(private / "audit")
            selected_transport = httpx.AsyncHTTPTransport(retries=0) if mode == "live" else transport
            async with open_adapter(
                Config(**public["provider"]),
                journal,
                selected_transport,
                v5_instructions=data["v5_instructions"],
                api_key=key,
            ) as adapter:
                for call in remaining:
                    value = call_value(call)
                    attempt_id = expected_attempt(call, journal.sequence + 1)
                    ledger.reserve(call, attempt_id, approval.limit_cny)
                    try:
                        generated = await adapter.generate(
                            value,
                            protocol=call["protocol"],
                            sample_id=call["sample_id"],
                            call_index=call["index"],
                            instructions=call_instructions(data, call),
                            request_hash=call["request_hash"] if call["protocol"] == "v9" else None,
                        )
                    except (Exception, asyncio.CancelledError) as error:
                        terminals = [
                            r
                            for r in journal_rows(journal)
                            if r["kind"] == "terminal" and r["attempt_id"] == attempt_id
                        ]
                        if isinstance(error, asyncio.CancelledError):
                            try:
                                if len(terminals) == 1:
                                    ledger.settle(call["call_id"], terminals[0], input_budget=call["budget"])
                            except Exception:
                                # Keep cancellation authoritative; recovery retains the unsettled reservation.
                                pass
                            raise
                        if len(terminals) == 1:
                            ledger.settle(call["call_id"], terminals[0], input_budget=call["budget"])
                        return {"state": "attempt_failed", **status(output_dir)}
                    output = generated.output.model_dump(mode="json")
                    write_private(private / f"{call['call_id']}.output.json", output)
                    terminal = journal_rows(journal)[-1]
                    if terminal["attempt_id"] != attempt_id or terminal["kind"] != "terminal":
                        raise EvaluationError("unexpected_terminal_identity")
                    ledger.settle(call["call_id"], terminal, output_hash=digest(output), input_budget=call["budget"])
                    if ledger.state()["stop_required"]:
                        return {"state": "usage_or_budget_stopped", **status(output_dir)}
                    if verified_state(public, data, private)["stop_required"]:
                        return {"state": "audit_or_provider_identity_stopped", **status(output_dir)}
        state = verified_state(public, data, private)
        packet = review_packet(pair, outputs_for(pair, state, private))
        packet_path = private / f"{pair['pair_id']}.review-packet.json"
        if packet_path.exists():
            if read_json(packet_path) != packet:
                raise EvaluationError("review_packet_changed")
        else:
            write_private(packet_path, packet)
        template_path = private / f"{pair['pair_id']}.review-template.json"
        if not template_path.exists():
            write_private(
                template_path,
                {
                    "packet_hash": digest(packet),
                    "reviewer": public["reviewer"],
                    "independent_human_review": False,
                    "candidates": {
                        row["review_id"]: {"supported": None, "atomic": None, "entailed_atom_ids": []}
                        for row in packet["candidate_groups"]
                    },
                },
            )
        return {
            "state": "awaiting_review",
            "pair_id": pair["pair_id"],
            "packet_hash": digest(packet),
            "private_review_packet": str(packet_path),
            "private_review_template": str(template_path),
            **status(output_dir),
        }


def journal_rows(journal):
    return [json.loads(line) for line in journal.events.read_bytes().splitlines()]


def import_review(output_dir, raw_review):
    public, data, private = load_session(output_dir)
    with exclusive(private):
        state = verified_state(public, data, private)
        for pair_id in state["reviews"]:
            existing = read_json(private / f"{pair_id}.review.json")
            if existing["review"] == raw_review:
                return existing["result"]
        if state["stop_required"] or any(not r["candidate_gate_passed"] for r in state["reviews"].values()):
            raise EvaluationError("cannot_review_stopped_session")
        pair = next((p for p in data["pairs"] if p["pair_id"] not in state["reviews"]), None)
        if pair is None:
            raise EvaluationError("no_pending_pair_review")
        outputs = outputs_for(pair, state, private)
        result = score_pair(
            pair, outputs, raw_review, reviewer=public["reviewer"], full=data["full"][pair["case"]["case_id"]]
        )
        receipt = {"review": raw_review, "result": result}
        receipt_path = private / f"{pair['pair_id']}.review.json"
        if receipt_path.exists():
            if read_json(receipt_path) != receipt:
                raise EvaluationError("pending_review_receipt_conflict")
        else:
            write_private(receipt_path, receipt)
        Ledger(private).append(
            "review",
            pair_id=pair["pair_id"],
            receipt_hash=digest(receipt),
            candidate_gate_passed=result["candidate_gate_passed"],
        )
        return result
