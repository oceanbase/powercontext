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
    for setting in ("datafile_size='4G'", "datafile_maxsize='8G'", "datafile_next='512M'"):
        assert setting in steps["oceanbase_setup"]["run"]
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
