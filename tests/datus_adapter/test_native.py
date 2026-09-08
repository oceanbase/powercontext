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

"""Optional real-library smoke, explicitly not an LLM or QA execution."""

import json
import os
import subprocess
from pathlib import Path

import pytest
from powercontext_datus.freeze import snapshot


@pytest.fixture
def runtime_python():
    configured = os.environ.get("DATUS_RUNTIME_PYTHON")
    if not configured:
        pytest.skip("set DATUS_RUNTIME_PYTHON to the separately locked Datus Python executable")
    # Do not resolve the venv executable symlink to the bare interpreter.
    executable = Path(configured).absolute()
    assert executable.is_file()
    return executable


def probe(runtime_python, root, *names):
    source = Path(__file__).resolve().parents[2] / "integrations/datus/src"
    env = {"PATH": os.environ.get("PATH", ""), "PYTHONPATH": str(source)}
    command = [str(runtime_python), "-m", "powercontext_datus.native", "--skill-root", str(root)]
    for name in names:
        command.extend(["--expected-skill", name])
    return subprocess.run(command, env=env, cwd=root.parent, capture_output=True, text=True, timeout=60)


def test_native_skill_load_inventory_isolation_and_pin(runtime_python, tmp_path):
    root = tmp_path / "skills"
    skill = root / "native-fixture"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: native-fixture\ndescription: Test fixture, never use as learning evidence.\n---\nUse read-only queries.\n"
    )
    frozen = snapshot(root)
    result = probe(runtime_python, root, "native-fixture")
    assert result.returncode == 0, (result.stdout, result.stderr)
    report = json.loads(result.stdout)
    assert report["native_agent_qa_runs"] == 0
    assert report["skills"]["inventory"] == ["native-fixture"]
    assert report["skills"]["files"] == frozen == snapshot(root)
    assert report["runtime"]["packages"]["datus-agent"] == "0.4.0"
    assert report["status"] == "passed"
    missing = probe(runtime_python, root, "missing-skill")
    assert missing.returncode == 1
    assert json.loads(missing.stdout)["error_type"] == "IntegrityError"


def test_native_empty_baseline_does_not_discover_builtin_skills(runtime_python, tmp_path):
    root = tmp_path / "skills"
    root.mkdir()
    result = probe(runtime_python, root)
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert json.loads(result.stdout)["skills"]["inventory"] == []


def test_native_observer_retains_rollback_failures_and_distinct_sql(runtime_python, tmp_path):
    source = Path(__file__).resolve().parents[2] / "integrations/datus/src"
    script = Path(__file__).with_name("native_observer_probe.py")
    path = tmp_path / "sidecar.jsonl"
    result = subprocess.run(
        [str(runtime_python), str(script), str(path)],
        env={"PATH": os.environ.get("PATH", ""), "PYTHONPATH": str(source)},
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, (result.stdout, result.stderr)
    records = [json.loads(line) for line in path.read_text().splitlines()]
    assert records[0]["kind"] == "capture_started"
    assert records[-1]["kind"] == "capture_finished"
    assert records[-1]["interrupted"] is False
    rolled_back = next(r for r in records if r.get("method") == "rollback_to" and r["kind"] == "actions_before")
    assert rolled_back["actions"][0]["status"] == "failed"
    assert rolled_back["actions"][0]["input"] == {"sql": "SELECT 1"}
    spans = [r["driver_span_id"] for r in records if r["kind"] == "sql_started"]
    assert len(spans) == len(set(spans)) == 3
    assert len([r for r in records if r["kind"] == "sql_failure"]) == 1
    assert path.stat().st_mode & 0o777 == 0o600
    incoming = [r for r in records if r["kind"] == "action_received"]
    assert len(incoming) == 2
    assert incoming[1]["action"]["input"]["sql"] == "SELECT 2"
    aborted = [json.loads(line) for line in (tmp_path / "aborted.jsonl").read_text().splitlines()]
    assert aborted[-1]["interrupted"] is True
