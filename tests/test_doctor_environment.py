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

"""Explicit-file diagnostics reach the configured HTTP endpoint without shell evaluation."""

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest
from typer.testing import CliRunner

from powercontext.cli.app import create_cli
from powercontext.cli.system import doctor_app


@pytest.fixture
def health_server():
    class Handler(BaseHTTPRequestHandler):
        readiness = "ready"

        def log_message(self, format, *args):  # noqa: A002
            pass

        def do_GET(self):
            payload = (
                {"status": "ok"}
                if self.path == "/health/live"
                else {"status": self.readiness, "checks": {"database": "ready"}}
            )
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(payload).encode())

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield server.server_port, Handler
    finally:
        server.shutdown()
        server.server_close()
        worker.join()


@pytest.mark.parametrize("server_file", [True, False])
@pytest.mark.parametrize("readiness", ["ready", "degraded"])
@pytest.mark.parametrize("lowercase_names", [False, True])
def test_doctor_uses_selected_file_and_preserves_shell(
    tmp_path, monkeypatch, health_server, server_file, readiness, lowercase_names
):
    port, handler = health_server
    handler.readiness = readiness
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("POWERCONTEXT_CLIENT_SERVER_URL", "http://127.0.0.1:1")
    monkeypatch.setenv("POWERCONTEXT_SERVER_HTTP_PORT", "1")
    monkeypatch.setenv("POWERCONTEXT_CLIENT_API_TOKEN", "caller-secret")
    marker = tmp_path / "executed"
    environment = tmp_path / "selected.env"
    endpoint = f"http://127.0.0.1:{port}"
    content = (
        f"POWERCONTEXT_SERVER_HTTP_HOST=127.0.0.1\nPOWERCONTEXT_SERVER_HTTP_PORT={port}\n"
        "POWERCONTEXT_CLIENT_SERVER_URL=http://127.0.0.1:1\n"
        if server_file
        else f"POWERCONTEXT_CLIENT_SERVER_URL={endpoint}\n"
    )
    if lowercase_names:
        content = content.lower()
    environment.write_text(content + f"POWERCONTEXT_CLIENT_API_TOKEN='file-secret'\nCUSTOM='$(touch {marker})'\n")
    result = CliRunner().invoke(create_cli([doctor_app]), ["doctor", "--env-file", str(environment), "--json"])

    assert result.exit_code == (0 if readiness == "ready" else 1), result.output
    response = json.loads(result.output)
    assert response["checks"]["server_readiness"]["detail"] == f"{endpoint} status={readiness}"
    assert "file-secret" not in result.output
    assert not marker.exists()
    assert os.environ["POWERCONTEXT_CLIENT_API_TOKEN"] == "caller-secret"  # noqa: S105
    assert os.environ["POWERCONTEXT_SERVER_HTTP_PORT"] == "1"


def test_doctor_explicit_url_overrides_file(tmp_path, health_server):
    port, _handler = health_server
    environment = tmp_path / "selected.env"
    environment.write_text("POWERCONTEXT_SERVER_HTTP_PORT=1\n")
    endpoint = f"http://127.0.0.1:{port}"
    result = CliRunner().invoke(
        create_cli([doctor_app]), ["doctor", "--env-file", str(environment), "--server-url", endpoint, "--json"]
    )
    assert result.exit_code == 0, result.output
    assert endpoint in json.loads(result.output)["checks"]["server_readiness"]["detail"]


def test_doctor_rejects_missing_environment_file(tmp_path):
    result = CliRunner().invoke(create_cli([doctor_app]), ["doctor", "--env-file", str(tmp_path / "missing.env")])
    assert result.exit_code == 2
    assert "Cannot diagnose the environment file" in result.output


def test_doctor_rejects_file_option_for_integration_diagnostics(tmp_path):
    environment = tmp_path / "selected.env"
    environment.write_text("POWERCONTEXT_SERVER_HTTP_PORT=8000\n")
    result = CliRunner().invoke(create_cli([doctor_app]), ["doctor", "--env-file", str(environment), "codex"])
    assert result.exit_code == 2
    assert "without a subcommand" in result.output
