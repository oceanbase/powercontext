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

"""Adversarial evaluator component checks; these never count as live QA."""

from __future__ import annotations

import copy
import json
from decimal import localcontext
from pathlib import Path

import pytest
from powercontext_datus import paired
from powercontext_datus.capture import reconcile
from powercontext_datus.evaluate import evaluate_case
from powercontext_datus.freeze import IntegrityError, digest_json, snapshot
from powercontext_datus.sandbox import Sandbox


def numbered(records):
    return [
        {"run_id": "synthetic", "task_id": "t", "attempt_id": "a", **r, "sequence": i, "monotonic_ns": i}
        for i, r in enumerate(records, 1)
    ]


def tool_records():
    return numbered([
        {"kind": "capture_started"},
        {"kind": "question_injected"},
        {
            "kind": "action_received",
            "action": {
                "role": "tool",
                "action_id": "call",
                "action_type": "list_tables",
                "status": "processing",
            },
        },
        {
            "kind": "operation_started",
            "operation_id": "op",
            "parent_id": None,
            "call_id": "call",
            "name": "list_tables",
            "online": True,
        },
        {"kind": "tool_returned", "operation_id": "op", "value": {"success": True}},
        {"kind": "operation_finished", "operation_id": "op", "status": "success"},
        {
            "kind": "action_received",
            "action": {
                "role": "tool",
                "action_id": "complete_call",
                "action_type": "list_tables",
                "status": "success",
            },
        },
        {"kind": "answer_submitted"},
        {"kind": "capture_finished", "interrupted": False},
    ])


@pytest.mark.parametrize(
    "damage",
    [
        "duplicate_processing",
        "duplicate_terminal",
        "conflicting_terminal",
        "missing_processing",
        "missing_terminal",
        "reversed",
        "wrong_name",
        "wrong_status",
        "dispatch_status_conflict",
    ],
)
def test_native_action_lifecycle_cannot_be_folded_into_sets(damage):
    records = tool_records()
    assert reconcile(records)["trace_complete"]
    if damage.startswith("duplicate_") or damage == "conflicting_terminal":
        duplicate = copy.deepcopy(records[2 if damage == "duplicate_processing" else 6])
        if damage == "conflicting_terminal":
            duplicate["action"]["status"] = "failed"
        records.insert(-2, duplicate)
    elif damage.startswith("missing_"):
        records.pop(2 if damage == "missing_processing" else 6)
    elif damage == "reversed":
        records[2], records[6] = records[6], records[2]
    elif damage == "wrong_name":
        records[6]["action"]["action_type"] = "execute_sql"
    elif damage == "wrong_status":
        records[2]["action"]["status"] = "success"
    else:
        records[6]["action"]["status"] = "failed"
    result = reconcile(numbered([{k: v for k, v in r.items() if k != "sequence"} for r in records]))
    assert not result["trace_complete"] and result["steps"] is None, result


@pytest.mark.parametrize(
    "order",
    [
        (2, 6, 3, 4, 5),  # C GEN-5: success before dispatch even starts.
        (2, 3, 6, 4, 5),  # Terminal during dispatch.
        (3, 2, 4, 5, 6),  # Processing only arrives after dispatch starts.
        (2, 4, 3, 5, 6),  # Return before dispatch.
        (2, 3, 5, 4, 6),  # Return after dispatch finishes.
        (2, 3, 5, 6),  # A successful invocation cannot omit its return.
    ],
)
def test_action_and_dispatch_must_share_one_ordered_lifecycle(order):
    records = tool_records()
    reordered = [*records[:2], *(records[i] for i in order), *records[-2:]]
    result = reconcile(numbered([{k: v for k, v in r.items() if k != "sequence"} for r in reordered]))
    assert not result["trace_complete"] and result["steps"] is None, result


@pytest.mark.parametrize("mode", ["exception", "failure_payload", "success"])
def test_ordered_native_failures_and_success_remain_countable(mode):
    records = tool_records()
    if mode == "exception":
        records[5]["status"] = "failure"
        records[6]["action"]["status"] = "failed"
        records.pop(4)
    elif mode == "failure_payload":
        records[4]["value"]["success"] = False
        records[6]["action"]["status"] = "failed"
    result = reconcile(numbered([{k: v for k, v in r.items() if k != "sequence"} for r in records]))
    assert result["trace_complete"] and result["steps"] == 1, result
    assert result["operations"][0]["failed"] == (mode != "success")


def test_unknown_dispatch_terminal_status_cannot_certify_a_failed_action():
    records = tool_records()
    records[5]["status"] = "not_started"
    records[6]["action"]["status"] = "failed"
    result = reconcile(records)
    assert not result["trace_complete"] and result["steps"] is None, result


@pytest.mark.parametrize("order", [(0, 2, 1, 3, 4, 5, 6, 7, 8), (0, 1, 2, 3, 4, 5, 7, 6, 8)])
def test_native_dispatch_stays_inside_the_active_question(order):
    records = tool_records()
    result = reconcile(numbered([{k: v for k, v in records[i].items() if k != "sequence"} for i in order]))
    assert not result["trace_complete"] and result["steps"] is None, result


def test_native_exception_cannot_also_claim_a_successful_return():
    records = tool_records()
    records[5]["status"] = "failure"
    records[6]["action"]["status"] = "failed"
    result = reconcile(records)
    assert not result["trace_complete"] and result["steps"] is None, result


def test_native_dispatch_requires_an_explicit_online_start():
    records = tool_records()
    records[3]["online"] = "true"
    result = reconcile(records)
    assert not result["trace_complete"] and result["steps"] is None, result


def test_interleaved_calls_are_valid_when_each_local_lifecycle_is_ordered():
    records = tool_records()
    first = records[2:7]
    second = copy.deepcopy(first)
    for record in second:
        if "operation_id" in record:
            record["operation_id"] = "op_second"
        if "call_id" in record:
            record["call_id"] = "call_second"
        if "action" in record:
            record["action"]["action_id"] += "_second"
    interleaved = [first[0], second[0], first[1], *second[1:], *first[2:]]
    result = reconcile(numbered([*records[:2], *interleaved, *records[-2:]]))
    assert result["trace_complete"] and result["steps"] == 2, result


def decimal_run(number):
    table = {"columns": ["value"], "rows": [[{"decimal": number}]]}
    records = numbered([
        {"kind": "capture_started"},
        {"kind": "question_injected"},
        {"kind": "sql_started", "driver_span_id": "s", "sql": "SELECT amount"},
        {
            "kind": "sql_link",
            "driver_span_id": "s",
            "parent_id": None,
            "online": True,
            "dialect": "sqlite",
            "executemany": False,
        },
        {"kind": "sql_success", "driver_span_id": "s"},
        {"kind": "sql_rows", "driver_span_id": "s", "complete": True, **table},
        {
            "kind": "answer_submitted",
            "output": {"sql_query_final": "SELECT amount"},
            "answer": '{"columns":["value"],"rows":[[' + number + "]]}",
        },
        {"kind": "capture_finished", "interrupted": False},
    ])
    return {"records": records, "returncode": 0, "timeout": False, "malformed_output": False}, table


@pytest.mark.parametrize(
    "number", ["12345678901234567890.12", "-0.000000000000000000000000012345678901", "1.234567890123456789e50"]
)
@pytest.mark.parametrize("precision", [2, 28])
def test_standard_json_decimal_answer_is_lossless(number, precision):
    run, table = decimal_run(number)
    oracle = {"expected": table, "declared_answer": table, "ordered": True}
    with localcontext() as context:
        context.prec = precision
        verdict, _ = evaluate_case("t", run, oracle, state_valid=True, data_version_verified=True)
    assert verdict.correct and verdict.trace_complete and verdict.steps == 1
    assert verdict.answer_grounded
    run["records"][-2]["answer"] = '{"columns":["value"],"rows":[[0]]}'
    wrong, _ = evaluate_case("t", run, oracle, state_valid=True, data_version_verified=True)
    assert not wrong.answer_grounded


@pytest.mark.parametrize("placement", ["root", "nested", "cache_named"])
def test_runtime_directory_symlinks_cannot_hide_from_identity(tmp_path, placement):
    root = tmp_path / "runtime"
    root.mkdir()
    target = tmp_path / "host"
    target.mkdir()
    (target / "code.py").write_text("original")
    paired.runtime_files(root)
    if placement == "root":
        link = tmp_path / "runtime-link"
        link.symlink_to(root, target_is_directory=True)
        root = link
    else:
        (root / ("__pycache__" if placement == "cache_named" else "host-runtime")).symlink_to(
            target, target_is_directory=True
        )
    with pytest.raises(IntegrityError, match="directory"):
        paired.runtime_files(root)


def test_runtime_file_symlink_tracks_target_content(tmp_path):
    root = tmp_path / "runtime"
    root.mkdir()
    binary = tmp_path / "python"
    binary.write_bytes(b"first binary")
    (root / "python").symlink_to(binary)
    before = paired.runtime_files(root)
    binary.write_bytes(b"second binary")
    assert paired.runtime_files(root) != before


def test_internal_runtime_directory_alias_and_target_are_both_inventoried(tmp_path):
    library = tmp_path / "lib"
    library.mkdir()
    code = library / "code.py"
    code.write_text("original")
    before = paired.runtime_files(tmp_path)
    (tmp_path / "lib64").symlink_to("lib", target_is_directory=True)
    alias = paired.runtime_files(tmp_path)
    assert alias != before
    code.write_text("modified")
    assert paired.runtime_files(tmp_path) != alias


@pytest.mark.parametrize(
    "artifact",
    [
        {"family": "skill", "revision": 1},
        {"family": "skill", "artifact_id": "", "revision": 1},
        {"family": "skill", "artifact_id": " ", "revision": 1},
        {"family": "skill", "artifact_id": "a", "revision": True},
        {"family": "skill", "artifact_id": "a", "revision": "1"},
        {"family": "skill", "artifact_id": "a", "revision": 0},
        {"family": "skill", "artifact_id": "a", "revision": 1, "extra": "unbound"},
        {"family": "document", "artifact_id": "a", "revision": 1},
    ],
)
def test_enhanced_delivery_requires_complete_strict_artifact_identity(tmp_path, artifact):
    root = tmp_path / "skill"
    root.mkdir()
    (root / "SKILL.md").write_text("Synthetic Skill")
    plan = {
        "arms": {
            "native": {"skill_root": str(tmp_path), "skill_names": []},
            "enhanced": {"skill_root": str(tmp_path), "skill_names": ["skill"]},
        }
    }
    ref = {
        "name": "skill",
        "files": snapshot(root),
        "scope_id": "scope",
        "artifact": {"family": "skill", "artifact_id": "approved", "revision": 1},
        "tree_digest": "a" * 64,
        "archive_digest": "b" * 64,
    }
    admission = {"skill_deliveries": {"native": [], "enhanced": [ref]}}
    paired.validate_deliveries(plan, admission)
    ref["artifact"] = artifact
    with pytest.raises(IntegrityError, match="delivery"):
        paired.validate_deliveries(plan, admission)


@pytest.fixture
def component_plan(tmp_path):
    common = tmp_path / "common.txt"
    common.write_text("Synthetic common")
    oracle = tmp_path / "oracle.json"
    _, table = decimal_run("1.00")
    tasks = [{"task_id": task, "question": "Synthetic question"} for task in ("t1", "t2")]
    oracle.write_text(json.dumps({t["task_id"]: {"expected": table, "declared_answer": table} for t in tasks}))
    admission = tmp_path / "admission.json"
    admission.write_text(
        json.dumps({
            "evidence_kind": "component_fixture",
            "roster_sha256": digest_json(tasks),
            "common_sha256": paired.file_hash(common),
            "oracle_sha256": paired.file_hash(oracle),
        })
    )
    for path in ("native", "enhanced", "bridge", "runtime/bin"):
        (tmp_path / path).mkdir(parents=True)
    return {
        "evidence_kind": "component_fixture",
        "tasks": tasks,
        "common_file": str(common),
        "oracle_file": str(oracle),
        "admission_file": str(admission),
        "timeout_seconds": 30,
        "public": {
            "max_turns": 3,
            "current_date": "2026-01-01",
            "database": {"type": "sqlite", "name": "fixture"},
            "model": {"type": "openai", "model": "fixture", "base_url": "http://127.0.0.1:1"},
        },
        "arms": {arm: {"skill_root": str(tmp_path / arm), "skill_names": []} for arm in paired.ARMS},
    }


@pytest.mark.parametrize("phase", ["freeze", "pair"])
def test_all_tasks_and_arms_reuse_one_in_memory_credential_read(tmp_path, component_plan, monkeypatch, phase):
    plan = component_plan
    key = tmp_path / "dummy-key"
    key.write_text("dummy-first-identity")
    key.chmod(0o600)
    reads, supplied = [], []

    def credentials(_plan):
        reads.append(1)
        return dict.fromkeys(("model_api_key", "db_password"), paired.read_secret(str(key)))

    def worker(_self, request, **kwargs):
        supplied.append(dict(request["credentials"]))
        key.write_text("dummy-second-identity")
        request["credentials"]["model_api_key"] = "worker-mutation-must-not-affect-next-case"
        if request["prepare_only"]:
            return {
                "returncode": 0,
                "malformed_output": False,
                "records": [{"kind": "effective_config", "effective_sha256": "fixture"}],
            }
        return decimal_run("1.00")[0]

    monkeypatch.setattr(paired, "credentials_for", credentials)
    monkeypatch.setattr(Sandbox, "run", worker)
    sandbox = Sandbox(tmp_path / "runtime/bin/python", tmp_path / "bridge")
    if phase == "freeze":
        artifact = paired.freeze_plan(plan, sandbox)
    else:
        manifest = {
            "version": 1,
            "plan": plan,
            "inputs": paired.input_identity(plan, sandbox),
            "effective": dict.fromkeys(paired.ARMS, "fixture"),
        }
        artifact = paired.run_pair(manifest, sandbox, tmp_path / "output")
        assert artifact["state_valid"]
        assert all(not arm["accepted"] and arm["component_pass"] for arm in artifact["arms"].values())
    assert len(supplied) == (2 if phase == "freeze" else 4)
    assert reads == [1]
    assert all(v == dict.fromkeys(("model_api_key", "db_password"), "dummy-first-identity") for v in supplied)
    assert "dummy-first-identity" not in json.dumps(artifact)
    assert "dummy-second-identity" not in json.dumps(artifact)


def test_sample_batch_reuses_credentials_without_saving_them(tmp_path, component_plan, monkeypatch):
    # Synthetic worker only: exercising the admission/dispatch contract is not
    # evidence of real independent learning or a valid business-data receipt.
    plan = component_plan
    plan["evidence_kind"] = "independent_learning"
    plan["public"]["model"]["base_url"] = "https://fixture.invalid"
    plan["public"]["database"]["type"] = "mysql"
    admission = paired.read_json(Path(plan["admission_file"]))
    admission.update(
        evidence_kind="independent_learning",
        independent_author="fixture-author",
        independent_reviewer="fixture-reviewer",
    )
    for name in ("source_receipt", "exposure_ledger", "service_authorization_receipt", "data_version_receipt"):
        admission[name] = {"file": plan["common_file"], "sha256": paired.file_hash(Path(plan["common_file"]))}
    Path(plan["admission_file"]).write_text(json.dumps(admission))
    reads, supplied = [], []

    def credentials(_plan):
        reads.append(1)
        return {"model_api_key": "synthetic-key-" + str(len(reads))}

    def worker(_self, request, **kwargs):
        supplied.append(dict(request["credentials"]))
        return decimal_run("1.00")[0]

    monkeypatch.setattr(paired, "credentials_for", credentials)
    monkeypatch.setattr(Sandbox, "run", worker)
    paired.run_samples(plan, Sandbox(tmp_path / "runtime/bin/python", tmp_path / "bridge"), tmp_path / "output")
    assert reads == [1] and len(supplied) == 2
    assert supplied[0] == supplied[1] == {"model_api_key": "synthetic-key-1"}
    assert all("synthetic-key-" not in path.read_text() for path in (tmp_path / "output").glob("*.json"))
