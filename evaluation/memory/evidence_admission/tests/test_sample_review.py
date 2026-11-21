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

"""Human observations cannot approve unreviewed candidates or replace old evidence."""

from __future__ import annotations

import json

import pytest

from powercontext_eval_evidence_admission.manifests import EvaluationError, file_hash, read_json
from powercontext_eval_evidence_admission.models import digest
from powercontext_eval_evidence_admission.sample_review import (
    main,
    prepare_appendix,
    verify_appendix,
    write_appendix,
)


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


@pytest.fixture
def recorded_sample(tmp_path):
    session, fixtures = tmp_path / "private", tmp_path / "fixtures"
    fixture = fixtures / "synthetic-v1.json"
    save(fixture, {"cases": [{"case_id": "small-complete", "required": [{"exact_literals": ["保持\r\n"]}]}]})
    output = {
        "candidates": [{"kind": "fact", "text": "private-source-and-response-sentinel 保持\r\n", "evidence_ids": ["e"]}]
    }
    output_hash = digest(output)
    manifest = {
        "identity_hash": digest("identity"),
        "identity": {"files": {"evaluation/memory/evidence_admission/fixtures/synthetic-v1.json": file_hash(fixture)}},
        "rows": [],
    }
    freeze = {"revision": "a" * 40, "identity_hash": manifest["identity_hash"], "verified": True}
    queue = {"basis_result_sha256": "", "records": []}
    state = {"freeze": freeze, "records": []}
    selectors, annotations = [], []
    for repetition in (0, 1):
        row_id = digest({"row": repetition})
        value = {"evidence": [{"text": "private-source-and-response-sentinel", "evidence_id": "e"}]}
        input_hash = digest(value)
        call_id = digest({"row_id": row_id, "input_index": 0, "input_hash": input_hash})
        request = {
            "messages": [{"content": "frozen instruction"}, {"content": "old schema"}, {"content": json.dumps(value)}]
        }
        response = {"choices": [{"message": {"content": json.dumps(output)}}]}
        save(session / "inputs" / (row_id + ".json"), [value])
        save(session / "audit" / (call_id + ".request.json"), request)
        save(session / "audit" / (call_id + ".response-bytes.json"), response)
        save(session / "audit" / (call_id + ".output.json"), output)
        context = digest({"context": repetition})
        save(session / "audit" / (call_id + ".review.json"), {"output_hash": output_hash, "context_hash": context})
        record = {
            "call_id": call_id,
            "row_id": row_id,
            "input_index": 0,
            "input_hash": input_hash,
            "response_hash": digest((session / "audit" / (call_id + ".response-bytes.json")).read_bytes().hex()),
            "output_hash": output_hash,
            "outcome": "returned",
            "request_facts": {"http_request_hash": digest(request)},
        }
        state["records"].append(record)
        row = {
            "row_id": row_id,
            "dataset": "synthetic-v1",
            "case": "small-complete",
            "budget": 2048,
            "repetition": repetition,
            "arm": "bounded-fragments-v3",
            "sample": {"input_hashes": [input_hash]},
            "required_atom_hashes": [digest("atom")],
            "admitted_atom_hashes": [digest("atom")],
        }
        manifest["rows"].append(row)
        queue["records"].append(
            {
                **{k: v for k, v in row.items() if k != "sample"},
                **{k: record[k] for k in ("call_id", "input_index", "input_hash", "response_hash")},
                "review_output_hash": output_hash,
                "review_context_hash": context,
                "review_priority": "accepted_output_semantics",
                "unresolved_candidates": [
                    {
                        "candidate_index": 0,
                        "candidate_hash": digest(output["candidates"][0]),
                        "candidate_key": digest({"index": 0, "candidate": output["candidates"][0]}),
                    }
                ],
            }
        )
        selectors.append(
            {
                "sample_id": f"sample-{repetition}",
                "dataset": "synthetic-v1",
                "case": "small-complete",
                "budget": 2048,
                "repetition": repetition,
                "reported_finding": "compound_candidate",
            }
        )
        annotations.append(
            {
                "sample_id": f"sample-{repetition}",
                "candidate_index": 0,
                "field": "atomic",
                "value": False,
                "reason_code": "independent_assertions_combined",
            }
        )
    state["preparation_hash"] = digest(manifest)
    save(session / "preparation.json", manifest)
    save(session / "state.json", state)
    result_file, queue_file, declaration_file = [
        tmp_path / name for name in ("result.json", "queue.json", "declaration.json")
    ]
    save(
        result_file,
        {
            "decision": "insufficient_evidence",
            "identity_hash": manifest["identity_hash"],
            "freeze": freeze,
            "records": state["records"],
            "semantic_unresolved_candidates": 2,
        },
    )
    queue["basis_result_sha256"] = file_hash(result_file)
    save(queue_file, queue)
    save(
        declaration_file,
        {
            "schema_version": "evidence-admission.human-sample-declaration.v1",
            "reviewer": "human-reviewer",
            "recorded_on": "2026-11-07",
            "basis_result_sha256": file_hash(result_file),
            "basis_queue_sha256": file_hash(queue_file),
            "preparation_hash": digest(manifest),
            "state_hash": digest(state),
            "execution_revision": "a" * 40,
            "samples": selectors,
            "annotations": annotations,
        },
    )
    return session, declaration_file, queue_file, result_file, fixtures


def test_partial_judgements_are_call_bound_and_never_clear_semantics(recorded_sample, tmp_path):
    documents = prepare_appendix(*recorded_sample)
    judgements = documents["human-judgements.json"]["records"]
    assert len(judgements) == 2
    assert judgements[0]["candidate_key"] == judgements[1]["candidate_key"]
    assert judgements[0]["call_id"] != judgements[1]["call_id"]
    assert judgements[0]["review_context_hash"] != judgements[1]["review_context_hash"]
    summary = documents["summary.json"]
    assert summary["original_semantic_unresolved_candidates"] == 2
    assert summary["fully_adjudicated_candidates_added"] == 0 and not summary["production_authorized"]
    assert summary["human_atomic_false"] == 2
    assert not summary["original_scores_changed"] and summary["provider_requests"] == 0
    directory = tmp_path / "appendix"
    write_appendix(directory, recorded_sample[0], documents)
    assert verify_appendix(directory)["verified"]
    assert all("private-source-and-response-sentinel" not in p.read_text() for p in directory.iterdir())
    assert "description" not in json.dumps(read_json(next((recorded_sample[0] / "audit").glob("*.output.json"))))


@pytest.mark.parametrize("target", ["input", "output", "response", "context", "candidate"])
def test_tampered_evidence_cannot_be_attested(recorded_sample, target):
    session, declaration_file, queue_file, _, _ = recorded_sample
    record = read_json(queue_file)["records"][0]
    if target == "candidate":
        queue = read_json(queue_file)
        queue["records"][0]["unresolved_candidates"][0]["candidate_key"] = "b" * 64
        save(queue_file, queue)
        declaration = read_json(declaration_file)
        declaration["basis_queue_sha256"] = file_hash(queue_file)
        save(declaration_file, declaration)
    elif target == "input":
        save(session / "inputs" / (record["row_id"] + ".json"), [{"evidence": []}])
    else:
        suffix = {"output": "output.json", "response": "response-bytes.json", "context": "review.json"}[target]
        path = session / "audit" / (record["call_id"] + "." + suffix)
        data = read_json(path)
        if target == "context":
            data["context_hash"] = "b" * 64
        else:
            data["tampered"] = True
        save(path, data)
    with pytest.raises(EvaluationError, match="review_.*changed"):
        prepare_appendix(*recorded_sample)


def test_arm_ambiguity_does_not_turn_into_a_review(recorded_sample):
    session, declaration_file, queue_file, result_file, _ = recorded_sample
    manifest, state, queue, result = [
        read_json(p) for p in (session / "preparation.json", session / "state.json", queue_file, result_file)
    ]
    row = manifest["rows"][1]
    row["repetition"] = 0
    row["arm"] = "window-reduction-v1"
    # Keep input/request bytes unchanged; this second call now supplies an alternative arm at the same coordinates.
    queue["records"][1]["repetition"] = 0
    queue["records"][1]["arm"] = row["arm"]
    state["preparation_hash"] = digest(manifest)
    save(session / "preparation.json", manifest)
    save(session / "state.json", state)
    save(result_file, result)
    save(queue_file, queue)
    declaration = read_json(declaration_file)
    declaration.update(
        samples=declaration["samples"][:1],
        annotations=[],
        preparation_hash=digest(manifest),
        state_hash=digest(state),
        basis_queue_sha256=file_hash(queue_file),
    )
    save(declaration_file, declaration)
    documents = prepare_appendix(*recorded_sample)
    assert documents["summary.json"]["ambiguous_entries"] == 1
    assert documents["summary.json"]["uniquely_bound_entries"] == 0
    assert not documents["human-judgements.json"]["records"]
    declaration["annotations"] = [
        {
            "sample_id": "sample-0",
            "candidate_index": 0,
            "field": "atomic",
            "value": False,
            "reason_code": "independent_assertions_combined",
        }
    ]
    save(declaration_file, declaration)
    with pytest.raises(EvaluationError, match="requires_unique_call"):
        prepare_appendix(*recorded_sample)


@pytest.mark.parametrize("conflict", [False, True])
def test_duplicate_or_conflicting_annotations_do_not_replace_judgements(recorded_sample, conflict):
    declaration_file = recorded_sample[1]
    declaration = read_json(declaration_file)
    extra = dict(declaration["annotations"][0])
    if conflict:
        extra["value"] = True
    declaration["annotations"].append(extra)
    save(declaration_file, declaration)
    with pytest.raises(
        EvaluationError, match="review_conflicting_annotation" if conflict else "review_duplicate_annotation"
    ):
        prepare_appendix(*recorded_sample)


def test_existing_or_session_destination_is_never_overwritten(recorded_sample, tmp_path):
    documents = prepare_appendix(*recorded_sample)
    directory = tmp_path / "appendix"
    write_appendix(directory, recorded_sample[0], documents)
    before = {p.name: p.read_bytes() for p in directory.iterdir()}
    with pytest.raises(EvaluationError, match="already_exists"):
        write_appendix(directory, recorded_sample[0], documents)
    assert before == {p.name: p.read_bytes() for p in directory.iterdir()}
    with pytest.raises(EvaluationError, match="overlaps_session"):
        write_appendix(recorded_sample[0] / "reviews-new", recorded_sample[0], documents)


def test_incomplete_or_changed_appendix_cannot_verify(recorded_sample, tmp_path):
    directory = tmp_path / "appendix"
    write_appendix(directory, recorded_sample[0], prepare_appendix(*recorded_sample))
    (directory / "summary.json").write_text("{}\n")
    with pytest.raises(EvaluationError, match="artifact_changed"):
        verify_appendix(directory)
    (directory / "COMPLETE.json").unlink()
    with pytest.raises(EvaluationError):
        verify_appendix(directory)


def test_rejected_call_cannot_be_promoted_by_a_human_annotation(recorded_sample):
    session, declaration_file, _, result_file, _ = recorded_sample
    state, result = read_json(session / "state.json"), read_json(result_file)
    for data in (state, result):
        data["records"][0]["outcome"] = "failed"
    save(session / "state.json", state)
    save(result_file, result)
    queue_file = recorded_sample[2]
    queue = read_json(queue_file)
    queue["basis_result_sha256"] = file_hash(result_file)
    save(queue_file, queue)
    declaration = read_json(declaration_file)
    declaration.update(
        state_hash=digest(state), basis_result_sha256=file_hash(result_file), basis_queue_sha256=file_hash(queue_file)
    )
    save(declaration_file, declaration)
    with pytest.raises(EvaluationError, match="rejected_output_not_accepted"):
        prepare_appendix(*recorded_sample)


def test_cli_errors_do_not_disclose_private_values(recorded_sample, tmp_path, capsys):
    args = ["build"]
    for flag, value in zip(("session", "declaration", "queue", "result", "fixtures"), recorded_sample, strict=True):
        args.extend(["--" + flag, str(value)])
    args.extend(["--output", str(tmp_path / "appendix")])
    assert main(args) == 0
    assert main(args) == 2
    out = capsys.readouterr().out
    assert "review_output_already_exists" in out and "private-source-and-response-sentinel" not in out


def test_same_recorded_review_reproduces_identical_files(recorded_sample, tmp_path):
    paths = [tmp_path / name for name in ("first", "second")]
    for path in paths:
        write_appendix(path, recorded_sample[0], prepare_appendix(*recorded_sample))
    assert {p.name: p.read_bytes() for p in paths[0].iterdir()} == {p.name: p.read_bytes() for p in paths[1].iterdir()}
