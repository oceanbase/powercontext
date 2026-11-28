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
from powercontext_eval_evidence_admission.v10.scoring import candidate_key, ground_truth
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


def test_old_materialization_identity_is_not_silently_upgraded(tmp_path):
    snapshot = core.identity()
    snapshot.pop("materialization_protocol")
    core.dump(tmp_path / "preparation.json", {"identity": snapshot, "reliability_passed": True})
    with pytest.raises(EvaluationError, match="preparation_identity_or_reliability_changed"):
        workflow.load(tmp_path, committed=False)


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
        assert record["parsed_output_hash"] == digest(read_json(tmp_path / "audit/native.parsed.json"))
        assert read_json(tmp_path / "audit/native.attempt.json")["parsed_output_hash"] == record["parsed_output_hash"]
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
            kind="fact",
            text=case.required[0].text,
            description=(case.required[0].text)[:200],
            evidence_ids=(values[0].evidence[0].evidence_id,),
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
    core.dump(tmp_path / "inputs" / (row["row_id"] + ".json"), [v.model_dump(mode="json") for v in values] * 2)
    return manifest


@pytest.fixture(params=[False, True], ids=["accepted", "citation-rejected"])
def reviewable_attempt(tmp_path, monkeypatch, request):
    async def run():
        manifest = await controlled_session(tmp_path, monkeypatch)
        row = manifest["rows"][-1]
        value = read_json(tmp_path / "inputs" / (row["row_id"] + ".json"))[0]
        case = next(c for c in core.suite_for(row["dataset"]).cases if c.case_id == row["case"])
        candidates = [
            {
                "kind": "fact",
                "text": case.required[0].text,
                "description": case.required[0].text,
                "evidence_ids": [value["evidence"][0]["evidence_id"]],
            }
        ]
        if request.param:
            candidates.append({**candidates[0], "evidence_ids": ["not-supplied"]})
        response = mock_response()
        response["model"] = "deepseek-flash"
        response["choices"][0]["message"]["content"] = json.dumps({"candidates": candidates})
        await workflow.run_next(tmp_path, transport=httpx.MockTransport(lambda req: httpx.Response(200, json=response)))
        record = read_json(tmp_path / "state.json")["records"][0]
        review = read_json(tmp_path / "audit" / (record["call_id"] + ".review.json"))
        output = AtomicMemoryExtractionOutput.model_validate({"candidates": candidates})
        review.update(reviewer="synthetic-human-reviewer", reviewer_kind="human")
        review["judgements"] = {
            candidate_key(c, i): {
                "supported": True,
                "atomic": True,
                "false_attribution": False,
                "entailed_atom_ids": [ground_truth(case)[0]["id"]],
            }
            for i, c in enumerate(output.candidates)
        }
        review_file = tmp_path / "human-review.json"
        core.dump(review_file, review)
        return record, review_file, request.param

    return asyncio.run(run())


def test_bound_review_import_preserves_rejected_delivery_and_original_evidence(tmp_path, reviewable_attempt):
    record, review_file, rejected = reviewable_attempt
    call_id = record["call_id"]
    assert (tmp_path / "audit" / (call_id + ".output.json")).exists() is not rejected
    original_state = (tmp_path / "state.json").read_bytes()
    original_audit = {p.name: p.read_bytes() for p in (tmp_path / "audit").iterdir()}
    result = workflow.apply_review(tmp_path, call_id, review_file)
    assert result["unresolved"] == 0
    assert (tmp_path / "state.json").read_bytes() == original_state
    assert {p.name: p.read_bytes() for p in (tmp_path / "audit").iterdir()} == original_audit
    summary = report.summarize(tmp_path)
    row = summary["rows"][-1]
    reviewed = summary["records"][0]
    assert reviewed["outcome"] == record["outcome"]
    assert reviewed["score"]["reviewer_kind"] == "human"
    assert reviewed["score"]["retained_atoms"] == 1
    assert bool(row["proven_delivered_atom_hashes"]) is not rejected
    assert row["failed_output"] is rejected
    if rejected:
        assert reviewed["score"]["invalid_support_candidates"] == 1
        assert summary["candidate_known_quality_failure_rows"] == 1
        assert summary["decision"] == "insufficient_evidence"
    with pytest.raises(EvaluationError, match="output_exists"):
        workflow.apply_review(tmp_path, call_id, review_file)


@pytest.mark.parametrize(
    "tamper",
    [
        "input",
        "request",
        "response",
        "output",
        "receipt",
        "missing_output",
        "missing_response",
        "review_output",
        "review_context",
        "review_candidate",
        "unscored",
        "cancelled",
    ],
)
def test_bound_review_import_rejects_changed_or_unavailable_audit(tmp_path, reviewable_attempt, tamper):
    record, review_file, rejected = reviewable_attempt
    call_id = record["call_id"]
    audit = tmp_path / "audit"
    kind = "parsed" if rejected else "output"
    if tamper == "response":
        path = audit / (call_id + ".response-bytes.json")
        path.write_bytes(path.read_bytes() + b" ")
    elif tamper == "missing_response":
        (audit / (call_id + ".response-bytes.json")).unlink()
    elif tamper == "missing_output":
        # A rejected output may never fall back to a planted accepted-output file, or vice versa.
        path = audit / (call_id + f".{kind}.json")
        core.dump(audit / (call_id + (".output.json" if rejected else ".decoy.json")), read_json(path))
        path.unlink()
    elif tamper in {"unscored", "cancelled"}:
        state = read_json(tmp_path / "state.json")
        if tamper == "unscored":
            state["records"][0]["score"] = None
        else:
            state["records"][0]["outcome"] = "cancelled"
        core.dump(tmp_path / "state.json", state)
    else:
        paths = {
            "input": tmp_path / "inputs" / (record["row_id"] + ".json"),
            "request": audit / (call_id + ".request.json"),
            "output": audit / (call_id + f".{kind}.json"),
            "receipt": audit / (call_id + ".review.json"),
        }
        path = review_file if tamper.startswith("review_") else paths[tamper]
        data = read_json(path)
        if tamper == "input":
            data[0]["evidence"][0]["content"] = {"changed": "fixture"}
        elif tamper == "request":
            data["model"] = "changed"
        elif tamper == "output":
            data["candidates"][0]["text"] = "Changed candidate."
        elif tamper in {"receipt", "review_output"}:
            data["output_hash"] = "changed"
        elif tamper == "review_context":
            data["context_hash"] = "changed"
        else:
            data["judgements"]["unknown-candidate"] = next(iter(data["judgements"].values()))
        core.dump(path, data)
    with pytest.raises(EvaluationError):
        workflow.apply_review(tmp_path, call_id, review_file)
    assert not (tmp_path / "reviews" / (call_id + ".json")).exists()


@pytest.mark.parametrize(
    "field,replacement",
    [
        ("unsupported_candidates", 37),
        ("retained_atom_hashes", []),
        ("review_hash", "changed"),
        ("unsupported_candidates", False),
    ],
)
def test_report_rejects_changed_review_score(tmp_path, reviewable_attempt, field, replacement):
    record, review_file, _ = reviewable_attempt
    workflow.apply_review(tmp_path, record["call_id"], review_file)
    path = tmp_path / "reviews" / (record["call_id"] + ".human.json")
    derivative = read_json(path)
    derivative["score"][field] = replacement
    core.dump(path, derivative)
    with pytest.raises(EvaluationError, match="review_score_changed"):
        report.summarize(tmp_path)


@pytest.mark.parametrize(
    "change", ["input", "request", "response", "output", "receipt", "review", "review_judgement", "call_id", "kind"]
)
def test_report_rechecks_bound_review_evidence(tmp_path, reviewable_attempt, change):
    record, review_file, rejected = reviewable_attempt
    call_id = record["call_id"]
    workflow.apply_review(tmp_path, call_id, review_file)
    derivative_path = tmp_path / "reviews" / (call_id + ".human.json")
    if change == "response":
        path = tmp_path / "audit" / (call_id + ".response-bytes.json")
        path.write_bytes(path.read_bytes() + b" ")
    else:
        paths = {
            "input": tmp_path / "inputs" / (record["row_id"] + ".json"),
            "request": tmp_path / "audit" / (call_id + ".request.json"),
            "output": tmp_path / "audit" / (call_id + (".parsed.json" if rejected else ".output.json")),
            "receipt": tmp_path / "audit" / (call_id + ".review.json"),
        }
        path = paths.get(change, derivative_path)
        data = read_json(path)
        if change == "input":
            data[0]["evidence"][0]["content"] = "Changed."
        elif change == "request":
            data["model"] = "changed"
        elif change == "output":
            data["candidates"][0]["text"] = "Changed."
        elif change == "receipt":
            data["context_hash"] = "changed"
        elif change == "review":
            data["review"]["context_hash"] = "changed"
        elif change == "review_judgement":
            next(iter(data["review"]["judgements"].values()))["supported"] = False
        elif change == "call_id":
            data["call_id"] = "changed"
        else:
            data["output_kind"] = "output" if rejected else "parsed"
        core.dump(path, data)
    with pytest.raises(EvaluationError):
        report.summarize(tmp_path)


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
                        "description": ("A candidate with a citation never supplied.")[:200],
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


def test_public_report_blocks_decision_when_a_returned_response_has_no_score(tmp_path, monkeypatch):
    async def scenario():
        await controlled_session(tmp_path, monkeypatch)
        response = mock_response()
        response["model"] = "deepseek-flash"
        response["choices"][0]["message"]["content"] = '{"candidates":[]}'
        for _ in range(3):
            await workflow.run_next(
                tmp_path, transport=httpx.MockTransport(lambda req: httpx.Response(200, json=response))
            )

        state = read_json(tmp_path / "state.json")
        state["records"][0]["score"] = None
        state["records"][0]["score_required"] = True
        state["records"][0]["scoring_failed"] = True
        core.dump(tmp_path / "state.json", state)

        result = report.summarize(tmp_path)
        assert result["decision"] == "insufficient_evidence"
        assert result["execution_complete"] is False
        assert result["scoring_incomplete_calls"] == 1
        assert "scoring_incomplete" in result["formal_blockers"]
        assert result["rows"][-1]["measurement_complete"] is False

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
                    {
                        "kind": "fact",
                        "description": ("The deployment settings were recorded.")[:200],
                        "text": "The deployment settings were recorded.",
                        "evidence_ids": [evidence_id],
                    }
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


def test_cli_cancellation_before_source_read_closes_ready_session(tmp_path, monkeypatch):
    from argparse import Namespace

    from powercontext_eval_evidence_admission.v18 import __main__ as cli

    core.dump(tmp_path / "state.json", {"state": "ready", "known_charge_cny": "0", "records": []})

    async def cancel_before_source(*args, **kwargs):
        raise asyncio.CancelledError

    monkeypatch.setattr(cli, "run_next", cancel_before_source)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(cli.execute(Namespace(command="run", session=tmp_path, execute_live=True)))
    state = read_json(tmp_path / "state.json")
    assert state["state"] == "cancelled" and state["records"] == []
    assert state["known_charge_cny"] == "0"


async def reviewed_report_session(
    tmp_path,
    monkeypatch,
    *,
    case_id="single-source-overflow",
    reviewer="assistant",
    exact=False,
    empty=False,
    review_receipts=None,
):
    """Controlled formal-gate fixture; every provider response is local, never live evidence."""
    from argparse import Namespace

    from powercontext_eval_evidence_admission.v18 import __main__ as cli

    manifest = await controlled_session(tmp_path, monkeypatch, case_id)
    manifest["rows"][1]["sample"]["window_attempts"] = [[0, 1, 0, False]]
    state = read_json(tmp_path / "state.json")
    state["preparation_hash"] = digest(manifest)
    core.dump(tmp_path / "state.json", state)
    row = manifest["rows"][-1]
    case = next(c for c in core.suite_for(row["dataset"]).cases if c.case_id == row["case"])
    truth = ground_truth(case)
    text = truth[0]["text"] if truth else "Routine synthetic status was recorded."
    if not exact:
        text = "Recorded fact: " + text
    reviews = []
    for call in workflow.calls_for(manifest):
        value = read_json(tmp_path / "inputs" / (row["row_id"] + ".json"))[call["input_index"]]
        candidates = (
            []
            if empty
            else [
                {
                    "kind": "fact",
                    "text": text,
                    "description": text,
                    "evidence_ids": [e["evidence_id"] for e in value["evidence"]],
                }
            ]
        )
        response = mock_response()
        response["model"] = "deepseek-flash"
        response["choices"][0]["message"]["content"] = json.dumps({"candidates": candidates})
        await workflow.run_next(
            tmp_path, transport=httpx.MockTransport(lambda req, body=response: httpx.Response(200, json=body))
        )
        call_id = digest(call)
        review = read_json(tmp_path / "audit" / (call_id + ".review.json"))
        review.update(reviewer="synthetic-reviewer", reviewer_kind=reviewer)
        output = AtomicMemoryExtractionOutput.model_validate({"candidates": candidates})
        review["judgements"] = {
            candidate_key(candidate, index): {
                "supported": True,
                "atomic": True,
                "false_attribution": False,
                "entailed_atom_ids": [truth[0]["id"]] if truth else [],
            }
            for index, candidate in enumerate(output.candidates)
        }
        path = tmp_path / (call_id + ".review-input.json")
        core.dump(path, review)
        imported = await cli.execute(Namespace(command="review", session=tmp_path, call_id=call_id, review_file=path))
        if review_receipts is not None:
            review_receipts.append(imported)
        reviews.append((call_id, path))
    await workflow.run_next(
        tmp_path, transport=httpx.MockTransport(lambda req: pytest.fail("unexpected provider call"))
    )
    state = read_json(tmp_path / "state.json")
    # Exercise the report's live decision branch without making a live request.
    state["mode"] = "live"
    core.dump(tmp_path / "state.json", state)
    return manifest, reviews


@pytest.mark.parametrize(
    ("reviewer", "exact", "formal_unresolved"),
    [("assistant", False, 1), ("human", False, 0), ("assistant", True, 0)],
)
def test_review_cli_distinguishes_diagnostic_and_formal_status(
    tmp_path, monkeypatch, reviewer, exact, formal_unresolved
):
    receipts = []
    asyncio.run(
        reviewed_report_session(tmp_path, monkeypatch, reviewer=reviewer, exact=exact, review_receipts=receipts)
    )
    assert receipts
    for receipt in receipts:
        assert receipt["reviewer_kind"] == reviewer
        assert receipt["unresolved"] == 0
        assert receipt["formal_unresolved"] == formal_unresolved
        assert receipt["formal_human_review_required"] is (formal_unresolved > 0)
    result = report.summarize(tmp_path)
    assert ("qualifying_human_review_missing" in result["formal_blockers"]) is (formal_unresolved > 0)


def test_assistant_review_cannot_clear_the_formal_follow_up_gate(tmp_path, monkeypatch):
    asyncio.run(reviewed_report_session(tmp_path, monkeypatch))
    result = report.summarize(tmp_path)
    assert result["live_execution_complete"]
    assert result["decision"] == "insufficient_evidence"
    assert "qualifying_human_review_missing" in result["formal_blockers"]
    assert not result["quality_gate_passed"]
    assert not result["reproducible_window_improvement"]
    assert not result["rows"][-1]["proven_delivered_atom_hashes"]
    assert result["rows"][-1]["semantic_unresolved"] == 2
    for record in result["records"]:
        assert record["score"]["retained_atoms"] == 0
        assert record["assistant_diagnostic_score"]["score_kind"] == "assistant_diagnostic"
        assert record["assistant_diagnostic_score"]["retained_atoms"] == 1


@pytest.mark.parametrize("audit_available", [False, True])
def test_legacy_assistant_score_cache_cannot_supply_formal_evidence(tmp_path, monkeypatch, audit_available):
    _, reviews = asyncio.run(reviewed_report_session(tmp_path, monkeypatch))
    state = read_json(tmp_path / "state.json")
    for record in state["records"]:
        call_id = record["call_id"]
        path = tmp_path / "reviews" / (call_id + ".json")
        derivative = read_json(path)
        record["score"] = derivative["score"]
        core.dump(tmp_path / "audit" / (call_id + ".review.json"), derivative["review"])
        path.unlink()
        if not audit_available:
            (tmp_path / "audit" / (call_id + ".response-bytes.json")).unlink()
    core.dump(tmp_path / "state.json", state)
    result = report.summarize(tmp_path)
    assert result["decision"] == "insufficient_evidence"
    assert result["human_review_required_calls"] == 2
    assert "qualifying_human_review_missing" in result["formal_blockers"]
    assert not result["rows"][-1]["proven_delivered_atom_hashes"]
    assert all(r["score"] is None and r["assistant_diagnostic_score"] for r in result["records"])
    if audit_available:
        for call_id, review_file in reviews:
            review = read_json(review_file)
            review["reviewer_kind"] = "human"
            core.dump(review_file, review)
            workflow.apply_review(tmp_path, call_id, review_file)
        assert report.summarize(tmp_path)["decision"] == "bounded_projection_follow_up_required"


def test_human_review_appends_after_assistant_without_changing_originals(tmp_path, monkeypatch):
    _, reviews = asyncio.run(reviewed_report_session(tmp_path, monkeypatch))
    original_state = (tmp_path / "state.json").read_bytes()
    original_audit = {p.name: p.read_bytes() for p in (tmp_path / "audit").iterdir()}
    diagnostics = {p.name: p.read_bytes() for p in (tmp_path / "reviews").iterdir()}
    for call_id, path in reviews:
        review = read_json(path)
        review.update(reviewer="synthetic-human-reviewer", reviewer_kind="human")
        core.dump(path, review)
        workflow.apply_review(tmp_path, call_id, path)
        with pytest.raises(EvaluationError, match="output_exists"):
            workflow.apply_review(tmp_path, call_id, path)
    result = report.summarize(tmp_path)
    assert result["decision"] == "bounded_projection_follow_up_required"
    assert result["quality_gate_passed"] and result["formal_blockers"] == []
    assert all(r["score"]["score_kind"] == "human_bound_review" for r in result["records"])
    assert all(r["assistant_diagnostic_score"] for r in result["records"])
    assert (tmp_path / "state.json").read_bytes() == original_state
    assert {p.name: p.read_bytes() for p in (tmp_path / "audit").iterdir()} == original_audit
    assert all((tmp_path / "reviews" / name).read_bytes() == value for name, value in diagnostics.items())


def test_assistant_diagnostic_does_not_discard_deterministic_evidence(tmp_path, monkeypatch):
    asyncio.run(reviewed_report_session(tmp_path, monkeypatch, exact=True))
    result = report.summarize(tmp_path)
    assert result["decision"] == "bounded_projection_follow_up_required"
    assert result["formal_blockers"] == []
    assert all(r["score"]["score_kind"] == "deterministic_exact_ground_truth" for r in result["records"])


@pytest.mark.parametrize("reviewer", ["human", "assistant"])
@pytest.mark.parametrize("empty", [False, True], ids=["nonempty-output", "empty-output"])
def test_zero_fact_control_requires_empty_candidates_even_with_bound_review(tmp_path, monkeypatch, reviewer, empty):
    asyncio.run(
        reviewed_report_session(tmp_path, monkeypatch, case_id="negative-control", reviewer=reviewer, empty=empty)
    )
    result = report.summarize(tmp_path)
    row = result["rows"][-1]
    assert row["required_atom_hashes"] == row["proven_delivered_atom_hashes"] == []
    assert row["known_quality_failure"] is not empty
    assert row["negative_control_nonempty_output"] is not empty
    assert result["candidate_known_quality_failure_rows"] == int(not empty)
    assert not result["quality_gate_passed"]
    if reviewer == "human":
        assert all(r["score"]["semantic_complete"] is empty for r in result["records"])
        assert result["decision"] == "no_production_change"
    elif not empty:
        assert result["decision"] == "insufficient_evidence"


def test_legacy_human_review_is_readable_but_two_formal_reviews_are_rejected(tmp_path, monkeypatch):
    _, reviews = asyncio.run(reviewed_report_session(tmp_path, monkeypatch, reviewer="human"))
    call_id, path = reviews[0]
    human = tmp_path / "reviews" / (call_id + ".human.json")
    legacy = tmp_path / "reviews" / (call_id + ".json")
    original = human.read_bytes()
    human.rename(legacy)
    assert report.summarize(tmp_path)["decision"] == "bounded_projection_follow_up_required"
    with pytest.raises(EvaluationError, match="output_exists"):
        workflow.apply_review(tmp_path, call_id, path)
    human.write_bytes(original)
    with pytest.raises(EvaluationError, match="multiple_human_reviews"):
        report.summarize(tmp_path)


def test_assistant_review_in_human_slot_is_not_a_formal_review(tmp_path, monkeypatch):
    _, reviews = asyncio.run(reviewed_report_session(tmp_path, monkeypatch))
    call_id, _ = reviews[0]
    (tmp_path / "reviews" / (call_id + ".json")).rename(tmp_path / "reviews" / (call_id + ".human.json"))
    with pytest.raises(EvaluationError, match="human_review_kind_changed"):
        report.summarize(tmp_path)


def test_assistant_diagnostic_cannot_replace_an_existing_human_review(tmp_path, monkeypatch):
    _, reviews = asyncio.run(reviewed_report_session(tmp_path, monkeypatch, reviewer="human"))
    for call_id, path in reviews:
        review = read_json(path)
        review["reviewer_kind"] = "assistant"
        for judgement in review["judgements"].values():
            judgement.update(supported=False, entailed_atom_ids=[])
        core.dump(path, review)
        workflow.apply_review(tmp_path, call_id, path)
    result = report.summarize(tmp_path)
    assert result["decision"] == "bounded_projection_follow_up_required"
    assert all(r["score"]["retained_atoms"] == 1 for r in result["records"])
    assert all(r["assistant_diagnostic_score"]["unsupported_candidates"] == 1 for r in result["records"])


def test_human_review_does_not_hide_a_tampered_assistant_derivative(tmp_path, monkeypatch):
    _, reviews = asyncio.run(reviewed_report_session(tmp_path, monkeypatch, reviewer="human"))
    call_id, path = reviews[0]
    review = read_json(path)
    review["reviewer_kind"] = "assistant"
    core.dump(path, review)
    workflow.apply_review(tmp_path, call_id, path)
    derivative = tmp_path / "reviews" / (call_id + ".json")
    data = read_json(derivative)
    data["score"]["retained_atoms"] = 99
    core.dump(derivative, data)
    with pytest.raises(EvaluationError, match="review_score_changed"):
        report.summarize(tmp_path)


def test_nonempty_negative_control_blocks_an_otherwise_passing_follow_up(tmp_path, monkeypatch):
    import shutil

    positive = tmp_path / "positive"
    negative = tmp_path / "negative"
    manifest, _ = asyncio.run(reviewed_report_session(positive, monkeypatch, reviewer="human"))
    control, _ = asyncio.run(
        reviewed_report_session(negative, monkeypatch, case_id="negative-control", reviewer="human")
    )
    manifest["rows"].extend(control["rows"])
    state = read_json(positive / "state.json")
    other_state = read_json(negative / "state.json")
    state["records"].extend(other_state["records"])
    state["known_charge_cny"] = str(Decimal(state["known_charge_cny"]) + Decimal(other_state["known_charge_cny"]))
    for folder in ("inputs", "audit", "reviews"):
        shutil.copytree(negative / folder, positive / folder, dirs_exist_ok=True)
    core.dump(positive / "state.json", state)
    monkeypatch.setattr(report, "load", lambda *args, **kwargs: manifest)
    result = report.summarize(positive)
    assert result["live_execution_complete"] and result["reproducible_window_improvement"]
    assert result["semantic_unresolved_candidates"] == 0 and result["formal_blockers"] == []
    assert result["candidate_known_quality_failure_rows"] == 1
    assert result["decision"] == "no_production_change"
    assert not result["quality_gate_passed"]


def test_partial_request_scores_still_aggregate_into_complete_sample_coverage(tmp_path, monkeypatch):
    async def scenario():
        manifest = await controlled_session(tmp_path, monkeypatch, "single-source-overflow")
        manifest["rows"][1]["sample"]["window_attempts"] = [[0, 1, 0, False]]
        state = read_json(tmp_path / "state.json")
        state["preparation_hash"] = digest(manifest)
        core.dump(tmp_path / "state.json", state)
        row = manifest["rows"][-1]
        case = next(c for c in core.suite_for(row["dataset"]).cases if c.case_id == row["case"])
        value = read_json(tmp_path / "inputs" / (row["row_id"] + ".json"))[0]
        fact = ground_truth(case)[0]["text"]
        for candidates in (
            [],
            [
                {
                    "kind": "fact",
                    "text": fact,
                    "description": fact,
                    "evidence_ids": [e["evidence_id"] for e in value["evidence"]],
                }
            ],
        ):
            response = mock_response()
            response["model"] = "deepseek-flash"
            response["choices"][0]["message"]["content"] = json.dumps({"candidates": candidates})
            await workflow.run_next(
                tmp_path, transport=httpx.MockTransport(lambda req, body=response: httpx.Response(200, json=body))
            )
        await workflow.run_next(
            tmp_path, transport=httpx.MockTransport(lambda req: pytest.fail("unexpected provider call"))
        )
        state = read_json(tmp_path / "state.json")
        state.update(state="finished", mode="live")
        core.dump(tmp_path / "state.json", state)

    asyncio.run(scenario())
    result = report.summarize(tmp_path)
    assert result["records"][0]["score"]["semantic_complete"] is False
    assert result["records"][1]["score"]["semantic_complete"] is True
    assert result["decision"] == "bounded_projection_follow_up_required"
    assert result["rows"][-1]["known_quality_failure"] is False


@pytest.mark.parametrize("version", ["v18", "v19", "v20"])
def test_versioned_export_preserves_assistant_review_blocker(tmp_path, monkeypatch, version):
    from importlib import import_module

    manifest, _ = asyncio.run(reviewed_report_session(tmp_path, monkeypatch))
    module = report if version == "v18" else import_module(f"powercontext_eval_evidence_admission.{version}.workflow")
    if version != "v18":
        state = read_json(tmp_path / "state.json")
        monkeypatch.setattr(
            module,
            "session_data",
            lambda *args, **kwargs: (
                manifest,
                state,
                {
                    "authorization": "controlled-fixture",
                    "source_freeze": {},
                    "approved_total_cny": "5",
                },
            ),
        )
        monkeypatch.setattr(module, "status", lambda directory: {})
        monkeypatch.setattr(module, "load", lambda *args, **kwargs: manifest)
    output = tmp_path / "public-report.json"
    assert module.export(tmp_path, output)["decision"] == "insufficient_evidence"
    assert "qualifying_human_review_missing" in read_json(output)["formal_blockers"]
