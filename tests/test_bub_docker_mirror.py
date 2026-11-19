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

"""Exercise the nested Docker entrypoint's optional registry mirror contract."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="The container entrypoint requires POSIX sh")


@pytest.mark.parametrize("mirror", ["", "http://registry.example.test:5000", "https://registry.example.test"])
def test_nested_docker_mirror_preserves_workload_arguments(tmp_path: Path, mirror: str) -> None:
    commands = tmp_path / "bin"
    commands.mkdir()
    state = tmp_path / "daemon.json"
    executable = (
        f"#!{sys.executable}\n"
        + r"""
import json
import os
import sys
from pathlib import Path

name = Path(sys.argv[0]).name
state = Path(os.environ["DOCKER_MIRROR_TEST_STATE"])
if name == "dockerd":
    state.write_text(json.dumps(sys.argv[1:]))
elif name == "docker":
    raise SystemExit(0 if state.exists() else 1)
elif name == "powercontext-e2e":
    print(json.dumps({"arguments": sys.argv[1:], "docker": json.loads(state.read_text())}))
"""
    )
    for name in ("dockerd", "docker", "socat", "powercontext-e2e"):
        command = commands / name
        command.write_text(executable)
        command.chmod(0o755)
    environment = {
        **os.environ,
        "PATH": f"{commands}{os.pathsep}{os.defpath}",
        "DOCKER_MIRROR_TEST_STATE": str(state),
        "POWERCONTEXT_E2E_DOCKER_REGISTRY_MIRROR": mirror,
    }
    arguments = ["acceptance", "--manifest", "fixture path/tasks"]
    result = subprocess.run(
        ["/bin/sh", str(ROOT / "e2e/bub/container-entrypoint.sh"), *arguments],
        env=environment,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    observed = json.loads(result.stdout)
    assert observed["arguments"] == arguments
    daemon = observed["docker"]
    if mirror:
        assert daemon[daemon.index("--registry-mirror") + 1] == mirror
    else:
        assert "--registry-mirror" not in daemon
    if mirror.startswith("http://"):
        assert daemon[daemon.index("--insecure-registry") + 1] == "registry.example.test:5000"
    else:
        assert "--insecure-registry" not in daemon
