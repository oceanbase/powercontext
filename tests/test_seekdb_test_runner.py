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

"""Native crashes and external deadlines remain distinct CI failures."""

import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.skipif(sys.platform == "win32", reason="native watchdog owns POSIX process groups")
@pytest.mark.parametrize(
    ("program", "code", "message"),
    [
        ("print('completed')", 0, "completed"),
        ("raise SystemExit(1)", 1, ""),
        ("import os, signal; os.kill(os.getpid(), signal.SIGTERM)", 143, "terminated by SIGTERM"),
        ("import time; time.sleep(60)", 124, "test exceeded"),
    ],
)
def test_native_test_runner_preserves_exit_and_deadline_outcomes(tmp_path, program, code, message):
    # Use a disposable child instead of loading a database into the test runner.
    # The checked-in watchdog still launches, diagnoses and terminates a real process.
    (tmp_path / "pytest.py").write_text(program)
    script = Path(__file__).parents[1] / ".github/scripts/run_seekdb_migration_test.py"
    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--diagnose-after",
            "2",
            "--timeout",
            "4",
            "--diagnostics",
            str(tmp_path / "diagnostics.log"),
            "synthetic-node",
        ],
        cwd=tmp_path,
        env={**os.environ, "TMPDIR": str(tmp_path), "PYTHONPATH": str(tmp_path)},
        text=True,
        capture_output=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == code, result.stdout + result.stderr
    assert message in result.stdout
