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

"""Packaging tests for the standalone ``powercontext-langgraph`` distribution.

The package ships separately from the root ``powercontext`` wheel, so it must carry its own Apache-2.0 license.
These tests build the wheel and assert the license text and metadata travel with it.
"""

from __future__ import annotations

import re
import subprocess
import zipfile
from pathlib import Path
from shutil import which

import pytest

_PROJECT = Path(__file__).resolve().parents[2] / "integrations" / "langgraph"


def _build_wheel(out_dir: Path) -> Path:
    uv = which("uv")
    if uv is None:
        pytest.skip("uv is required to build the wheel")
    try:
        result = subprocess.run(
            [uv, "build", "--wheel", "--out-dir", str(out_dir), str(_PROJECT)],
            capture_output=True,
            text=True,
            timeout=300,
        )
    except FileNotFoundError:  # uv disappeared between the which() probe and the call
        pytest.skip("uv is required to build the wheel")
    except subprocess.TimeoutExpired:  # a slow or offline build environment, not a packaging defect
        pytest.skip("wheel build timed out")
    # A non-zero exit is a real packaging defect (e.g. invalid license metadata) — fail rather than skip.
    assert result.returncode == 0, f"uv build failed:\n{result.stdout}\n{result.stderr}"
    wheels = list(out_dir.glob("*.whl"))
    assert wheels, "no wheel was produced"
    return wheels[0]


def test_wheel_bundles_license_and_metadata(tmp_path: Path) -> None:
    wheel = _build_wheel(tmp_path)
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        license_members = [
            name for name in names if re.fullmatch(r"powercontext_langgraph-[^/]+\.dist-info/licenses/LICENSE", name)
        ]
        assert license_members, f"LICENSE not bundled in wheel; members: {names}"
        assert "Apache License" in archive.read(license_members[0]).decode("utf-8")

        metadata_name = next(name for name in names if name.endswith(".dist-info/METADATA"))
        metadata = archive.read(metadata_name).decode("utf-8")
    assert "License-Expression: Apache-2.0" in metadata
    assert "License-File: LICENSE" in metadata
