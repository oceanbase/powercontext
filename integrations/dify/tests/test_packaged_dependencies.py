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

"""The packaged daemon dependency set must match the SDK test environment."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path


def test_shipped_runtime_pin_drift_fails_the_requirements_check(tmp_path):
    project = Path(__file__).resolve().parents[1]
    (tmp_path / "plugin").mkdir()
    for name in ("pyproject.toml", "uv.lock", "generate_requirements.py", "plugin/requirements.txt"):
        shutil.copyfile(project / name, tmp_path / name)
    lock = (tmp_path / "uv.lock").read_bytes()
    command = [sys.executable, str(tmp_path / "generate_requirements.py"), "--check"]
    subprocess.run(command, cwd=tmp_path, check=True, capture_output=True, text=True, timeout=30)

    shipped = tmp_path / "plugin/requirements.txt"
    shipped.write_text(
        shipped.read_text(encoding="utf-8").replace("dify-plugin==", "dify-plugin!=", 1), encoding="utf-8"
    )
    stale = subprocess.run(command, cwd=tmp_path, capture_output=True, text=True, timeout=30)
    assert stale.returncode != 0
    assert "Dify requirements require regeneration" in stale.stderr
    assert (tmp_path / "uv.lock").read_bytes() == lock
