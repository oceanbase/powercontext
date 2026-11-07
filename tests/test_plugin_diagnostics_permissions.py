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

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

_PLUGIN_DIAGNOSTICS = (
    Path(__file__).parents[1] / "integrations/codex/plugins/powercontext/hooks/diagnostics.py",
    Path(__file__).parents[1] / "integrations/claude-code/plugins/powercontext/hooks/diagnostics.py",
)


@pytest.mark.skipif(os.name == "nt", reason="POSIX umask is not available on Windows")
@pytest.mark.parametrize("module_path", _PLUGIN_DIAGNOSTICS)
def test_diagnostics_respect_restrictive_umask(module_path: Path, tmp_path: Path) -> None:
    state_path = tmp_path / "nested" / "state" / "diagnostics.json"
    probe = """
import importlib.util
import pathlib
import sys

module_path = pathlib.Path(sys.argv[1])
state_path = pathlib.Path(sys.argv[2])
spec = importlib.util.spec_from_file_location("diagnostics_probe", module_path)
module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(module)
assert module.should_emit("server_unavailable")
assert (state_path.stat().st_mode & 0o777) == 0o600
assert (state_path.parent.stat().st_mode & 0o777) == 0o700
assert (state_path.parent.parent.stat().st_mode & 0o777) == 0o700
lock_path = state_path.with_name(f"{state_path.name}.lock")
assert (lock_path.stat().st_mode & 0o777) == 0o600
"""
    environment = {**os.environ, "POWERCONTEXT_DIAGNOSTIC_STATE_FILE": str(state_path)}
    subprocess.run(
        [sys.executable, "-c", probe, str(module_path), str(state_path)],
        check=True,
        env=environment,
        capture_output=True,
        text=True,
        umask=0o077,
    )
