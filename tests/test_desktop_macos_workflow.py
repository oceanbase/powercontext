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

"""Regression coverage for the macOS DMG packaging recovery path."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).parents[1]


def _macos_build_command() -> str:
    workflow = yaml.safe_load((ROOT / ".github/workflows/desktop.yml").read_text(encoding="utf-8"))
    steps = workflow["jobs"]["macos"]["steps"]
    return next(step["run"] for step in steps if step.get("name") == "Build unsigned macOS app and DMG")


def test_recovered_macos_retry_still_uploads_diagnostics() -> None:
    workflow = yaml.safe_load((ROOT / ".github/workflows/desktop.yml").read_text(encoding="utf-8"))
    steps = workflow["jobs"]["macos"]["steps"]
    build_step = next(step for step in steps if step.get("name") == "Build unsigned macOS app and DMG")
    upload_step = next(step for step in steps if step.get("name") == "Upload macOS build diagnostics")

    assert build_step["id"] == "macos_build"
    assert upload_step["if"] == "${{ failure() || steps.macos_build.outputs.retried == 'true' }}"
    assert "desktop/.artifacts/macos-build*.log" in upload_step["with"]["path"]
    assert upload_step["with"]["include-hidden-files"] is True


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


@pytest.mark.skipif(os.name == "nt", reason="The macOS workflow uses POSIX process semantics")
@pytest.mark.parametrize(
    ("sequence", "exit_code", "calls", "retried", "state_logs"),
    [
        ("success", 0, 1, False, 0),
        ("busy,success", 0, 2, True, 1),
        ("busy_eject,success", 0, 2, True, 1),
        ("failure", 42, 1, False, 1),
        ("busy,failure", 42, 2, True, 2),
        ("busy,busy,busy", 1, 3, True, 3),
    ],
)
def test_macos_dmg_build_preserves_transient_failure_evidence(
    tmp_path: Path,
    sequence: str,
    exit_code: int,
    calls: int,
    retried: bool,
    state_logs: int,
) -> None:
    bash = shutil.which("bash")
    assert bash is not None
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    counter = tmp_path / "calls"
    output = tmp_path / "github-output"
    _write_executable(
        bin_dir / "pnpm",
        """#!/usr/bin/env bash
set -eu
if [[ "${1:-}" == "--version" ]]; then echo 'test-pnpm'; exit 0; fi
count=0
if [[ -f "$TEST_CALL_COUNT" ]]; then count="$(cat "$TEST_CALL_COUNT")"; fi
count=$((count + 1))
printf '%s' "$count" > "$TEST_CALL_COUNT"
IFS=, read -r -a outcomes <<< "$TEST_PNPM_SEQUENCE"
outcome="${outcomes[$((count - 1))]:-success}"
case "$outcome" in
  success) echo 'bundle complete' ;;
  busy) echo 'hdiutil: create failed - Resource busy' >&2; exit 1 ;;
  busy_eject) echo "hdiutil: couldn't eject disk2 - Resource busy" >&2; exit 1 ;;
  failure) echo 'unrelated packaging failure' >&2; exit 42 ;;
  *) exit 99 ;;
esac
""",
    )
    result = subprocess.run(
        [bash, "-c", "sleep() { :; }\n" + _macos_build_command()],
        cwd=tmp_path,
        env={
            **os.environ,
            "PATH": os.pathsep.join((str(bin_dir), os.environ["PATH"])),
            "GITHUB_OUTPUT": str(output),
            "TEST_CALL_COUNT": str(counter),
            "TEST_PNPM_SEQUENCE": sequence,
        },
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    assert result.returncode == exit_code, result.stderr
    assert int(counter.read_text(encoding="utf-8")) == calls
    assert ("retried=true" in output.read_text(encoding="utf-8") if output.exists() else False) is retried
    diagnostics = sorted((tmp_path / ".artifacts").glob("macos-build-attempt-*-state.log"))
    assert len(diagnostics) == state_logs
    assert all("## attached disk images" in path.read_text(encoding="utf-8", errors="replace") for path in diagnostics)
    assert all("## toolchain" in path.read_text(encoding="utf-8", errors="replace") for path in diagnostics)
    if os.uname().sysname != "Darwin":
        assert all(
            "unavailable on a non-macOS runner" in path.read_text(encoding="utf-8", errors="replace")
            for path in diagnostics
        )
