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

"""The public MiniMax doctor must enforce HTTP consent before sending credentials."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]


@pytest.fixture
def diagnostic_http_server():
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - match the HTTP handler interface.
            pass

        def do_GET(self):
            authorized = self.headers.get("Authorization") == "Bearer synthetic-minimax-token"
            requests.append((self.path, authorized))
            if self.path == "/v1/server-info":
                if not authorized:
                    self.send_error(401)
                    return
                payload = {
                    "schema_version": {"major": 1, "minor": 0},
                    "product": "powercontext",
                    "server_id": "diagnostic-fixture",
                    "package_version": "1.4.1",
                    "api_contract_version": {"major": 1, "minor": 0},
                    "feature_contracts": {},
                }
            elif self.path == "/health/live":
                payload = {"status": "ok"}
            elif self.path == "/health/ready":
                payload = {"status": "ready", "checks": {}}
            else:
                self.send_error(404)
                return
            content = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://minimax-diagnostics.invalid:{server.server_port}", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()


@pytest.mark.parametrize("private_enabled", [False, True], ids=["plugin", "private"])
@pytest.mark.parametrize(
    "override",
    ["saved", "other-endpoint", "client-false", "client-invalid", "minimax-false", "minimax-invalid"],
)
def test_doctor_enforces_consent_before_any_http_request(tmp_path, diagnostic_http_server, private_enabled, override):
    endpoint, requests = diagnostic_http_server
    profile = tmp_path / "minimax"
    plugin = profile / "plugins/powercontext"
    shutil.copytree(ROOT / "integrations/minimax/plugins/powercontext", plugin)
    entry = {
        "type": "streamable-http",
        "url": endpoint + "/mcp",
        "headers": {"Authorization": "Bearer synthetic-minimax-token"},
    }
    (profile / "mcp.json").write_text(
        json.dumps({"mcpServers": {"powercontext": {**entry, "enabled": private_enabled}}})
    )
    bundle_path = plugin / "powercontext.mcp.json"
    bundle = json.loads(bundle_path.read_bytes())
    bundle["mcpServers"]["powercontext"] = entry
    bundle_path.write_text(json.dumps(bundle))
    settings = tmp_path / "clients.json"
    settings.write_text(
        json.dumps({
            "version": 1,
            "hosts": {
                "minimax": {
                    "server_url": "http://previous.invalid:8000" if override == "other-endpoint" else endpoint,
                    "allow_insecure_http": True,
                    "minimax_data_dir": str(profile),
                }
            },
        })
    )
    site = tmp_path / "python-site"
    site.mkdir()
    # Keep a non-loopback URL for policy checks while routing all test HTTP to this listener.
    (site / "sitecustomize.py").write_text(
        "import socket\n"
        "original = socket.getaddrinfo\n"
        "def mapped(host, *args, **kwargs):\n"
        "    if host in ('minimax-diagnostics.invalid', b'minimax-diagnostics.invalid'):\n"
        "        host = '127.0.0.1'\n"
        "    return original(host, *args, **kwargs)\n"
        "socket.getaddrinfo = mapped\n"
    )
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("POWERCONTEXT_", "MINIMAX_", "MAVIS_")) and not key.lower().endswith("_proxy")
    }
    environment.update(
        POWERCONTEXT_HOME=str(tmp_path / "pc"),
        POWERCONTEXT_CLIENT_CONFIG_FILE=str(settings),
        MINIMAX_DATA_DIR=str(profile),
        PYTHONPATH=os.pathsep.join((str(site), str(ROOT / "src"))),
        PATH="",
        NO_PROXY="*",
    )
    if "-" in override and override != "other-endpoint":
        host, value = override.split("-", 1)
        environment[f"POWERCONTEXT_{host.upper()}_ALLOW_INSECURE_HTTP"] = value
    before = {
        path: path.read_bytes() for path in [settings, *plugin.rglob("*"), profile / "mcp.json"] if path.is_file()
    }
    result = subprocess.run(
        [sys.executable, "-c", "from powercontext.cli.app import main; main()", "doctor", "minimax", "--json"],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    checks = json.loads(result.stdout)["hosts"]["minimax"]["checks"]
    if override == "saved":
        assert checks["server_access"]["status"] == "ok", result.stdout
        assert requests == [("/health/live", False), ("/health/ready", False), ("/v1/server-info", True)]
    else:
        assert result.returncode != 0, result.stdout
        assert requests == []
        assert checks.get("server_access", {}).get("status") != "ok"
    assert {path: path.read_bytes() for path in before} == before
    assert "synthetic-minimax-token" not in result.stdout + result.stderr
