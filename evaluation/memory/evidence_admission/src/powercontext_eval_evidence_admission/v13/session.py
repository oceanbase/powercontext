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

"""Rebuild frozen candidate requests and persist an isolated, immutable diagnostic plan."""

from __future__ import annotations

import tempfile
from pathlib import Path

from powercontext.builtin.artifacts.atomic_memory.extraction import AtomicMemoryExtractionInput

from ..manifests import EvaluationError, file_hash, load_suite, read_json
from ..models import Support, digest
from ..replay import replay_case
from ..runner import run_sample
from ..v6.workflow import InputProbe
from ..v7.workflow import write_result
from ..v9_live.ledger import Ledger, write_private
from ..v9_preflight.adapter import Journal
from ..v10.protocol import bind, pack
from ..v11.window import identity as window_identity
from .freeze import committed_freeze, identity


def call_request(call):
    request = bind(
        AtomicMemoryExtractionInput.model_validate(call["value"]),
        tuple(Support.model_validate(s) for s in call["supports"]),
    )
    if request.binding_hash != call["request_hash"]:
        raise EvaluationError("v13_private_request_binding_changed")
    return request


async def prepare_session(baseline_dir, output_dir, *, mode="offline_mock", preflight_dir=None):
    if output_dir.exists() or mode not in {"live", "offline_mock"}:
        raise EvaluationError("v13_invalid_output_or_mode")
    frozen = identity()
    contract = frozen["contract"]
    marker = read_json(baseline_dir / "COMPLETE.json")
    for name, checksum in marker["files"].items():
        if Path(name).name != name or file_hash(baseline_dir / name) != checksum:
            raise EvaluationError("v13_baseline_bundle_changed")
    baseline = read_json(baseline_dir / "comparison.json")
    if (
        file_hash(baseline_dir / "comparison.json") != contract["baseline_v11_sha256"]
        or baseline["identity"] != window_identity()
        or not baseline["summary"]["window_offline_gate_passed"]
    ):
        raise EvaluationError("v13_baseline_not_verified")
    suite = load_suite(experiment_version="v5", dataset=contract["dataset"])
    calls = []
    for case_id in contract["ordered_cases"]:
        case = next(c for c in suite.cases if c.case_id == case_id)
        probe = InputProbe()
        sample = await run_sample(suite, case, contract["arm"], contract["budget"], 0, generator=probe)
        plan = pack(probe.inputs, sample.supports, sample.budget, suite.experiment.v3, allow_repacking=True)
        previous = next(r for r in baseline["samples"] if r["sample_id"] == sample.sample_id)
        if (
            not plan.accepted
            or len(plan.requests) != 1
            or not previous["reference_required_content_fits"]
            or [{"binding_hash": r.binding_hash, "cost": r.cost} for r in plan.requests] != previous["requests"]
        ):
            raise EvaluationError("v13_frozen_candidate_plan_changed")
        request = plan.requests[0]
        async with replay_case(case) as replay:
            full = replay.value.model_dump(mode="json")
        calls.append(
            {
                "call_id": digest({"sample": sample.sample_id, "protocol": "v13", "index": 0}),
                "sample_id": sample.sample_id,
                "protocol": "v13",
                "index": 0,
                "budget": sample.budget,
                "case_label": case_id,
                "case": case.model_dump(mode="json"),
                "sample": sample.model_dump(mode="json"),
                "full": full,
                "value": request.encoded.value.model_dump(mode="json"),
                "supports": [s.model_dump(mode="json") for s in request.encoded.supports],
                "request_hash": request.binding_hash,
                "cost": request.cost,
            }
        )
    freeze = committed_freeze() if mode == "live" else None
    if mode == "live":
        if preflight_dir is None:
            raise EvaluationError("v13_sdk_preflight_required")
        preflight = read_json(preflight_dir / "result.json")
        if (
            digest(preflight) != read_json(preflight_dir / "COMPLETE.json")["result_hash"]
            or preflight["identity"] != frozen
            or not preflight["sdk_pipeline_contract_passed"]
            or preflight["request_hashes"] != [c["request_hash"] for c in calls]
            or preflight["provider_requests"] != 0
        ):
            raise EvaluationError("v13_sdk_preflight_changed")
    if identity() != frozen:
        raise EvaluationError("v13_code_changed_during_preparation")
    private = Path(tempfile.mkdtemp(prefix="powercontext-v13-"))
    private.chmod(0o700)
    write_result(output_dir / "LOCATION.json", {"private_root": str(private), "live_authorized": False})
    data = {"calls": calls}
    public = {
        "schema": "evidence-admission.v13.session.v1",
        "identity": frozen,
        "freeze": freeze,
        "execution_mode": mode,
        "provider": contract["provider"],
        "private_root": str(private),
        "private_plan_hash": digest(data),
        "calls": [
            {
                k: c[k]
                for k in ("call_id", "sample_id", "protocol", "index", "budget", "case_label", "request_hash", "cost")
            }
            for c in calls
        ],
        "budget_carry_forward": contract["budget_carry_forward"],
        "sdk_http_request_hashes": {
            c["call_id"]: row["sdk_request_facts"]["http_request_hash"]
            for c, row in zip(calls, preflight["requests"], strict=True)
        }
        if mode == "live"
        else {},
        "sdk_preflight_hash": digest(preflight) if mode == "live" else None,
        "formal_acceptance": False,
        "production_authorized": False,
    }
    write_private(private / "inputs.json", data)
    write_private(private / "plan.json", public)
    Journal(private / "audit")
    Ledger(private, create={"plan_hash": digest(public), "mode": mode})
    write_result(output_dir / "plan.json", public)
    write_result(
        output_dir / "approval-template.json",
        {
            "plan_hash": digest(public),
            "live_authorized": False,
            "approved_by": None,
            "currency": "CNY",
            "limit_cny": contract["budget_carry_forward"]["remaining_conditional_cny"],
            "max_cases": 1,
            "accept_conditional_billing_envelope": False,
            "accept_current_model_alias_route": False,
        },
    )
    write_result(
        output_dir / "READY.json", {"plan_hash": digest(public), "provider_requests": 0, "live_authorized": False}
    )
    return public


def load_session(directory):
    public = read_json(directory / "plan.json")
    if public["identity"] != identity() or read_json(directory / "READY.json")["plan_hash"] != digest(public):
        raise EvaluationError("v13_session_identity_changed")
    private = Path(public["private_root"])
    if read_json(private / "plan.json") != public:
        raise EvaluationError("v13_private_public_plan_mismatch")
    data = read_json(private / "inputs.json")
    if digest(data) != public["private_plan_hash"]:
        raise EvaluationError("v13_private_plan_changed")
    for call in data["calls"]:
        call_request(call)
    return public, data, private
