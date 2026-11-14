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

"""No-resend and protected-budget regressions for a continuation using the real native SDK."""

from __future__ import annotations

import asyncio
import copy
from decimal import Decimal

import httpx
import pytest

from powercontext_eval_evidence_admission.manifests import EvaluationError, read_json
from powercontext_eval_evidence_admission.models import digest
from powercontext_eval_evidence_admission.v9_preflight.workflow import mock_response
from powercontext_eval_evidence_admission.v18 import core
from powercontext_eval_evidence_admission.v18.adapter import extract
from powercontext_eval_evidence_admission.v18.workflow import calls_for, recorder, reservation
from powercontext_eval_evidence_admission.v19 import workflow


def test_attempted_unknown_usage_is_excluded_from_remaining_plan():
    manifest = {"rows": [{"row_id": "row", "sample": {"input_hashes": ["a", "b", "c"]}}]}
    calls = calls_for(manifest)
    attempted = [{"call_id": digest(calls[0]), **calls[0], "known_charge_cny": None}]
    assert workflow.remaining_calls(manifest, attempted) == calls[1:]
    with pytest.raises(EvaluationError, match="exact_attempted_prefix"):
        workflow.remaining_calls(manifest, [{**attempted[0], "call_id": digest(calls[1])}])
    with pytest.raises(EvaluationError, match="input_binding"):
        workflow.remaining_calls(manifest, [{**attempted[0], "input_hash": "changed"}])


def test_every_unknown_keeps_full_context_reserve_and_known_cost_is_not_reset():
    state = {"known_charge_cny": "0.005214", "records": [{"known_charge_cny": None}]}
    assert workflow.budget_available(state, "5") == Decimal("2.770600")
    assert workflow.budget_available(state, "15") == Decimal("12.770600")
    state["records"].append({"known_charge_cny": None})
    assert workflow.budget_available(state, "5") == Decimal("0.657064") < reservation()


def test_budget_increase_cannot_reuse_default_five_yuan_authorization(tmp_path):
    with pytest.raises(EvaluationError, match="explicit_authorization"):
        workflow.prepare(tmp_path / "absent", tmp_path / "new", approved_total="15")


@pytest.mark.parametrize("budget", ["NaN", "Infinity", "-1", "101", "invalid"])
def test_invalid_budget_rejected_before_reading_private_sources(tmp_path, budget):
    with pytest.raises(EvaluationError, match="invalid_budget"):
        workflow.prepare(tmp_path / "absent", tmp_path / "new", approved_total=budget)


@pytest.fixture
def continuation(tmp_path, monkeypatch):
    async def build():
        source = tmp_path / "old"
        case = core.suite_for("synthetic-v1").cases[0]
        rows, hashes = [], {}
        for arm in core.ARMS:
            row, values = await core.prepare_one("synthetic-v1", case, arm, 4096, 0)
            rows.append(row)
            core.dump(source / "inputs" / (row["row_id"] + ".json"), [v.model_dump(mode="json") for v in values])
            call = calls_for({"rows": [row]})[0]
            record = {}
            response = mock_response()
            response["model"] = "deepseek-flash"
            response["choices"][0]["message"]["content"] = '{"candidates":[]}'
            await extract(
                values[0],
                api_key="offline-not-a-credential",
                record=record,
                save=recorder(tmp_path / "old-mock-preflight", digest(call)),
                transport=httpx.MockTransport(lambda request, body=response: httpx.Response(200, json=body)),
            )
            hashes[digest(call)] = record["request_facts"]["http_request_hash"]
        snapshot = core.identity()
        manifest = {
            "schema": "offline-regression",
            "identity": snapshot,
            "identity_hash": digest(snapshot),
            "rows": rows,
            "expected_rows": 3,
            "reliability_passed": True,
            "nondeterministic_groups": 0,
            "paired_identity_mismatches": 0,
            "generation_performed": False,
        }
        calls = calls_for(manifest)
        old = {
            "call_id": digest(calls[0]),
            **calls[0],
            "known_charge_cny": None,
            "provider_usage": None,
            "outcome": "output_or_inference_failed",
            "pipeline_returned": False,
        }
        state = {
            "state": "unknown_usage_stopped",
            "records": [old],
            "known_charge_cny": "0",
            "mode": "live",
            "freeze": {"identity_hash": digest(snapshot)},
            "next_call": 0,
        }
        preflight = {"passed": True, "preparation_hash": digest(manifest), "request_hashes": hashes}
        p = copy.deepcopy(workflow.policy())
        p.update(
            original_identity_hash=digest(snapshot),
            original_preparation_hash=digest(manifest),
            original_state_hash=digest(state),
            original_preflight_hash=digest(preflight),
            original_records_hash=digest(state["records"]),
            attempted_prefix=1,
            remaining_calls=2,
            remaining_calls_hash=digest(calls[1:]),
        )
        monkeypatch.setattr(workflow, "policy", lambda: p)
        core.dump(source / "preparation.json", manifest)
        core.dump(source / "state.json", state)
        core.dump(source / "preflight.json", preflight)
        directory = tmp_path / "new"
        workflow.prepare(source, directory)
        await workflow.preflight(directory)
        current = read_json(directory / "state.json")
        current.update(state="ready", mode="offline", preflight_hash=digest(read_json(directory / "preflight.json")))
        core.dump(directory / "state.json", current)
        return directory, source

    return asyncio.run(build())


def test_native_continuation_never_resends_unknown_prefix_and_history_stays_immutable(continuation):
    directory, source = continuation
    original = (source / "state.json").read_bytes()
    seen = []

    def respond(request):
        seen.append(request)
        response = mock_response()
        response["model"] = "deepseek-flash"
        response["choices"][0]["message"]["content"] = '{"candidates":[]}'
        return httpx.Response(200, json=response)

    async def run():
        assert (await workflow.run_next(directory, transport=httpx.MockTransport(respond)))["state"] == "ready"
        assert (await workflow.run_next(directory, transport=httpx.MockTransport(respond)))["state"] == "ready"
        assert (await workflow.run_next(directory, transport=httpx.MockTransport(respond)))["state"] == "finished"
        with pytest.raises(EvaluationError, match="no_resend"):
            await workflow.run_next(directory, transport=httpx.MockTransport(respond))

    asyncio.run(run())
    assert len(seen) == 2
    records = read_json(directory / "state.json")["records"]
    assert len(records) == len({r["call_id"] for r in records}) == 3
    assert records[0]["known_charge_cny"] is None
    assert (source / "state.json").read_bytes() == original


def test_second_unknown_usage_stops_without_resending_and_keeps_reserve(continuation):
    directory, _ = continuation

    def fail(request):
        raise httpx.ConnectError("private error must not appear in report", request=request)

    result = asyncio.run(workflow.run_next(directory, transport=httpx.MockTransport(fail)))
    assert result["state"] == "unknown_usage_stopped"
    assert result["new_attempted_calls"] == 1
    assert Decimal(result["protected_available_cny"]) < reservation()
    with pytest.raises(EvaluationError, match="no_resend"):
        asyncio.run(workflow.run_next(directory, transport=httpx.MockTransport(fail)))


def test_budget_guard_includes_inherited_unknown_before_any_transport(continuation):
    directory, _ = continuation
    state = read_json(directory / "state.json")
    state["known_charge_cny"] = "1"
    core.dump(directory / "state.json", state)

    def forbidden(request):
        pytest.fail("budget guard sent a request")

    result = asyncio.run(workflow.run_next(directory, transport=httpx.MockTransport(forbidden)))
    assert result["state"] == "budget_stopped" and result["new_attempted_calls"] == 0


def test_scoring_failure_preserves_received_usage_and_stops(continuation, monkeypatch):
    directory, _ = continuation

    def bad_score(*args):
        raise ValueError("private evaluator failure")

    monkeypatch.setattr(workflow, "score_output", bad_score)
    response = mock_response()
    response["model"] = "deepseek-flash"
    response["choices"][0]["message"]["content"] = '{"candidates":[]}'
    result = asyncio.run(
        workflow.run_next(directory, transport=httpx.MockTransport(lambda request: httpx.Response(200, json=response)))
    )
    assert result["state"] == "audit_failed" and result["new_attempted_calls"] == 1
    last = read_json(directory / "state.json")["records"][-1]
    assert last["known_charge_cny"] is not None and last["provider_usage"] is not None


def test_scoring_interruption_resumes_without_resending_or_double_charging(continuation, monkeypatch):
    directory, _ = continuation
    original_score_output = workflow.score_output
    sent_requests = 0

    def respond(_request):
        nonlocal sent_requests
        sent_requests += 1
        response = mock_response()
        response["model"] = "deepseek-flash"
        response["choices"][0]["message"]["content"] = '{"candidates":[]}'
        return httpx.Response(200, json=response)

    def terminate_before_scoring(*_args, **_kwargs):
        raise SystemExit("simulated termination before scoring")

    monkeypatch.setattr(workflow, "score_output", terminate_before_scoring)
    with pytest.raises(SystemExit, match="simulated termination before scoring"):
        asyncio.run(workflow.run_next(directory, transport=httpx.MockTransport(respond)))

    pending = read_json(directory / "state.json")
    assert pending["state"] == "scoring_pending"
    assert pending["next_call"] == 1
    assert len(pending["records"]) == 2
    assert pending["records"][-1]["score_required"] is True
    assert pending["records"][-1]["scoring_state"] == "pending"
    assert pending["records"][-1]["score"] is None
    recorded_charge = pending["known_charge_cny"]
    assert sent_requests == 1

    monkeypatch.setattr(workflow, "score_output", original_score_output)

    def reject_resend(_request):
        pytest.fail("scoring recovery must not resend the provider request")

    resumed = asyncio.run(workflow.run_next(directory, transport=httpx.MockTransport(reject_resend)))
    recovered = read_json(directory / "state.json")
    assert resumed["state"] == recovered["state"] == "ready"
    assert recovered["next_call"] == 2
    assert recovered["known_charge_cny"] == recorded_charge
    assert len(recovered["records"]) == 2
    assert recovered["records"][-1]["scoring_state"] == "complete"
    assert recovered["records"][-1]["score"] is not None
    assert sent_requests == 1
