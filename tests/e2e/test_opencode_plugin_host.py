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
import os
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from shutil import which
from time import monotonic
from typing import Any, cast

import pytest


class _RecordingServer(ThreadingHTTPServer):
    requests: list[tuple[str, dict[str, Any]]]
    captured: threading.Event


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        payload = cast(dict[str, Any], json.loads(self.rfile.read(length)))
        server = cast(_RecordingServer, self.server)
        server.requests.append((self.path, payload))
        if self.path == "/v1/sources/content":
            server.captured.set()
        response: dict[str, Any]
        if self.path == "/v1/context/prepare":
            response = {
                "schema": "powercontext.prepared-context.v1",
                "status": "empty",
                "content": None,
                "content_bytes": 0,
            }
        else:
            response = {"position": 1}
        body = json.dumps(response).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - stdlib override name
        del format, args
        return


def test_opencode_run_normalizes_prompt_before_recall_and_capture(tmp_path: Path) -> None:
    executable = which("opencode")
    if executable is None:
        pytest.skip("OpenCode is not installed")
    version = subprocess.run([executable, "--version"], check=True, capture_output=True, text=True).stdout.strip()
    if not version.startswith("1."):
        pytest.skip(f"OpenCode 1.x is required, found {version}")

    root = Path(__file__).resolve().parents[2]
    plugin = root / "integrations" / "opencode" / "plugins" / "powercontext" / "lib" / "index.js"
    server = _RecordingServer(("127.0.0.1", 0), _Handler)
    server.requests = []
    server.captured = threading.Event()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        project = tmp_path / "project"
        project.mkdir()
        env = os.environ.copy()
        for name in ("config", "data", "cache", "state"):
            path = tmp_path / name
            path.mkdir()
            env[f"XDG_{name.upper()}_HOME"] = str(path)
        env.update({
            "OPENCODE_CONFIG_CONTENT": json.dumps({"plugin": [plugin.as_uri()]}),
            "OPENCODE_DISABLE_AUTOUPDATE": "true",
            "POWERCONTEXT_OPENCODE_BASE_URL": f"http://127.0.0.1:{server.server_port}",
            "POWERCONTEXT_OPENCODE_SCOPE_ID": "project:test",
        })
        process = subprocess.Popen(
            [
                executable,
                "run",
                "--dir",
                str(project),
                "--model",
                "invalid/model",
                "multi word prompt",
                "--format",
                "json",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )
        captured = False
        try:
            deadline = monotonic() + 30
            while monotonic() < deadline and process.poll() is None:
                if server.captured.wait(timeout=0.25):
                    captured = True
                    break
            captured = captured or server.captured.is_set()
        finally:
            if process.poll() is None:
                process.terminate()
            try:
                stdout, stderr = process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                stdout, stderr = process.communicate(timeout=5)
        assert captured, (
            f"OpenCode did not invoke the capture hook; stdout={stdout[-2000:]!r}, stderr={stderr[-2000:]!r}"
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    requests = dict(server.requests)
    assert requests["/v1/context/prepare"]["query"] == "multi word prompt"
    assert requests["/v1/sources/content"]["content"] == "multi word prompt"
