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

"""Observable SDK/Pipeline, accounting, approval, review and immutable-freeze regressions."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from powercontext.builtin.artifacts.atomic_memory.extraction import AtomicMemoryCandidate
from powercontext.builtin.inference import InferenceError

from powercontext_eval_evidence_admission.historical_artifacts import historical_results_root
from powercontext_eval_evidence_admission.manifests import EvaluationError, read_json
from powercontext_eval_evidence_admission.v9_live.ledger import Ledger, write_private
from powercontext_eval_evidence_admission.v9_preflight.workflow import mock_response
from powercontext_eval_evidence_admission.v12 import freeze
from powercontext_eval_evidence_admission.v12.preflight import controlled_output, controlled_review
from powercontext_eval_evidence_admission.v12.runner import import_review, run_next, status
from powercontext_eval_evidence_admission.v12.session import load_session, prepare_session


def baseline():
    try:
        return historical_results_root() / "v11-offline"
    except EvaluationError as error:
        pytest.skip(str(error))


async def session(tmp_path):
    if "description" in AtomicMemoryCandidate.model_fields:
        pytest.skip("frozen v11 baseline requires its recorded runtime; current description schema rejects it")
    directory = tmp_path / "session"
    await prepare_session(baseline(), directory)
    public, data, private = load_session(directory)
    approval = read_json(directory / "approval-template.json")
    approval.update(
        approved_by="offline-test",
        max_cases=2,
        accept_conditional_billing_envelope=True,
        accept_current_model_alias_route=True,
    )
    return directory, public, data, private, approval


def test_current_runtime_rejects_archived_baseline_before_session_publication(tmp_path):
    async def scenario():
        with pytest.raises(EvaluationError, match="baseline_not_verified"):
            await prepare_session(baseline(), tmp_path / "rejected-session")
        assert not (tmp_path / "rejected-session").exists()

    asyncio.run(scenario())


def response_for(call, *, no_copy=False, compound=False):
    response = mock_response()
    output = controlled_output(call)
    if no_copy:
        from powercontext_eval_evidence_admission.v9.codec import TemplateOutput, render
        from powercontext_eval_evidence_admission.v12.session import call_request

        request = call_request(call)
        rendered = render(
            TemplateOutput.model_validate(output), request.encoded, request_hash=request.encoded.binding_hash
        )
        for c, value in zip(output["candidates"], rendered.candidates, strict=True):
            c["parts"] = [{"type": "text", "text": value.text}]
    if compound:
        output["candidates"] = [
            {
                "kind": "fact",
                "description": "Synthetic protocol candidate",
                "evidence_ids": ["e0"],
                "parts": [part for c in output["candidates"] for part in c["parts"]],
            }
        ]
    response["choices"][0]["message"]["content"] = json.dumps(output, ensure_ascii=False)
    return response


def test_real_sdk_pipeline_review_sequence_and_unicode_are_not_resent(tmp_path):
    async def scenario():
        directory, _, data, private, approval = await session(tmp_path)
        seen = []
        for index, call in enumerate(data["calls"]):

            def handler(request, current=call):
                seen.append(json.loads(request.content))
                return httpx.Response(200, json=response_for(current))

            transport = httpx.MockTransport(handler)
            step = await run_next(directory, approval, transport=transport, api_key="offline-key")
            again = await run_next(directory, approval, transport=transport, api_key="offline-key")
            assert step["private_review_packet"] == again["private_review_packet"]
            assert len(seen) == index + 1
            packet = read_json(private / f"{call['call_id']}.packet.json")
            result = import_review(directory, controlled_review(call, packet))
            assert result["candidate_gate_passed"] and result["copy_parts"] > 0
            assert result["v10_score"]["retained_parent_claims"] == 1
            assert result["legacy_score"]["unresolved"] > 0
            assert not result["formal_acceptance"]
            if index == 1:
                texts = [c["text"] for c in packet["output"]["candidates"]]
                assert any("👩🏽‍💻" in t and "é" in t and t.endswith("\r\n") for t in texts)
        checked = status(directory)
        assert checked["provider_requests"] == 0 and checked["mock_http_requests"] == 2
        assert checked["reviewed_cases"] == 2 and not checked["stop_required"]
        assert all(b["max_tokens"] == 2048 and "max_completion_tokens" not in b for b in seen)
        assert all(
            b["messages"][0]["content"].endswith("Use copy parts for exact literals and raw Unicode.") for b in seen
        )

    asyncio.run(scenario())


@pytest.mark.parametrize("defect", ["http", "invalid_json", "invalid_copy"])
def test_protocol_failure_has_one_auditable_attempt_and_never_retries(tmp_path, defect):
    async def scenario():
        directory, _, data, private, approval = await session(tmp_path)
        seen = []

        def handler(request):
            seen.append(request)
            if defect == "http":
                return httpx.Response(500, json={"error": {"message": "private-provider-value"}})
            response = response_for(data["calls"][0])
            if defect == "invalid_json":
                response["choices"][0]["message"]["content"] = "{"
            else:
                output = controlled_output(data["calls"][0])
                output["candidates"][0]["parts"][0]["byte_end"] = 999999
                response["choices"][0]["message"]["content"] = json.dumps(output)
            return httpx.Response(200, json=response)

        transport = httpx.MockTransport(handler)
        with pytest.raises((InferenceError, EvaluationError)):
            await run_next(directory, approval, transport=transport, api_key="offline-key")
        assert len(seen) == 1 and status(directory)["stop_required"]
        with pytest.raises(EvaluationError, match="stopped"):
            await run_next(directory, approval, transport=transport, api_key="offline-key")
        assert len(seen) == 1
        events = [json.loads(line) for line in (private / "audit/attempts.jsonl").read_bytes().splitlines()]
        terminals = [e for e in events if e["kind"] == "terminal"]
        assert len(terminals) == 1 and terminals[0]["outcome"] == "failed"
        assert terminals[0]["error_category"]

    asyncio.run(scenario())


@pytest.mark.parametrize("defect", ["missing_usage", "input_budget", "output_bound"])
def test_unknown_or_excess_usage_stops_with_output_preserved(tmp_path, defect):
    async def scenario():
        directory, _, data, private, approval = await session(tmp_path)

        def handler(_):
            response = response_for(data["calls"][0])
            if defect == "missing_usage":
                response.pop("usage")
            elif defect == "input_budget":
                response["usage"].update(prompt_tokens=4097, total_tokens=4108)
            else:
                response["usage"].update(completion_tokens=2049, total_tokens=2066)
            return httpx.Response(200, json=response)

        transport = httpx.MockTransport(handler)
        if defect == "output_bound":
            with pytest.raises((InferenceError, EvaluationError)):
                await run_next(directory, approval, transport=transport, api_key="offline-key")
        else:
            step = await run_next(directory, approval, transport=transport, api_key="offline-key")
            assert step["state"] == "stopped_after_response"
            assert (private / f"{data['calls'][0]['call_id']}.output.json").exists()
        checked = status(directory)
        assert checked["stop_required"] and checked["mock_http_requests"] == 1
        if defect == "missing_usage":
            assert checked["held_reservation_cny"] == "2.113536" and checked["unknown_usage_call_ids"]

    asyncio.run(scenario())


@pytest.mark.parametrize("defect", ["budget", "wrong_plan", "scope"])
def test_approval_and_budget_stop_before_any_http(tmp_path, defect):
    async def scenario():
        directory, _, data, private, approval = await session(tmp_path)
        if defect == "budget":
            approval["limit_cny"] = "2.00"
        elif defect == "wrong_plan":
            approval["plan_hash"] = "old-v9-plan"
        else:
            approval["max_cases"] = 1
            await run_next(
                directory,
                approval,
                transport=httpx.MockTransport(lambda _: httpx.Response(200, json=response_for(data["calls"][0]))),
                api_key="offline-key",
            )
            packet = read_json(private / f"{data['calls'][0]['call_id']}.packet.json")
            import_review(directory, controlled_review(data["calls"][0], packet))
        before = status(directory)["mock_http_requests"]

        def never_send(_):
            raise AssertionError("blocked action must not send")

        with pytest.raises(EvaluationError):
            await run_next(directory, approval, transport=httpx.MockTransport(never_send), api_key="offline-key")
        assert status(directory)["mock_http_requests"] == before

    asyncio.run(scenario())


def test_actual_task_cancellation_propagates_and_keeps_unknown_reservation(tmp_path):
    async def scenario():
        directory, _, _, private, approval = await session(tmp_path)
        entered = asyncio.Event()

        async def waiting(_):
            entered.set()
            await asyncio.Event().wait()

        transport = httpx.MockTransport(waiting)
        task = asyncio.create_task(run_next(directory, approval, transport=transport, api_key="offline-key"))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        checked = status(directory)
        assert checked["stop_required"] and checked["held_reservation_cny"] == "2.113536"
        assert checked["settled_calls"] == 0 and not (private / "COMPLETE.json").exists()
        with pytest.raises(EvaluationError, match="stopped"):
            await run_next(directory, approval, transport=transport, api_key="offline-key")

    asyncio.run(scenario())


@pytest.mark.parametrize("defect", ["compound", "no_copy", "unresolved"])
def test_quality_and_copy_gate_failures_are_preserved_and_stop_next_case(tmp_path, defect):
    async def scenario():
        directory, _, data, private, approval = await session(tmp_path)
        call = data["calls"][0]
        await run_next(
            directory,
            approval,
            transport=httpx.MockTransport(
                lambda _: httpx.Response(
                    200, json=response_for(call, compound=defect == "compound", no_copy=defect == "no_copy")
                )
            ),
            api_key="offline-key",
        )
        packet = read_json(private / f"{call['call_id']}.packet.json")
        if defect == "compound":
            review = packet["review_template"]
            for judgement in review["judgements"].values():
                judgement.update(
                    supported=True,
                    atomic=False,
                    false_attribution=False,
                    entailed_atom_ids=[a["id"] for a in packet["ground_truth"]],
                )
        else:
            review = controlled_review(call, packet)
            if defect == "unresolved":
                review["judgements"] = {}
        result = import_review(directory, review)
        assert not result["candidate_gate_passed"] and status(directory)["stop_required"]
        if defect == "compound":
            assert result["v10_score"]["nonatomic_candidates"] == 1
        elif defect == "no_copy":
            assert result["v10_score"]["diagnostic_success"] and not result["copy_path_observed"]
        else:
            assert result["v10_score"]["unresolved_candidates"] == 4
        with pytest.raises(EvaluationError, match="stopped"):
            await run_next(directory, approval, transport=httpx.MockTransport(lambda _: None), api_key="offline-key")
        assert status(directory)["mock_http_requests"] == 1

    asyncio.run(scenario())


def test_review_receipt_recovers_without_new_http(tmp_path, monkeypatch):
    async def scenario():
        directory, _, data, private, approval = await session(tmp_path)
        call = data["calls"][0]
        await run_next(
            directory,
            approval,
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json=response_for(call))),
            api_key="offline-key",
        )
        review = controlled_review(call, read_json(private / f"{call['call_id']}.packet.json"))
        original = Ledger.append

        def interrupted(self, kind, **facts):
            if kind == "review":
                raise OSError("simulated receipt boundary")
            return original(self, kind, **facts)

        with monkeypatch.context() as scoped:
            scoped.setattr(Ledger, "append", interrupted)
            with pytest.raises(OSError):
                import_review(directory, review)
        result = import_review(directory, review)
        assert result == import_review(directory, review)
        assert status(directory)["reviewed_cases"] == 1 and status(directory)["mock_http_requests"] == 1

    asyncio.run(scenario())


def test_raw_response_tampering_is_rejected(tmp_path):
    async def scenario():
        directory, _, data, private, approval = await session(tmp_path)
        await run_next(
            directory,
            approval,
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json=response_for(data["calls"][0]))),
            api_key="offline-key",
        )
        path = next((private / "audit").glob("*.response.bin"))
        path.write_bytes(b"modified")
        with pytest.raises(EvaluationError):
            status(directory)

    asyncio.run(scenario())


def test_genuine_git_freeze_accepts_committed_bytes_and_rejects_untracked_bytes(tmp_path, monkeypatch):
    # Read actual Git blobs; no mocked commit flag can establish the production guard.
    monkeypatch.setattr(freeze, "frozen_paths", lambda: [freeze.REPOSITORY / "pyproject.toml"])
    assert freeze.committed_freeze()["verified"]
    temporary = freeze.REPOSITORY / ".artifacts/evidence-admission/v12-readiness/checks/untracked-freeze-test.json"
    temporary.parent.mkdir(parents=True, exist_ok=True)
    write_private(temporary, {"uncommitted": True})
    monkeypatch.setattr(freeze, "frozen_paths", lambda: [temporary])
    with pytest.raises(EvaluationError, match="not_committed"):
        freeze.committed_freeze()
    temporary.unlink()
