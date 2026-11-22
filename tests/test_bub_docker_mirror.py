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

"""Exercise the nested Docker entrypoint's network and workload contracts."""

import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="The container entrypoint requires POSIX sh")


def _run_entrypoint(
    tmp_path: Path, *, mirror: str = "", mtu: int | None = 1450, route: str = "ipv4"
) -> tuple[subprocess.CompletedProcess[str], Path]:
    commands = tmp_path / "bin"
    commands.mkdir()
    state = tmp_path / "daemon.json"
    network = tmp_path / "net"
    interface = network / "fixture0"
    interface.mkdir(parents=True)
    if mtu is not None:
        (interface / "mtu").write_text(f"{mtu}\n")
    source = (ROOT / "e2e/bub/container-entrypoint.sh").read_text()
    # Map only Linux sysfs reads so the real shell entrypoint also runs on macOS.
    sysfs_mtu = '"/sys/class/net/$parent_interface/mtu"'
    assert source.count(sysfs_mtu) == 2
    entrypoint = tmp_path / "entrypoint.sh"
    entrypoint.write_text(source.replace(sysfs_mtu, shlex.quote(str(network)) + '/"$parent_interface/mtu"'))
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
elif name == "ip":
    route = os.environ["DOCKER_MIRROR_TEST_ROUTE"]
    if route == "ipv4" and "-6" not in sys.argv:
        print("default via 192.0.2.1 dev fixture0")
    elif route == "ipv6" and "-6" in sys.argv:
        print("default via 2001:db8::1 dev fixture0")
elif name == "docker":
    raise SystemExit(0 if state.exists() else 1)
elif name == "powercontext-e2e":
    print(json.dumps({"arguments": sys.argv[1:], "docker": json.loads(state.read_text())}))
"""
    )
    for name in ("dockerd", "docker", "socat", "powercontext-e2e", "ip"):
        if name == "ip" and route == "unavailable":
            continue
        command = commands / name
        command.write_text(executable)
        command.chmod(0o755)
    # Do not inherit a host ip binary in the missing-command control.
    for name in ("awk", "sleep", "sed"):
        binary = shutil.which(name)
        assert binary is not None
        (commands / name).symlink_to(binary)
    environment = {
        **os.environ,
        "PATH": str(commands),
        "DOCKER_MIRROR_TEST_STATE": str(state),
        "DOCKER_MIRROR_TEST_ROUTE": route,
        "POWERCONTEXT_E2E_DOCKER_REGISTRY_MIRROR": mirror,
    }
    arguments = ["acceptance", "--manifest", "fixture path/tasks"]
    result = subprocess.run(
        ["/bin/sh", str(entrypoint), *arguments],
        env=environment,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    return result, state


@pytest.mark.parametrize(
    ("mirror", "mtu"),
    [("", 1400), ("http://registry.example.test:5000", 1450), ("https://registry.example.test", 1420)],
)
def test_nested_docker_mirror_preserves_workload_arguments(tmp_path: Path, mirror: str, mtu: int) -> None:
    result, _state = _run_entrypoint(tmp_path, mirror=mirror, mtu=mtu)
    assert result.returncode == 0, result.stderr
    observed = json.loads(result.stdout)
    assert observed["arguments"] == ["acceptance", "--manifest", "fixture path/tasks"]
    daemon = observed["docker"]
    assert f"--mtu={mtu}" in daemon
    assert f"--default-network-opt=bridge=com.docker.network.driver.mtu={mtu}" in daemon
    if mirror:
        assert daemon[daemon.index("--registry-mirror") + 1] == mirror
    else:
        assert "--registry-mirror" not in daemon
    if mirror.startswith("http://"):
        assert daemon[daemon.index("--insecure-registry") + 1] == "registry.example.test:5000"
    else:
        assert "--insecure-registry" not in daemon


def test_nested_docker_uses_ipv6_default_route_mtu(tmp_path: Path) -> None:
    result, _state = _run_entrypoint(tmp_path, route="ipv6", mtu=1380)
    assert result.returncode == 0, result.stderr
    daemon = json.loads(result.stdout)["docker"]
    assert "--mtu=1380" in daemon
    assert "--default-network-opt=bridge=com.docker.network.driver.mtu=1380" in daemon


@pytest.mark.parametrize(("route", "mtu"), [("unavailable", 1450), ("missing", 1450), ("ipv4", None)])
def test_nested_docker_does_not_start_without_parent_mtu(tmp_path: Path, route: str, mtu: int | None) -> None:
    result, state = _run_entrypoint(tmp_path, route=route, mtu=mtu)
    assert result.returncode != 0
    assert "Cannot determine the parent interface MTU" in result.stderr
    assert not state.exists()
    assert result.stdout == ""
