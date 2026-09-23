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

"""Run the native MiniMax adapter against an isolated HTTP fixture."""

import json
import os
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "integrations/minimax/plugins/powercontext/hooks/recall.py"


def test_native_prompt_hook_uses_private_endpoint_and_server_scope_without_capture(tmp_path) -> None:
    received = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            received.append((self.path, request, self.headers.get("Authorization")))
            if self.path == "/v1/scope-bindings/resolve":
                payload = {
                    "scope_id": "server-scope",
                    "title": "Project",
                    "summary": "Context",
                    "version": 1,
                    "context_references": [],
                    "external_references": [],
                }
            else:
                payload = {
                    "schema": "powercontext.prepared-context.v1",
                    "status": "ready",
                    "content": "Prior decision",
                    "content_bytes": 14,
                }
            data = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    (tmp_path / "mcp.json").write_text(
        json.dumps({
            "mcpServers": {
                "powercontext": {
                    "type": "streamable-http",
                    "url": f"http://127.0.0.1:{server.server_port}/mcp/",
                    "headers": {"Authorization": "private-credential"},
                }
            }
        })
    )
    try:
        executable = shutil.which("powercontext-hook")
        assert executable is not None
        result = subprocess.run(
            [executable, "--script", str(SCRIPT)],
            input=json.dumps({"hook_event_name": "UserPromptSubmit", "prompt": "Continue", "cwd": str(tmp_path)}),
            env=dict(os.environ, MINIMAX_DATA_DIR=str(tmp_path)),
            text=True,
            capture_output=True,
            check=True,
            timeout=10,
        )
        output = json.loads(result.stdout)["hookSpecificOutput"]
        assert output["hookEventName"] == "UserPromptSubmit"
        assert "untrusted" in output["additionalContext"]
        assert "Prior decision" in output["additionalContext"]
        assert [path for path, _, _ in received] == ["/v1/scope-bindings/resolve", "/v1/context/prepare"]
        assert received[1][1]["scope_id"] == "server-scope"
        assert all(value == "private-credential" for _, _, value in received)
        assert "private-credential" not in result.stdout + result.stderr
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
