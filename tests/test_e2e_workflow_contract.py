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

WORKFLOW = (Path(__file__).parents[1] / ".github" / "workflows" / "e2e-harness.yml").read_text(encoding="utf-8")


def test_e2e_scan_keeps_raw_scanner_output_out_of_artifacts() -> None:
    assert "--json > /dev/null 2>&1" not in WORKFLOW
    assert "run: bash scripts/scan_e2e_evidence.sh" in WORKFLOW
    assert "path: ${{ runner.temp }}/powercontext-e2e-diagnostics-${{ matrix.database }}/summary.txt" in WORKFLOW

    script = Path(__file__).parents[1] / "scripts" / "scan_e2e_evidence.sh"
    script_text = script.read_text(encoding="utf-8")
    assert 'scanner_json="${DIAGNOSTICS_PATH}/trufflehog.jsonl"' in script_text
    assert 'scanner_stderr="${DIAGNOSTICS_PATH}/trufflehog.stderr.log"' in script_text
    assert "path: ${{ runner.temp }}/powercontext-e2e-diagnostics-${{ matrix.database }}/summary.txt" in WORKFLOW


def test_e2e_scan_classifies_findings_and_retries_infrastructure_errors() -> None:
    script = (Path(__file__).parents[1] / "scripts" / "scan_e2e_evidence.sh").read_text(encoding="utf-8")
    assert 'test "${scanner_status}" -eq 183' in script
    assert 'echo "scan_status=findings"' in script
    assert 'echo "scan_status=infra_error"' in script
    assert "scanner_attempts=2" in script
    assert 'test "${scanner_status}" -ne 0 && test "${scanner_status}" -ne 183' in script


def test_e2e_diagnostics_upload_only_runs_for_failed_validation_or_scan() -> None:
    assert "steps.evidence_scan.outcome != 'success'" in WORKFLOW
    assert "steps.acceptance_scenarios.outcome != 'success'" in WORKFLOW
    assert "Enforce replay evidence policy" not in WORKFLOW
