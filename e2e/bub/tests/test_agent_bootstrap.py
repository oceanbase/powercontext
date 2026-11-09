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

"""Exercise ACP bootstrap with offline packages and a real Python virtual environment."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import zipfile

import pytest

from powercontext_e2e.agent_bootstrap import runner_setup_command


@pytest.mark.parametrize("complete", [True, False])
def test_python_image_bootstrap_avoids_system_install_and_preserves_package_failures(tmp_path, complete):
    if sys.platform == "win32":
        pytest.skip("Harbor task images use Linux bash")
    wheels = tmp_path / "wheels"
    wheels.mkdir()
    packages = [("agent_client_protocol", "1.0.0rc2")]
    if complete:
        packages.append(("uv", "0.10.12"))
    for name, version in packages:
        dist = f"{name}-{version}.dist-info"
        with zipfile.ZipFile(wheels / f"{name}-{version}-py3-none-any.whl", "w") as wheel:
            wheel.writestr(f"{dist}/METADATA", f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n")
            wheel.writestr(f"{dist}/WHEEL", "Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n")
            wheel.writestr(f"{dist}/RECORD", "")
    binaries = tmp_path / "bin"
    binaries.mkdir()
    (binaries / "python3").symlink_to(sys.executable)
    runner, log = tmp_path / "runner", tmp_path / "logs" / "bootstrap.log"
    # A prepared Python image must work even when system package installation is unavailable.
    command = runner_setup_command(str(runner), "echo unexpected-system-install; exit 67", log_path=str(log))
    shell = shutil.which("bash")
    assert shell is not None
    result = subprocess.run(  # noqa: S603 -- checked-in bootstrap and an isolated offline fixture
        [shell, "-c", command],
        env={
            **os.environ,
            "PATH": f"{binaries}{os.pathsep}{os.environ['PATH']}",
            "PIP_NO_INDEX": "1",
            "PIP_FIND_LINKS": str(wheels),
        },
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    evidence = log.read_text()
    assert "unexpected-system-install" not in evidence
    if complete:
        assert result.returncode == 0, result.stdout + result.stderr
        subprocess.run(  # noqa: S603 -- interpreter just created in the isolated fixture
            [
                str(runner / "bin/python"),
                "-c",
                "from importlib.metadata import version; "
                "assert version('agent-client-protocol') == '1.0.0rc2'; assert version('uv') == '0.10.12'",
            ],
            check=True,
        )
        assert "ACP bootstrap: ready" in evidence
    else:
        assert result.returncode != 0
        assert "uv==0.10.12" in evidence
        assert "ACP bootstrap: ready" not in evidence
