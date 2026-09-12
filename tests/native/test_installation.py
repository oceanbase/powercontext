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

"""Acceptance through the installed CLI and a real HTTP Server, without model services."""

from __future__ import annotations

import email
import hashlib
import os
import shutil
import socket
import subprocess
import sys
import time
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest
from dotenv import dotenv_values

ROOT = Path(__file__).parents[2]
WHEEL = os.environ.get("POWERCONTEXT_INSTALL_WHEEL")
pytestmark = pytest.mark.skipif(not WHEEL, reason="set POWERCONTEXT_INSTALL_WHEEL to run installation acceptance")


def run(
    command: list[str], root: Path, env: dict[str, str], name: str, *, stdin: str = "", success: bool = True
) -> str:
    with (root / f"{name}.log").open("w", encoding="utf-8") as log:
        result = subprocess.run(
            command,
            cwd=root,
            env=env,
            input=stdin,
            text=True,
            stdout=log,
            stderr=subprocess.STDOUT,
            timeout=300,
            check=False,
        )
    output = (root / f"{name}.log").read_text(encoding="utf-8", errors="replace")
    assert (result.returncode == 0) == success, output
    return output


def request(url: str, token: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    with httpx.Client(trust_env=False, timeout=5, headers=headers) as client:
        response = client.get(url) if body is None else client.post(url, json=body)
        response.raise_for_status()
        return response.json()


@contextmanager
def running_server(cli: str, root: Path, env: dict[str, str], token: str, name: str) -> Iterator[str]:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    log_path = root / f"{name}.log"
    with log_path.open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            [cli, "server", "run", "--env-file", str(root / ".env"), "--host", "127.0.0.1", "--port", str(port)],
            cwd=root,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        try:
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline and process.poll() is None:
                try:
                    request(url + "/health/ready", token)
                    break
                except (httpx.TransportError, httpx.HTTPStatusError):
                    time.sleep(0.2)
            else:
                pytest.fail(log_path.read_text(encoding="utf-8", errors="replace"))
            yield url
        finally:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


@pytest.mark.parametrize("environment", ["bootstrap-global", "bootstrap-cn", "existing"])
def test_install_configure_remember_and_reinstall(tmp_path: Path, environment: str) -> None:
    assert WHEEL is not None
    wheel = Path(WHEEL).resolve()
    with zipfile.ZipFile(wheel) as archive:
        metadata = next(name for name in archive.namelist() if name.endswith(".dist-info/METADATA"))
        version = email.message_from_bytes(archive.read(metadata))["Version"]
    # The direct URL and checksum constraint make this a test of the current build.
    constraints = tmp_path / "constraints.txt"
    checksum = hashlib.sha256(wheel.read_bytes()).hexdigest()
    constraints.write_text(f"powercontext @ {wheel.as_uri()}#sha256={checksum}\n", encoding="utf-8")
    uv_config = tmp_path / "uv.toml"
    uv_config.write_text("", encoding="utf-8")
    home_dir = tmp_path / "home"
    home_dir.mkdir()
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("UV_", "PIP_", "PYTHON", "POWERCONTEXT_")) and key != "VIRTUAL_ENV"
    }
    env.update(
        HOME=str(home_dir),
        USERPROFILE=str(home_dir),
        UV_TOOL_DIR=str(tmp_path / "tools"),
        UV_TOOL_BIN_DIR=str(tmp_path / "bin"),
        UV_CACHE_DIR=str(tmp_path / "cache"),
        UV_PYTHON_INSTALL_DIR=str(tmp_path / "python"),
        UV_NO_CONFIG="1",
        UV_CONSTRAINT=str(constraints),
        UV_NO_PROGRESS="1",
        POWERCONTEXT_HOME=str(tmp_path / "state"),
    )
    if sys.platform == "win32":
        # Python inherits PowerShell 7 module paths, which Windows PowerShell cannot load.
        env.pop("PSMODULEPATH", None)
        shell = shutil.which("powershell.exe")
        assert shell is not None
        installer = [
            shell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(ROOT / "website/public/install.ps1"),
        ]
        cli = str(tmp_path / "bin/powercontext.exe")
        system_path = os.pathsep.join([str(Path(os.environ["SYSTEMROOT"]) / "System32"), os.environ["SYSTEMROOT"]])
    else:
        installer = ["/bin/bash", str(ROOT / "website/public/install.sh")]
        cli = str(tmp_path / "bin/powercontext")
        system_path = os.defpath
    installer += ["--no-hosts", "--version", str(version)]
    if environment.startswith("bootstrap"):
        if sys.platform == "win32":
            env["PATH"] = system_path
            env["UV_PYTHON_NO_REGISTRY"] = "1"
        else:
            # Keep OS utilities available while removing Python and uv from discovery.
            utilities = tmp_path / "utilities"
            utilities.mkdir()
            for directory in system_path.split(os.pathsep):
                for executable in Path(directory).iterdir():
                    if executable.name.startswith(("python", "pypy", "uv")) or not executable.is_file():
                        continue
                    target = utilities / executable.name
                    if not target.exists():
                        target.symlink_to(executable)
            env["PATH"] = str(utilities)
        env.update(TZ="Asia/Shanghai", LC_ALL="C", LANG="en_US.UTF-8")
        if environment == "bootstrap-global":
            env["POWERCONTEXT_INSTALL_REGION"] = "cn"
            installer += ["--region", "global"]
            region = "global"
        else:
            region = "cn"
    else:
        env.pop("UV_NO_CONFIG")
        uv_config.write_text('[[index]]\nurl = "https://pypi.org/simple"\ndefault = true\n', encoding="utf-8")
        env.update(
            TZ="UTC",
            LC_ALL="zh_CN.UTF-8",
            UV_CONFIG_FILE=str(uv_config),
            UV_PYTHON_DOWNLOADS="never",
            UV_PYTHON_INSTALL_MIRROR="https://127.0.0.1:9/unavailable",
            POWERCONTEXT_UV_INSTALLER_URL="https://127.0.0.1:9/unavailable",
        )
        region = "cn"
    output = run(installer, tmp_path, env, "install")
    assert f"Download region: {region}" in output
    if environment == "existing":
        # An explicitly selected source must not silently fall back to a public index.
        run([*installer, "--index-url", "https://127.0.0.1:9/simple"], tmp_path, env, "explicit-index", success=False)
    assert run([cli, "--version"], tmp_path, env, "version").strip() == version
    run([cli, "config", "init", "--template", "--output", ".env"], tmp_path, env, "configure", stdin="\n")
    config = (tmp_path / ".env").read_bytes()
    run([cli, "config", "validate", "--env-file", ".env"], tmp_path, env, "validate")
    token = dotenv_values(tmp_path / ".env").get("POWERCONTEXT_SERVER_AUTH_TOKEN") or ""
    text = "Installation acceptance preserves the release decision."
    with running_server(cli, tmp_path, env, token, "server") as url:
        scope = request(url + "/v1/scopes/default", token)["scope_id"]
        request(url + "/v1/memory/remember", token, {"scope_id": scope, "kind": "decision", "text": text})
        query = {"scope_id": scope, "query": "release decision", "mode": "fts"}
        found = request(url + "/v1/memory/search", token, query)
        assert text in [hit["text"] for hit in found["hits"]]
    env.update(
        UV_OFFLINE="1", UV_PYTHON_DOWNLOADS="never", POWERCONTEXT_UV_INSTALLER_URL="https://127.0.0.1:9/unavailable"
    )
    run(installer, tmp_path, env, "reinstall-offline")
    assert (tmp_path / ".env").read_bytes() == config
    with running_server(cli, tmp_path, env, token, "server-restarted") as url:
        found = request(url + "/v1/memory/search", token, query)
        assert text in [hit["text"] for hit in found["hits"]]
