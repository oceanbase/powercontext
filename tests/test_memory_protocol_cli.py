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

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest
from typer.testing import CliRunner

from powercontext.cli.app import create_cli
from powercontext.cli.system import doctor_app
from powercontext.http._generated.schema import OPENAPI_SCHEMA


@pytest.mark.parametrize(
    "contract, expected",
    [
        (OPENAPI_SCHEMA, "ok"),
        ({"openapi": "3.0.3", "paths": OPENAPI_SCHEMA["paths"]}, "failed"),
        (
            {
                "openapi": "3.0.3",
                "paths": {},
                "x-powercontext-feature-contracts": {"memory.explicit": {"major": 2, "minor": 0}},
            },
            "failed",
        ),
        (None, "degraded"),
    ],
)
def test_cli_distinguishes_service_health_from_memory_compatibility(contract, expected, monkeypatch):
    paths = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            paths.append(self.path)
            payload = {
                "/health/live": {"status": "ok"},
                "/health/ready": {"status": "ready", "checks": {"runtime": "ready"}},
                "/openapi.json": contract,
            }[self.path]
            self.send_response(404 if payload is None else 200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(payload).encode())

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - stdlib signature.
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr("powercontext.cli.system._local_service_diagnostics", lambda url: {})
    try:
        result = CliRunner().invoke(
            create_cli([doctor_app]), ["doctor", "--server-url", f"http://127.0.0.1:{server.server_port}", "--json"]
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
    checks = json.loads(result.output)["checks"]
    assert checks["server_liveness"]["status"] == checks["server_readiness"]["status"] == "ok"
    assert checks["server_memory_protocol"]["status"] == expected
    assert (result.exit_code == 0) is (expected == "ok")
    assert paths == ["/health/live", "/health/ready", "/openapi.json"]


@pytest.mark.parametrize(
    "credential, expected, expected_authorization",
    [
        ("matching", "ok", "Bearer selected-host-token"),
        ("rejected", "degraded", "Bearer rejected-token"),
        ("different-url", "skipped", None),
        ("absent", "skipped", None),
    ],
)
def test_host_discovery_uses_only_its_url_bound_credential(
    credential, expected, expected_authorization, tmp_path, monkeypatch
):
    from types import SimpleNamespace

    from powercontext.cli.authorization import credential_path, write_stored_authorization
    from powercontext.cli.hosts import diagnostic_result, doctor_checks
    from powercontext.cli.system import Diagnostic, DiagnosticStatus

    received = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            authorization = self.headers.get("Authorization")
            received.append((self.path, authorization))
            if self.path == "/openapi.json":
                authorized = authorization == "Bearer selected-host-token"
                self.send_response(200 if authorized else 401)
                payload = OPENAPI_SCHEMA if authorized else {"detail": "unauthorized"}
            else:
                self.send_response(200)
                payload = {"status": "ok"} if self.path == "/health/live" else {"status": "ready", "checks": {}}
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(payload).encode())

        def log_message(self, format, *args):  # noqa: A002 - stdlib signature.
            pass

    monkeypatch.setenv("WORKBUDDY_HOME", str(tmp_path / "workbuddy"))
    monkeypatch.setenv("POWERCONTEXT_CLIENT_API_TOKEN", "unrelated-global-token")
    monkeypatch.setattr("powercontext.cli.system._local_service_diagnostics", lambda url: {})
    monkeypatch.setattr(
        "powercontext.cli.installation.installers",
        lambda: {
            "workbuddy": lambda: SimpleNamespace(verify=lambda: {"hooks": Diagnostic(DiagnosticStatus.OK, "installed")})
        },
    )
    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        endpoint = f"http://127.0.0.1:{server.server_port}"
        monkeypatch.setattr(
            "powercontext.cli.native_transport.resolve_host_transport", lambda host, **kw: (endpoint, False)
        )
        if credential != "absent":
            write_stored_authorization(
                credential_path("workbuddy"),
                server_url=endpoint if credential != "different-url" else "http://127.0.0.1:1",
                value="rejected-token" if credential == "rejected" else "selected-host-token",
            )
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            report = diagnostic_result(doctor_checks("workbuddy"))
        finally:
            server.shutdown()
            thread.join()
    assert report["checks"]["server_memory_protocol"]["status"] == expected
    assert (report["status"] == "ok") is (expected != "degraded")
    assert received == [("/health/live", None), ("/health/ready", None), ("/openapi.json", expected_authorization)]
    assert "token" not in json.dumps(report)
    if expected != "ok":
        assert "credentials" in report["checks"]["server_memory_protocol"]["detail"]
        assert "expose" not in report["checks"]["server_memory_protocol"]["detail"]


def test_discovery_does_not_forward_credentials_to_redirects():
    from powercontext.cli.memory_protocol import server_memory_protocol
    from powercontext.cli.system import DiagnosticStatus

    paths = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            paths.append(self.path)
            self.send_response(302)
            self.send_header("Location", "/redirect-target")
            self.end_headers()

        def log_message(self, format, *args):  # noqa: A002 - stdlib signature.
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            result = server_memory_protocol(
                f"http://127.0.0.1:{server.server_port}", authorization="Bearer selected-host-token"
            )
        finally:
            server.shutdown()
            thread.join()
    assert result.status is DiagnosticStatus.DEGRADED
    assert paths == ["/openapi.json"]
