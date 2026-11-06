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

"""Finite serial comparisons: quality failures are data; audit, money and cancellation stop the session."""

from __future__ import annotations

import asyncio
import fcntl
import os
from contextlib import contextmanager
from decimal import Decimal

import httpx
from powercontext.builtin.artifacts.atomic_memory.extraction import (
    AtomicMemoryExtractionInput,
    AtomicMemoryExtractionOutput,
)
from powercontext.builtin.inference.errors import InferenceError

from ..manifests import EvaluationError, read_json
from ..models import Support, digest
from ..v9_preflight.workflow import mock_response
from ..v17.workflow import credentials
from .adapter import extract
from .core import committed_freeze, comparable, contract, dump, identity, prepare_one, suite_for
from .scoring import score_output


@contextmanager
def locked(directory):
    fd = os.open(directory / ".lock", os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(fd, "w") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise EvaluationError("v18_session_already_running") from None
        yield


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
            dump(path, value, new=kind != "attempt")

    return save


def charge(usage):
    if not isinstance(usage, dict):
        return None
    counts = [usage.get("prompt_tokens"), usage.get("completion_tokens")]
    if any(type(c) is not int or c < 0 for c in counts):
        return None
    policy = contract()
    return (
        Decimal(counts[0]) * Decimal(policy["input_per_million_cny"])
        + Decimal(counts[1]) * Decimal(policy["output_per_million_cny"])
    ) / 1000000


def reservation():
    return charge(
        {"prompt_tokens": contract()["input_context_ceiling"], "completion_tokens": contract()["max_output_tokens"]}
    )


def can_reserve(known_charge):
    p = contract()
    available = Decimal(p["budget_total_cny"]) - Decimal(p["prior_known_upper_cny"]) - Decimal(known_charge)
    return available >= reservation()


def load(directory, *, committed=True):
    manifest = read_json(directory / "preparation.json")
    if manifest["identity"] != identity() or not manifest["reliability_passed"]:
        raise EvaluationError("v18_preparation_identity_or_reliability_changed")
    if committed and committed_freeze()["identity_hash"] != manifest["identity_hash"]:
        raise EvaluationError("v18_committed_identity_changed")
    return manifest


def calls_for(manifest):
    return [
        {"row_id": row["row_id"], "input_index": index, "input_hash": value_hash}
        for row in manifest["rows"]
        for index, value_hash in enumerate(row["sample"]["input_hashes"])
    ]


async def preflight(directory, *, progress=None):
    manifest = load(directory, committed=False)
    hashes = {}
    calls = calls_for(manifest)
    for call in calls:
        values = read_json(directory / "inputs" / (call["row_id"] + ".json"))
        value = AtomicMemoryExtractionInput.model_validate(values[call["input_index"]])
        if digest(value.model_dump(mode="json")) != call["input_hash"]:
            raise EvaluationError("v18_private_input_changed")
        call_id = digest(call)
        record = {}
        response = mock_response()
        response["model"] = contract()["allowed_response_models"][0]
        response["choices"][0]["message"]["content"] = '{"candidates":[]}'
        await extract(
            value,
            api_key="offline-not-a-credential",
            record=record,
            save=recorder(directory / "preflight", call_id),
            transport=httpx.MockTransport(lambda req, body=response: httpx.Response(200, json=body)),
        )
        hashes[call_id] = record["request_facts"]["http_request_hash"]
        if progress is not None and len(hashes) % 25 == 0:
            progress({"preflight_calls": len(hashes), "total": len(calls), "provider_requests": 0})
    result = {
        "identity_hash": manifest["identity_hash"],
        "preparation_hash": digest(manifest),
        "request_hashes": hashes,
        "calls": len(calls),
        "provider_requests": 0,
        "passed": True,
    }
    dump(directory / "preflight.json", result, new=True)
    return {k: v for k, v in result.items() if k != "request_hashes"}


def initialize_live(directory):
    manifest = load(directory)
    preflight = read_json(directory / "preflight.json")
    if not preflight["passed"] or preflight["preparation_hash"] != digest(manifest):
        raise EvaluationError("v18_preflight_not_bound_to_preparation")
    state = read_json(directory / "state.json")
    if state["state"] != "prepared":
        raise EvaluationError("v18_already_initialized_no_reset")
    state.update(state="ready", mode="live", next_call=0, freeze=committed_freeze(), preparation_hash=digest(manifest))
    dump(directory / "state.json", state)
    return {"state": "ready", "planned_calls": len(calls_for(manifest)), "freeze": state["freeze"]}


async def run_next(directory, *, execute_live=False, transport=None, api_key=None):
    with locked(directory):
        manifest = load(directory, committed=execute_live)
        state = read_json(directory / "state.json")
        calls = calls_for(manifest)
        if state["state"] != "ready" or state["preparation_hash"] != digest(manifest):
            raise EvaluationError("v18_session_not_ready_no_resend")
        if state["next_call"] == len(calls):
            state["state"] = "finished"
            dump(directory / "state.json", state)
            return {"state": "finished"}
        if execute_live and transport is not None:
            raise EvaluationError("v18_live_transport_injection_forbidden")
        if (state.get("mode") == "live") != execute_live:
            raise EvaluationError("v18_execution_mode_mismatch")
        if execute_live and state["freeze"]["identity_hash"] != committed_freeze()["identity_hash"]:
            raise EvaluationError("v18_live_freeze_changed")
        if not can_reserve(state["known_charge_cny"]):
            state.update(state="budget_stopped", stop_reason="next_full_context_reservation_unavailable")
            dump(directory / "state.json", state)
            return {"state": state["state"], "executed_calls": len(state["records"])}
        call = calls[state["next_call"]]
        row = next(r for r in manifest["rows"] if r["row_id"] == call["row_id"])
        case = next(c for c in suite_for(row["dataset"]).cases if c.case_id == row["case"])
        rebuilt, values = await prepare_one(row["dataset"], case, row["arm"], row["budget"], row["repetition"])
        if comparable(row) != comparable(rebuilt) or row["admitted_atom_hashes"] != rebuilt["admitted_atom_hashes"]:
            raise EvaluationError("v18_source_rebuild_or_admission_changed")
        value = values[call["input_index"]]
        if digest(value.model_dump(mode="json")) != call["input_hash"]:
            raise EvaluationError("v18_input_changed_after_rebuild")
        call_id = digest(call)
        expected = read_json(directory / "preflight.json")["request_hashes"][call_id]
        key = credentials() if execute_live else api_key or "offline-not-a-credential"
        state.update(state="in_flight", in_flight=call_id)
        dump(directory / "state.json", state)
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
        try:
            output = await extract(
                value,
                api_key=key,
                transport=transport or httpx.AsyncHTTPTransport(retries=0),
                record=record,
                save=save,
                expected_hash=expected,
            )
            record["outcome"] = "returned"
        except asyncio.CancelledError:
            state.update(state="cancelled", stop_reason="local_cancellation_no_resend")
            record["outcome"] = "cancelled"
            state["records"].append(record)
            dump(directory / "state.json", state)
            raise
        except (InferenceError, httpx.HTTPError, TimeoutError):
            # Complete raw usage permits measuring invalid outputs without retrying the request.
            record["outcome"] = "output_or_inference_failed"
            parsed = directory / "audit" / (call_id + ".parsed.json")
            if parsed.exists():
                output = AtomicMemoryExtractionOutput.model_validate(read_json(parsed))
        except Exception:  # noqa: BLE001 -- audit boundary persists a terminal state and removes sensitive errors
            # Do not expose provider values or continue after an evaluator/audit failure.
            state.update(state="audit_failed", stop_reason="evaluator_or_audit_failure")
            state["records"].append(record)
            dump(directory / "state.json", state)
            raise EvaluationError("v18_evaluator_or_audit_failure") from None
        known = charge(record.get("provider_usage"))
        record["known_charge_cny"] = str(known) if known is not None else None
        if output is not None:
            supports = tuple(Support.model_validate(s) for s in row["sample"]["supports"])
            result, review = score_output(case, value, supports, output)
            save("review", review)
            record["score"] = result
        state["records"].append(record)
        if known is None:
            state.update(state="unknown_usage_stopped", stop_reason="unknown_usage_not_zero")
        else:
            state["known_charge_cny"] = str(Decimal(state["known_charge_cny"]) + known)
            if record.get("response_model") not in contract()["allowed_response_models"]:
                state.update(state="provider_changed", stop_reason="unrecognized_response_model")
            else:
                state.update(state="ready", next_call=state["next_call"] + 1, in_flight=None)
        save("attempt", record)
        dump(directory / "state.json", state)
        return {
            "state": state["state"],
            "completed_calls": len(state["records"]),
            "planned_calls": len(calls),
            "outcome": record["outcome"],
            "known_charge_cny": state["known_charge_cny"],
        }


async def replay_recorded(directory, output_directory):
    manifest = load(directory)
    state = read_json(directory / "state.json")
    if output_directory.exists():
        raise EvaluationError("v18_replay_output_exists")
    results = []
    for old in state["records"]:
        path = directory / "audit" / (old["call_id"] + ".response-bytes.json")
        if not path.exists():
            results.append({"call_id": old["call_id"], "replay": "unavailable_raw_response"})
            continue
        raw = path.read_bytes()
        if digest(raw.hex()) != old["response_hash"]:
            raise EvaluationError("v18_recorded_response_changed")
        row = next(r for r in manifest["rows"] if r["row_id"] == old["row_id"])
        case = next(c for c in suite_for(row["dataset"]).cases if c.case_id == row["case"])
        rebuilt, values = await prepare_one(row["dataset"], case, row["arm"], row["budget"], row["repetition"])
        if comparable(row) != comparable(rebuilt):
            raise EvaluationError("v18_recorded_source_changed")
        record = {}
        outcome, scored = "returned", None
        try:
            output = await extract(
                values[old["input_index"]],
                api_key="offline-not-a-credential",
                record=record,
                save=recorder(output_directory, old["call_id"]),
                expected_hash=old["request_facts"]["http_request_hash"],
                transport=httpx.MockTransport(
                    lambda req, body=raw, status=old["http_status"]: httpx.Response(status, content=body)
                ),
            )
            review = read_json(directory / "audit" / (old["call_id"] + ".review.json"))
            scored, _ = score_output(
                case,
                values[old["input_index"]],
                tuple(Support.model_validate(s) for s in row["sample"]["supports"]),
                output,
                review,
            )
        except (InferenceError, httpx.HTTPError, TimeoutError):
            outcome = "output_or_inference_failed"
            parsed = output_directory / "audit" / (old["call_id"] + ".parsed.json")
            if parsed.exists() and old.get("score") is not None:
                review = read_json(directory / "audit" / (old["call_id"] + ".review.json"))
                scored, _ = score_output(
                    case,
                    values[old["input_index"]],
                    tuple(Support.model_validate(s) for s in row["sample"]["supports"]),
                    AtomicMemoryExtractionOutput.model_validate(read_json(parsed)),
                    review,
                )
        if (
            outcome != old["outcome"]
            or scored != old.get("score")
            or record.get("output_hash") != old.get("output_hash")
        ):
            raise EvaluationError("v18_recorded_output_or_score_changed")
        results.append({"call_id": old["call_id"], "replay": "matched", "outcome": outcome})
    result = {
        "provider_requests": 0,
        "results": results,
        "all_available_matched": all(r["replay"] == "matched" for r in results),
    }
    dump(output_directory / "result.json", result, new=True)
    return result


def apply_review(directory, call_id, review_file):
    with locked(directory):
        manifest = load(directory)
        state = read_json(directory / "state.json")
        record = next(r for r in state["records"] if r["call_id"] == call_id)
        row = next(r for r in manifest["rows"] if r["row_id"] == record["row_id"])
        case = next(c for c in suite_for(row["dataset"]).cases if c.case_id == row["case"])
        value = AtomicMemoryExtractionInput.model_validate(
            read_json(directory / "inputs" / (row["row_id"] + ".json"))[record["input_index"]]
        )
        output = AtomicMemoryExtractionOutput.model_validate(
            read_json(directory / "audit" / (call_id + ".output.json"))
        )
        review = read_json(review_file)
        result, _ = score_output(
            case, value, tuple(Support.model_validate(s) for s in row["sample"]["supports"]), output, review
        )
        # Keep automatic/original records immutable; audited scores are separate derivatives.
        derivative = {"call_id": call_id, "original_record_hash": digest(record), "review": review, "score": result}
        dump(directory / "reviews" / (call_id + ".json"), derivative, new=True)
        return {"call_id": call_id, "review_hash": digest(review), "unresolved": result["unresolved_candidates"]}
