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
import os
import shutil
import socket
import subprocess
import sys
import time
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
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


def reinstall_offline(
    installer: list[str], root: Path, env: dict[str, str], version: str, *, interactive: bool
) -> None:
    if not interactive:
        run(installer, root, env, "reinstall-offline")
        return
    # A piped default install succeeds without implicitly opening Agent selection.
    controller = """import errno, os, pty, sys
pid, terminal = pty.fork()
if pid == 0:
    os.execv('/bin/bash', ['/bin/bash', '-o', 'pipefail', '-c',
        'cat "$1" | /bin/bash -s -- --version "$2"', 'installer-pipeline', *sys.argv[1:]])
try:
    os.write(terminal, b'\\n')
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
    output = run(
        [sys.executable, "-c", controller, str(ROOT / "website/public/install.sh"), str(version)],
        root,
        env,
        "reinstall-offline",
    )
    assert "Runtime installed:" in output
    assert "Select hosts" not in output


def utilities_without_python_or_uv(root: Path, system_path: str) -> Path:
    """Keep OS utilities available while removing Python and uv from PATH discovery."""
    utilities = root / "utilities"
    utilities.mkdir()
    for directory in system_path.split(os.pathsep):
        for executable in Path(directory).iterdir():
            if executable.name.startswith(("python", "pypy", "uv")) or not executable.is_file():
                continue
            target = utilities / executable.name
            if not target.exists():
                target.symlink_to(executable)
    return utilities


@pytest.mark.parametrize("environment", ["bootstrap-global", "bootstrap-cn", "existing"])
def test_install_configure_remember_and_reinstall(tmp_path: Path, environment: str) -> None:
    assert WHEEL is not None
    wheel = Path(WHEEL).resolve()
    with zipfile.ZipFile(wheel) as archive:
        metadata = next(name for name in archive.namelist() if name.endswith(".dist-info/METADATA"))
        version = email.message_from_bytes(archive.read(metadata))["Version"]
    # The direct URL selects this build; a file URL hash fragment is not an integrity check.
    constraints = tmp_path / "constraints.txt"
    constraints.write_text(f"powercontext @ {wheel.as_uri()}\n", encoding="utf-8")
    home_dir = tmp_path / "home with spaces 测试"
    home_dir.mkdir()
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("UV_", "PIP_", "PYTHON", "POWERCONTEXT_")) and key != "VIRTUAL_ENV"
    }
    env.update(
        HOME=str(home_dir),
        USERPROFILE=str(home_dir),
        # These subprocesses write UTF-8 log files, rather than a Windows console.
        PYTHONIOENCODING="utf-8",
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
        if environment == "bootstrap-cn":
            # Exercise the documented downloaded-scriptblock entry point as well as -File.
            wrapper = tmp_path / "downloaded-installer.ps1"
            script_path = str(ROOT / "website/public/install.ps1").replace("'", "''")
            wrapper.write_text(
                f"& ([scriptblock]::Create([IO.File]::ReadAllText('{script_path}'))) @args\n", encoding="utf-8"
            )
            installer[-1] = str(wrapper)
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
            env["PATH"] = str(utilities_without_python_or_uv(tmp_path, system_path))
        env.update(TZ="Asia/Shanghai", LC_ALL="C", LANG="en_US.UTF-8")
        if environment == "bootstrap-global":
            env["POWERCONTEXT_INSTALL_REGION"] = "cn"
            installer += ["--region", "global"]
    else:
        env.pop("UV_NO_CONFIG")
        uv_config = tmp_path / "uv.toml"
        uv_config.write_text('[[index]]\nurl = "https://pypi.org/simple"\ndefault = true\n', encoding="utf-8")
        env.update(
            TZ="UTC",
            LC_ALL="zh_CN.UTF-8",
            UV_CONFIG_FILE=str(uv_config),
            UV_PYTHON_DOWNLOADS="never",
            UV_PYTHON_INSTALL_MIRROR="https://127.0.0.1:9/unavailable",
            POWERCONTEXT_UV_INSTALLER_URL="https://127.0.0.1:9/unavailable",
        )
    run(installer, tmp_path, env, "install")
    if environment == "existing":
        # A fresh tool/cache forces resolution; exact reinstallation may legitimately reuse the existing tool.
        failed_source_env = {
            **env,
            "UV_TOOL_DIR": str(tmp_path / "failed-source-tools"),
            "UV_TOOL_BIN_DIR": str(tmp_path / "failed-source-bin"),
            "UV_CACHE_DIR": str(tmp_path / "failed-source-cache"),
        }
        run(
            [*installer, "--index-url", "https://127.0.0.1:9/simple"],
            tmp_path,
            failed_source_env,
            "explicit-index",
            success=False,
        )
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
    reinstall_offline(
        installer, tmp_path, env, str(version), interactive=environment == "existing" and sys.platform != "win32"
    )
    assert (tmp_path / ".env").read_bytes() == config
    with running_server(cli, tmp_path, env, token, "server-restarted") as url:
        found = request(url + "/v1/memory/search", token, query)
        assert text in [hit["text"] for hit in found["hits"]]


def test_latest_exact_versions_and_client_profile(tmp_path: Path) -> None:
    """Resolve real wheels from a controlled index with stable and prerelease candidates."""
    assert WHEEL is not None
    uv = shutil.which("uv")
    assert uv is not None
    repository = tmp_path / "index"
    packages = repository / "powercontext"
    packages.mkdir(parents=True)
    shutil.copyfile(WHEEL, packages / Path(WHEEL).name)
    for version in ("1.2.1", "1.2.2rc1"):
        run(
            [uv, "build", "--project", str(ROOT), "--wheel", "--out-dir", str(packages)],
            tmp_path,
            {**os.environ, "SETUPTOOLS_SCM_PRETEND_VERSION": version},
            f"build-{version}",
        )
    (packages / "index.html").write_text(
        "\n".join(f'<a href="{wheel.name}">{wheel.name}</a>' for wheel in sorted(packages.glob("*.whl"))),
        encoding="utf-8",
    )
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("UV_", "PIP_", "PYTHON", "POWERCONTEXT_")) and key != "VIRTUAL_ENV"
    }
    env.update(
        PYTHONIOENCODING="utf-8",
        UV_NO_CONFIG="1",
        UV_TOOL_DIR=str(tmp_path / "tools"),
        UV_TOOL_BIN_DIR=str(tmp_path / "bin with spaces"),
        UV_CACHE_DIR=str(tmp_path / "cache"),
        UV_NO_PROGRESS="1",
        POWERCONTEXT_HOME=str(tmp_path / "state"),
    )
    if sys.platform == "win32":
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
        cli = str(tmp_path / "bin with spaces/powercontext.exe")
        python = str(tmp_path / "tools/powercontext/Scripts/python.exe")
    else:
        installer = ["/bin/bash", str(ROOT / "website/public/install.sh")]
        cli = str(tmp_path / "bin with spaces/powercontext")
        python = str(tmp_path / "tools/powercontext/bin/python")
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(SimpleHTTPRequestHandler, directory=str(repository)))
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    env["UV_INDEX"] = f"http://127.0.0.1:{server.server_port}"
    try:
        # Contradictory host intent must fail before any tool installation.
        run([*installer, "--host", "codex", "--no-hosts"], tmp_path, env, "host-choice-conflict", success=False)
        assert not Path(cli).exists()
        client_install = [*installer, "--no-hosts", "--profile", "client"]
        run([*client_install, "--version", "1.2.0"], tmp_path, env, "install-old")
        assert run([cli, "--version"], tmp_path, env, "old-version").strip() == "1.2.0"
        run(client_install, tmp_path, env, "install-latest")
        assert run([cli, "--version"], tmp_path, env, "latest-version").strip() == "1.2.1"
        if shutil.which("git"):
            output = run(
                [*installer, "--profile", "client", "--host", "unknown-host"],
                tmp_path,
                env,
                "integration-failure",
                success=False,
            )
            assert "Runtime installed" in output
            assert "powercontext-v1.2.1" in output
            assert run([cli, "--version"], tmp_path, env, "retained-runtime").strip() == "1.2.1"
        run(
            [python, "-c", "from importlib.util import find_spec; assert find_spec('fastapi') is None"],
            tmp_path,
            env,
            "client-without-server",
        )
        assert not (tmp_path / "state").exists()
        run([*client_install, "--version", "1.2.2rc1"], tmp_path, env, "install-prerelease")
        assert run([cli, "--version"], tmp_path, env, "prerelease-version").strip() == "1.2.2rc1"
        run([*client_install, "--version", "99.0.0"], tmp_path, env, "missing-version", success=False)
        assert run([cli, "--version"], tmp_path, env, "preserved-version").strip() == "1.2.2rc1"
        run(client_install, tmp_path, env, "stable-after-prerelease")
        assert run([cli, "--version"], tmp_path, env, "restored-stable-version").strip() == "1.2.1"
        run([*installer, "--no-hosts", "--version", "1.2.0"], tmp_path, env, "switch-to-local")
        run([python, "-c", "import fastapi"], tmp_path, env, "local-includes-server")
        run([*client_install, "--version", "1.2.0"], tmp_path, env, "switch-to-client")
        run(
            [python, "-c", "from importlib.util import find_spec; assert find_spec('fastapi') is None"],
            tmp_path,
            env,
            "server-dependencies-removed",
        )
    finally:
        server.shutdown()
        worker.join(timeout=5)
        server.server_close()
