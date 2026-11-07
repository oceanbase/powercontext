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

"""Persist an immutable plan and private inputs before any credentials are accessed."""

from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path
from typing import Any

from powercontext.builtin.artifacts.atomic_memory.extraction import AtomicMemoryExtractionInput

from ..manifests import EvaluationError, read_json
from ..models import Sample, Support, digest
from ..replay import replay_case
from ..runner import instructions_for, run_sample
from ..v6.workflow import INPUT_KEYS, InputProbe
from ..v7.workflow import reference_run, write_result
from ..v9.admission import pack
from ..v9.codec import INSTRUCTIONS, prepare
from ..v9_preflight.adapter import Config, Journal
from ..v9_preflight.contract import identity as adapter_identity
from .contract import contract, identity
from .ledger import Ledger, sync_directory, write_private


def call_value(call):
    value = AtomicMemoryExtractionInput.model_validate(call["value"])
    supports = tuple(Support.model_validate(s) for s in call["supports"])
    if call["protocol"] == "v9":
        result = prepare(value, supports)
        if result.binding_hash != call["request_hash"]:
            raise EvaluationError("live_private_request_binding_mismatch")
        return result
    if call["protocol"] != "v5":
        raise EvaluationError("unsupported_live_protocol")
    actual = digest(call["value"])
    if actual != call["request_hash"]:
        raise EvaluationError("live_private_request_binding_mismatch")
    return value


async def prepare_session(v9_root, preflight_root, output_dir, *, mode="live", reviewer="assistant"):
    if mode not in {"live", "offline_mock"} or not reviewer.strip():
        raise EvaluationError("invalid_session_mode_or_reviewer")
    if output_dir.exists():
        raise EvaluationError("live_output_already_exists")
    frozen = identity()
    preflight = read_json(preflight_root / "mock/result.json")
    complete = read_json(preflight_root / "mock/COMPLETE.json")
    accepted = read_json(preflight_root / "summary.json")
    if (
        digest(preflight) != complete["result_hash"]
        or preflight["preflight_identity"] != adapter_identity()
        or not preflight["sdk_request_contract_and_audit_passed"]
        or not preflight["model_visible_text_budget_passed"]
        or digest(adapter_identity()) != accepted["adapter_identity_hash"]
        or digest(preflight) != accepted["mock_result_hash"]
    ):
        raise EvaluationError("live_sdk_preflight_not_verified")
    for path, expected in accepted["artifacts"].items():
        if hashlib.sha256(Path(path).read_bytes()).hexdigest() != expected:
            raise EvaluationError("preflight_artifact_changed")
    draft = read_json(v9_root / "online-preflight-draft.json")
    if digest(draft) != preflight["v9_draft_hash"] or len(draft["samples"]) != 45:
        raise EvaluationError("live_plan_draft_changed")
    refdir = Path(read_json(v9_root / "comparison-primary/comparison.json")["reference_dir"])
    suite, manifest, references = reference_run("synthetic-v1", refdir)
    if digest(manifest) != draft["v5_reference_manifest_hash"]:
        raise EvaluationError("live_reference_changed")
    private = Path(tempfile.mkdtemp(prefix="powercontext-v9-live-"))
    private.chmod(0o700)
    # This receipt survives a failed preparation; it does not authorize execution.
    write_result(output_dir / "LOCATION.json", {"private_root": str(private), "mode": mode, "live_authorized": False})
    cases = {digest(c.model_dump(mode="json")): c for c in suite.cases}
    refs = {r.sample_id: r for r in references}
    full = {}
    pairs: list[dict[str, Any]] = []
    first = contract()["first_pair"]
    entries = sorted(draft["samples"], key=lambda e: 0 if all(e[k] == v for k, v in first.items()) else 1)
    for entry in entries:
        reference = refs[entry["sample_id"]]
        case = cases[reference.case_hash]
        if case.case_id not in full:
            async with replay_case(case) as replay:
                full[case.case_id] = replay.value.model_dump(mode="json")
        probe = InputProbe()
        sample = await run_sample(suite, case, reference.arm, reference.budget, reference.repetition, generator=probe)
        if any(getattr(reference, k) != getattr(sample, k) for k in INPUT_KEYS):
            raise EvaluationError("live_reconstructed_selection_changed")
        plan = pack(probe.inputs, sample.supports, sample.budget, suite.experiment.v3)
        if (
            not plan.accepted
            or len(probe.inputs) != entry["v5"]["calls"]
            or list(sample.input_hashes) != entry["v5"]["input_hashes"]
            or [{"cost": r.cost, "request_binding_hash": r.binding_hash} for r in plan.requests]
            != entry["v9"]["requests"]
        ):
            raise EvaluationError("live_reconstructed_request_plan_changed")
        calls = []
        for protocol, values in (("v5", probe.inputs), ("v9", plan.requests)):
            for index, value in enumerate(values):
                raw = value.value if protocol == "v9" else value
                ids = {e.evidence_id for e in raw.evidence}
                supports = value.supports if protocol == "v9" else sample.supports
                calls.append(
                    {
                        "call_id": digest({"sample": sample.sample_id, "protocol": protocol, "index": index}),
                        "sample_id": sample.sample_id,
                        "protocol": protocol,
                        "index": index,
                        "budget": sample.budget,
                        "value": raw.model_dump(mode="json"),
                        "supports": [s.model_dump(mode="json") for s in supports if s.evidence_id in ids],
                        "request_hash": value.binding_hash if protocol == "v9" else digest(raw.model_dump(mode="json")),
                    }
                )
        pairs.append(
            {
                "pair_id": sample.sample_id,
                "case": case.model_dump(mode="json"),
                "sample": sample.model_dump(mode="json"),
                "calls": calls,
            }
        )
    private_plan = {"pairs": pairs, "full": full, "v5_instructions": instructions_for(suite)}
    for pair in pairs:
        for call in pair["calls"]:
            call_value(call)
    rows = [{k: v for k, v in c.items() if k not in {"value", "supports"}} for p in pairs for c in p["calls"]]
    if len(rows) != 102 or len({r["call_id"] for r in rows}) != 102 or len(pairs) != 45:
        raise EvaluationError("live_plan_count_mismatch")
    expected = {(r["sample_id"], r["protocol"], r["call_index"]): r for r in preflight["requests"]}
    for call in rows:
        previous = expected[(call["sample_id"], call["protocol"], call["index"])]
        if call["request_hash"] != previous["request_hash"]:
            raise EvaluationError("live_request_differs_from_sdk_preflight")
    public = {
        "schema": "evidence-admission.v9-live.plan.v1",
        "identity": frozen,
        "execution_mode": mode,
        "reviewer": reviewer,
        "independent_human_review": False,
        "provider": {"model": "deepseek-v4-flash", "base_url": "https://api.deepseek.com"},
        "private_root": str(private),
        "private_plan_hash": digest(private_plan),
        "draft_hash": digest(draft),
        "sdk_preflight_result_hash": digest(preflight),
        "pair_order": [p["pair_id"] for p in pairs],
        "calls": rows,
        "first_pair": first,
        "live_authorized": False,
        "production_authorized": False,
    }
    if identity() != frozen:
        raise EvaluationError("live_code_changed_during_preparation")
    write_private(private / "inputs.json", private_plan)
    write_private(private / "plan.json", public)
    Journal(private / "audit")
    Ledger(private, create={"plan_hash": digest(public), "mode": mode})
    sync_directory(private)
    write_result(output_dir / "plan.json", public)
    write_result(
        output_dir / "approval-template.json",
        {
            "plan_hash": digest(public),
            "live_authorized": False,
            "approved_by": None,
            "currency": "CNY",
            "limit_cny": "5.00",
            "max_pairs": 1,
            "accept_conditional_billing_envelope": False,
            "accept_current_model_alias_route": False,
        },
    )
    write_result(
        output_dir / "READY.json", {"plan_hash": digest(public), "provider_requests": 0, "live_authorized": False}
    )
    return {"pairs": len(pairs), "requests": len(rows), "plan_hash": digest(public), "provider_requests": 0}


def load_session(output_dir):
    public = read_json(output_dir / "plan.json")
    if public["identity"] != identity() or read_json(output_dir / "READY.json")["plan_hash"] != digest(public):
        raise EvaluationError("live_session_identity_changed")
    private = Path(public["private_root"])
    if read_json(private / "plan.json") != public:
        raise EvaluationError("private_public_plan_mismatch")
    data = read_json(private / "inputs.json")
    if digest(data) != public["private_plan_hash"]:
        raise EvaluationError("private_plan_hash_mismatch")
    Config(**public["provider"])
    for pair in data["pairs"]:
        Sample.model_validate(pair["sample"])
    return public, data, private


def call_instructions(data, call):
    return INSTRUCTIONS if call["protocol"] == "v9" else data["v5_instructions"]
