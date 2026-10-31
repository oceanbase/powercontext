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

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import httpx
import pytest
from powercontext.builtin.artifacts.atomic_memory.extraction import AtomicMemoryEvidence, AtomicMemoryExtractionInput
from powercontext.sources import SourceRef

from powercontext_eval_evidence_admission.manifests import EvaluationError, load_suite
from powercontext_eval_evidence_admission.models import PreparationScore, Sample, Support, digest
from powercontext_eval_evidence_admission.v9.codec import prepare
from powercontext_eval_evidence_admission.v9_live.contract import identity
from powercontext_eval_evidence_admission.v9_live.ledger import Ledger, money, reservation, write_private
from powercontext_eval_evidence_admission.v9_live.plan import load_session
from powercontext_eval_evidence_admission.v9_live.runner import expected_attempt, import_review, run_next, status
from powercontext_eval_evidence_admission.v9_live.scoring import atoms, review_packet, score_pair
from powercontext_eval_evidence_admission.v9_preflight.adapter import Journal
from powercontext_eval_evidence_admission.v9_preflight.workflow import mock_response


def pair_for(case_id="small-complete", repetition=0):
    case = next(c for c in load_suite(experiment_version="v5").cases if c.case_id == case_id)
    body, metadata = case.sources[0].capture()
    metadata = {**metadata, "z_fixture": "last", "a_fixture": "first"}
    evidence = AtomicMemoryEvidence(
        evidence_id="full-source-id",
        source_ref=SourceRef(source_type="content", source_id=case.sources[0].source_id),
        journal_position=1,
        content={"content": body},
        source_metadata=metadata,
    )
    value = AtomicMemoryExtractionInput(evidence=(evidence,))
    support = Support(
        evidence_id=evidence.evidence_id,
        source_hash=digest(case.sources[0].source_id),
        materialization_hash=digest("material"),
        projection_hash=digest(evidence.model_dump(mode="json")),
        field_hash=digest("/content/content"),
        byte_start=0,
        byte_end=len(body.encode()),
        char_start=0,
        char_end=len(body),
    )
    supports = (
        support,
        *(
            support.model_copy(
                update={
                    "field_hash": digest(f"/source_metadata/{key}"),
                    "byte_start": 0,
                    "byte_end": len(value.encode()),
                    "char_start": 0,
                    "char_end": len(value),
                }
            )
            for key, value in metadata.items()
        ),
    )
    pair_id = digest({"case": case_id, "repetition": repetition})
    sample = Sample(
        sample_id=pair_id,
        case_hash=digest(case.model_dump(mode="json")),
        arm="bounded-fragments-v3",
        budget=4096,
        repetition=repetition,
        completeness="complete",
        status="prepared",
        supports=supports,
        preparation=PreparationScore(
            required_count=len(case.required),
            retained_claim_hashes=(),
            missing_claim_hashes=(),
            exact_literals_retained=0,
            exact_literals_total=0,
            required_content_fits=True,
        ),
    )
    request = prepare(value, supports)
    calls = [
        {
            "call_id": digest({"sample": pair_id, "protocol": protocol, "index": 0}),
            "sample_id": pair_id,
            "protocol": protocol,
            "index": 0,
            "budget": 4096,
            "value": value.model_dump(mode="json"),
            "supports": [s.model_dump(mode="json") for s in supports],
            "request_hash": request.binding_hash if protocol == "v9" else digest(value.model_dump(mode="json")),
        }
        for protocol in ("v5", "v9")
    ]
    return {
        "pair_id": pair_id,
        "case": case.model_dump(mode="json"),
        "sample": sample.model_dump(mode="json"),
        "calls": calls,
    }, value.model_dump(mode="json")


@pytest.fixture
def session(tmp_path):
    pair, full = pair_for()
    other, _ = pair_for(repetition=1)
    with tempfile.TemporaryDirectory(prefix="powercontext-live-test-") as name:
        private = Path(name)
        private.chmod(0o700)
        data = {"pairs": [pair, other], "full": {"small-complete": full}, "v5_instructions": "Return JSON."}
        public = {
            "identity": identity(),
            "execution_mode": "offline_mock",
            "reviewer": "assistant",
            "provider": {"model": "deepseek-v4-flash", "base_url": "https://api.deepseek.com"},
            "private_root": name,
            "private_plan_hash": digest(data),
            "pair_order": [pair["pair_id"], other["pair_id"]],
        }
        output = tmp_path / "session"
        output.mkdir()
        write_private(private / "inputs.json", data)
        write_private(private / "plan.json", public)
        write_private(output / "plan.json", public)
        write_private(output / "READY.json", {"plan_hash": digest(public)})
        Journal(private / "audit")
        Ledger(private, create={"plan_hash": digest(public), "mode": "offline_mock"})
        approval = {
            "plan_hash": digest(public),
            "live_authorized": False,
            "approved_by": "offline-fixture",
            "currency": "CNY",
            "limit_cny": "5.00",
            "max_pairs": 1,
            "accept_conditional_billing_envelope": True,
            "accept_current_model_alias_route": True,
        }
        yield output, private, data, approval


def handler_for(seen, mode=None):
    def handler(request):
        body = json.loads(request.content)
        seen.append(body)
        response = mock_response()
        is_v9 = "quote_id" in body["messages"][1]["content"]
        candidate = (
            {
                "kind": "fact",
                "evidence_ids": ["e0"],
                "parts": [{"type": "copy", "quote_id": "q0", "byte_start": 0, "byte_end": 46}],
            }
            if is_v9
            else {
                "kind": "fact",
                "evidence_ids": ["full-source-id"],
                "text": "The release approval requires two maintainers.",
            }
        )
        response["choices"][0]["message"]["content"] = json.dumps({"candidates": [candidate]})
        if mode == "v5-empty" and not is_v9 or mode == "v9-empty" and is_v9:
            response["choices"][0]["message"]["content"] = '{"candidates":[]}'
        if mode == "no-usage":
            response.pop("usage")
        if mode == "http":
            return httpx.Response(503, json={"error": {"message": "private synthetic provider error"}})
        if mode == "timeout":
            raise httpx.ReadTimeout("private synthetic timeout", request=request)
        if mode == "cancel":
            raise asyncio.CancelledError()
        if mode == "length":
            response["choices"][0]["finish_reason"] = "length"
        if mode == "json":
            response["choices"][0]["message"]["content"] = "{"
        if mode == "input-over-budget":
            response["usage"]["prompt_tokens"] = 4097
        if mode == "envelope-overflow":
            response["usage"]["prompt_tokens"] = 1048577
        return httpx.Response(200, json=response)

    return httpx.MockTransport(handler)


def execute(session, transport, approval=None):
    output, _, _, original = session
    return asyncio.run(run_next(output, approval or original, transport=transport, api_key="offline-key-never-real"))


def review_for(session):
    _output, private, data, _ = session
    pair = data["pairs"][0]
    outputs = {c["call_id"]: json.loads((private / f"{c['call_id']}.output.json").read_text()) for c in pair["calls"]}
    packet = review_packet(pair, outputs)
    return {
        "packet_hash": digest(packet),
        "reviewer": "assistant",
        "independent_human_review": False,
        "candidates": {
            r["review_id"]: {"supported": True, "atomic": True, "entailed_atom_ids": ["approval"]}
            for r in packet["candidate_groups"]
        },
    }


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-1", "sensitive-value", True, 1.2, None])
def test_currency_rejects_nonfinite_negative_or_inexact_values(value):
    with pytest.raises(EvaluationError, match="invalid_currency_amount"):
        money(value)


def test_budget_reservation_is_conditional_context_bound_not_character_proxy(session):
    seen = []
    approval = {**session[3], "limit_cny": str(reservation() - money("0.000001"))}
    assert reservation() == money("2.113536")
    with pytest.raises(EvaluationError, match="insufficient_currency_reservation"):
        execute(session, handler_for(seen), approval)
    assert not seen and status(session[0])["reserved_calls"] == 0


def test_pair_waits_for_explicit_review_and_never_repeats_completed_requests(session):
    seen = []
    assert execute(session, handler_for(seen))["state"] == "awaiting_review"
    assert execute(session, handler_for(seen))["state"] == "awaiting_review"
    assert len(seen) == 2
    result = import_review(session[0], review_for(session))
    assert result["candidate_gate_passed"] and result["scores"]["v9"]["complete_success"]
    with pytest.raises(EvaluationError, match="approved_pair_scope_exhausted"):
        execute(session, handler_for(seen))
    approval = {**session[3], "max_pairs": 2}
    assert execute(session, handler_for(seen), approval)["state"] == "awaiting_review"
    assert len(seen) == 4 and status(session[0])["provider_requests"] == 0


def test_baseline_quality_failure_is_retained_without_blocking_candidate_success(session):
    execute(session, handler_for([], "v5-empty"))
    result = import_review(session[0], review_for(session))
    assert not result["scores"]["v5"]["complete_success"]
    assert result["candidate_gate_passed"]


def test_candidate_quality_failure_stops_further_pairs(session):
    seen = []
    execute(session, handler_for(seen, "v9-empty"))
    result = import_review(session[0], review_for(session))
    assert not result["candidate_gate_passed"] and status(session[0])["stop_required"]
    with pytest.raises(EvaluationError, match="session_stopped"):
        execute(session, handler_for(seen), {**session[3], "max_pairs": 2})
    assert len(seen) == 2


@pytest.mark.parametrize(
    "mode", ["no-usage", "http", "timeout", "length", "json", "input-over-budget", "envelope-overflow"]
)
def test_failed_or_unknown_usage_requests_stop_and_are_never_retried(session, mode):
    seen = []
    result = execute(session, handler_for(seen, mode))
    assert result["stop_required"] and len(seen) == 1
    if mode in {"no-usage", "http", "timeout"}:
        assert result["held_reservation_cny"] == str(reservation())
        assert len(result["unknown_usage_call_ids"]) == 1
    with pytest.raises(EvaluationError, match="session_stopped"):
        execute(session, handler_for(seen))
    assert len(seen) == 1


def test_cancel_propagates_and_keeps_unknown_cost_reserved(session):
    seen = []
    with pytest.raises(asyncio.CancelledError):
        execute(session, handler_for(seen, "cancel"))
    assert len(seen) == 1
    assert status(session[0])["held_reservation_cny"] == str(reservation())


def test_output_write_failure_leaves_pending_reservation_instead_of_resending(session, monkeypatch):
    seen = []
    from powercontext_eval_evidence_admission.v9_live import runner

    original = runner.write_private

    def fail_output(path, value):
        if path.name.endswith(".output.json"):
            raise OSError("synthetic durable output failure")
        original(path, value)

    monkeypatch.setattr(runner, "write_private", fail_output)
    with pytest.raises(OSError):
        execute(session, handler_for(seen))
    assert len(status(session[0])["pending_call_ids"]) == 1
    with pytest.raises(EvaluationError, match="session_stopped"):
        execute(session, handler_for(seen))
    assert len(seen) == 1


def test_spawned_process_exit_after_reservation_is_recoverable_as_unknown_never_resent(session):
    output, private, data, approval = session
    call = data["pairs"][0]["calls"][0]
    script = """import json,os,sys
from pathlib import Path
from powercontext_eval_evidence_admission.v9_live.ledger import Ledger,exclusive
private=Path(sys.argv[1]); call=json.loads(sys.argv[2])
with exclusive(private):
    Ledger(private).reserve(call,sys.argv[3],'5.00')
    os._exit(23)
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(private), json.dumps(call), expected_attempt(call, 1)],
        capture_output=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 23
    assert status(output)["pending_call_ids"] == [call["call_id"]]
    seen = []
    with pytest.raises(EvaluationError, match="session_stopped"):
        execute(session, handler_for(seen), approval)
    assert not seen


def test_spawned_process_lock_prevents_concurrent_session_execution(session):
    script = """import sys
from pathlib import Path
from powercontext_eval_evidence_admission.v9_live.ledger import exclusive
with exclusive(Path(sys.argv[1])):
    print('locked',flush=True)
    sys.stdin.readline()
"""
    process = subprocess.Popen(
        [sys.executable, "-c", script, str(session[1])],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert process.stdout.readline().strip() == "locked"
        seen = []
        with pytest.raises(EvaluationError, match="session_already_running"):
            execute(session, handler_for(seen))
        assert not seen
        process.communicate("release\n", timeout=15)
        assert process.returncode == 0
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=15)


@pytest.mark.parametrize(
    "change",
    [
        "plan_hash",
        "currency",
        "live_authorized",
        "accept_conditional_billing_envelope",
        "accept_current_model_alias_route",
    ],
)
def test_approval_is_bound_to_plan_currency_mode_and_explicit_assumptions(session, change):
    value = {**session[3]}
    value[change] = "changed" if isinstance(value[change], str) else not value[change]
    seen = []
    with pytest.raises(EvaluationError, match="execution_authorization"):
        execute(session, handler_for(seen), value)
    assert not seen


def test_offline_session_cannot_receive_a_real_transport(session):
    with pytest.raises(EvaluationError, match="offline_session_requires_mock_transport"):
        execute(session, None)


def test_changed_private_inputs_fail_before_execution(session):
    path = session[1] / "inputs.json"
    path.write_text("{}")
    with pytest.raises(EvaluationError, match="private_plan_hash_mismatch"):
        load_session(session[0])


def test_changed_output_and_torn_ledger_fail_closed(session):
    execute(session, handler_for([]))
    call = session[2]["pairs"][0]["calls"][0]
    path = session[1] / f"{call['call_id']}.output.json"
    raw = path.read_bytes()
    path.write_text('{"candidates":[]}')
    with pytest.raises(EvaluationError, match="durable_output_hash_mismatch"):
        status(session[0])
    path.write_bytes(raw)
    with (session[1] / "execution.jsonl").open("ab") as stream:
        stream.write(b"{")
    with pytest.raises(EvaluationError, match="execution_ledger_incomplete_write"):
        status(session[0])


@pytest.mark.parametrize("field", ["packet_hash", "reviewer", "candidates"])
def test_stale_or_incomplete_semantic_review_cannot_advance(session, field):
    execute(session, handler_for([]))
    review = review_for(session)
    review[field] = {} if field == "candidates" else "wrong"
    with pytest.raises(EvaluationError, match="review_identity_or_completeness"):
        import_review(session[0], review)
    assert status(session[0])["reviewed_pairs"] == 0


def literal_review(case_id, *, transform=None, composite=False):
    pair, full = pair_for(case_id)
    from powercontext_eval_evidence_admission.models import Case

    expected = atoms(Case.model_validate(pair["case"]))
    parent = pair["case"]["required"][0]["text"]
    outputs = {}
    for call in pair["calls"]:
        texts = [parent] if call["protocol"] == "v5" or composite else [a["text"] for a in expected]
        if transform and call["protocol"] == "v9":
            texts = [transform(text) for text in texts]
        outputs[call["call_id"]] = {
            "candidates": [{"kind": "fact", "text": text, "evidence_ids": ["full-source-id"]} for text in texts]
        }
    packet = review_packet(pair, outputs)
    review = {
        "packet_hash": digest(packet),
        "reviewer": "assistant",
        "independent_human_review": False,
        "candidates": {},
    }
    for row in packet["candidate_groups"]:
        text = row["candidate"]["text"]
        ids = (
            [a["id"] for a in expected]
            if text == parent
            else [a["id"] for a in expected if (transform(a["text"]) if transform else a["text"]) == text]
        )
        review["candidates"][row["review_id"]] = {"supported": True, "atomic": len(ids) == 1, "entailed_atom_ids": ids}
    return score_pair(pair, outputs, review, reviewer="assistant", full=full)


def test_split_atoms_pass_while_legacy_scores_remain_unchanged_and_composite_baseline_fails_atomicity():
    result = literal_review("exact-literals")
    assert result["candidate_gate_passed"] and result["scores"]["v9"]["retained_atoms"] == 4
    assert len(result["legacy_v5_scorer"]["v5"]["retained_claim_hashes"]) == 1
    assert not result["legacy_v5_scorer"]["v9"]["retained_claim_hashes"]
    assert result["scores"]["v5"]["nonatomic_candidates"] == 1


def test_composite_candidate_cannot_pass_merely_by_containing_every_literal():
    result = literal_review("exact-literals", composite=True)
    assert result["scores"]["v9"]["retained_atoms"] == 4
    assert not result["candidate_gate_passed"]


@pytest.mark.parametrize("before,after", [("03", "3"), ("0.075", ".075"), ("batch_A-17", "batch_a-17")])
def test_semantic_review_cannot_override_exact_literal_loss(before, after):
    result = literal_review("exact-literals", transform=lambda text: text.replace(before, after))
    assert not result["candidate_gate_passed"] and result["scores"]["v9"]["literal_mismatches"] == 1


@pytest.mark.parametrize(
    "transform",
    [
        lambda text: text.replace("é", "é"),
        lambda text: text.replace("\r\n", "\n"),
        lambda text: text.replace("👩🏽‍💻", "👩‍💻"),
    ],
)
def test_unicode_normalization_newline_and_modifier_changes_remain_failures(transform):
    result = literal_review("unicode-boundaries", transform=transform)
    assert not result["candidate_gate_passed"] and result["scores"]["v9"]["literal_mismatches"] == 1


def test_unicode_split_preserves_raw_bytes_without_claiming_persistence_fidelity():
    result = literal_review("unicode-boundaries")
    assert result["candidate_gate_passed"] and result["scores"]["v9"]["retained_literals"] == 3


def test_duplicate_reservations_are_rejected_without_corrupting_ledger(session):
    ledger = Ledger(session[1])
    call = session[2]["pairs"][0]["calls"][0]
    ledger.reserve(call, expected_attempt(call, 1), "5.00")
    before = ledger.path.read_bytes()
    with pytest.raises(EvaluationError):
        ledger.reserve(call, expected_attempt(call, 2), "5.00")
    assert ledger.path.read_bytes() == before


def test_funds_exhausted_between_calls_can_resume_only_unsent_call_after_explicit_budget_extension(session):
    seen = []
    approval = {**session[3], "limit_cny": "2.113600"}
    with pytest.raises(EvaluationError, match="insufficient_currency_reservation"):
        execute(session, handler_for(seen), approval)
    assert len(seen) == 1 and status(session[0])["settled_calls"] == 1
    assert not status(session[0])["pending_call_ids"]
    assert execute(session, handler_for(seen))["state"] == "awaiting_review"
    assert len(seen) == 2 and status(session[0])["settled_calls"] == 2


def test_spawned_process_exit_inside_sdk_transport_keeps_sent_attempt_unknown(session):
    script = """import asyncio,json,os,sys,httpx
from pathlib import Path
from powercontext_eval_evidence_admission.v9_live.runner import run_next
def handler(request):
    os._exit(24)
asyncio.run(run_next(Path(sys.argv[1]),json.loads(sys.argv[2]),transport=httpx.MockTransport(handler),api_key='offline-key'))
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(session[0]), json.dumps(session[3])],
        capture_output=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 24
    observed = status(session[0])
    assert observed["reserved_calls"] == 1 and observed["settled_calls"] == 0
    assert observed["mock_http_requests"] == 1 and not observed["audit_attempt_coverage_complete"]
    assert observed["held_reservation_cny"] == str(reservation())
    seen = []
    with pytest.raises(EvaluationError, match="session_stopped"):
        execute(session, handler_for(seen))
    assert not seen


def test_cancel_survives_settlement_write_failure(session, monkeypatch):
    def fail(*args, **kwargs):
        raise OSError("synthetic settlement write failure")

    monkeypatch.setattr(Ledger, "settle", fail)
    with pytest.raises(asyncio.CancelledError):
        execute(session, handler_for([], "cancel"))
    assert len(status(session[0])["pending_call_ids"]) == 1


def test_provider_metadata_change_stops_before_next_pair(session):
    seen = []
    wrapped = handler_for(seen)

    async def handler(request):
        response = await wrapped.handle_async_request(request)
        value = json.loads(response.content)
        value["model"] = "different-response-model" if len(seen) == 2 else value["model"]
        return httpx.Response(200, json=value)

    result = execute(session, httpx.MockTransport(handler))
    assert result["stop_required"] and not result["provider_metadata_consistent"]
    with pytest.raises(EvaluationError, match="cannot_review_stopped_session"):
        import_review(session[0], review_for(session))


def test_review_cannot_approve_atom_without_request_local_support(session):
    from powercontext.builtin.artifacts.atomic_memory.extraction import AtomicMemoryCandidate

    from powercontext_eval_evidence_admission.models import Case
    from powercontext_eval_evidence_admission.v9_live.scoring import atom_supported

    pair = session[2]["pairs"][0]
    atom = atoms(Case.model_validate(pair["case"]))[0]
    candidate = AtomicMemoryCandidate(kind="fact", text=atom["text"], evidence_ids=("full-source-id",))
    support = Support.model_validate(pair["calls"][0]["supports"][0])
    midpoint = atom["byte_end"] // 2
    left = support.model_copy(update={"byte_end": midpoint})
    right = support.model_copy(update={"byte_start": midpoint})
    assert not atom_supported(atom, candidate, (left,))
    assert not atom_supported(atom, candidate, (right,))
    assert atom_supported(atom, candidate, (left, right))
    assert not atom_supported(atom, candidate, (left, right.model_copy(update={"materialization_hash": "other"})))


def test_session_request_cap_counts_all_reserved_attempts(session):
    ledger = Ledger(session[1])
    for index in range(102):
        call = {"call_id": digest(index), "request_hash": digest({"request": index})}
        attempt = digest({"attempt": index})
        ledger.reserve(call, attempt, "5.00")
        ledger.settle(
            call["call_id"],
            {
                "attempt_id": attempt,
                "outcome": "success",
                "error_category": None,
                "usage_known": True,
                "input_tokens": 0,
                "output_tokens": 0,
            },
            output_hash=digest({}),
        )
    with pytest.raises(EvaluationError, match="session_call_limit_reached"):
        ledger.reserve({"call_id": digest(103), "request_hash": digest("last")}, digest("attempt"), "5.00")
    assert len(ledger.state()["reserved"]) == 102


def test_review_receipt_recovers_after_ledger_write_failure_without_rewriting_scores(session, monkeypatch):
    execute(session, handler_for([]))
    raw = review_for(session)
    original = Ledger.append

    def fail_review(self, kind, **facts):
        if kind == "review":
            raise OSError("synthetic review ledger failure")
        return original(self, kind, **facts)

    monkeypatch.setattr(Ledger, "append", fail_review)
    with pytest.raises(OSError):
        import_review(session[0], raw)
    assert status(session[0])["reviewed_pairs"] == 0
    monkeypatch.setattr(Ledger, "append", original)
    recovered = import_review(session[0], raw)
    assert recovered["candidate_gate_passed"]
    assert import_review(session[0], raw) == recovered
    assert status(session[0])["reviewed_pairs"] == 1


def test_persisted_native_metadata_order_reaches_actual_sdk_unchanged(session):
    seen = []
    execute(session, handler_for(seen))
    value = json.loads(seen[0]["messages"][2]["content"])
    assert list(value["evidence"][0]["source_metadata"]) == ["z_fixture", "a_fixture"]
