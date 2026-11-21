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

"""Bounded preparation, durable one-step execution, and explicitly labelled diagnostic review."""

from __future__ import annotations

import asyncio
import fcntl
import json
import os
import shlex
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path

import httpx
from powercontext.builtin.artifacts.atomic_memory.extraction import AtomicMemoryExtractionOutput

from ..manifests import EvaluationError, load_suite, read_json
from ..models import digest
from ..runner import run_sample
from ..v6.workflow import InputProbe
from ..v7.fidelity import quote_inventory
from ..v9_preflight.workflow import mock_response
from ..v10.scoring import candidate_key, ground_truth, review_binding, score, supported_atom
from ..v16.admission import pack
from ..v16.workflow import minimal_case_record
from .adapter import extract
from .freeze import committed_freeze, contract, verify_comparisons


def save_json(path, value, *, new=False):
    """Private files, atomic replacement and fsync; no raw audit data is printed."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(path.name + ".tmp")
    if new and path.exists():
        raise EvaluationError("v17_output_exists")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


@contextmanager
def locked(directory):
    with (directory / ".lock").open("a") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise EvaluationError("v17_session_already_running") from None
        yield


def reservation():
    policy = contract()
    return (
        Decimal(policy["input_context_ceiling"]) * Decimal(policy["input_per_million_cny"])
        + Decimal(policy["max_output_tokens"]) * Decimal(policy["output_per_million_cny"])
    ) / 1000000


def charge(usage):
    policy = contract()
    if not isinstance(usage, dict):
        return None
    counts = [usage.get("prompt_tokens"), usage.get("completion_tokens")]
    if any(type(c) is not int or c < 0 for c in counts):
        return None
    return (
        Decimal(counts[0]) * Decimal(policy["input_per_million_cny"])
        + Decimal(counts[1]) * Decimal(policy["output_per_million_cny"])
    ) / 1000000


def reserve_next(known_charge):
    policy = contract()
    remaining = Decimal(policy["budget_total_cny"]) - Decimal(policy["prior_known_upper_cny"]) - known_charge
    if remaining < reservation():
        raise EvaluationError("v17_conditional_budget_reservation_unavailable")
    return remaining


async def rebuild(baseline_root):
    previous = verify_comparisons(baseline_root)
    policy = contract()
    suite = load_suite(experiment_version="v5", dataset="synthetic-v1")
    runtime, rows = [], []
    for case_id, limit in policy["cases"].items():
        case = next(c for c in suite.cases if c.case_id == case_id)
        probe = InputProbe()
        sample = await run_sample(suite, case, policy["arm"], policy["budget"], policy["repetition"], generator=probe)
        plan = pack(probe.inputs, sample.supports, sample.budget, suite.experiment.v3)
        record = minimal_case_record(case, sample, plan)
        old = next(r for r in previous["cases"] if r["case"] == case_id)
        if record != old or not plan.accepted or len(plan.requests) != limit:
            raise EvaluationError("v17_input_plan_changed")
        if not record["required_literals_copy_selectable"]:
            raise EvaluationError("v17_minimal_literals_not_copy_selectable")
        for index, request in enumerate(plan.requests):
            call_id = f"{case_id}-{index}"
            rows.append(
                {
                    "call_id": call_id,
                    "case": case_id,
                    "request_hash": request.binding_hash,
                    "estimated_input_tokens": request.cost["single_estimated_input_tokens"],
                    "cell_count": len(request.cells),
                }
            )
            runtime.append((call_id, case, request))
    if len(rows) != policy["maximum_requests"]:
        raise EvaluationError("v17_call_manifest_changed")
    return runtime, rows


async def prepare(baseline_root, directory, mode):
    if directory.exists():
        raise EvaluationError("v17_session_exists")
    freeze = committed_freeze()
    _, calls = await rebuild(baseline_root)
    if committed_freeze() != freeze:
        raise EvaluationError("v17_identity_changed_during_preparation")
    plan = {
        "schema": "evidence-admission.v17.execution-plan.v1",
        "freeze": freeze,
        "contract": contract(),
        "calls": calls,
        "formal_acceptance": False,
        "production_authorized": False,
    }
    directory.mkdir(parents=True, mode=0o700)
    save_json(directory / "plan.json", plan, new=True)
    save_json(directory / "location.json", {"baseline_root": str(baseline_root.resolve())}, new=True)
    save_json(
        directory / "state.json",
        {"mode": mode, "plan_hash": digest(plan), "state": "ready", "next": 0, "known_charge_cny": "0", "records": []},
        new=True,
    )
    return plan


def load(directory):
    plan, state = read_json(directory / "plan.json"), read_json(directory / "state.json")
    if (
        digest(plan) != state["plan_hash"]
        or plan["freeze"]["identity_hash"] != committed_freeze()["identity_hash"]
        or plan["contract"] != contract()
    ):
        raise EvaluationError("v17_plan_or_execution_identity_changed")
    return plan, state


def credentials():
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if key:
        return key
    # Read only the explicitly named literal assignment, never execute the user's shell startup file.
    for line in (Path.home() / ".bashrc").read_text().splitlines():
        candidate = line.strip().removeprefix("export ").strip()
        if not candidate.startswith("DEEPSEEK_API_KEY="):
            continue
        values = shlex.split(candidate.split("=", 1)[1], comments=True)
        if len(values) == 1 and values[0] and not any(s in values[0] for s in ("$", "`", "\n")):
            key = values[0]
    if not key:
        raise EvaluationError("v17_configured_credential_unavailable")
    return key


def recorder(directory, call_id):
    def save(kind, value):
        path = directory / "audit" / f"{call_id}.{kind}.json"
        if isinstance(value, bytes):
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(value)
                stream.flush()
                os.fsync(stream.fileno())
        else:
            save_json(path, value, new=kind != "attempt")

    return save


async def run_next(directory, *, execute_live=False, transport=None, api_key=None):
    with locked(directory):
        plan, state = load(directory)
        if state["state"] != "ready" or state["next"] >= len(plan["calls"]):
            raise EvaluationError("v17_session_not_ready_no_resend")
        live = state["mode"] == "live"
        if live != execute_live or live and transport is not None:
            raise EvaluationError("v17_execution_mode_mismatch")
        if live:
            preflight = read_json(directory / "preflight.json")
            if preflight["plan_hash"] != digest(plan) or not preflight["passed"]:
                raise EvaluationError("v17_valid_preflight_required")
        baseline = Path(read_json(directory / "location.json")["baseline_root"])
        runtime, rows = await rebuild(baseline)
        if rows != plan["calls"]:
            raise EvaluationError("v17_rebuilt_manifest_changed")
        call_id, case, request = runtime[state["next"]]
        reserve_next(Decimal(state["known_charge_cny"]))
        key = credentials() if live else api_key or "offline-not-a-credential"
        state["state"] = "in_flight"
        save_json(directory / "state.json", state)
        record = {
            "call_id": call_id,
            "case": case.case_id,
            "request_hash": request.binding_hash,
            "provider_request_started": False,
            "pipeline_entered": False,
            "pipeline_returned": False,
            "provider_usage": None,
        }
        save = recorder(directory, call_id)
        expected = preflight["http_request_hashes"][call_id] if live else None
        output = None
        try:
            output = await extract(
                request,
                api_key=key,
                transport=transport or httpx.AsyncHTTPTransport(retries=0),
                record=record,
                save=save,
                expected_hash=expected,
            )
            usage = record["provider_usage"]
            if not isinstance(usage, dict) or charge(usage) is None:
                raise EvaluationError("v17_unknown_usage")
            if (
                usage["prompt_tokens"] > contract()["input_context_ceiling"]
                or usage["completion_tokens"] > contract()["max_output_tokens"]
            ):
                raise EvaluationError("v17_usage_ceiling_exceeded")
            details = usage.get("completion_tokens_details") or {}
            if (
                record.get("finish_reason") != "stop"
                or details.get("reasoning_tokens", 0) != 0
                or record.get("reasoning_content_present")
            ):
                raise EvaluationError("v17_incomplete_or_thinking_response")
            if live and record["response_model"] != "deepseek-flash":
                raise EvaluationError("v17_unexpected_model_route")
        except asyncio.CancelledError:
            record["failure_category"] = "cancelled"
            state["state"] = "cancelled"
            raise
        except Exception as error:  # noqa: BLE001 -- Persist failed billing state before any retry can occur.
            record["failure_category"] = (
                str(error)
                if isinstance(error, EvaluationError) and str(error).startswith("v17_")
                else type(error).__name__
            )
            state["state"] = "failed"
        else:
            state["state"] = "awaiting_review"
            expected_atoms = ground_truth(case)
            local = [a for a in expected_atoms if supported_atom(a, quote_inventory(request.value, request.supports))]
            packet = {
                "case": case.model_dump(mode="json"),
                "input": request.value.model_dump(mode="json"),
                "supports": [s.model_dump(mode="json") for s in request.supports],
                "output": output.model_dump(mode="json"),
                "expected_atoms": expected_atoms,
                "local_expected_atom_hashes": sorted(digest(a["id"]) for a in local),
                "review_template": {
                    "reviewer": "",
                    "reviewer_kind": "assistant",
                    "output_hash": digest(output.model_dump(mode="json")),
                    "context_hash": review_binding(case, request.value, request.supports, output),
                    "judgements": {candidate_key(c, i): {} for i, c in enumerate(output.candidates)},
                },
            }
            save("packet", packet)
        finally:
            cost = charge(record["provider_usage"])
            if record["provider_request_started"] and cost is not None:
                state["known_charge_cny"] = str(Decimal(state["known_charge_cny"]) + cost)
            record["known_charge_cny"] = str(cost) if cost is not None else None
            save("attempt", record)
            state["records"].append(record)
            save_json(directory / "state.json", state)
        return status(directory)


def review(directory, judgement):
    with locked(directory):
        plan, state = load(directory)
        if state["state"] != "awaiting_review":
            raise EvaluationError("v17_not_awaiting_review")
        call_id = plan["calls"][state["next"]]["call_id"]
        packet = read_json(directory / "audit" / f"{call_id}.packet.json")
        from powercontext.builtin.artifacts.atomic_memory.extraction import AtomicMemoryExtractionInput

        from ..models import Case, Support

        result = score(
            Case.model_validate(packet["case"]),
            AtomicMemoryExtractionInput.model_validate(packet["input"]),
            tuple(Support.model_validate(s) for s in packet["supports"]),
            AtomicMemoryExtractionOutput.model_validate(packet["output"]),
            judgement,
        )
        passed = not any(
            result[k]
            for k in (
                "unsupported_candidates",
                "false_attribution_candidates",
                "invalid_support_candidates",
                "unresolved_candidates",
                "nonatomic_candidates",
                "literal_mismatches",
            )
        ) and set(result["retained_atom_hashes"]) == set(packet["local_expected_atom_hashes"])
        receipt = {"review": judgement, "score": result, "request_local_gate_passed": passed}
        save_json(directory / "audit" / f"{call_id}.review.json", receipt, new=True)
        state["records"][-1].update(
            review_hash=digest(receipt),
            diagnostic_gate_passed=passed,
            semantic_review_kind=judgement["reviewer_kind"],
            score=result,
        )
        state["next"] += 1
        case_id = packet["case"]["case_id"]
        last_for_case = state["next"] == len(plan["calls"]) or plan["calls"][state["next"]]["case"] != case_id
        if passed and last_for_case:
            required = {digest(a["id"]) for a in ground_truth(Case.model_validate(packet["case"]))}
            retained = {
                atom
                for row in state["records"]
                if row["case"] == case_id
                for atom in row.get("score", {}).get("retained_atom_hashes", [])
            }
            passed = retained == required
            state["records"][-1]["case_complete_gate_passed"] = passed
        state["state"] = "ready" if passed else "quality_failed"
        if passed and state["next"] == len(plan["calls"]):
            state["state"] = "diagnostic_complete"
        save_json(directory / "state.json", state)
        return status(directory)


def status(directory):
    plan, state = load(directory)
    records = state["records"]
    live = state["mode"] == "live"
    unknown = sum(r["provider_request_started"] and charge(r["provider_usage"]) is None for r in records)
    public_records = []
    for row in records:
        public = {k: v for k, v in row.items() if k != "provider_usage"}
        usage = row["provider_usage"]
        public["provider_usage"] = (
            {
                k: usage.get(k) if type(usage.get(k)) is int and usage[k] >= 0 else None
                for k in ("prompt_tokens", "completion_tokens", "total_tokens")
            }
            if isinstance(usage, dict)
            else None
        )
        public_records.append(public)
    return {
        "schema": "evidence-admission.v17.status.v1",
        "execution_freeze": plan["freeze"],
        "plan_hash": digest(plan),
        "state": state["state"],
        "reviewed_calls": state["next"],
        "provider_requests": sum(bool(r["provider_request_started"]) for r in records) if live else 0,
        "mock_http_requests": sum(bool(r["provider_request_started"]) for r in records) if not live else 0,
        "known_new_upper_charge_cny": state["known_charge_cny"],
        "cumulative_known_upper_cny": str(
            Decimal(contract()["prior_known_upper_cny"]) + Decimal(state["known_charge_cny"])
        ),
        "unknown_usage_calls": unknown,
        "unknown_usage_is_zero": False,
        "unknown_reserved_upper_cny": str(reservation() * unknown),
        "records": public_records,
        "formal_acceptance": False,
        "production_authorized": False,
        "decision": "insufficient_evidence",
        "formal_blockers": ["minimal_candidate_only_not_all_arm_matrix", "independent_semantic_review_not_established"],
    }


async def preflight(baseline_root, directory):
    plan = await prepare(baseline_root, directory, "offline_mock")
    for _ in plan["calls"]:
        step = await run_next(
            directory, transport=httpx.MockTransport(lambda request: httpx.Response(200, json=mock_response()))
        )
        if step["state"] != "awaiting_review":
            raise EvaluationError("v17_sdk_preflight_failed")
        # Advance transport-only preflight without inventing semantic reviews.
        state = read_json(directory / "state.json")
        state["next"] += 1
        state["state"] = "ready" if state["next"] < len(plan["calls"]) else "preflight_complete"
        save_json(directory / "state.json", state)
    result = {
        "schema": "evidence-admission.v17.preflight.v1",
        "passed": True,
        "plan_hash": digest(plan),
        "http_request_hashes": {
            r["call_id"]: r["request_facts"]["http_request_hash"] for r in status(directory)["records"]
        },
        "mock_http_requests": len(plan["calls"]),
        "provider_requests": 0,
        "semantic_quality": "not_measured",
    }
    save_json(directory / "preflight.json", result, new=True)
    return result
