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

"""Reconstruct and inspect every planned paired request through a real SDK and MockTransport."""

from __future__ import annotations

import json
import tempfile
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx

from ..manifests import EvaluationError, read_json
from ..models import digest
from ..runner import instructions_for, run_sample
from ..v6.workflow import INPUT_KEYS, InputProbe
from ..v7.workflow import reference_run, write_result
from ..v9.admission import pack
from ..v9.codec import INSTRUCTIONS
from ..v9.contract import identity as v9_identity
from ..v9.workflow import verified_comparisons
from .adapter import Config, Journal, open_adapter, summary
from .contract import identity


def mock_response():
    return {
        "id": "offline-preflight",
        "object": "chat.completion",
        "created": 0,
        "model": "offline-placeholder-not-provider-model",
        "system_fingerprint": "offline-placeholder",
        "choices": [
            {"index": 0, "message": {"role": "assistant", "content": '{"candidates":[]}'}, "finish_reason": "stop"}
        ],
        "usage": {"prompt_tokens": 17, "completion_tokens": 11, "total_tokens": 28},
    }


async def preflight(v9_root, session_path, output_dir):
    if output_dir.exists():
        raise EvaluationError("sdk_preflight_output_already_exists")
    frozen = identity()
    draft = read_json(v9_root / "online-preflight-draft.json")
    accepted = read_json(v9_root / "summary.json")
    verified_comparisons(v9_root, v9_identity())
    if (
        draft["v9_identity"] != v9_identity()
        or not accepted["all_arms_offline_gate_passed"]
        or draft["authorized_to_execute"]
        or len(draft["samples"]) != 45
        or draft["total_call_cap"] != 102
    ):
        raise EvaluationError("v9_offline_prerequisites_changed")
    provider = read_json(session_path)["provider"]
    base_url = "https://api.deepseek.com"
    if (
        provider["provider"] != "openai-compatible"
        or provider["endpoint_hash"] != digest(base_url)
        or provider["model"] != "deepseek-v4-flash"
    ):
        raise EvaluationError("preflight_provider_identity_changed")
    config = Config(provider["model"], base_url)
    reference_dir = read_json(v9_root / "comparison-primary/comparison.json")["reference_dir"]
    suite, manifest, references = reference_run("synthetic-v1", Path(reference_dir))
    if digest(manifest) != draft["v5_reference_manifest_hash"]:
        raise EvaluationError("preflight_reference_manifest_changed")
    cases = {digest(c.model_dump(mode="json")): c for c in suite.cases}
    refs = {r.sample_id: r for r in references}
    private = Path(tempfile.mkdtemp(prefix="powercontext-v9-sdk-preflight-")) / "audit"
    journal = Journal(private)
    write_result(
        output_dir / "session-location.json",
        {
            "preflight_identity": frozen,
            "private_audit_root": str(private),
            "execution_mode": "mock_transport",
            "provider_requests": 0,
        },
    )
    rows: list[dict[str, Any]] = []
    count = 0

    def handler(request):
        nonlocal count
        count += 1
        if count > 102:
            raise EvaluationError("mock_request_cap_exceeded")
        return httpx.Response(200, json=mock_response())

    async with open_adapter(
        config,
        journal,
        httpx.MockTransport(handler),
        v5_instructions=instructions_for(suite),
        api_key="offline-placeholder-never-a-real-key",
    ) as adapter:
        for entry in draft["samples"]:
            reference = refs[entry["sample_id"]]
            case = cases[reference.case_hash]
            probe = InputProbe()
            sample = await run_sample(
                suite, case, reference.arm, reference.budget, reference.repetition, generator=probe
            )
            if any(getattr(reference, key) != getattr(sample, key) for key in INPUT_KEYS):
                raise EvaluationError("sdk_preflight_reference_selection_changed")
            plan = pack(probe.inputs, sample.supports, sample.budget, suite.experiment.v3)
            if (
                not plan.accepted
                or len(probe.inputs) != entry["v5"]["calls"]
                or len(plan.requests) != entry["v9"]["calls"]
                or list(sample.input_hashes) != entry["v5"]["input_hashes"]
            ):
                raise EvaluationError("sdk_preflight_call_plan_changed")
            if [{"cost": r.cost, "request_binding_hash": r.binding_hash} for r in plan.requests] != entry["v9"][
                "requests"
            ]:
                raise EvaluationError("sdk_preflight_wire_identity_changed")
            for protocol, values in (("v5", probe.inputs), ("v9", plan.requests)):
                for index, value in enumerate(values):
                    await adapter.generate(
                        value,
                        protocol=protocol,
                        sample_id=sample.sample_id,
                        call_index=index,
                        instructions=instructions_for(suite) if protocol == "v5" else INSTRUCTIONS,
                        request_hash=None if protocol == "v5" else value.binding_hash,
                    )
                    events = [json.loads(line) for line in journal.events.read_bytes().splitlines()]
                    terminal = events[-1]
                    facts = next(r for r in reversed(events) if r["kind"] == "http_request")
                    rows.append(
                        {
                            "sample_id": sample.sample_id,
                            "case": case.case_id,
                            "budget": sample.budget,
                            "repetition": sample.repetition,
                            "protocol": protocol,
                            "call_index": index,
                            "attempt_id": terminal["attempt_id"],
                            "request_hash": value.binding_hash
                            if protocol == "v9"
                            else digest(value.model_dump(mode="json")),
                            "sdk_request": {k: v for k, v in facts.items() if k not in {"attempt_id", "kind"}},
                            "terminal_outcome": terminal["outcome"],
                            "quality_review": terminal["quality_review"],
                            "provider_output": "not_run",
                            "mock_output": "empty_placeholder",
                        }
                    )
            print(json.dumps({"completed_pairs": len({r["sample_id"] for r in rows}), "total_pairs": 45}), flush=True)
    audit = summary(private)
    if (
        count != 102
        or not audit["attempt_coverage_complete"]
        or audit["attempts"] != 102
        or not audit["response_capture_complete"]
    ):
        raise EvaluationError("preflight_audit_or_request_count_mismatch")
    if identity() != frozen:
        raise EvaluationError("sdk_preflight_code_changed_during_run")
    totals = {}
    for protocol in ("v5", "v9"):
        selected = [r for r in rows if r["protocol"] == protocol]
        totals[protocol] = {
            "requests": len(selected),
            "model_visible_text_estimated_tokens": sum(
                r["sdk_request"]["model_visible_text_estimated_tokens"] for r in selected
            ),
            "http_json_estimated_tokens": sum(r["sdk_request"]["http_json_estimated_tokens"] for r in selected),
            "http_request_bytes": sum(r["sdk_request"]["http_request_bytes"] for r in selected),
            "model_visible_text_over_budget_requests": sum(
                r["sdk_request"]["model_visible_text_estimated_tokens"] > r["budget"] for r in selected
            ),
            "http_json_over_budget_requests_diagnostic": sum(
                r["sdk_request"]["http_json_estimated_tokens"] > r["budget"] for r in selected
            ),
        }
    price = frozen["pricing"]
    proxy_input = sum(t["model_visible_text_estimated_tokens"] for t in totals.values())
    fee = (
        Decimal(proxy_input) * Decimal(price["flash_peak_input_cache_miss"])
        + Decimal(draft["total_maximum_output_tokens"]) * Decimal(price["flash_peak_output"])
    ) / Decimal(1000000)
    result = {
        "schema": "evidence-admission.v9-sdk-preflight.result.v1",
        "preflight_identity": frozen,
        "v9_draft_hash": digest(draft),
        "provider_requested": provider,
        "private_audit_root": str(private),
        "pairs": 45,
        "sample_identities": 90,
        "mock_http_requests": count,
        "provider_requests": 0,
        "mock_audit": audit,
        "mock_usage_is_simulated": True,
        "provider_usage": None,
        "request_totals": totals,
        "requests": rows,
        "sdk_request_contract_and_audit_passed": True,
        "model_visible_text_budget_passed": all(
            t["model_visible_text_over_budget_requests"] == 0 for t in totals.values()
        ),
        "actual_provider_budget_gate": "not_run_role_framing_and_tokenizer_unknown",
        "fee_proposal": {
            "currency": "CNY",
            "peak_all_cache_miss_proxy_estimate": str(fee),
            "input_proxy_tokens": proxy_input,
            "maximum_output_tokens": draft["total_maximum_output_tokens"],
            "proposed_budget": price["proposed_budget_cny"],
            "approved_budget": None,
            "estimate_is_not_a_hard_bound": True,
            "price_source": price["source"],
        },
        "live_adapter_implemented": True,
        "actual_model_generation": "not_run",
        "live_authorized": False,
        "production_authorized": False,
    }
    write_result(output_dir / "result.json", result)
    write_result(output_dir / "COMPLETE.json", {"result_hash": digest(result)})
    return {
        k: result[k]
        for k in (
            "pairs",
            "sample_identities",
            "mock_http_requests",
            "provider_requests",
            "request_totals",
            "sdk_request_contract_and_audit_passed",
            "model_visible_text_budget_passed",
            "fee_proposal",
            "live_authorized",
        )
    }
