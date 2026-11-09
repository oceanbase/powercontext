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

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

WORKFLOW = (Path(__file__).parents[1] / ".github" / "workflows" / "e2e-harness.yml").read_text(encoding="utf-8")


def test_supplied_scope_keeps_acceptance_runnable_after_the_detector_is_skipped() -> None:
    jobs = yaml.safe_load(WORKFLOW)["jobs"]
    assert jobs["changes"]["if"] == "${{ inputs.suites == '' }}"
    assert "!cancelled()" in jobs["validate"]["if"]
    assert jobs["acceptance"]["needs"] == "validate"
    # A successful validate job does not clear the skipped ancestor for Actions'
    # implicit success(). Acceptance needs its own status function and success gate.
    assert jobs["acceptance"]["if"] == "${{ !cancelled() && needs.validate.result == 'success' }}"
    assert jobs["acceptance"]["strategy"]["matrix"] == "${{ fromJSON(needs.validate.outputs.matrix) }}"


def run_inline_python(step, env):
    script = step["run"].removeprefix("python3 - <<'PY'\n").removesuffix("PY\n")
    return subprocess.run(
        [sys.executable, "-c", script], env={**os.environ, **env}, capture_output=True, text=True, check=False
    )


@pytest.mark.parametrize(
    ("suites", "expected"),
    [
        (
            {"harness": True},
            [
                {"database": "sqlite", "replay": True, "native": False},
                {"database": "oceanbase", "replay": True, "native": False},
            ],
        ),
        ({"oceanbase_acceptance": True}, [{"database": "oceanbase", "replay": False, "native": True}]),
        (
            {"harness": True, "oceanbase_acceptance": True},
            [
                {"database": "sqlite", "replay": True, "native": False},
                {"database": "oceanbase", "replay": True, "native": True},
            ],
        ),
    ],
)
def test_acceptance_plan_only_allocates_selected_workers(tmp_path, suites, expected):
    step = next(step for step in yaml.safe_load(WORKFLOW)["jobs"]["validate"]["steps"] if step.get("id") == "plan")
    output = tmp_path / "output"
    result = run_inline_python(step, {"CI_SUITES": json.dumps(suites), "GITHUB_OUTPUT": str(output)})
    assert result.returncode == 0, result.stderr
    values = dict(line.split("=", 1) for line in output.read_text().splitlines())
    assert json.loads(values["matrix"]) == {"include": expected}
    assert json.loads(values["replay"]) is suites.get("harness", False)


@pytest.mark.parametrize("mode", ["replay", "native", "both"])
@pytest.mark.parametrize("outcome", ["success", "skipped", "failure", "missing"])
def test_acceptance_gate_rejects_unexpected_skips_failures_and_missing_evidence(mode, outcome):
    jobs = yaml.safe_load(WORKFLOW)["jobs"]
    step = next(step for step in jobs["acceptance"]["steps"] if "STEP_RESULTS" in step.get("env", {}))
    plan = {"database": "oceanbase", "replay": mode != "native", "native": mode != "replay"}
    selected = ["oceanbase_setup"]
    if plan["replay"]:
        selected.extend(["acceptance_scenarios", "evidence_scan", "replay_evidence"])
    if plan["native"]:
        selected.extend(["native_acceptance", "native_results"])
    # Unselected work is intentionally skipped. Every selected step is mandatory.
    for target in selected:
        results = {
            name: {"outcome": "success" if name in selected else "skipped"}
            for name in (
                "oceanbase_setup",
                "acceptance_scenarios",
                "evidence_scan",
                "replay_evidence",
                "native_acceptance",
                "native_results",
            )
        }
        if outcome == "missing":
            results.pop(target)
        else:
            results[target]["outcome"] = outcome
        result = run_inline_python(step, {"ACCEPTANCE_PLAN": json.dumps(plan), "STEP_RESULTS": json.dumps(results)})
        assert (result.returncode == 0) is (outcome == "success"), result.stderr
        if outcome != "success":
            assert target in result.stderr


def test_shared_oceanbase_setup_and_evidence_are_independent_of_replay_selection():
    jobs = yaml.safe_load(WORKFLOW)["jobs"]
    steps = {step.get("id"): step for step in jobs["acceptance"]["steps"] if "id" in step}
    assert steps["oceanbase_setup"]["if"] == "matrix.database == 'oceanbase'"
    assert (
        'scripts/oceanbase_ci.py wait --container "$container_id" --database powercontext'
        in (steps["oceanbase_setup"]["run"])
    )
    assert steps["native_acceptance"]["if"] == "matrix.native"
    assert steps["acceptance_scenarios"]["if"] == "matrix.replay"
    for name in ("evidence_scan", "replay_evidence"):
        assert "matrix.replay" in steps[name]["if"]
    assert steps["native_results"]["if"] == "always() && matrix.native"
    for name in ("native_results", "replay_evidence"):
        assert steps[name]["with"]["if-no-files-found"] == "error"


def test_standalone_harness_retains_formatting_and_full_fallback():
    workflow = yaml.safe_load(WORKFLOW)
    assert workflow[True]["workflow_call"]["inputs"]["preflight-checked"]["default"] is False
    steps = workflow["jobs"]["validate"]["steps"]
    lint = next(step for step in steps if step.get("run") == "make harness-lint")
    assert "!inputs.preflight-checked" in lint["if"]
    plan = next(step for step in steps if step.get("id") == "plan")
    assert '\'{"harness":true,"oceanbase_acceptance":true}\'' in plan["env"]["CI_SUITES"]
    main = yaml.safe_load((Path(__file__).parents[1] / ".github/workflows/master.yml").read_text())
    assert main["jobs"]["harness-validation"]["with"]["preflight-checked"] is True
    assert ".oceanbase_acceptance" in main["jobs"]["harness-validation"]["if"]


def test_e2e_scan_keeps_raw_scanner_output_out_of_artifacts() -> None:
    steps = yaml.safe_load(WORKFLOW)["jobs"]["acceptance"]["steps"]
    diagnostics = next(
        step for step in steps if step.get("with", {}).get("name", "").startswith("e2e-acceptance-diagnostics-")
    )
    assert diagnostics["with"]["path"] == (
        "${{ runner.temp }}/powercontext-e2e-diagnostics-${{ matrix.database }}/summary.txt"
    )


def test_e2e_diagnostics_upload_only_runs_for_failed_validation_or_scan() -> None:
    assert "steps.evidence_scan.outcome != 'success'" in WORKFLOW
    assert "steps.acceptance_scenarios.outcome != 'success'" in WORKFLOW


def test_native_snapshot_precedes_replay_container_teardown() -> None:
    steps = yaml.safe_load(WORKFLOW)["jobs"]["acceptance"]["steps"]
    collect_index = next(i for i, step in enumerate(steps) if step["name"] == "Collect OceanBase native diagnostics")
    replay_index = next(i for i, step in enumerate(steps) if step.get("id") == "acceptance_scenarios")
    upload_index = next(i for i, step in enumerate(steps) if step["name"] == "Upload OceanBase native reports")
    # run.sh removes the container before returning, even when replay fails.
    assert collect_index < replay_index < upload_index
    assert steps[collect_index]["if"] == "always() && matrix.database == 'oceanbase'"
    assert steps[replay_index]["env"]["POWERCONTEXT_E2E_OCEANBASE_DIAGNOSTICS"] == (
        "${{ runner.temp }}/oceanbase-acceptance/replay-failure"
    )


@pytest.mark.parametrize(("native", "replay"), [(True, True), (True, False), (False, True)])
def test_oceanbase_acceptance_reserves_time_for_evidence_and_cleanup(native, replay):
    job = yaml.safe_load(WORKFLOW)["jobs"]["acceptance"]

    def oceanbase_minutes(value):
        if isinstance(value, int):
            return value
        match = re.fullmatch(r"\$\{\{ matrix.database == 'oceanbase' && (\d+) \|\| \d+ }}", value)
        assert match, value
        return int(match[1])

    steps = {step.get("id"): step for step in job["steps"] if "id" in step}
    workload = steps["oceanbase_setup"]["timeout-minutes"]
    if native:
        workload += steps["native_acceptance"]["timeout-minutes"]
    if replay:
        workload += oceanbase_minutes(steps["acceptance_scenarios"]["timeout-minutes"])
    # Reserve ten minutes outside the workload, including bounded diagnostics.
    diagnostics = next(step for step in job["steps"] if step["name"] == "Collect OceanBase native diagnostics")
    assert diagnostics["timeout-minutes"] <= 2
    assert workload + 10 <= oceanbase_minutes(job["timeout-minutes"])


@pytest.mark.skipif(sys.platform == "win32", reason="Workflow steps require a POSIX shell")
def test_native_acceptance_survives_fresh_step_shell_and_uploads_its_report(tmp_path, monkeypatch):
    steps = {step.get("id"): step for step in yaml.safe_load(WORKFLOW)["jobs"]["acceptance"]["steps"] if "id" in step}
    commands = tmp_path / "bin"
    commands.mkdir()
    # Execute the actual workflow scripts in separate shells and use the real
    # diagnostic wrapper. Substitute external commands, not the step handoff.
    executable = (
        f"#!{sys.executable}\n"
        + r"""
import json
import os
import subprocess
import sys
from pathlib import Path

args = sys.argv[1:]
name = Path(sys.argv[0]).name
if name == "timeout":
    while args[0].startswith("--"):
        args.pop(0)
    os.execvp(args[1], args[1:])
if name == "docker":
    if "ps" in args:
        print("shared-oceanbase")
    elif args[0] == "inspect":
        print("127.0.0.1")
elif name == "python":
    if args[:2] == ["scripts/oceanbase_ci.py", "wait"]:
        raise SystemExit(0)
    assert args[:2] == ["scripts/oceanbase_ci.py", "run"], args
    container = args[args.index("--container") + 1]
    deadline = float(args[args.index("--timeout") + 1])
    assert container == "shared-oceanbase", container
    assert 0 < deadline <= 3450, deadline
    Path(os.environ["RUNNER_TEMP"], "invocation.json").write_text(json.dumps(args))
    os.execv(sys.executable, [sys.executable, *args])
elif name == "uv":
    if "pytest" in args:
        path = Path(next(arg.split("=", 1)[1] for arg in args if arg.startswith("--junitxml=")))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('<testsuite tests="1" failures="0" errors="0" skipped="0"><testcase name="native"/></testsuite>')
    else:
        script = sys.stdin.read()
        if "ET.parse" in script:
            raise SystemExit(subprocess.run([sys.executable, "-c", script]).returncode)
        # Version query requires the external database, omitted in this boundary test.
        assert "OceanBaseConfig" in script
"""
    )
    for name in ("docker", "timeout", "python", "uv"):
        path = commands / name
        path.write_text(executable, encoding="utf-8")
        path.chmod(0o755)
    monkeypatch.setenv("PATH", f"{commands}{os.pathsep}{os.defpath}")
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    monkeypatch.setenv("POWERCONTEXT_E2E_OUTPUT", str(tmp_path / "replay"))
    monkeypatch.setenv("GITHUB_ENV", str(tmp_path / "env"))
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "outputs"))
    monkeypatch.delenv("container_id", raising=False)
    monkeypatch.delenv("native_deadline", raising=False)
    root = Path(__file__).parents[1]
    setup = subprocess.run(
        ["/bin/bash", "-e", "-o", "pipefail", "-c", steps["oceanbase_setup"]["run"]],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert setup.returncode == 0, setup.stderr
    outputs = dict(line.split("=", 1) for line in (tmp_path / "outputs").read_text().splitlines())
    env = dict(os.environ)
    env.update(line.split("=", 1) for line in (tmp_path / "env").read_text().splitlines())
    for key, value in steps["native_acceptance"].get("env", {}).items():
        match = re.fullmatch(r"\$\{\{ steps.oceanbase_setup.outputs.(\w+) }}", value)
        assert match, value
        env[key] = outputs[match[1]]
    native = subprocess.run(
        ["/bin/bash", "-e", "-o", "pipefail", "-c", steps["native_acceptance"]["run"]],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert native.returncode == 0, native.stdout + native.stderr
    assert "OceanBase Atomic Memory, HTTP and archive results:" in native.stdout
    invocation = json.loads((tmp_path / "invocation.json").read_text())
    report = Path(steps["native_results"]["with"]["path"].replace("${{ runner.temp }}", str(tmp_path)))
    assert f"--junitxml={report}" in invocation
    assert report.is_file()
