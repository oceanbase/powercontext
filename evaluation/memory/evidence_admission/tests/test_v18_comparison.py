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

"""Admission-vs-generation regressions through native SDK/Pipeline, without network or credentials."""

from __future__ import annotations

import asyncio
import copy
import json
from decimal import Decimal

import httpx
import pytest
from powercontext.builtin.artifacts.atomic_memory.extraction import (
    AtomicMemoryCandidate,
    AtomicMemoryExtractionInput,
    AtomicMemoryExtractionOutput,
)
from powercontext.builtin.inference.errors import InferenceError

from powercontext_eval_evidence_admission.manifests import EvaluationError, read_json
from powercontext_eval_evidence_admission.models import digest
from powercontext_eval_evidence_admission.runner import INSTRUCTIONS
from powercontext_eval_evidence_admission.v9_preflight.workflow import mock_response
from powercontext_eval_evidence_admission.v18 import core, report, workflow
from powercontext_eval_evidence_admission.v18.adapter import extract
from powercontext_eval_evidence_admission.v18.scoring import score_output


def test_native_generation_is_identical_across_three_arms():
    suite = core.suite_for("synthetic-v1")
    assert suite.experiment.arms == core.ARMS
    assert suite.experiment.citation_protocol is None
    assert suite.experiment.selection_cost_protocol is None
    assert "copy parts" not in INSTRUCTIONS
    assert suite.experiment.v3.max_calls_per_sample == 16
    assert suite.experiment.v3.total_input_budget_multiplier == 8


def test_common_fixture_size_is_972_not_number_of_http_requests():
    assert sum(len(core.suite_for(d).cases) * 3 * 3 * 3 for d in core.DATASETS) == 972


def test_preparation_probe_is_not_generation_evidence():
    async def scenario():
        suite = core.suite_for("synthetic-v1")
        case = next(c for c in suite.cases if c.case_id == "small-complete")
        rows = []
        for arm in core.ARMS:
            row, values = await core.prepare_one("synthetic-v1", case, arm, 4096, 0)
            rows.append(row)
            assert row["sample"]["status"] == "prepared"
            assert row["sample"]["generation"] is None
            assert row["sample"]["output_hashes"] == []
            assert row["sample"]["generation_calls"] == 0
            assert row["planned_calls"] == len(values) == 1
        assert len({digest(r["sample"]["materializations"]) for r in rows}) == 1
        assert len({digest(r["sample"]["projections"]) for r in rows}) == 1

    asyncio.run(scenario())


def test_rejected_literal_controls_never_become_model_loss():
    async def scenario():
        suite = core.suite_for("synthetic-v1")
        case = next(c for c in suite.cases if c.case_id == "exact-literals")
        for arm in core.ARMS[:2]:
            row, values = await core.prepare_one("synthetic-v1", case, arm, 4096, 0)
            assert row["sample"]["status"] == "budget_rejected"
            assert row["planned_calls"] == 0 and not values
            assert row["sample"]["generation"] is None

    asyncio.run(scenario())


def test_real_native_sdk_has_no_copy_schema_and_records_exact_body(tmp_path):
    async def scenario():
        case = core.suite_for("synthetic-v1").cases[0]
        row, values = await core.prepare_one("synthetic-v1", case, core.ARMS[0], 4096, 0)
        response = mock_response()
        response["model"] = "deepseek-flash"
        response["choices"][0]["message"]["content"] = '{"candidates":[]}'
        record = {}
        output = await extract(
            values[0],
            api_key="offline-test",
            record=record,
            save=workflow.recorder(tmp_path, "native"),
            transport=httpx.MockTransport(lambda req, body=response: httpx.Response(200, json=body)),
        )
        assert output.candidates == () and record["pipeline_returned"]
        body = read_json(tmp_path / "audit/native.request.json")
        assert body["messages"][0]["content"] == INSTRUCTIONS
        assert "CopyRef" not in body["messages"][1]["content"]
        assert json.loads(body["messages"][2]["content"]) == values[0].model_dump(mode="json")
        assert body["thinking"] == {"type": "disabled"}
        assert record["request_facts"]["http_request_hash"] == digest(body)
        assert (tmp_path / "audit/native.response-bytes.json").stat().st_mode & 0o777 == 0o600
        assert row["sample"]["memory_mutations"] == 0

    asyncio.run(scenario())


def test_exact_full_fact_is_proven_but_paraphrase_remains_unresolved():
    async def scenario():
        from powercontext_eval_evidence_admission.models import Support

        case = core.suite_for("synthetic-v1").cases[0]
        row, values = await core.prepare_one("synthetic-v1", case, core.ARMS[0], 4096, 0)
        supports = tuple(Support.model_validate(s) for s in row["sample"]["supports"])
        exact = AtomicMemoryCandidate(
            kind="fact", text=case.required[0].text, evidence_ids=(values[0].evidence[0].evidence_id,)
        )
        result, _ = score_output(case, values[0], supports, AtomicMemoryExtractionOutput(candidates=(exact,)))
        assert result["retained_atoms"] == 1 and result["unresolved_candidates"] == 0
        assert result["score_kind"] == "deterministic_exact_ground_truth"
        assert "formal_blockers" not in result
        paraphrase = exact.model_copy(
            update={"text": "An unreviewed paraphrase, not an automatically established fact."}
        )
        result, _ = score_output(case, values[0], supports, AtomicMemoryExtractionOutput(candidates=(paraphrase,)))
        assert result["retained_atoms"] == 0 and result["unresolved_candidates"] == 1

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "usage", [None, {}, {"prompt_tokens": True, "completion_tokens": 0}, {"prompt_tokens": 1, "completion_tokens": -1}]
)
def test_unknown_usage_is_never_zero(usage):
    assert workflow.charge(usage) is None


def test_cumulative_reservation_uses_v9_scope_and_v17_actual_usage():
    assert workflow.reservation() == Decimal("2.113536")
    assert workflow.can_reserve("2.775814")
    assert not workflow.can_reserve("2.775815")


async def controlled_session(tmp_path, monkeypatch, case_id="small-complete"):
    case = next(c for c in core.suite_for("synthetic-v1").cases if c.case_id == case_id)
    row, values = await core.prepare_one("synthetic-v1", case, core.ARMS[-1], 4096, 0)
    # Two explicitly planned calls; no fake claim about a complete formal matrix.
    row["planned_calls"] = 2
    row["sample"]["input_hashes"] *= 2
    controls = []
    for arm in core.ARMS[:2]:
        control = copy.deepcopy(row)
        control.update(row_id=digest(arm), arm=arm, planned_calls=0)
        control["sample"].update(input_hashes=[], status="budget_rejected", reason="input_budget")
        controls.append(control)
    manifest = {
        "rows": [*controls, row],
        "identity_hash": "controlled",
        "reliability_passed": True,
        "nondeterministic_groups": 0,
        "paired_identity_mismatches": 0,
    }
    monkeypatch.setattr(workflow, "load", lambda directory, committed=True: manifest)
    monkeypatch.setattr(report, "load", lambda directory, committed=True: manifest)

    async def rebuild(*args):
        return row, values * 2

    monkeypatch.setattr(workflow, "prepare_one", rebuild)
    hashes = {}
    for call in workflow.calls_for(manifest):
        record = {}
        response = mock_response()
        response["model"] = "deepseek-flash"
        response["choices"][0]["message"]["content"] = '{"candidates":[]}'
        await extract(
            values[0],
            api_key="offline",
            record=record,
            save=workflow.recorder(tmp_path / "preflight", digest(call)),
            transport=httpx.MockTransport(lambda req, body=response: httpx.Response(200, json=body)),
        )
        hashes[digest(call)] = record["request_facts"]["http_request_hash"]
    core.dump(tmp_path / "preflight.json", {"request_hashes": hashes})
    core.dump(
        tmp_path / "state.json",
        {
            "state": "ready",
            "mode": "mock",
            "next_call": 0,
            "preparation_hash": digest(manifest),
            "records": [],
            "known_charge_cny": "0",
        },
    )
    return manifest


def test_known_quality_failure_continues_next_planned_request_without_retry(tmp_path, monkeypatch):
    async def scenario():
        await controlled_session(tmp_path, monkeypatch)
        response = mock_response()
        response["model"] = "deepseek-flash"
        response["choices"][0]["message"]["content"] = '{"candidates":[]}'
        attempts = []

        def handle(request):
            attempts.append(request)
            return httpx.Response(200, json=response)

        for _ in range(2):
            status = await workflow.run_next(tmp_path, transport=httpx.MockTransport(handle))
            assert status["state"] == "ready"
        assert (await workflow.run_next(tmp_path, transport=httpx.MockTransport(handle)))["state"] == "finished"
        assert len(attempts) == 2
        state = read_json(tmp_path / "state.json")
        assert all(r["score"]["retained_atoms"] == 0 for r in state["records"])
        assert len({r["call_id"] for r in state["records"]}) == 2
        with pytest.raises(EvaluationError, match="not_ready_no_resend"):
            await workflow.run_next(tmp_path, transport=httpx.MockTransport(handle))

    asyncio.run(scenario())


def test_unknown_usage_stops_even_when_output_is_valid(tmp_path, monkeypatch):
    async def scenario():
        await controlled_session(tmp_path, monkeypatch)
        response = mock_response()
        response.pop("usage", None)
        response["model"] = "deepseek-flash"
        response["choices"][0]["message"]["content"] = '{"candidates":[]}'
        result = await workflow.run_next(
            tmp_path, transport=httpx.MockTransport(lambda req: httpx.Response(200, json=response))
        )
        assert result["state"] == "unknown_usage_stopped"
        with pytest.raises(EvaluationError, match="not_ready_no_resend"):
            await workflow.run_next(
                tmp_path, transport=httpx.MockTransport(lambda req: httpx.Response(200, json=response))
            )
        assert read_json(tmp_path / "state.json")["known_charge_cny"] == "0"

    asyncio.run(scenario())


def test_request_hash_mismatch_stops_before_provider(tmp_path):
    async def scenario():
        called = []
        # SDK versions may normalize a transport guard into InferenceError. The external
        # guarantee is rejection before sending, not the adapter's private exception class.
        with pytest.raises((EvaluationError, InferenceError)):
            await extract(
                AtomicMemoryExtractionInput(evidence=()),
                api_key="offline",
                record={},
                save=workflow.recorder(tmp_path, "changed"),
                expected_hash="wrong",
                transport=httpx.MockTransport(lambda req: called.append(req)),
            )
        assert not called

    asyncio.run(scenario())


def test_cli_requires_explicit_live_flag_without_reading_credentials():
    from argparse import Namespace

    from powercontext_eval_evidence_admission.v18.__main__ import execute

    with pytest.raises(EvaluationError, match="explicit_execute_live_required"):
        asyncio.run(execute(Namespace(command="run", session=None, execute_live=False)))


def test_invalid_citation_response_is_captured_and_does_not_force_a_retry(tmp_path, monkeypatch):
    async def scenario():
        await controlled_session(tmp_path, monkeypatch)
        response = mock_response()
        response["model"] = "deepseek-flash"
        response["choices"][0]["message"]["content"] = json.dumps(
            {
                "candidates": [
                    {
                        "kind": "fact",
                        "text": "A candidate with a citation never supplied.",
                        "evidence_ids": ["not-supplied"],
                    }
                ]
            }
        )
        result = await workflow.run_next(
            tmp_path, transport=httpx.MockTransport(lambda req: httpx.Response(200, json=response))
        )
        assert result["state"] == "ready" and result["outcome"] == "output_or_inference_failed"
        record = read_json(tmp_path / "state.json")["records"][0]
        assert record["score"]["invalid_support_candidates"] == 1
        assert record["known_charge_cny"] is not None
        assert (tmp_path / "audit" / (record["call_id"] + ".response-bytes.json")).exists()

    asyncio.run(scenario())


def test_cancellation_persists_terminal_state_and_prohibits_resend(tmp_path, monkeypatch):
    async def scenario():
        await controlled_session(tmp_path, monkeypatch)

        async def cancel(request):
            raise asyncio.CancelledError

        with pytest.raises(asyncio.CancelledError):
            await workflow.run_next(tmp_path, transport=httpx.MockTransport(cancel))
        assert read_json(tmp_path / "state.json")["state"] == "cancelled"
        with pytest.raises(EvaluationError, match="not_ready_no_resend"):
            await workflow.run_next(tmp_path, transport=httpx.MockTransport(cancel))

    asyncio.run(scenario())


def test_budget_stops_before_sending_and_does_not_reset_ledger(tmp_path, monkeypatch):
    async def scenario():
        await controlled_session(tmp_path, monkeypatch)
        state = read_json(tmp_path / "state.json")
        state["known_charge_cny"] = "2.775815"
        core.dump(tmp_path / "state.json", state)
        called = []
        result = await workflow.run_next(tmp_path, transport=httpx.MockTransport(lambda req: called.append(req)))
        assert result["state"] == "budget_stopped" and not called
        assert read_json(tmp_path / "state.json")["known_charge_cny"] == "2.775815"

    asyncio.run(scenario())


def test_public_report_distinguishes_unrun_calls_from_measured_quality_failure(tmp_path, monkeypatch):
    async def scenario():
        await controlled_session(tmp_path, monkeypatch)
        before = report.summarize(tmp_path)
        assert before["decision"] == "insufficient_evidence"
        assert before["rows"][-1]["unrun_calls"] == 2
        assert before["rows"][0]["zero_call_reason"] == "input_budget"
        response = mock_response()
        response["model"] = "deepseek-flash"
        response["usage"]["private_debug"] = "must-not-be-exported"
        response["choices"][0]["message"]["content"] = '{"candidates":[]}'
        for _ in range(3):
            await workflow.run_next(
                tmp_path, transport=httpx.MockTransport(lambda req: httpx.Response(200, json=response))
            )
        after = report.summarize(tmp_path)
        assert after["execution_complete"] and not after["quality_gate_passed"]
        assert after["decision"] == "insufficient_evidence"
        assert not after["live_execution_complete"]
        assert after["candidate_known_quality_failure_rows"] == 1
        assert "must-not-be-exported" not in json.dumps(after)
        assert after["rows"][-1]["unrun_calls"] == 0
        first = after["records"][0]["call_id"]
        (tmp_path / "audit" / (first + ".response-bytes.json")).unlink()
        assert report.summarize(tmp_path)["decision"] == "insufficient_evidence"

    asyncio.run(scenario())


def test_unresolved_semantics_does_not_hide_proven_literal_absence(tmp_path, monkeypatch):
    async def scenario():
        await controlled_session(tmp_path, monkeypatch, "exact-literals")
        response = mock_response()
        response["model"] = "deepseek-flash"
        request = read_json(next(iter((tmp_path / "preflight/audit").glob("*.request.json"))))
        evidence_id = json.loads(request["messages"][2]["content"])["evidence"][0]["evidence_id"]
        response["choices"][0]["message"]["content"] = json.dumps(
            {
                "candidates": [
                    {"kind": "fact", "text": "The deployment settings were recorded.", "evidence_ids": [evidence_id]}
                ]
            }
        )
        for _ in range(3):
            await workflow.run_next(
                tmp_path, transport=httpx.MockTransport(lambda req: httpx.Response(200, json=response))
            )
        result = report.summarize(tmp_path)
        candidate = result["rows"][-1]
        assert candidate["semantic_unresolved"] == 2
        assert len(candidate["required_literal_hashes"]) == 4
        assert candidate["raw_present_literal_hashes"] == []
        assert candidate["known_literal_absence"] and candidate["known_quality_failure"]
        assert result["decision"] == "insufficient_evidence"

    asyncio.run(scenario())


def test_cancellation_after_response_keeps_usage_and_remains_reportable(tmp_path, monkeypatch):
    async def scenario():
        await controlled_session(tmp_path, monkeypatch)
        response = mock_response()
        response["model"] = "deepseek-flash"
        response["choices"][0]["message"]["content"] = '{"candidates":[]}'
        actual_extract = workflow.extract

        async def cancel_after_response(*args, **kwargs):
            await actual_extract(*args, **kwargs)
            raise asyncio.CancelledError

        monkeypatch.setattr(workflow, "extract", cancel_after_response)
        with pytest.raises(asyncio.CancelledError):
            await workflow.run_next(
                tmp_path, transport=httpx.MockTransport(lambda req: httpx.Response(200, json=response))
            )
        state = read_json(tmp_path / "state.json")
        assert state["state"] == "cancelled"
        assert Decimal(state["known_charge_cny"]) > 0
        assert state["records"][0]["outcome"] == "cancelled"
        result = report.summarize(tmp_path)
        assert result["decision"] == "insufficient_evidence" and not result["execution_complete"]
        assert result["unknown_usage_calls"] == 0
        assert not result["rows"][-1]["proven_delivered_atom_hashes"]

    asyncio.run(scenario())


def test_invalid_negative_control_output_is_a_quality_failure_with_zero_required_facts(tmp_path, monkeypatch):
    async def scenario():
        await controlled_session(tmp_path, monkeypatch, "negative-control")
        response = mock_response()
        response["model"] = "deepseek-flash"
        response["choices"][0]["message"]["content"] = "{not-json"
        for _ in range(3):
            await workflow.run_next(
                tmp_path, transport=httpx.MockTransport(lambda req: httpx.Response(200, json=response))
            )
        result = report.summarize(tmp_path)
        candidate = result["rows"][-1]
        assert candidate["required_atom_hashes"] == []
        assert candidate["failed_output"] and candidate["known_quality_failure"]
        assert result["candidate_known_quality_failure_rows"] == 1
        assert not result["quality_gate_passed"]

    asyncio.run(scenario())
