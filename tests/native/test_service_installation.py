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

"""Installed-script acceptance on disposable native macOS/Linux user-service runners."""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path

import httpx
import pytest

from powercontext.cli.config import write_environment

ROOT = Path(__file__).resolve().parents[2]
WHEEL = os.environ.get("POWERCONTEXT_INSTALL_WHEEL")
pytestmark = [
    pytest.mark.native_service,
    pytest.mark.skipif(
        os.environ.get("POWERCONTEXT_RUN_NATIVE_SERVICE_TESTS") != "1"
        or not WHEEL
        or sys.platform not in {"linux", "darwin"},
        reason="requires a release-shaped wheel and a disposable native user-service runner",
    ),
]


def test_installer_service_reconciles_configuration_and_preserves_memory_after_upgrade(tmp_path: Path) -> None:
    assert WHEEL is not None
    uv = shutil.which("uv")
    assert uv is not None
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("POWERCONTEXT_", "UV_", "PIP_", "PYTHON")) and key != "VIRTUAL_ENV"
    }
    constraints = tmp_path / "constraints.txt"
    constraints.write_text(f"powercontext @ {Path(WHEEL).resolve().as_uri()}\n")
    environment.update(
        UV_CACHE_DIR=os.environ.get("UV_CACHE_DIR", str(Path.home() / ".cache" / "uv")),
        UV_NO_CONFIG="1",
        UV_OFFLINE="1",
        UV_PYTHON_DOWNLOADS="never",
        UV_CONSTRAINT=str(constraints),
        UV_TOOL_DIR=str(tmp_path / "tools"),
        UV_TOOL_BIN_DIR=str(tmp_path / "bin"),
    )
    cli = str(tmp_path / "bin" / "powercontext")
    configuration = tmp_path / "personal server.env"
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    token = "native-installation-token"  # noqa: S105
    content = (
        f"POWERCONTEXT_HOME='{tmp_path / 'data'}'\n"
        "POWERCONTEXT_SERVER_HTTP_HOST=127.0.0.1\n"
        f"POWERCONTEXT_SERVER_HTTP_PORT={port}\n"
        "POWERCONTEXT_SERVER_ACCESS_MODE=enforced\n"
        f"POWERCONTEXT_SERVER_AUTH_TOKEN={token}\n"
    )
    write_environment(configuration, content, backup=False)
    installer = ["/bin/bash", str(ROOT / "website/public/install.sh"), "--region", "global"]

    def run(command: list[str], name: str) -> str:
        result = subprocess.run(command, env=environment, capture_output=True, text=True, timeout=300)
        output = result.stdout + result.stderr
        (tmp_path / f"{name}.log").write_text(output)
        assert result.returncode == 0, output
        return result.stdout

    # This runner is disposable. Never replace an existing personal service.
    before = subprocess.run(
        [str(Path(sys.executable).with_name("powercontext")), "service", "status", "--json"],
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert json.loads(before.stdout)["registration"] == "not_installed", before.stdout + before.stderr
    try:
        output = run([*installer, "--version", "1.2.0", "--service", "--env-file", str(configuration)], "install")
        assert "Personal service verified with:" in output
        status = json.loads(run([cli, "service", "status", "--json"], "status"))
        assert status["manager"] == "active"
        assert status["server_liveness"] == "live"
        endpoint = f"http://127.0.0.1:{port}"
        with httpx.Client(base_url=endpoint, headers={"Authorization": f"Bearer {token}"}, trust_env=False) as client:
            scope = client.get("/v1/scopes/default").json()["scope_id"]
            response = client.post(
                "/v1/memory/remember",
                json={"scope_id": scope, "kind": "decision", "text": "Keep native service memory."},
            )
            response.raise_for_status()

        content += "POWERCONTEXT_CODEX_SCOPE_ID=scope_example\n"
        write_environment(configuration, content, backup=False)
        # Stale status intentionally exits nonzero; inspect its public JSON separately.
        stale = subprocess.run(
            [cli, "service", "status", "--json"], env=environment, capture_output=True, text=True, timeout=30
        )
        assert stale.returncode == 1
        assert json.loads(stale.stdout)["definition"] == "stale"
        run([*installer, "--version", "1.2.0", "--service", "--env-file", str(configuration)], "reconcile")

        run([cli, "service", "uninstall"], "stop-before-upgrade")
        upgraded = tmp_path / "upgraded"
        build = subprocess.run(
            [uv, "build", "--offline", "--wheel", "--out-dir", str(upgraded)],
            cwd=ROOT,
            env={**environment, "SETUPTOOLS_SCM_PRETEND_VERSION": "1.2.1"},
            capture_output=True,
            text=True,
            timeout=300,
        )
        assert build.returncode == 0, build.stdout + build.stderr
        constraints.write_text(f"powercontext @ {next(upgraded.glob('*.whl')).as_uri()}\n")
        run([*installer, "--version", "1.2.1", "--service", "--env-file", str(configuration)], "upgrade")
        assert run([cli, "--version"], "upgraded-version").strip() == "1.2.1"
        assert configuration.read_text() == content
        with httpx.Client(base_url=endpoint, headers={"Authorization": f"Bearer {token}"}, trust_env=False) as client:
            response = client.post(
                "/v1/memory/search", json={"scope_id": scope, "query": "native service memory", "mode": "fts"}
            )
            response.raise_for_status()
            assert any(hit["text"] == "Keep native service memory." for hit in response.json()["hits"])
    finally:
        if Path(cli).exists():
            run([cli, "service", "uninstall"], "cleanup")
