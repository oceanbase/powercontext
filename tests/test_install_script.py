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

"""Regression: piping the installer must not consume interactive Agent selection input."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / "website/public/install.sh"


@pytest.mark.skipif(sys.platform == "win32", reason="the regression concerns a Unix controlling terminal")
def test_piped_installer_reads_agent_selection_from_terminal(tmp_path: Path) -> None:
    pytest.importorskip("pty")
    bash = shutil.which("bash")
    assert bash is not None
    commands = tmp_path / "bin"
    commands.mkdir()
    # Only the downstream tools are substitutes; the actual installer runs through a terminal and pipe.
    for name, content in {
        "uv": '#!/bin/sh\ncase "$1 $2" in\n"python find") echo "$TEST_PYTHON";;\n"tool dir") echo "$TEST_BIN";;\nesac\n',
        "powercontext": '#!/bin/sh\nif [ "$1" = setup ]; then read -r selection; printf "%s\\n" "$selection" > "$TEST_SELECTION"; fi\n',
    }.items():
        executable = commands / name
        executable.write_text(content)
        executable.chmod(0o755)
    selection = tmp_path / "selection"
    env = dict(
        os.environ,
        PATH=f"{commands}{os.pathsep}{os.environ['PATH']}",
        UV_OFFLINE="1",
        TEST_BIN=str(commands),
        TEST_PYTHON=sys.executable,
        TEST_SELECTION=str(selection),
    )
    controller = """import errno, os, pty, sys
pid, terminal = pty.fork()
if pid == 0:
    os.execv(sys.argv[1], [sys.argv[1], '-o', 'pipefail', '-c',
        'cat "$1" | "$2"', 'installer-pipeline', sys.argv[2], sys.argv[1]])
try:
    os.write(terminal, b'codex\\n')
    while True:
        try:
            data = os.read(terminal, 4096)
        except OSError as error:
            if error.errno == errno.EIO:
                break
            raise
        if not data:
            break
        os.write(1, data)
finally:
    os.close(terminal)
_, status = os.waitpid(pid, 0)
sys.exit(os.waitstatus_to_exitcode(status))
"""
    result = subprocess.run(
        [sys.executable, "-c", controller, bash, str(SCRIPT)],
        env=env,
        cwd=tmp_path,
        text=True,
        capture_output=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert selection.read_text().strip() == "codex"
