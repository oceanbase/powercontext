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

from pathlib import Path

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
    assert set(jobs["acceptance"]["strategy"]["matrix"]["database"]) == {"sqlite", "oceanbase"}


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
