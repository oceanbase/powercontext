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
import sqlite3
import subprocess
from pathlib import Path

import pytest
from gateway_fixture import SQL, TABLE, gateway
from powercontext_datus.capture import reconcile
from powercontext_datus.evaluate import evaluate_case, model_metrics
from powercontext_datus.freeze import IntegrityError, digest_json
from powercontext_datus.paired import file_hash, freeze_plan, read_secret, run_one, run_pair
from powercontext_datus.sandbox import Sandbox


@pytest.fixture
def sandbox():
    configured = os.environ.get("DATUS_RUNTIME_PYTHON")
    if not configured:
        pytest.skip("requires the pinned native Datus runtime")
    return Sandbox(Path(configured).absolute(), Path("integrations/datus/src").absolute())


@pytest.fixture
def plan(tmp_path):
    for arm in ("native", "enhanced"):
        (tmp_path / arm).mkdir()
    skill = tmp_path / "enhanced/fixture"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: fixture\ndescription: Synthetic instruction.\n---\nUse read-only SQL.\n"
    )
    common = tmp_path / "common.txt"
    common.write_text("Synthetic component schema: sample(value integer). Not learning or benchmark evidence.")
    db = sqlite3.connect(tmp_path / "database.sqlite")
    db.executescript("CREATE TABLE sample(value INTEGER); INSERT INTO sample VALUES (7),(7),(NULL);")
    db.close()
    tasks = [{"task_id": "fixture-1", "question": "List the sample values."}]
    oracle_file = tmp_path / "oracle.json"
    oracle_file.write_text(json.dumps({"fixture-1": {"expected": TABLE, "declared_answer": TABLE, "ordered": True}}))
    admission_file = tmp_path / "admission.json"
    admission_file.write_text(
        json.dumps({
            "evidence_kind": "component_fixture",
            "roster_sha256": digest_json(tasks),
            "common_sha256": file_hash(common),
            "oracle_sha256": file_hash(oracle_file),
        })
    )
    return {
        "evidence_kind": "component_fixture",
        "common_file": str(common),
        "oracle_file": str(oracle_file),
        "admission_file": str(admission_file),
        "database_file": str(tmp_path / "database.sqlite"),
        "tasks": tasks,
        "timeout_seconds": 90,
        "public": {
            "model": {"type": "openai", "model": "gpt-4o-mini", "base_url": "", "temperature": 0},
            "max_turns": 6,
            "current_date": "2026-01-01",
            "database": {"type": "sqlite", "name": "fixture"},
        },
        "arms": {
            "native": {"skill_root": str(tmp_path / "native"), "skill_names": []},
            "enhanced": {"skill_root": str(tmp_path / "enhanced"), "skill_names": ["fixture"]},
        },
    }


def test_native_graph_full_rows_failures_retries_and_wrapper_deduplication(sandbox, plan):
    with gateway(skill=True, sql_tool=True, fail_sql=True) as (endpoint, calls):
        plan["public"]["model"]["base_url"] = endpoint
        run = run_one(plan, sandbox, "enhanced", plan["tasks"][0], run_id="component")
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
