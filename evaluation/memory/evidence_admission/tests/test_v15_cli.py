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

"""CLI discovery must work without repository metadata or model dependencies."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("help_only", [True, False])
def test_cli_outside_git_can_show_help_but_not_execute_a_freeze(tmp_path, help_only):
    source = Path(__file__).parents[1] / "src/powercontext_eval_evidence_admission/v15/__main__.py"
    entry = tmp_path / "entry.py"
    entry.write_bytes(source.read_bytes())
    output = tmp_path / "freeze.json"
    args = ["--help"] if help_only else ["freeze", "--output", str(output)]
    result = subprocess.run(
        [sys.executable, "-I", str(entry), *args], cwd=tmp_path, capture_output=True, text=True, check=False
    )
    assert result.returncode == (0 if help_only else 2)
    assert "Traceback" not in result.stderr
    if help_only:
        assert "compare" in result.stdout and "replay" in result.stdout
    else:
        assert "Git source checkout" in result.stderr
    assert not output.exists()
