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


"""Real-library/process/HTTP-fixture evidence, explicitly excluded from QA scores."""

from __future__ import annotations

import copy
import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import gateway_fixture
import pytest
from gateway_fixture import SQL, TABLE, gateway
from powercontext_datus.capture import reconcile
from powercontext_datus.evaluate import evaluate_case, model_metrics
from powercontext_datus.freeze import IntegrityError, digest_json
from powercontext_datus.paired import file_hash, freeze_plan, read_secret, run_one, run_pair
from powercontext_datus.report import summarize
from powercontext_datus.sandbox import Sandbox


@pytest.fixture
def sandbox():
    configured = os.environ.get("DATUS_RUNTIME_PYTHON")
    if not configured:
        pytest.skip("requires the pinned native Datus runtime")
    return Sandbox(Path(configured).absolute(), Path("integrations/datus/src").absolute())


def test_native_graph_full_rows_failures_retries_and_wrapper_deduplication(sandbox, plan, tmp_path):
    with gateway(skill=True, sql_tool=True, fail_sql=True) as (endpoint, calls):
        plan["public"]["model"]["base_url"] = endpoint
        run = run_one(plan, sandbox, "enhanced", plan["tasks"][0], run_id="component")
    (tmp_path / "native-graph-raw.json").write_text(json.dumps(run, ensure_ascii=False))
    assert run["returncode"] == 0, run
    trace = reconcile(run["records"])
    assert trace["trace_complete"], trace
    # Skill, failing SQL, successful SQL, workflow SQL. The SQL tool/driver
    # wrapper is one operation; running the same SQL again is another.
    assert trace["steps"] == 4, trace
    assert sum(op["failed"] for op in trace["operations"]) == 1
    assert [v["rows"] for v in trace["sql_results"]] == [TABLE["rows"], TABLE["rows"]]
    assert len(calls) == 4
    verdict, _ = evaluate_case(
        "fixture-1", run, {"expected": TABLE, "declared_answer": TABLE}, state_valid=True, data_version_verified=True
    )
    assert verdict.correct and verdict.answer_grounded and verdict.native_execution
    assert verdict.steps == 4
    metrics = model_metrics(
        run["records"], {"input_per_million": "1", "output_per_million": "2", "cached_per_million": "0.5"}
    )
    assert metrics["calls_started"] == 4
    assert metrics["input_tokens"] == 200 and metrics["output_tokens"] == 80
    assert metrics["cost"] == "0.00034"
    assert all(
        {t["function"]["name"] for t in call["tools"]} == {"load_skill", "describe_table", "list_tables", "execute_sql"}
        for call in calls
    )
    assert all(SQL not in call["messages"][0]["content"] for call in calls)
    damaged = copy.deepcopy(run)
    damaged["records"] = [r for r in damaged["records"] if r["kind"] != "sql_rows"]
    assert reconcile(damaged["records"])["steps"] is None
    duplicate = copy.deepcopy(run["records"])
    duplicate.insert(-1, next(r.copy() for r in duplicate if r["kind"] == "operation_started"))
    for index, record in enumerate(duplicate, 1):
        record["sequence"] = index
    assert reconcile(duplicate)["steps"] is None
    unknown = copy.deepcopy(run["records"])
    unknown.insert(
        -1, {**unknown[0], "kind": "action_received", "action": {"role": "tool", "action_id": "unobserved-child-call"}}
    )
    for index, record in enumerate(unknown, 1):
        record["sequence"] = index
    assert "unmatched_native_action" in reconcile(unknown)["trace_issues"]
    for status in ("success", "failed"):
        replayed = copy.deepcopy(run["records"])
        terminal = next(
            r
            for r in replayed
            if r["kind"] == "action_received"
            and r["action"].get("role") == "tool"
            and r["action"].get("status") == "success"
        )
        replay = copy.deepcopy(terminal)
        replay["action"]["status"] = status
        replayed.insert(-1, replay)
        for index, record in enumerate(replayed, 1):
            record["sequence"] = index
        assert reconcile(replayed)["steps"] is None
    premature = copy.deepcopy(run["records"])
    terminal = next(
        r for r in premature if r["kind"] == "action_received" and r["action"].get("action_id") == "complete_call_0"
    )
    premature.remove(terminal)
    before_dispatch = next(
        i for i, r in enumerate(premature) if r["kind"] == "operation_started" and r["call_id"] == "call_0"
    )
    premature.insert(before_dispatch, terminal)
    for index, record in enumerate(premature, 1):
        record.update(sequence=index, monotonic_ns=index)
    corrupted = reconcile(premature)
    assert not corrupted["trace_complete"] and corrupted["steps"] is None
    (tmp_path / "premature-terminal.json").write_text(json.dumps({"records": premature, "reconciled": corrupted}))
    orphaned = copy.deepcopy(run["records"])
    orphan = copy.deepcopy(next(r for r in orphaned if r["kind"] == "tool_returned"))
    orphan["operation_id"] = "unobserved-dispatch"
    orphaned.insert(next(i for i, r in enumerate(orphaned) if r["kind"] == "answer_submitted"), orphan)
    for index, record in enumerate(orphaned, 1):
        record.update(sequence=index, monotonic_ns=index)
    unbound = reconcile(orphaned)
    (tmp_path / "orphan-return.json").write_text(json.dumps({"records": orphaned, "reconciled": unbound}))
    assert not unbound["trace_complete"] and unbound["steps"] is None, unbound


def test_native_sql_result_does_not_prove_final_answer(sandbox, plan):
    with gateway(wrong_answer=True) as (endpoint, _):
        plan["public"]["model"]["base_url"] = endpoint
        run = run_one(plan, sandbox, "native", plan["tasks"][0], run_id="component-wrong")
    verdict, _ = evaluate_case(
        "fixture-1", run, {"expected": TABLE, "declared_answer": TABLE}, state_valid=True, data_version_verified=True
    )
    assert verdict.correct and verdict.native_execution
    assert not verdict.answer_grounded
    conflict, _ = evaluate_case(
        "fixture-1",
        run,
        {"expected": TABLE, "declared_answer": {"columns": ["value"], "rows": [[123]]}},
        state_valid=True,
        data_version_verified=True,
    )
    assert not conflict.oracle_consistent


def test_pair_freezes_actual_runtime_and_never_counts_fixtures_as_live(sandbox, plan, tmp_path):
    plan["tasks"].append({"task_id": "fixture-2", "question": "List the same synthetic sample values again."})
    oracles = json.loads(Path(plan["oracle_file"]).read_text())
    oracles["fixture-2"] = oracles["fixture-1"]
    Path(plan["oracle_file"]).write_text(json.dumps(oracles))
    admission = json.loads(Path(plan["admission_file"]).read_text())
    admission.update(roster_sha256=digest_json(plan["tasks"]), oracle_sha256=file_hash(Path(plan["oracle_file"])))
    Path(plan["admission_file"]).write_text(json.dumps(admission))
    with gateway() as (endpoint, calls):
        plan["public"]["model"]["base_url"] = endpoint
        manifest = freeze_plan(plan, sandbox)
        assert len(calls) == 0  # Preparation does not send the question/model request.
        report = run_pair(manifest, sandbox, tmp_path / "evidence")
    assert report["real_development_runs"] == {"native": 0, "enhanced": 0}
    assert report["formal_state"] == "not_started"
    assert report["component_case_runs"] == 4
    assert all(v["joint_pass"] == 2 for v in report["arms"].values()), report
    assert all(not v["accepted"] and v["component_pass"] for v in report["arms"].values()), report
    sessions = []
    for path in sorted((tmp_path / "evidence").glob("case-*.json")):
        evidence = json.loads(path.read_text())
        probe = next(r for r in evidence["records"] if r["kind"] == "isolation_probe")
        assert probe["all_denied"] and probe["frozen_read_only"] and probe["proc_absent"]
        sessions.append(next(r["session_id"] for r in evidence["records"] if r["kind"] == "effective_config"))
    assert len(set(sessions)) == 4
    Path(plan["common_file"]).write_text("drift")
    with pytest.raises(IntegrityError):
        run_pair(manifest, sandbox, tmp_path / "must-not-exist")
    assert not (tmp_path / "must-not-exist").exists()


def test_os_denies_gold_sibling_trace_and_host_env(sandbox, plan, tmp_path):
    sibling = tmp_path / "other-question.json"
    sibling.write_text("other question result")
    request = {
        "run_id": "isolation",
        "attempt_id": "probe",
        "task": {"task_id": "probe"},
        "denied_paths": [
            plan["oracle_file"],
            str(sibling),
            "/proc/self/environ",
            "/proc/1/root",
            "/proc/self/fd",
            str(Path.home() / ".datus/config.yml"),
        ],
        "probe_only": True,
    }
    run = sandbox.run(
        request, common=Path(plan["common_file"]), skills=Path(plan["arms"]["native"]["skill_root"]), timeout=60
    )
    assert run["returncode"] == 0, run
    probe = next(r for r in run["records"] if r["kind"] == "isolation_probe")
    assert probe["all_denied"] and probe["private_write"] and probe["frozen_read_only"]
    assert Path(plan["common_file"]).read_text().startswith("Synthetic")


def test_secret_reference_rejects_public_permissions_and_symlinks(tmp_path):
    key = tmp_path / "task-key"
    key.write_text("fixture-secret")
    key.chmod(0o644)
    with pytest.raises(IntegrityError):
        read_secret(str(key))
    key.chmod(0o600)
    assert read_secret(str(key)) == "fixture-secret"
    link = tmp_path / "key-link"
    link.symlink_to(key)
    with pytest.raises(IntegrityError):
        read_secret(str(link))


def test_unknown_model_usage_stays_unknown():
    assert model_metrics([])["cost"] is None
    assert model_metrics([])["input_tokens"] is None


def test_timeout_is_retained_with_unknown_steps(sandbox, plan):
    plan["timeout_seconds"] = 0.1
    run = run_one(plan, sandbox, "native", plan["tasks"][0], run_id="timeout-fixture")
    assert run["timeout"] and run["returncode"] is None
    verdict, _ = evaluate_case(
        "fixture-1", run, {"expected": TABLE, "declared_answer": TABLE}, state_valid=True, data_version_verified=True
    )
    assert verdict.steps is None and "timeout/cancel" in verdict.failures


def test_native_driver_nested_operations_and_chunked_results(sandbox, tmp_path):
    probe = Path(__file__).with_name("native_capture_probe.py").absolute()
    result = subprocess.run(
        [str(sandbox.python), str(probe)],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=tmp_path,
        env={"PATH": os.defpath, "PYTHONPATH": str(sandbox.bridge)},
    )
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert json.loads(result.stdout)["result"]["steps"] == 5


def test_ordered_final_answer_obeys_frozen_policy_without_borrowing_tolerance(sandbox, plan, monkeypatch):
    monkeypatch.setattr(gateway_fixture, "TABLE", {"columns": ["value"], "rows": [[None], [7], [7]]})
    with gateway() as (endpoint, _):
        plan["public"]["model"]["base_url"] = endpoint
        run = run_one(plan, sandbox, "native", plan["tasks"][0], run_id="ordered-regression")
    assert run["returncode"] == 0, run
    oracle = {"expected": TABLE, "declared_answer": TABLE, "ordered": True}
    ordered, _ = evaluate_case("fixture-1", run, oracle, state_valid=True, data_version_verified=True)
    assert ordered.correct and not ordered.answer_grounded
    assert summarize(["fixture-1"], [ordered], data_version_verified=True)["joint_pass"] == 0
    oracle["ordered"] = False
    unordered, _ = evaluate_case("fixture-1", run, oracle, state_valid=True, data_version_verified=True)
    assert unordered.answer_grounded
    damaged = copy.deepcopy(run)
    answer = next(r for r in damaged["records"] if r["kind"] == "answer_submitted")
    answer["answer"] = json.dumps({"columns": ["value"], "rows": [[None], [7.1], [7]]})
    oracle["absolute_tolerance"] = "1"
    tolerant, _ = evaluate_case("fixture-1", damaged, oracle, state_valid=True, data_version_verified=True)
    assert tolerant.correct and not tolerant.answer_grounded


def test_drift_after_model_dispatch_preserves_started_case_and_stops_pair(sandbox, plan, tmp_path):
    def drift(_request):
        Path(plan["common_file"]).write_text("changed after the actual HTTP model dispatch")

    with gateway(on_request=drift) as (endpoint, calls):
        plan["public"]["model"]["base_url"] = endpoint
        manifest = freeze_plan(plan, sandbox)
        report = run_pair(manifest, sandbox, tmp_path / "drift-evidence")
    first = json.loads((tmp_path / "drift-evidence/case-0000.json").read_text())
    second = json.loads((tmp_path / "drift-evidence/case-0001.json").read_text())
    assert len(calls) == 1
    assert first.get("not_started") is False and first["process_started"]
    assert first["wall_seconds"] > 0
    assert first["control_failure"] == "leakage/state_drift"
    assert any(r["kind"] == "question_injected" for r in first["records"])
    assert model_metrics(first["records"])["calls_started"] == 1
    assert second["not_started"] and not second["records"]
    assert not report["state_valid"] and report["formal_state"] == "not_started"
    assert all(v["joint_pass"] == 0 and not v["accepted"] for v in report["arms"].values())


def test_mysql_control_commands_and_ack_failures_are_not_hidden(sandbox, tmp_path):
    probe = Path(__file__).with_name("mysql_command_probe.py").absolute()
    result = subprocess.run(
        [str(sandbox.python), str(probe)],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=tmp_path,
        env={"PATH": os.defpath, "PYTHONPATH": str(sandbox.bridge)},
    )
    assert result.returncode == 0, (result.stdout, result.stderr)
    evidence = json.loads(result.stdout)
    assert evidence["reconciled"]["trace_complete"], evidence
    assert evidence["reconciled"]["steps"] == 6
    assert sum(op["failed"] for op in evidence["reconciled"]["operations"]) == 1
    assert len(evidence["commands"]) == 6
    assert evidence["missing_ack"]["steps"] is None
    assert evidence["bypassed"]["steps"] is None
    assert "mysql_command_boundary_bypassed" in evidence["bypassed"]["trace_issues"]
    assert evidence["send_failure"]["trace_complete"] and evidence["send_failure"]["steps"] == 1
    assert evidence["send_failure"]["operations"][0]["failed"]
    assert evidence["cursor"]["trace_complete"] and evidence["cursor"]["steps"] == 2
    assert len(evidence["cursor"]["sql_results"]) == 2


@pytest.mark.parametrize("mutation", ["common_changed", "common_deleted", "skill_changed", "skill_symlink"])
@pytest.mark.parametrize("timeout", [False, True])
def test_post_launch_validation_never_discards_raw_or_partial_output(tmp_path, monkeypatch, mutation, timeout):
    common = tmp_path / "common"
    common.write_text("frozen common")
    skills = tmp_path / "skills"
    skills.mkdir()
    skill = skills / "SKILL.md"
    skill.write_text("frozen skill")
    raw = json.dumps({"kind": "question_injected", "sequence": 1, "run_id": "r", "task_id": "t", "attempt_id": "a"})
    raw += "\npartial non-JSON worker output"

    def completed(*args, **kwargs):
        if mutation == "common_changed":
            common.write_text("drift")
        elif mutation == "common_deleted":
            common.unlink()
        elif mutation == "skill_changed":
            skill.write_text("drift")
        else:
            skill.unlink()
            skill.symlink_to(common)
        if timeout:
            raise subprocess.TimeoutExpired("component-probe", 1, output=raw.encode(), stderr=b"private error")
        return SimpleNamespace(returncode=0, stdout=raw, stderr="private error")

    monkeypatch.setattr(Sandbox, "execution_identity", lambda self: {"cpuinfo": "fixture"})
    monkeypatch.setattr(Sandbox, "command", lambda *args, **kwargs: ["component-probe"])
    monkeypatch.setattr(subprocess, "run", completed)
    run = Sandbox(tmp_path / "python", tmp_path / "bridge").run({}, common=common, skills=skills)
    assert run["stdout"] == raw and len(run["records"]) == 1
    assert run["process_started"] and run["not_started"] is False
    assert run["malformed_output"] and run["timeout"] == timeout
    assert run["control_failure"] == "leakage/state_drift" and not run["state_valid"]
    assert run["wall_seconds"] is not None and run["stderr_bytes"] > 0
