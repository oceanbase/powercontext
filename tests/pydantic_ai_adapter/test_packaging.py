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

"""Published-wheel compatibility tests for integrations using the shared capture module."""

from __future__ import annotations

import os
import re
import subprocess
import sys
import zipfile
from email.parser import Parser
from pathlib import Path
from shutil import which

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_PROJECTS = {
    "core": _ROOT,
    "pydantic-ai": _ROOT / "integrations" / "pydantic-ai",
    "bub": _ROOT / "integrations" / "bub",
}
_PYDANTIC_AI_README = _PROJECTS["pydantic-ai"] / "README.md"
_PYDANTIC_AI_HOW_TOS = (
    _ROOT / "docs" / "en" / "docs" / "how-to" / "configure-pydantic-ai.md",
    _ROOT / "docs" / "zh" / "docs" / "how-to" / "configure-pydantic-ai.md",
)
_OPENAI_INSTALL = 'uv add powercontext-pydantic-ai "pydantic-ai-slim[openai]"'


def _build_wheel(project: Path, out_dir: Path) -> Path:
    uv = which("uv")
    if uv is None:
        pytest.skip("uv is required to build integration wheels")
    out_dir.mkdir()
    try:
        result = subprocess.run(
            [uv, "build", "--wheel", "--out-dir", str(out_dir), str(project)],
            capture_output=True,
            text=True,
            timeout=300,
        )
    except subprocess.TimeoutExpired:
        pytest.skip("wheel build timed out")
    assert result.returncode == 0, f"uv build failed:\n{result.stdout}\n{result.stderr}"
    wheels = list(out_dir.glob("*.whl"))
    assert len(wheels) == 1
    return wheels[0]


@pytest.fixture(scope="module")
def built_wheels(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    root = tmp_path_factory.mktemp("pydantic-ai-wheels")
    return {name: _build_wheel(project, root / name) for name, project in _PROJECTS.items()}


def _requires_dist(wheel: Path) -> list[str]:
    with zipfile.ZipFile(wheel) as archive:
        metadata_name = next(name for name in archive.namelist() if name.endswith(".dist-info/METADATA"))
        metadata = Parser().parsestr(archive.read(metadata_name).decode("utf-8"))
    return metadata.get_all("Requires-Dist", [])


@pytest.mark.parametrize("package", ["pydantic-ai", "bub"])
def test_integration_wheels_require_the_first_core_release_with_shared_capture(
    built_wheels: dict[str, Path],
    package: str,
) -> None:
    assert "powercontext[client]>=0.0.3" in _requires_dist(built_wheels[package])


def test_openai_install_command_is_consistent_across_public_guides() -> None:
    for path in (_PYDANTIC_AI_README, *_PYDANTIC_AI_HOW_TOS):
        assert _OPENAI_INSTALL in path.read_text(encoding="utf-8")


def _first_python_example(path: Path) -> str:
    match = re.search(r"```python\n(?P<code>.*?)```", path.read_text(encoding="utf-8"), flags=re.DOTALL)
    assert match is not None
    return match.group("code")


def test_documented_openai_agent_constructs_from_installed_wheels(
    built_wheels: dict[str, Path],
    tmp_path: Path,
) -> None:
    uv = which("uv")
    if uv is None:
        pytest.skip("uv is required to install integration wheels")
    site_packages = tmp_path / "site-packages"
    install = subprocess.run(
        [
            uv,
            "pip",
            "install",
            "--target",
            str(site_packages),
            "--no-deps",
            str(built_wheels["core"]),
            str(built_wheels["pydantic-ai"]),
        ],
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert install.returncode == 0, f"wheel install failed:\n{install.stdout}\n{install.stderr}"

    english_example = _first_python_example(_PYDANTIC_AI_HOW_TOS[0])
    assert english_example == _first_python_example(_PYDANTIC_AI_HOW_TOS[1])
    script = f"""
import sys
from pathlib import Path

site_packages = Path({str(site_packages)!r})
sys.path.insert(0, str(site_packages))

{english_example}

import powercontext.client.capture as capture_module
import powercontext_pydantic_ai as adapter_module

assert Path(capture_module.__file__).resolve().is_relative_to(site_packages)
assert Path(adapter_module.__file__).resolve().is_relative_to(site_packages)
assert agent is not None
"""
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env["OPENAI_API_KEY"] = "docs-smoke-test-key"
    smoke = subprocess.run(
        [sys.executable, "-I", "-c", script],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert smoke.returncode == 0, f"documented example failed:\n{smoke.stdout}\n{smoke.stderr}"
