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

"""Controlled copy responses exercise the actual SDK and Pipeline, without model requests."""

from __future__ import annotations

import json

import httpx

from ..manifests import EvaluationError, read_json
from ..models import Case, digest
from ..v7.workflow import write_result
from ..v9_preflight.workflow import mock_response
from ..v10.scoring import ground_truth
from .freeze import identity
from .runner import import_review, run_next, status
from .session import call_request, load_session, prepare_session


def controlled_output(call):
    """Ground truth constructs only fake responses; it never changes the model input."""
    request = call_request(call)
    candidates = []
    for atom in ground_truth(Case.model_validate(call["case"])):
        quote = next(q for q in request.encoded.quotes if atom["text"] in q.value)
        start = quote.value.index(atom["text"])
        left = len(quote.value[:start].encode())
        candidates.append(
            {
                "kind": "fact",
                "evidence_ids": [f"e{quote.evidence_index}"],
                "parts": [
                    {
                        "type": "copy",
                        "quote_id": quote.quote_id,
                        "byte_start": left,
                        "byte_end": left + len(atom["text"].encode()),
                    }
                ],
            }
        )
    return {"candidates": candidates}


def controlled_review(call, packet):
    """An explicit judgement only for exactly constructed, locally controlled outputs."""
    review = packet["review_template"]
    expected = ground_truth(Case.model_validate(call["case"]))
    actual = packet["output"]["candidates"]
    if len(actual) != len(expected) or any(c["text"] != a["text"] for c, a in zip(actual, expected, strict=True)):
        raise EvaluationError("v13_controlled_output_changed")
    for judgement, atom in zip(review["judgements"].values(), expected, strict=True):
        judgement.update(supported=True, atomic=True, false_attribution=False, entailed_atom_ids=[atom["id"]])
    review["reviewer"] = "offline_controlled_fixture_not_live_review"
    return review


async def preflight(baseline_dir, output_dir):
    if output_dir.exists():
        raise EvaluationError("v13_preflight_output_exists")
    frozen = identity()
    public = await prepare_session(baseline_dir, output_dir / "session")
    approval = read_json(output_dir / "session/approval-template.json")
    approval.update(
        approved_by="offline_controlled_fixture",
        max_cases=2,
        accept_conditional_billing_envelope=True,
        accept_current_model_alias_route=True,
    )
    _, data, private = load_session(output_dir / "session")
    results = []
    for call in data["calls"]:

        def handler(_request, current=call):
            response = mock_response()
            response["choices"][0]["message"]["content"] = json.dumps(controlled_output(current), ensure_ascii=False)
            return httpx.Response(200, json=response)

        step = await run_next(
            output_dir / "session", approval, transport=httpx.MockTransport(handler), api_key="offline-key"
        )
        if step["state"] != "awaiting_review" or step["stop_required"]:
            raise EvaluationError("v13_controlled_pipeline_failed")
        packet = read_json(private / f"{call['call_id']}.packet.json")
        reviewed = import_review(output_dir / "session", controlled_review(call, packet))
        if not reviewed["candidate_gate_passed"]:
            raise EvaluationError("v13_controlled_review_failed")
        results.append(
            {
                "case": call["case_label"],
                "request_hash": call["request_hash"],
                "cost": call["cost"],
                "controlled_diagnostic": reviewed,
            }
        )
    events = [json.loads(line) for line in (private / "audit/attempts.jsonl").read_bytes().splitlines()]
    requests = [e for e in events if e["kind"] == "http_request"]
    if len(requests) != len(results) or any(e["model_visible_text_estimated_tokens"] > 4096 for e in requests):
        raise EvaluationError("v13_sdk_model_text_budget_exceeded")
    for row, event in zip(results, requests, strict=True):
        row["sdk_request_facts"] = {
            k: event[k]
            for k in (
                "http_request_hash",
                "http_request_bytes",
                "http_json_estimated_tokens",
                "model_visible_text_estimated_tokens",
                "user_payload_hash",
                "instructions_hash",
                "sdk_output_schema_hash",
                "thinking_disabled_verified",
            )
        }
    checked = status(output_dir / "session")
    if (
        identity() != frozen
        or checked["stop_required"]
        or checked["mock_http_requests"] != 2
        or any(not row["sdk_request_facts"]["thinking_disabled_verified"] for row in results)
    ):
        raise EvaluationError("v13_preflight_identity_or_count_changed")
    result = {
        "schema": "evidence-admission.v13.sdk-preflight.v1",
        "identity": frozen,
        "request_hashes": [c["request_hash"] for c in data["calls"]],
        "requests": results,
        "plan_hash": digest(public),
        "sdk_pipeline_contract_passed": True,
        "provider_requests": 0,
        "mock_http_requests": 2,
        "provider_usage": None,
        "semantic_review": "controlled_fake_output_only_not_live_quality",
        "live_authorized": False,
        "formal_acceptance": False,
        "production_authorized": False,
    }
    write_result(output_dir / "result.json", result)
    write_result(output_dir / "COMPLETE.json", {"result_hash": digest(result)})
    return {"sdk_pipeline_contract_passed": True, "provider_requests": 0, "mock_http_requests": 2}
