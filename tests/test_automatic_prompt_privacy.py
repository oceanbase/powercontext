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

"""Automatic hooks must not capture recognized credentials or forward them as queries."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest

ROOT = Path(__file__).resolve().parents[1]
CASES = json.loads((ROOT / "tests/fixtures/automatic_prompt_privacy.json").read_text(encoding="utf-8"))["cases"]
HOOKS = {
    "claude": "claude-code/plugins/powercontext/hooks/user_prompt_submit.py",
    "codex": "codex/plugins/powercontext/hooks/recall.py",
    "workbuddy": "workbuddy/plugins/powercontext/hooks/workbuddy_powercontext_hook.py",
}
DISABLED_CASES = [case for case in CASES if case["id"].endswith("-header") or "private-key" in case["id"]]


@pytest.mark.parametrize("host", HOOKS)
@pytest.mark.parametrize(
    ("case", "capture_enabled"),
    [(case, True) for case in CASES] + [(case, False) for case in DISABLED_CASES],
    ids=[case["id"] for case in CASES] + [f"{case['id']}-capture-disabled" for case in DISABLED_CASES],
)
def test_automatic_hook_skips_credentials_without_disabling_preparation(tmp_path, host, case, capture_enabled):
    requests: list[tuple[str, dict[str, object]]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            value = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append((self.path, value))
            status = 200
            if self.path == "/v1/scope-bindings/resolve":
                result = {"scope_id": "scope-privacy"}
            elif self.path == "/v1/context/prepare":
                result = {
                    "schema": "powercontext.prepared-context.v1",
                    "status": "ready",
                    "content": "safe historical context",
                    "content_bytes": len("safe historical context"),
                }
            elif self.path == "/v1/sources/content":
                status = 202
                result = {
                    "status": "accepted",
                    "source": {"name": "content", "source_id": value["source_id"]},
                    "position": 1,
                }
            else:
                self.send_error(404)
                return
            body = json.dumps(result).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *_args):  # noqa: A002 - BaseHTTPRequestHandler override.
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("POWERCONTEXT_", "CLAUDE_PLUGIN_OPTION_"))
    }
    prefix = f"POWERCONTEXT_{host.upper()}_"
    environment.update({
        f"{prefix}SERVER_URL": f"http://127.0.0.1:{server.server_port}",
        f"{prefix}CAPTURE_PROMPTS": str(capture_enabled).lower(),
        f"{prefix}FLUSH_ON_CAPTURE": "false",
        "POWERCONTEXT_DIAGNOSTIC_STATE_FILE": str(tmp_path / "diagnostics.json"),
        "POWERCONTEXT_BOOTSTRAP_STATE_DIR": str(tmp_path / "bootstrap"),
        "POWERCONTEXT_CLAUDE_BOOTSTRAP_STATE_DIR": str(tmp_path / "bootstrap"),
        "CODEX_HOME": str(tmp_path / "codex"),
    })
    if "authorization" in case:
        environment[f"{prefix}AUTHORIZATION"] = case["authorization"]
    hook = ROOT / "integrations" / HOOKS[host]
    if host == "codex":
        # Codex's installed MCP configuration intentionally outranks environment URLs.
        plugin = tmp_path / "plugin"
        shutil.copytree(hook.parents[1], plugin, ignore=shutil.ignore_patterns("__pycache__", ".venv"))
        configuration = plugin / ".mcp.json"
        value = json.loads(configuration.read_text(encoding="utf-8"))
        value["mcpServers"]["powercontext"]["url"] = f"http://127.0.0.1:{server.server_port}/mcp/"
        configuration.write_text(json.dumps(value), encoding="utf-8")
        hook = plugin / "hooks/recall.py"
    try:
        completed = subprocess.run(
            [sys.executable, str(hook)],
            input=json.dumps({
                "hook_event_name": "UserPromptSubmit",
                "prompt": case["prompt"],
                "cwd": str(tmp_path),
            }),
            capture_output=True,
            text=True,
            env=environment,
            timeout=15,
            check=True,
        )
    finally:
        server.shutdown()
        thread.join()
        server.server_close()

    prepares = [value for path, value in requests if path == "/v1/context/prepare"]
    captures = [value for path, value in requests if path == "/v1/sources/content"]
    assert prepares, completed.stderr
    output = json.loads(completed.stdout)
    assert "safe historical context" in output["hookSpecificOutput"]["additionalContext"]
    if case["sensitive"]:
        assert captures == []
        assert "synthetic-value-2084" not in json.dumps(requests)
        assert "synthetic_value_2084" not in json.dumps(requests)
        assert "synthetic-value-2084" not in completed.stdout + completed.stderr
        assert "synthetic_value_2084" not in completed.stdout + completed.stderr
        assert "sensitive_content" in completed.stderr
    else:
        assert len(captures) == 1
        assert captures[0]["content"] == case["prompt"]
        assert prepares[0]["query"] == case["prompt"]
