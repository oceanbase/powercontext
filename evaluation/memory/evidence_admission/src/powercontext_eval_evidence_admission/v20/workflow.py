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

"""Continue only unattempted calls, carrying immutable history and unknown cost exposure."""

from __future__ import annotations

import asyncio
import shutil
from decimal import Decimal, InvalidOperation
from pathlib import Path

import httpx
from powercontext.builtin.artifacts.atomic_memory.extraction import (
    AtomicMemoryExtractionInput,
    AtomicMemoryExtractionOutput,
)
from powercontext.builtin.inference.errors import (
    InferenceConfigurationError,
    InferenceError,
    InferenceTimeoutError,
    InferenceUnavailableError,
    InvalidInferenceOutputError,
)

from ..manifests import EvaluationError, file_hash, read_json
from ..models import Support, digest
from ..v9_preflight.workflow import mock_response
from ..v17.workflow import credentials
from ..v18 import core, report
from ..v18.adapter import extract
from ..v18.scoring import score_output
from ..v18.workflow import account_record, calls_for, load, locked, recorder, reservation


def policy():
    return read_json(Path(__file__).with_name("contract.json"))


def remaining_calls(manifest, records):
    """An attempted request with missing usage is excluded just like a successful request."""
    calls = calls_for(manifest)
    expected = [digest(c) for c in calls[: len(records)]]
    if [r["call_id"] for r in records] != expected:
        raise EvaluationError("v20_history_is_not_exact_attempted_prefix")
    for call, record in zip(calls, records, strict=False):
        if {key: record[key] for key in call} != call:
            raise EvaluationError("v20_history_input_binding_changed")
    return calls[len(records) :]


def budget_available(state, total):
    known = Decimal(core.contract()["prior_known_upper_cny"]) + Decimal(state["known_charge_cny"])
    unknown = sum(r.get("known_charge_cny") is None for r in state["records"])
    return Decimal(total) - known - reservation() * unknown


def failure_category(error):
    """Audit the local boundary failure without retaining exception values or claiming a server cause."""
    for kind, label in (
        (InferenceTimeoutError, "inference_timeout"),
        (InferenceUnavailableError, "provider_unavailable"),
        (InferenceConfigurationError, "inference_configuration"),
        (InvalidInferenceOutputError, "invalid_output"),
        (httpx.HTTPError, "http_transport"),
        (TimeoutError, "request_timeout"),
    ):
        if isinstance(error, kind):
            return label
    return "inference_failure"


def prepare(
    source, directory, *, approved_total="20.00", authorization="user_approved_cumulative_20_CNY_after_budget_proposal"
):
    p = policy()
    try:
        total = Decimal(approved_total)
    except InvalidOperation:
        raise EvaluationError("v20_invalid_budget") from None
    if not total.is_finite() or total <= 0 or total > 100 or not authorization.strip():
        raise EvaluationError("v20_invalid_budget_or_authorization")
    if (
        total > Decimal(p["approved_budget_default_cny"])
        and authorization == "user_approved_cumulative_20_CNY_after_budget_proposal"
    ):
        raise EvaluationError("v20_budget_increase_needs_explicit_authorization_reference")
    original = read_json(source / "preparation.json")
    old_state = read_json(source / "state.json")
    old_preflight = read_json(source / "preflight.json")
    for value, key in (
        (original, "original_preparation_hash"),
        (old_state, "original_state_hash"),
        (old_preflight, "original_preflight_hash"),
        (old_state["records"], "original_records_hash"),
    ):
        if digest(value) != p[key]:
            raise EvaluationError("v20_historical_receipt_changed")
    if (
        original["identity_hash"] != p["original_identity_hash"]
        or digest(original["identity"]) != original["identity_hash"]
    ):
        raise EvaluationError("v20_historical_identity_changed")
    current = core.identity()
    for name, expected in original["identity"]["files"].items():
        if file_hash(core.REPOSITORY / name) != expected:
            raise EvaluationError("v20_inherited_generation_dependency_changed")
    for key in (
        "contract",
        "instructions_hash",
        "output_schema_hash",
        "sdk_versions",
        "python",
        "unicode_database_version",
    ):
        if original["identity"][key] != current[key]:
            raise EvaluationError("v20_inherited_environment_changed")
    unscored_returned = [
        r["call_id"]
        for r in old_state["records"]
        if r.get("outcome") == "returned" and r.get("score") is None and not r.get("scoring_failed")
    ]
    if unscored_returned:
        raise EvaluationError("v20_prior_scoring_incomplete")
    remaining = remaining_calls(original, old_state["records"])
    if len(remaining) != p["remaining_calls"] or digest(remaining) != p["remaining_calls_hash"]:
        raise EvaluationError("v20_remaining_plan_changed")
    if directory.exists():
        raise EvaluationError("v20_session_exists_no_reset")
    directory.mkdir(parents=True, mode=0o700)
    # Copy private receipts rather than linking writable history into the continuation.
    inherited = {}
    for folder in ("inputs", "audit", "reviews"):
        for path in sorted((source / folder).rglob("*.json")):
            relative = path.relative_to(source)
            target = directory / relative
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            shutil.copyfile(path, target)
            target.chmod(0o600)
            inherited[relative.as_posix()] = file_hash(path)
    manifest = {**original, "schema": p["schema"], "identity": current, "identity_hash": digest(current)}
    continuation = {
        "contract": p,
        "source_identity_hash": original["identity_hash"],
        "source_freeze": old_state["freeze"],
        "inherited_files": inherited,
        "approved_total_cny": str(total),
        "authorization": authorization,
        "remaining_calls": remaining,
        "preparation_hash": digest(manifest),
        "original_state": old_state,
        "original_preflight": old_preflight,
    }
    core.dump(directory / "preparation.json", manifest, new=True)
    core.dump(directory / "continuation.json", continuation, new=True)
    state = {
        **old_state,
        "state": "prepared",
        "in_flight": None,
        "stop_reason": None,
        "next_call": len(old_state["records"]),
        "continuation_hash": digest(continuation),
        "preparation_hash": digest(manifest),
    }
    core.dump(directory / "state.json", state, new=True)
    return {"state": "prepared", "remaining_calls": len(remaining), "approved_total_cny": str(total)}


def session_data(directory, *, committed=True):
    manifest = load(directory, committed=committed)
    state = read_json(directory / "state.json")
    continuation = read_json(directory / "continuation.json")
    if (
        state["continuation_hash"] != digest(continuation)
        or continuation["contract"] != policy()
        or continuation["preparation_hash"] != digest(manifest)
        or digest(state["records"][: policy()["attempted_prefix"]]) != policy()["original_records_hash"]
    ):
        raise EvaluationError("v20_continuation_binding_changed")
    return manifest, state, continuation


async def preflight(directory, *, progress=None):
    manifest, state, continuation = session_data(directory, committed=False)
    if state["state"] != "prepared":
        raise EvaluationError("v20_preflight_requires_prepared_session")
    hashes = {}
    for call in continuation["remaining_calls"]:
        call_id = digest(call)
        value = AtomicMemoryExtractionInput.model_validate(
            read_json(directory / "inputs" / (call["row_id"] + ".json"))[call["input_index"]]
        )
        if digest(value.model_dump(mode="json")) != call["input_hash"]:
            raise EvaluationError("v20_private_input_changed")
        record = {}
        response = mock_response()
        response["model"] = core.contract()["allowed_response_models"][0]
        response["choices"][0]["message"]["content"] = '{"candidates":[]}'
        expected = continuation["original_preflight"]["request_hashes"][call_id]
        await extract(
            value,
            api_key="offline-not-a-credential",
            record=record,
            save=recorder(directory / "preflight", call_id),
            expected_hash=expected,
            transport=httpx.MockTransport(lambda request, body=response: httpx.Response(200, json=body)),
        )
        hashes[call_id] = record["request_facts"]["http_request_hash"]
        if progress is not None and len(hashes) % 25 == 0:
            progress({"preflight_calls": len(hashes), "provider_requests": 0})
    result = {
        "passed": True,
        "calls": len(hashes),
        "provider_requests": 0,
        "preparation_hash": digest(manifest),
        "continuation_hash": digest(continuation),
        "request_hashes": hashes,
    }
    core.dump(directory / "preflight.json", result, new=True)
    return {k: v for k, v in result.items() if k != "request_hashes"}


def initialize(directory):
    with locked(directory):
        manifest, state, continuation = session_data(directory)
        check = read_json(directory / "preflight.json")
        if (
            state["state"] != "prepared"
            or not check["passed"]
            or (check["preparation_hash"], check["continuation_hash"], set(check["request_hashes"]))
            != (digest(manifest), digest(continuation), {digest(c) for c in continuation["remaining_calls"]})
        ):
            raise EvaluationError("v20_preflight_binding_or_state_changed")
        for relative, expected in continuation["inherited_files"].items():
            if file_hash(directory / relative) != expected:
                raise EvaluationError("v20_copied_history_changed")
        state.update(state="ready", mode="live", freeze=core.committed_freeze(), preflight_hash=digest(check))
        core.dump(directory / "state.json", state)
        return {"state": "ready", "remaining_calls": len(continuation["remaining_calls"]), "freeze": state["freeze"]}


async def run_next(directory, *, execute_live=False, transport=None):
    with locked(directory):
        manifest, state, continuation = session_data(directory, committed=execute_live)
        if state["state"] != "ready" or (state["mode"] == "live") != execute_live:
            raise EvaluationError("v20_session_not_ready_or_mode_mismatch_no_resend")
        if execute_live and transport is not None:
            raise EvaluationError("v20_live_transport_injection_forbidden")
        preflight_receipt = read_json(directory / "preflight.json")
        if state["preflight_hash"] != digest(preflight_receipt):
            raise EvaluationError("v20_preflight_changed")
        calls = calls_for(manifest)
        if state["next_call"] == len(calls):
            state["state"] = "finished"
            core.dump(directory / "state.json", state)
            return status(directory)
        if budget_available(state, continuation["approved_total_cny"]) < reservation():
            state.update(state="budget_stopped", stop_reason="unknown_reserves_plus_next_request_exceed_budget")
            core.dump(directory / "state.json", state)
            return status(directory)
        if len(state["records"]) != state["next_call"]:
            raise EvaluationError("v20_attempt_index_changed_no_resend")
        call = calls[state["next_call"]]
        call_id = digest(call)
        if call_id not in preflight_receipt["request_hashes"] or any(r["call_id"] == call_id for r in state["records"]):
            raise EvaluationError("v20_unplanned_or_already_attempted_call")
        row = next(r for r in manifest["rows"] if r["row_id"] == call["row_id"])
        case = next(c for c in core.suite_for(row["dataset"]).cases if c.case_id == row["case"])
        rebuilt, values = await core.prepare_one(row["dataset"], case, row["arm"], row["budget"], row["repetition"])
        if (
            core.comparable(row) != core.comparable(rebuilt)
            or row["admitted_atom_hashes"] != rebuilt["admitted_atom_hashes"]
        ):
            raise EvaluationError("v20_real_Source_rebuild_changed")
        value = values[call["input_index"]]
        if digest(value.model_dump(mode="json")) != call["input_hash"]:
            raise EvaluationError("v20_rebuilt_input_changed")
        key = credentials() if execute_live else "offline-not-a-credential"
        state.update(state="in_flight", in_flight=call_id)
        core.dump(directory / "state.json", state)
        record = {
            "call_id": call_id,
            **call,
            "provider_request_started": False,
            "pipeline_entered": False,
            "pipeline_returned": False,
            "provider_usage": None,
            "outcome": "pending",
        }
        save = recorder(directory, call_id)
        output = None
        terminal = None
        try:
            output = await extract(
                value,
                api_key=key,
                transport=transport or httpx.AsyncHTTPTransport(retries=0),
                record=record,
                save=save,
                expected_hash=preflight_receipt["request_hashes"][call_id],
            )
            record["outcome"] = "returned"
        except asyncio.CancelledError:
            record["outcome"] = "cancelled"
            record["failure_category"] = "cancelled"
            terminal = "cancelled"
        except (InferenceError, httpx.HTTPError, TimeoutError) as error:
            record["outcome"] = "output_or_inference_failed"
            record["failure_category"] = failure_category(error)
            parsed = directory / "audit" / (call_id + ".parsed.json")
            if parsed.exists():
                output = AtomicMemoryExtractionOutput.model_validate(read_json(parsed))
        except Exception:  # noqa: BLE001 -- terminal audit boundary never prints provider values
            record["outcome"] = "audit_failed"
            record["failure_category"] = "audit_failure"
            terminal = "audit_failed"
        known = account_record(state, record)
        # Score before recording the attempt: an unscored returned response must never
        # reach a finished continuation, so scoring failure is an explicit, resumable stop.
        scoring_failed = False
        if output is not None and terminal is None:
            try:
                record["score"], review = score_output(
                    case, value, tuple(Support.model_validate(s) for s in row["sample"]["supports"]), output
                )
                save("review", review)
            except Exception:  # noqa: BLE001 -- usage is already durably recorded
                scoring_failed = True
                record["scoring_failed"] = True
                record["failure_category"] = "scoring_failure"
        state["records"].append(record)
        if terminal:
            state.update(state=terminal, stop_reason=terminal + "_no_resend")
        elif scoring_failed:
            state.update(state="audit_failed", stop_reason="scoring_failure_no_resend")
        elif known is None:
            state.update(state="unknown_usage_stopped", stop_reason="new_unknown_usage_not_zero")
        elif record.get("response_model") not in core.contract()["allowed_response_models"]:
            state.update(state="provider_changed", stop_reason="unrecognized_response_model")
        else:
            state.update(state="ready", next_call=state["next_call"] + 1, in_flight=None)
        save("attempt", record)
        core.dump(directory / "state.json", state)
        if terminal == "cancelled":
            raise asyncio.CancelledError
        return status(directory)


def status(directory):
    state = read_json(directory / "state.json")
    continuation = read_json(directory / "continuation.json")
    return {
        "state": state["state"],
        "new_attempted_calls": len(state["records"]) - policy()["attempted_prefix"],
        "remaining_calls": policy()["remaining_calls"] - (len(state["records"]) - policy()["attempted_prefix"]),
        "session_known_upper_cny": state["known_charge_cny"],
        "protected_available_cny": str(budget_available(state, continuation["approved_total_cny"])),
        "approved_total_cny": continuation["approved_total_cny"],
    }


def export(directory, output):
    _, state, continuation = session_data(directory, committed=False)
    result = report.summarize(directory)
    result.update(
        schema="evidence-admission.v20.report.v1",
        continuation={
            "contract": policy(),
            "authorization": continuation["authorization"],
            "original_freeze": continuation["source_freeze"],
            "original_state_hash": policy()["original_state_hash"],
            "remaining_calls_hash": policy()["remaining_calls_hash"],
            **status(directory),
            "all_planned_calls_attempted": len(state["records"]) == len(calls_for(load(directory, committed=False))),
            "original_fourth_call_retried": False,
            "historical_attempts_retried": False,
        },
    )
    result["budget"]["approved_cny"] = continuation["approved_total_cny"]
    core.dump(output, result, new=True)
    return {"decision": result["decision"], "state": state["state"], "report_hash": digest(result), **status(directory)}
