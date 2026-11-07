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
