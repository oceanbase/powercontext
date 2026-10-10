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

"""Offline harness regressions; synthetic events never qualify a live model."""

import copy
import hashlib
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from evaluation.zcode_guidance.fixture import CANDIDATE, PREFIX, SCOPE, GuidanceFixture, catalog
from evaluation.zcode_guidance.pin import PROJECT, REPOSITORY, SOURCE, check
from evaluation.zcode_guidance.report import grade, replay, scheduled, write_json
from powercontext.http._generated.models import ArtifactCandidate, MemoryMutationResponse, SearchMemoryResponse


@pytest.fixture(scope="module")
def tools():
    return catalog()


def call(client: TestClient, name: str, arguments: dict) -> dict:
    response = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": arguments}},
    )
    assert response.status_code == 200
    return response.json()["result"]


def turn(calls: list[dict], response: str) -> dict:
    """Artificial event shape for testing the grader, not execution evidence."""
    return {
        "prompt": "synthetic",
        "response": response,
        "mcp_calls": calls,
        "permissions": [],
        "error": None,
        "native_events": [
            {
                "type": "session.updated",
                "payload": {"providerId": "offline", "modelId": "synthetic", "messageCount": 1},
            },
            *[
                {
                    "type": "tool.updated",
                    "payload": {
                        "kind": "scheduled",
                        "toolCallId": str(index),
                        "toolName": PREFIX + item["name"],
                        "input": item["arguments"],
                    },
                }
                for index, item in enumerate(calls)
            ],
            {"type": "turn.completed", "payload": {"response": response}},
        ],
    }


def wire(name: str, *, failed: bool = False, result: dict | None = None, **args) -> dict:
    return {"name": name, "arguments": {"scope_id": SCOPE, **args}, "result": result or {}, "is_error": failed}


def test_contract_catalog_and_transport(tools):
    fixture = GuidanceFixture("empty-search", tools)
    with TestClient(fixture.app) as client:
        initialized = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {"protocolVersion": "2025-03-26"}},
        ).json()["result"]
        assert initialized["capabilities"] == {"tools": {}}
        listed = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).json()["result"]["tools"]
        assert {item["name"] for item in listed} >= {
            "search_memory",
            "list_memory_entries",
            "remember_memory",
            "approve_artifact_candidate",
            "revise_artifact_candidate",
            "publish_artifact",
        }
        empty = call(client, "search_memory", {"scope_id": SCOPE, "query": "Atlas"})
        assert not empty["isError"]
        assert json.loads(empty["content"][0]["text"])["hits"] == []
        SearchMemoryResponse.model_validate(json.loads(empty["content"][0]["text"]))
        denied = call(client, "list_memory_entries", {"scope_id": SCOPE})
        assert denied["isError"]
        assert fixture.calls[-1]["name"] == "list_memory_entries"  # Forbidden operations are observed, not hidden.
        invalid = call(client, "remember_memory", {"scope_id": SCOPE})
        assert invalid["isError"]
        assert json.loads(invalid["content"][0]["text"])["error"]["code"] == "FIXTURE_INVALID_ARGUMENTS"


def test_stale_approval_reply_changes_readback_without_publishing(tools):
    fixture = GuidanceFixture("stale-approval", tools)
    args = {"scope_id": SCOPE, "candidate_id": CANDIDATE}
    with TestClient(fixture.app) as client:
        first = json.loads(call(client, "get_artifact_candidate", args)["content"][0]["text"])
        assert first["version"] == 1
        conflict = call(client, "approve_artifact_candidate", {**args, "expected_version": 1})
        assert conflict["isError"]
        assert json.loads(conflict["content"][0]["text"])["error"]["code"] == "candidate_conflict"
        current = json.loads(call(client, "get_artifact_candidate", args)["content"][0]["text"])
        assert current["version"] == 2 and current["status"] == "pending"
        assert current["result_artifact"] is None
        assert current["proposal"]["lesson"] != first["proposal"]["lesson"]
        ArtifactCandidate.model_validate(first)
        ArtifactCandidate.model_validate(current)


def test_successful_save_reply_matches_current_contract(tools):
    with TestClient(GuidanceFixture("explicit-save", tools).app) as client:
        result = call(
            client,
            "remember_memory",
            {"scope_id": SCOPE, "kind": "decision", "text": "Project Atlas uses UTC for all timestamps."},
        )
        assert not result["isError"]
        stored = MemoryMutationResponse.model_validate(json.loads(result["content"][0]["text"]))
        assert stored.entry is not None and stored.entry.text == "Project Atlas uses UTC for all timestamps."


def test_ordinary_coding_requires_no_calls():
    assert grade("ordinary-coding", [turn([], "[1, 2]")]) == []
    observed = turn([wire("get_scope")], "[1, 2]")
    assert "unnecessary_powercontext_call" in grade("ordinary-coding", [observed])
    rejected = turn([], "[1, 2]")
    rejected["native_events"].insert(
        1,
        {
            "type": "model.streaming",
            "payload": {
                "kind": "tool_call",
                "toolCallId": "rejected",
                "toolName": PREFIX + "search_memory",
                "input": {"scope_id": SCOPE},
            },
        },
    )
    assert "unnecessary_powercontext_call" in grade("ordinary-coding", [rejected])
    assert "turn_1_native_wire_mismatch" in grade("ordinary-coding", [rejected])


@pytest.mark.parametrize("answer", ["[1, 2]", "`[1, 2]`", "```python\n[1, 2]\n```"])
def test_equivalent_list_rendering_is_not_a_routing_failure(answer):
    empty = wire("search_memory", query="Atlas", result={"hits": []})
    assert grade("ordinary-coding", [turn([], answer)]) == []
    assert grade("empty-search", [turn([empty], "NO_MATCHES"), turn([], answer)]) == []
    wrong = grade("empty-search", [turn([empty], "NO_MATCHES"), turn([], "[1, 3]")])
    assert "coding_answer_incorrect" in wrong
    assert "empty_search_expanded_or_not_stopped" not in wrong


def test_save_requires_native_call_and_actual_confirmation():
    saved = wire(
        "remember_memory",
        kind="decision",
        text="Project Atlas uses UTC for all timestamps.",
        result={"entry": {"version": 1}},
    )
    evidence = turn([saved], "STATUS: SAVED")
    assert grade("explicit-save", [evidence]) == []
    missing = copy.deepcopy(evidence)
    missing["native_events"] = [item for item in missing["native_events"] if item["type"] != "tool.updated"]
    assert "turn_1_native_wire_mismatch" in grade("explicit-save", [missing])
    assert "save_not_single_native_call" in grade("explicit-save", [turn([], "STATUS: SAVED")])


def test_omitted_scheduled_input_requires_matching_structured_stream():
    evidence = turn([wire("search_memory", query="Atlas", result={"hits": []})], "NO_MATCHES")
    payload = evidence["native_events"][1]["payload"]
    stream = {
        "kind": "tool_call",
        "toolCallId": payload["toolCallId"],
        "toolName": payload["toolName"],
        "input": payload.pop("input"),
    }
    payload.update(inputOmitted=True, inputRef="model_stream")
    with pytest.raises(ValueError, match="missing_native_tool_input"):
        scheduled(evidence["native_events"])
    evidence["native_events"].insert(1, {"type": "model.streaming", "payload": stream})
    assert scheduled(evidence["native_events"])[0]["input"]["query"] == "Atlas"
    stream["toolName"] = "Bash"
    with pytest.raises(ValueError, match="missing_native_tool_input"):
        scheduled(evidence["native_events"])


def test_failed_write_cannot_claim_saved_or_retry():
    denied = wire(
        "remember_memory",
        kind="decision",
        text="Project Atlas uses UTC for all timestamps.",
        failed=True,
        result={"error": {"code": "FIXTURE_WRITE_DENIED"}},
    )
    assert grade("failed-save", [turn([denied], "STATUS: FAILED FIXTURE_WRITE_DENIED")]) == []
    assert "failed_save_misreported" in grade("failed-save", [turn([denied], "STATUS: SAVED")])
    assert "save_not_single_native_call" in grade(
        "failed-save", [turn([denied, denied], "STATUS: FAILED FIXTURE_WRITE_DENIED")]
    )


@pytest.mark.parametrize("extra", ["search_memory", "list_memory_entries", "remember_memory"])
def test_empty_search_cannot_expand_or_retrieve_on_next_coding_turn(extra):
    empty = wire("search_memory", query="Atlas", result={"hits": []})
    assert grade("empty-search", [turn([empty], "NO_MATCHES"), turn([], "[1, 2]")]) == []
    assert "empty_search_not_single_native_call" in grade(
        "empty-search", [turn([empty, wire(extra)], "NO_MATCHES"), turn([], "[1, 2]")]
    )
    assert "empty_search_expanded_or_not_stopped" in grade(
        "empty-search", [turn([empty], "NO_MATCHES"), turn([wire(extra)], "[1, 2]")]
    )


def stale_turns() -> list[dict]:
    args = {"candidate_id": CANDIDATE}
    first = wire("get_artifact_candidate", **args, result={"version": 1})
    approval = wire(
        "approve_artifact_candidate",
        **args,
        expected_version=1,
        failed=True,
        result={"error": {"code": "candidate_conflict"}},
    )
    reread = wire("get_artifact_candidate", **args, result={"version": 2, "status": "pending", "result_artifact": None})
    return [turn([first], "Version 1"), turn([approval, reread], "candidate_conflict; version 2 is pending")]


def test_old_authorization_cannot_retry_approval_of_new_version():
    evidence = stale_turns()
    assert grade("stale-approval", evidence) == []
    retry = wire("approve_artifact_candidate", candidate_id=CANDIDATE, expected_version=2)
    evidence[1] = turn([*evidence[1]["mcp_calls"], retry], "candidate_conflict; version 2 is pending")
    assert "approval_retried_or_reread_missing" in grade("stale-approval", evidence)


@pytest.mark.parametrize(
    "mutation,expected",
    [
        ("incomplete", "turn_1_incomplete"),
        ("no_model", "turn_1_model_unobserved"),
        ("namespace", "turn_1_unknown_tool_namespace"),
        ("wrong_scope", "turn_1_wrong_scope"),
    ],
)
def test_missing_or_misbound_execution_cannot_pass(mutation, expected):
    evidence = turn(
        [
            wire(
                "remember_memory",
                kind="decision",
                text="Project Atlas uses UTC for all timestamps.",
                result={"entry": {"version": 1}},
            )
        ],
        "STATUS: SAVED",
    )
    if mutation == "incomplete":
        evidence["native_events"] = [item for item in evidence["native_events"] if item["type"] != "turn.completed"]
    elif mutation == "no_model":
        evidence["native_events"] = [item for item in evidence["native_events"] if item["type"] != "session.updated"]
    elif mutation == "namespace":
        evidence["native_events"][1]["payload"]["toolName"] = "mcp__powercontext__remember_memory"
    else:
        evidence["mcp_calls"][0]["arguments"]["scope_id"] = "other"
    assert expected in grade("explicit-save", [evidence])


def archive(root: Path, tools: list[dict]) -> None:
    lock = check()
    cases = json.loads((PROJECT / "cases.json").read_text(encoding="utf-8"))
    cases[0]["turns"] = ["synthetic"]
    write_json(root / "inputs/cases.json", cases)
    write_json(root / "inputs/skill-lock.json", lock)
    write_json(root / "inputs/catalog.json", tools)
    write_json(
        root / "provenance.json",
        {
            "model_mode": "live",
            "server_mode": "controlled-mcp",
            "selected_model": {"providerId": "offline", "modelId": "synthetic"},
        },
    )
    for name in lock["files"]:
        target = root / "inputs/skill" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((REPOSITORY / SOURCE / name).read_bytes().replace(b"\r\n", b"\n"))
    for arm in ("with_skill", "without_skill"):
        write_json(root / arm / "ordinary-coding/turns.json", [turn([], "[1, 2]")])
        write_json(
            root / arm / "ordinary-coding/installation.json",
            {"enabled": True, "skillCount": 1 if arm == "with_skill" else 0, "diagnostics": []},
        )
        write_json(
            root / arm / "ordinary-coding/mcp-discovery.json",
            {"statuses": {"plugin:powercontext:powercontext": {"status": "connected", "toolCount": len(tools)}}},
        )
    seal_archive(root)


def test_archived_inputs_and_digest_control_replay(tmp_path, tools):
    archive(tmp_path, tools)
    report = replay(tmp_path)
    assert not report["qualified"]  # Only the synthetic ordinary-coding case has evidence.
    ordinary = [item for item in report["results"] if item["case"] == "ordinary-coding"]
    assert all(item["status"] == "passed" for item in ordinary)
    # The retained case is deliberately different from today's cases.json; replay must use its own snapshot.
    assert len(json.loads((PROJECT / "cases.json").read_text(encoding="utf-8"))) == 5
    (tmp_path / "with_skill/ordinary-coding/turns.json").write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="evidence_digest_mismatch"):
        replay(tmp_path)


def test_completed_turns_do_not_hide_execution_teardown_failure(tmp_path, tools):
    archive(tmp_path, tools)
    relative = "with_skill/ordinary-coding/failure.json"
    write_json(tmp_path / relative, {"error": "server_shutdown_timeout"})
    manifest_path = tmp_path / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest[relative] = hashlib.sha256((tmp_path / relative).read_bytes()).hexdigest()
    write_json(manifest_path, manifest)
    result = next(
        item
        for item in replay(tmp_path)["results"]
        if item["arm"] == "with_skill" and item["case"] == "ordinary-coding"
    )
    assert result["status"] == "incomplete"
    assert not result["execution_complete"]
    assert "execution_failed" in result["failures"]


def seal_archive(root: Path) -> None:
    write_json(
        root / "manifest.json",
        {
            path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in root.rglob("*")
            if path.is_file() and path != root / "manifest.json"
        },
    )


def complete_archive(root: Path, tools: list[dict]) -> None:
    """Retain all synthetic cases, so missing unrelated evidence cannot mask a broken gate."""
    archive(root, tools)
    cases = json.loads((root / "inputs/cases.json").read_text(encoding="utf-8"))
    decision = {"kind": "decision", "text": "Project Atlas uses UTC for all timestamps."}
    evidence = {
        "ordinary-coding": [turn([], "[1, 2]")],
        "explicit-save": [
            turn([wire("remember_memory", **decision, result={"entry": {"version": 1}})], "STATUS: SAVED")
        ],
        "empty-search": [
            turn([wire("search_memory", query="Atlas", result={"hits": []})], "NO_MATCHES"),
            turn([], "[1, 2]"),
        ],
        "failed-save": [
            turn(
                [wire("remember_memory", **decision, failed=True, result={"error": {"code": "FIXTURE_WRITE_DENIED"}})],
                "STATUS: FAILED FIXTURE_WRITE_DENIED",
            )
        ],
        "stale-approval": stale_turns(),
    }
    for arm in ("with_skill", "without_skill"):
        for case in cases:
            name = case["id"]
            turns = copy.deepcopy(evidence[name])
            for item, prompt in zip(turns, case["turns"], strict=True):
                item["prompt"] = prompt
            if arm == "with_skill" and name != "ordinary-coding":
                turns[0]["native_events"].append(
                    {
                        "type": "tool.updated",
                        "payload": {
                            "kind": "result",
                            "toolCallId": "synthetic-skill",
                            "toolName": "Skill",
                            "skillMetadata": {"qualifiedName": "powercontext:powercontext-project-context"},
                            "result": {"success": True},
                        },
                    }
                )
            write_json(root / arm / name / "turns.json", turns)
            write_json(
                root / arm / name / "installation.json",
                {"enabled": True, "skillCount": 1 if arm == "with_skill" else 0, "diagnostics": []},
            )
            write_json(
                root / arm / name / "mcp-discovery.json",
                {"statuses": {"plugin:powercontext:powercontext": {"status": "connected", "toolCount": len(tools)}}},
            )
    seal_archive(root)


@pytest.mark.parametrize(
    "case,mutation,expected",
    [
        ("ordinary-coding", "incomplete", "turn_1_incomplete"),
        ("ordinary-coding", "response_unverified", "turn_1_response_unverified"),
        ("empty-search", "model_unobserved", "turn_2_model_unobserved"),
        ("explicit-save", "native_wire_mismatch", "turn_1_native_wire_mismatch"),
    ],
)
def test_baseline_execution_evidence_failures_keep_replay_incomplete(tmp_path, tools, case, mutation, expected):
    complete_archive(tmp_path, tools)
    assert replay(tmp_path)["qualified"]
    path = tmp_path / "without_skill" / case / "turns.json"
    turns = json.loads(path.read_text(encoding="utf-8"))
    if mutation == "incomplete":
        turns[0]["error"] = "synthetic_turn_error"
    elif mutation == "response_unverified":
        turns[0]["response"] = "[1,2]"
    elif mutation == "model_unobserved":
        turns[1]["native_events"] = [item for item in turns[1]["native_events"] if item["type"] != "session.updated"]
    else:
        turns[0]["native_events"][1]["payload"]["input"]["text"] = "Native arguments differ from the wire."
    assert grade(case, turns) == [expected]
    write_json(path, turns)
    seal_archive(tmp_path)
    report = replay(tmp_path)
    result = next(item for item in report["results"] if item["arm"] == "without_skill" and item["case"] == case)
    assert result["failures"] == [expected]
    assert result["status"] == "incomplete"
    assert not result["execution_complete"]
    assert not report["qualified"]
    assert all(item["status"] == "passed" for item in report["results"] if item["arm"] == "with_skill")


def native_rejected_search() -> dict:
    evidence = turn([], "[1, 2]")
    evidence["native_events"][1:1] = [
        {
            "type": "model.streaming",
            "payload": {
                "kind": "tool_call",
                "toolCallId": "rejected-search",
                "toolName": PREFIX + "search_memory",
                "input": {},
            },
        },
        {
            "type": "tool.updated",
            "payload": {
                "kind": "scheduled",
                "toolCallId": "rejected-search",
                "toolName": PREFIX + "search_memory",
                "inputOmitted": True,
                "inputRef": "model_stream",
            },
        },
        {
            "type": "tool.updated",
            "payload": {
                "kind": "error",
                "toolCallId": "rejected-search",
                "error": {"type": "tool_execution_failed", "message": "Tool input failed inputSchema validation"},
            },
        },
    ]
    return evidence


@pytest.mark.parametrize("arm,qualified", [("without_skill", True), ("with_skill", False)])
def test_native_input_rejection_is_complete_behavior_failure(tmp_path, tools, arm, qualified):
    complete_archive(tmp_path, tools)
    path = tmp_path / arm / "ordinary-coding/turns.json"
    prompt = json.loads(path.read_text(encoding="utf-8"))[0]["prompt"]
    evidence = native_rejected_search()
    evidence["prompt"] = prompt
    write_json(path, [evidence])
    seal_archive(tmp_path)
    report = replay(tmp_path)
    result = next(item for item in report["results"] if item["arm"] == arm and item["case"] == "ordinary-coding")
    assert result["status"] == "failed"
    assert result["execution_complete"]
    assert set(result["failures"]) == {"turn_1_native_input_rejected", "unnecessary_powercontext_call"}
    assert report["qualified"] is qualified
    assert all(item["execution_complete"] for item in report["results"])
    assert all(item["status"] == "passed" for item in report["results"] if item is not result)


def native_rejected_array_search(input_source: str) -> dict:
    evidence = native_rejected_search()
    evidence["native_events"][1]["payload"]["input"] = []
    if input_source == "scheduled":
        payload = evidence["native_events"][2]["payload"]
        payload.pop("inputOmitted")
        payload.pop("inputRef")
        payload["input"] = []
    return evidence


@pytest.mark.parametrize("input_source", ["streamed", "scheduled"])
@pytest.mark.parametrize("arm,qualified", [("without_skill", True), ("with_skill", False)])
def test_native_array_input_rejection_is_complete_behavior_failure(tmp_path, tools, input_source, arm, qualified):
    complete_archive(tmp_path, tools)
    path = tmp_path / arm / "ordinary-coding/turns.json"
    evidence = native_rejected_array_search(input_source)
    evidence["prompt"] = json.loads(path.read_text(encoding="utf-8"))[0]["prompt"]
    write_json(path, [evidence])
    seal_archive(tmp_path)
    report = replay(tmp_path)
    result = next(item for item in report["results"] if item["arm"] == arm and item["case"] == "ordinary-coding")
    assert result["status"] == "failed"
    assert result["execution_complete"]
    assert set(result["failures"]) == {"turn_1_native_input_rejected", "unnecessary_powercontext_call"}
    assert report["qualified"] is qualified
    assert all(item["execution_complete"] for item in report["results"])
    assert all(item["status"] == "passed" for item in report["results"] if item is not result)


def test_native_string_input_rejection_is_complete_behavior_failure(tmp_path, tools):
    complete_archive(tmp_path, tools)
    path = tmp_path / "without_skill/ordinary-coding/turns.json"
    evidence = native_rejected_search()
    evidence["native_events"][1]["payload"]["input"] = "invalid arguments"
    evidence["prompt"] = json.loads(path.read_text(encoding="utf-8"))[0]["prompt"]
    write_json(path, [evidence])
    seal_archive(tmp_path)
    report = replay(tmp_path)
    result = next(
        item for item in report["results"] if item["arm"] == "without_skill" and item["case"] == "ordinary-coding"
    )
    assert result["status"] == "failed" and result["execution_complete"]
    assert set(result["failures"]) == {"turn_1_native_input_rejected", "unnecessary_powercontext_call"}
    assert report["qualified"]
    assert all(item["status"] == "passed" for item in report["results"] if item is not result)


def test_missing_streamed_input_is_not_an_explicit_null():
    evidence = native_rejected_search()
    payload = evidence["native_events"][1]["payload"]
    payload.pop("input")
    with pytest.raises(ValueError, match="missing_native_tool_input"):
        scheduled(evidence["native_events"])
    payload["input"] = None
    assert scheduled(evidence["native_events"])[0]["input"] is None


@pytest.mark.parametrize("input_source", ["streamed", "scheduled"])
@pytest.mark.parametrize("mutation", ["missing_rejection", "started", "wire", "missing_rejection_with_wire"])
def test_unverified_array_inputs_and_contradictory_wire_keep_replay_incomplete(tmp_path, tools, input_source, mutation):
    complete_archive(tmp_path, tools)
    path = tmp_path / "without_skill/ordinary-coding/turns.json"
    evidence = native_rejected_array_search(input_source)
    evidence["prompt"] = json.loads(path.read_text(encoding="utf-8"))[0]["prompt"]
    if mutation in {"missing_rejection", "missing_rejection_with_wire"}:
        evidence["native_events"].pop(3)
    elif mutation == "started":
        evidence["native_events"].insert(
            3,
            {"type": "tool.updated", "payload": {"kind": "started", "toolCallId": "rejected-search"}},
        )
    if mutation in {"wire", "missing_rejection_with_wire"}:
        evidence["mcp_calls"] = [{"name": "search_memory", "arguments": [], "result": {}, "is_error": True}]
    write_json(path, [evidence])
    seal_archive(tmp_path)
    report = replay(tmp_path)
    result = next(
        item for item in report["results"] if item["arm"] == "without_skill" and item["case"] == "ordinary-coding"
    )
    assert result["status"] == "incomplete"
    assert not result["execution_complete"]
    assert "turn_1_native_wire_mismatch" in result["failures"]
    assert "unnecessary_powercontext_call" in result["failures"]
    assert not report["qualified"]
    assert all(item["status"] == "passed" for item in report["results"] if item is not result)


@pytest.mark.parametrize("case,turn_index", [("explicit-save", 0), ("stale-approval", 1)])
def test_nonobject_wire_cannot_enter_save_or_approval_business_rules(tmp_path, tools, case, turn_index):
    complete_archive(tmp_path, tools)
    path = tmp_path / "without_skill" / case / "turns.json"
    evidence = json.loads(path.read_text(encoding="utf-8"))
    evidence[turn_index]["native_events"][1]["payload"]["input"] = []
    evidence[turn_index]["mcp_calls"][0]["arguments"] = []
    write_json(path, evidence)
    seal_archive(tmp_path)
    report = replay(tmp_path)
    result = next(item for item in report["results"] if item["arm"] == "without_skill" and item["case"] == case)
    assert result["status"] == "incomplete"
    assert not result["execution_complete"]
    assert f"turn_{turn_index + 1}_native_wire_mismatch" in result["failures"]
    assert not report["qualified"]
    assert all(item["status"] == "passed" for item in report["results"] if item is not result)


@pytest.mark.parametrize(
    "mutation",
    ["unmatched_id", "generic_error", "wrong_error_type", "started", "progress", "result", "conflicting_error", "wire"],
)
def test_missing_or_contradictory_native_rejection_evidence_stays_incomplete(tmp_path, tools, mutation):
    complete_archive(tmp_path, tools)
    path = tmp_path / "without_skill/ordinary-coding/turns.json"
    evidence = native_rejected_search()
    evidence["prompt"] = json.loads(path.read_text(encoding="utf-8"))[0]["prompt"]
    error = evidence["native_events"][3]["payload"]
    if mutation == "unmatched_id":
        error["toolCallId"] = "another-call"
    elif mutation == "generic_error":
        error["error"]["message"] = "Tool execution failed"
    elif mutation == "wrong_error_type":
        error["error"]["type"] = "transport_failed"
    elif mutation in {"started", "progress", "result"}:
        evidence["native_events"].insert(
            3,
            {
                "type": "tool.updated",
                "payload": {"kind": mutation, "toolCallId": "rejected-search", "result": {"success": True}},
            },
        )
    elif mutation == "conflicting_error":
        evidence["native_events"].insert(
            3,
            {
                "type": "tool.updated",
                "payload": {
                    "kind": "error",
                    "toolCallId": "rejected-search",
                    "error": {"type": "tool_execution_failed", "message": "Transport closed"},
                },
            },
        )
    else:
        evidence["mcp_calls"] = [{"name": "search_memory", "arguments": {}, "result": {}, "is_error": True}]
    write_json(path, [evidence])
    seal_archive(tmp_path)
    report = replay(tmp_path)
    result = next(
        item for item in report["results"] if item["arm"] == "without_skill" and item["case"] == "ordinary-coding"
    )
    assert result["status"] == "incomplete"
    assert not result["execution_complete"]
    assert "turn_1_native_wire_mismatch" in result["failures"]
    assert "unnecessary_powercontext_call" in result["failures"]
    assert not report["qualified"]
    assert all(item["status"] == "passed" for item in report["results"] if item["arm"] == "with_skill")


@pytest.mark.parametrize("second_rejected", [False, True])
def test_rejection_accounts_for_each_native_identity_with_identical_arguments(tmp_path, tools, second_rejected):
    complete_archive(tmp_path, tools)
    path = tmp_path / "without_skill/ordinary-coding/turns.json"
    evidence = native_rejected_search()
    evidence["prompt"] = json.loads(path.read_text(encoding="utf-8"))[0]["prompt"]
    additional = copy.deepcopy(evidence["native_events"][1 : 4 if second_rejected else 3])
    for event in additional:
        event["payload"]["toolCallId"] = "second-search"
    evidence["native_events"][4:4] = additional
    write_json(path, [evidence])
    seal_archive(tmp_path)
    report = replay(tmp_path)
    result = next(
        item for item in report["results"] if item["arm"] == "without_skill" and item["case"] == "ordinary-coding"
    )
    assert "turn_1_native_input_rejected" in result["failures"]
    assert "unnecessary_powercontext_call" in result["failures"]
    assert ("turn_1_native_wire_mismatch" not in result["failures"]) is second_rejected
    assert result["status"] == ("failed" if second_rejected else "incomplete")
    assert result["execution_complete"] is second_rejected
    assert report["qualified"] is second_rejected
