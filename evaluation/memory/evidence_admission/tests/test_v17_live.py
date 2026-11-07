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

"""Observed SDK, billing and durable stopping regressions; no provider requests."""

from __future__ import annotations

import asyncio
import json
from decimal import Decimal

import httpx
import pytest

from powercontext_eval_evidence_admission.manifests import EvaluationError, load_suite, read_json
from powercontext_eval_evidence_admission.v9_preflight.workflow import mock_response
from powercontext_eval_evidence_admission.v16.wire import prepare
from powercontext_eval_evidence_admission.v17 import workflow
from powercontext_eval_evidence_admission.v17.adapter import extract
from powercontext_eval_evidence_admission.v17.freeze import inherited

from .test_v14_codec import supplied


def test_historical_policy_rejects_the_changed_runtime_without_rewriting_its_identity():
    with pytest.raises(EvaluationError, match="inherited_dependency_changed"):
        inherited()


def test_per_request_full_context_reservation_and_cumulative_boundary():
    assert workflow.reservation() == Decimal("2.113536")
    assert workflow.reserve_next(Decimal("2.795944")) == Decimal("2.113536")
    with pytest.raises(EvaluationError, match="reservation_unavailable"):
        workflow.reserve_next(Decimal("2.795945"))


@pytest.mark.parametrize(
    "usage", [None, {}, {"prompt_tokens": True, "completion_tokens": 0}, {"prompt_tokens": 0, "completion_tokens": -1}]
)
def test_absent_or_invalid_usage_is_unknown_not_zero(usage):
    assert workflow.charge(usage) is None


def test_full_miss_peak_cost_includes_all_output():
    assert workflow.charge({"prompt_tokens": 1000, "completion_tokens": 200}) == Decimal("0.0036")


def test_named_literal_credential_can_be_loaded_without_executing_shell(tmp_path, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setattr(workflow.Path, "home", lambda: tmp_path)
    (tmp_path / ".bashrc").write_text("touch never-execute\nexport DEEPSEEK_API_KEY='offline-test-key' # local\n")
    assert workflow.credentials() == "offline-test-key"
    assert not (tmp_path / "never-execute").exists()


def test_shell_substitution_is_not_executed_for_credentials(tmp_path, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setattr(workflow.Path, "home", lambda: tmp_path)
    (tmp_path / ".bashrc").write_text('export DEEPSEEK_API_KEY="$(touch never-execute)"\n')
    with pytest.raises(EvaluationError, match="credential_unavailable"):
        workflow.credentials()
    assert not (tmp_path / "never-execute").exists()


def test_real_sdk_pipeline_preserves_copy_bytes_and_captures_request_settings(tmp_path):
    async def scenario():
        literal = "👩🏽‍💻e\u0301\r\n"
        value, supports = supplied("retain `" + literal + "` exactly")
        request = prepare(value, supports)
        cell = next(c for c in request.cells if c.value == literal)
        response = mock_response()
        response["choices"][0]["message"]["content"] = json.dumps(
            {
                "candidates": [
                    {
                        "kind": "fact",
                        "description": "Synthetic protocol candidate",
                        "evidence_ids": ["e0"],
                        "parts": [{"type": "copy", "quote_id": cell.cell_id}],
                    }
                ]
            },
            ensure_ascii=False,
        )
        record = {}
        output = await extract(
            request,
            api_key="offline-test-key",
            record=record,
            save=workflow.recorder(tmp_path, "copy"),
            transport=httpx.MockTransport(lambda req: httpx.Response(200, json=response)),
        )
        assert output.candidates[0].text.encode() == literal.encode()
        body = read_json(tmp_path / "audit/copy.request.json")
        assert body["thinking"] == {"type": "disabled"}
        assert body["temperature"] == 0
        assert body["max_tokens"] == 2048
        assert record["pipeline_returned"] is True
        assert record["request_facts"]["model_visible_text_estimated_tokens"] <= 4096

    asyncio.run(scenario())


def controlled_session(tmp_path, monkeypatch):
    """Control only session identity/Source reconstruction; actual SDK and Pipeline still execute."""
    value, supports = supplied("an admitted `value`")
    request = prepare(value, supports)
    case = load_suite(experiment_version="v5", dataset="synthetic-v1").cases[0]
    call = {"call_id": "controlled-0", "case": case.case_id, "request_hash": request.binding_hash}
    plan = {"freeze": {"identity_hash": "controlled"}, "calls": [call]}
    workflow.save_json(tmp_path / "location.json", {"baseline_root": str(tmp_path)})
    workflow.save_json(
        tmp_path / "state.json",
        {
            "mode": "offline_mock",
            "plan_hash": "controlled",
            "state": "ready",
            "next": 0,
            "known_charge_cny": "0",
            "records": [],
        },
    )
    monkeypatch.setattr(workflow, "load", lambda directory: (plan, read_json(directory / "state.json")))

    async def rebuild(_root):
        return [(call["call_id"], case, request)], [call]

    monkeypatch.setattr(workflow, "rebuild", rebuild)
    return plan


@pytest.mark.parametrize("fault", ["invalid_copy", "unknown_usage", "http_failure", "length", "thinking"])
def test_failed_request_is_audited_billed_when_known_and_never_resent(tmp_path, monkeypatch, fault):
    controlled_session(tmp_path, monkeypatch)
    response = mock_response()
    if fault == "invalid_copy":
        response["choices"][0]["message"]["content"] = json.dumps(
            {
                "candidates": [
                    {
                        "kind": "fact",
                        "description": "Synthetic protocol candidate",
                        "evidence_ids": ["e0"],
                        "parts": [{"type": "copy", "quote_id": "c999"}],
                    }
                ]
            }
        )
    elif fault == "unknown_usage":
        response.pop("usage")
    elif fault == "length":
        response["choices"][0]["finish_reason"] = "length"
    elif fault == "thinking":
        response["choices"][0]["message"]["reasoning_content"] = "controlled thinking"
    count = 0

    def handler(request):
        nonlocal count
        count += 1
        return httpx.Response(500 if fault == "http_failure" else 200, json=response)

    async def scenario():
        state = await workflow.run_next(tmp_path, transport=httpx.MockTransport(handler))
        assert state["state"] == "failed"
        assert (tmp_path / "audit/controlled-0.response-bytes.json").exists()
        assert state["unknown_usage_calls"] == (1 if fault == "unknown_usage" else 0)
        if fault != "unknown_usage":
            assert Decimal(state["known_new_upper_charge_cny"]) > 0
        with pytest.raises(EvaluationError, match="not_ready_no_resend"):
            await workflow.run_next(tmp_path, transport=httpx.MockTransport(handler))
        assert count == 1

    asyncio.run(scenario())


def test_real_task_cancellation_keeps_unknown_exposure_and_blocks_resend(tmp_path, monkeypatch):
    controlled_session(tmp_path, monkeypatch)

    async def scenario():
        started = asyncio.Event()

        async def handler(request):
            started.set()
            await asyncio.Event().wait()

        task = asyncio.create_task(workflow.run_next(tmp_path, transport=httpx.MockTransport(handler)))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        result = workflow.status(tmp_path)
        assert result["state"] == "cancelled"
        assert result["unknown_usage_calls"] == 1
        assert Decimal(result["unknown_reserved_upper_cny"]) == workflow.reservation()
        with pytest.raises(EvaluationError, match="not_ready_no_resend"):
            await workflow.run_next(tmp_path, transport=httpx.MockTransport(handler))

    asyncio.run(scenario())


def test_awaiting_review_cannot_resend_and_missing_case_facts_cannot_complete(tmp_path, monkeypatch):
    controlled_session(tmp_path, monkeypatch)

    async def scenario():
        await workflow.run_next(
            tmp_path, transport=httpx.MockTransport(lambda req: httpx.Response(200, json=mock_response()))
        )
        assert workflow.status(tmp_path)["state"] == "awaiting_review"
        with pytest.raises(EvaluationError, match="not_ready_no_resend"):
            await workflow.run_next(tmp_path, transport=httpx.MockTransport(lambda req: httpx.Response(200)))
        packet = read_json(tmp_path / "audit/controlled-0.packet.json")
        judgement = packet["review_template"]
        judgement["reviewer"] = "controlled_offline_fixture"
        original = judgement["output_hash"]
        judgement["output_hash"] = "wrong"
        with pytest.raises(EvaluationError, match="review_output_mismatch"):
            workflow.review(tmp_path, judgement)
        assert workflow.status(tmp_path)["state"] == "awaiting_review"
        judgement["output_hash"] = original
        final = workflow.review(tmp_path, judgement)
        assert final["state"] == "quality_failed"
        assert final["records"][-1]["case_complete_gate_passed"] is False

    asyncio.run(scenario())


def test_preflight_hash_mismatch_stops_before_provider(tmp_path):
    async def scenario():
        value, supports = supplied("an admitted `value`")
        request = prepare(value, supports)
        count = 0

        def handler(_request):
            nonlocal count
            count += 1
            return httpx.Response(200, json=mock_response())

        with pytest.raises(EvaluationError, match="preflight_request_hash_changed"):
            await extract(
                request,
                api_key="offline-key",
                transport=httpx.MockTransport(handler),
                record={},
                save=workflow.recorder(tmp_path, "mismatch"),
                expected_hash="wrong",
            )
        assert count == 0

    asyncio.run(scenario())
