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

"""Packaging tests for the standalone ``powercontext-langchain`` distribution."""

from __future__ import annotations

import re
import subprocess
import zipfile
from pathlib import Path
from shutil import which

import pytest

_PROJECT = Path(__file__).resolve().parents[2] / "integrations" / "langchain"
_LICENSE = _PROJECT / "LICENSE"


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
    except FileNotFoundError:
        pytest.skip("uv is required to build the wheel")
    except subprocess.TimeoutExpired:
        pytest.skip("wheel build timed out")
    assert result.returncode == 0, f"uv build failed:\n{result.stdout}\n{result.stderr}"
    wheels = list(out_dir.glob("*.whl"))
    assert wheels, "no wheel was produced"
    return wheels[0]


def test_license_file_is_present_in_project() -> None:
    assert _LICENSE.is_file()
    assert "Apache License" in _LICENSE.read_text(encoding="utf-8")


def test_wheel_bundles_middleware_license_and_dependencies(tmp_path: Path) -> None:
    wheel = _build_wheel(tmp_path)
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        assert "powercontext_langchain/client.py" in names
        assert "powercontext_langchain/middleware.py" in names
        assert "powercontext_langchain/scope.py" in names
        license_members = [
            name for name in names if re.fullmatch(r"powercontext_langchain-[^/]+\.dist-info/licenses/LICENSE", name)
        ]
        assert license_members, f"LICENSE not bundled in wheel; members: {names}"
        assert "Apache License" in archive.read(license_members[0]).decode("utf-8")
        metadata_name = next(name for name in names if name.endswith(".dist-info/METADATA"))
        metadata = archive.read(metadata_name).decode("utf-8")

    assert "Requires-Dist: langchain<2,>=1.3" in metadata
    assert "Requires-Dist: powercontext[client]<1,>=0.0.2" in metadata
    assert "Requires-Dist: powercontext-langgraph" not in metadata
    assert "Requires-Dist: langgraph" not in metadata
