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

"""CI admission, coverage and terminal-result budgets."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path

import ci_changes
import pytest
import yaml

ROOT = Path(__file__).parents[1]
RULES = tomllib.loads((ROOT / ".github/ci-paths.toml").read_text())


def workflow(name):
    result = yaml.safe_load((ROOT / ".github/workflows" / name).read_text())
    result["on"] = result.pop(True)
    return result


def combinations(matrix):
    if "include" in matrix:
        return {(row["python-version"], row["suite"]) for row in matrix["include"]}
    return {(version, suite) for version in matrix["python-version"] for suite in matrix["suite"]}


def test_ordinary_pr_has_three_compatibility_jobs():
    assert combinations(ci_changes.test_matrix("pull_request", ["src/powercontext/server/settings.py"], RULES)) == {
        ("3.11", "unit"),
        ("3.14", "unit"),
        ("3.12", "e2e"),
    }


@pytest.mark.parametrize("event", ["push", "schedule", "workflow_dispatch"])
def test_full_validation_keeps_all_supported_python_suite_pairs(event):
    assert combinations(ci_changes.test_matrix(event, [], RULES)) == {
        (version, suite) for version in ("3.11", "3.12", "3.13", "3.14") for suite in ("unit", "e2e")
    }


@pytest.mark.parametrize("path", ["uv.lock", "pyproject.toml", "tox.ini", "tests/conftest.py", "new-component/x.py"])
def test_dependency_and_unclassified_pr_changes_keep_full_compatibility(path):
    assert len(combinations(ci_changes.test_matrix("pull_request", [path], RULES))) == 8


def test_explicit_full_pr_profile_overrides_the_small_matrix():
    assert (
        len(
            combinations(
                ci_changes.test_matrix("pull_request", ["src/powercontext/server/settings.py"], RULES, full=True)
            )
        )
        == 8
    )


def test_selected_scopes_have_a_complete_terminal_result_contract():
    main = workflow("master.yml")["jobs"]
    assert RULES["checks"].keys() == RULES["suites"].keys()
    covered = {job for jobs in RULES["checks"].values() for job in jobs}
    assert covered | {"quality", "upgrade-release-gate", "ci-result"} == set(main)
    assert set(main["ci-result"]["needs"]) == set(main) - {"ci-result"}


def test_preflight_uses_one_runner_and_keeps_all_admission_checks_mandatory():
    jobs = workflow("master.yml")["jobs"]
    assert {name for name, job in jobs.items() if not job.get("needs")} == {"quality"}
    preflight = jobs["quality"]
    assert "!github.event.pull_request.draft" in preflight["if"]
    assert not preflight.get("continue-on-error")
    steps = preflight["steps"]
    checkouts = [step for step in steps if step.get("uses", "").startswith("actions/checkout@")]
    assert len(checkouts) == 1
    assert checkouts[0]["with"]["fetch-depth"] == 0
    selection = next(step for step in steps if step.get("id") == "select")
    license_check = next(step for step in steps if step.get("uses", "").startswith("apache/skywalking-eyes/header@"))
    quality = next(step for step in steps if step.get("run") == "make check")
    contract = next(step for step in steps if step.get("run") == "make contract-test")
    assert selection["env"]["CI_BASELINE_WORKFLOW"] == "master.yml"
    assert license_check["with"] == {"config": ".licenserc.yaml", "mode": "check"}
    for step in (selection, license_check, quality, contract):
        assert not step.get("continue-on-error")
        assert "if" not in step  # A preceding failure prevents further admission work.
    for output in ("suites", "test-matrix", "required-jobs"):
        assert preflight["outputs"][output] == "${{ steps.select.outputs." + output + " }}"
    for name, job in jobs.items():
        if name not in {"quality", "upgrade-release-gate", "ci-result"}:
            assert "quality" in job["needs"]
            assert "needs.quality.result == 'success'" in job["if"]


def test_installation_roles_use_only_two_python_runners():
    job = workflow("master.yml")["jobs"]["isolated-installations"]
    assert job["strategy"]["matrix"] == {"python-version": ["3.11", "3.14"]}
    build = [step for step in job["steps"] if "uv build --wheel" in step.get("run", "")]
    assert len(build) == 1
    verify = next(step for step in job["steps"] if "ci_installation_roles.py" in step.get("run", ""))
    assert not verify.get("continue-on-error")
    assert "if" not in verify
    reports = next(step for step in job["steps"] if step.get("uses", "").startswith("actions/upload-artifact@"))
    assert reports["if"] == "always()"
    assert "summary.json" in reports["with"]["path"]
    assert "logs/*.log" in reports["with"]["path"]


def test_automatic_host_workflows_share_one_scope_and_admission_gate():
    assert workflow("master.yml")["on"]["push"]["branches"] == ["master"]
    assert workflow("master.yml")["on"]["pull_request"]["branches"] == ["master"]
    main = workflow("master.yml")["jobs"]
    for path in (ROOT / ".github/workflows").glob("*.yml"):
        if path.name != "master.yml":
            events = workflow(path.name)["on"]
            assert "pull_request" not in events, path.name
            assert "master" not in events.get("push", {}).get("branches", []), path.name
    for job in main.values():
        if not job.get("uses", "").endswith(".yml") or "ci-changes" in job["uses"] or "upgrade-release" in job["uses"]:
            continue
        child = workflow(Path(job["uses"]).name)
        assert "pull_request" not in child["on"]
        assert "schedule" not in child["on"]
        assert "master" not in child["on"].get("push", {}).get("branches", [])
        assert job["with"]["suites"] == "${{ needs.quality.outputs.suites }}"
        assert {"quality", "tests"} <= set(job["needs"])
        assert "needs.quality.result == 'success'" in job["if"]
        assert "needs.tests.result" in job["if"]
        assert child["jobs"]["changes"]["if"] == "${{ inputs.suites == '' }}"
        for child_job in child["jobs"].values():
            if "needs.changes.result" in child_job.get("if", ""):
                assert "(inputs.suites == '' && needs.changes.result != 'success')" in child_job["if"]
                assert "fromJSON(inputs.suites || needs.changes.outputs.suites || '{}')" in child_job["if"]


def test_compatibility_and_upgrade_matrices_have_bounded_runner_bursts():
    assert int(workflow("master.yml")["jobs"]["tests"]["strategy"]["max-parallel"]) <= 3
    assert int(workflow("master.yml")["jobs"]["isolated-installations"]["strategy"]["max-parallel"]) <= 2
    upgrade = workflow("upgrade-release.yml")["jobs"]["cases"]["strategy"]
    assert int(upgrade["max-parallel"]) <= 3
    assert upgrade["fail-fast"] is False  # Complete upgrade evidence remains mandatory.
    native = workflow("managed-resource-candidates.yml")["jobs"]["candidate"]["strategy"]
    assert int(native["max-parallel"]) <= 2
    assert {row["target"] for row in native["matrix"]["include"]} == {"windows-x86_64", "macos-arm64", "macos-x86_64"}


def test_cancelled_or_draft_runs_cannot_start_terminal_runner_jobs():
    jobs = workflow("master.yml")["jobs"]
    for name in ("upgrade-release-gate", "ci-result"):
        assert "!cancelled()" in jobs[name]["if"]
        assert "!github.event.pull_request.draft" in jobs[name]["if"]
    assert "converted_to_draft" in workflow("master.yml")["on"]["pull_request"]["types"]


@pytest.mark.parametrize("selected_job", ["quality", "harness-validation"])
@pytest.mark.parametrize(
    ("selected_result", "unselected_result", "passes"),
    [
        ("success", "skipped", True),
        ("skipped", "skipped", False),
        ("cancelled", "skipped", False),
        ("failure", "skipped", False),
        ("success", "failure", False),
    ],
)
def test_actual_ci_result_script_rejects_missing_selected_acceptance(
    selected_job, selected_result, unselected_result, passes
):
    script = workflow("master.yml")["jobs"]["ci-result"]["steps"][0]["run"]
    script = script.split("<<'PYTHON'\n", 1)[1].rsplit("PYTHON", 1)[0]
    required = ["quality", "upgrade-release-gate", "harness-validation"]
    results = {job: {"result": "success"} for job in required}
    results[selected_job]["result"] = selected_result
    results["desktop-validation"] = {"result": unselected_result}
    run = subprocess.run(
        [sys.executable, "-c", script],
        env={**os.environ, "RESULTS": json.dumps(results), "REQUIRED": json.dumps(required)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert (run.returncode == 0) == passes, run.stdout + run.stderr
