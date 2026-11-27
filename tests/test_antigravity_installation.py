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


"""Exercise Antigravity through public CLI commands and the installed host hooks."""

import json
import os
import shlex
import subprocess
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest
from typer.testing import CliRunner

from powercontext.cli.app import create_cli

ROOT = Path(__file__).parents[1]


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("POWERCONTEXT_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("POWERCONTEXT_CLIENT_CONFIG_FILE", str(tmp_path / "clients.json"))
    monkeypatch.setenv("POWERCONTEXT_ANTIGRAVITY_STATE_DIR", str(tmp_path / "hook-state"))
    monkeypatch.setenv("POWERCONTEXT_DIAGNOSTIC_STATE_FILE", str(tmp_path / "diagnostics.json"))
    monkeypatch.chdir(tmp_path)


def test_antigravity_setup_configure_and_doctor(tmp_path, monkeypatch, server_checks):
    monkeypatch.setenv("POWERCONTEXT_ANTIGRAVITY_AUTHORIZATION", "fixture-token")
    runner, cli = CliRunner(), create_cli()
    result = runner.invoke(
        cli, ["setup", "antigravity", "--source", str(ROOT), "--server-url", "https://memory.example", "--json"]
    )
    assert result.exit_code == 0, result.output
    bundle = tmp_path / ".gemini/config/plugins/powercontext"
    assert json.loads((bundle / "plugin.json").read_text(encoding="utf-8"))["name"] == "powercontext"
    assert (bundle / "skills/powercontext-project-context/SKILL.md").is_file()
    mcp = bundle / "mcp_config.json"
    assert json.loads(mcp.read_text(encoding="utf-8"))["mcpServers"]["memory"]["url"] == "https://memory.example/mcp"
    assert json.loads(mcp.read_text(encoding="utf-8"))["mcpServers"]["memory"]["headers"] == {
        "Authorization": "Bearer fixture-token"
    }
    assert "fixture-token" not in result.output
    if os.name != "nt":
        assert mcp.stat().st_mode & 0o077 == 0
    hooks = (bundle / "hooks.json").read_bytes()

    monkeypatch.delenv("POWERCONTEXT_ANTIGRAVITY_AUTHORIZATION")
    result = runner.invoke(cli, ["config", "agent", "antigravity", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(mcp.read_text(encoding="utf-8"))["mcpServers"]["memory"]["headers"] == {
        "Authorization": "Bearer fixture-token"
    }

    result = runner.invoke(cli, ["config", "agent", "antigravity", "--server-url", "https://other.example", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(mcp.read_text(encoding="utf-8"))["mcpServers"]["memory"]["url"] == "https://other.example/mcp"
    assert "headers" not in json.loads(mcp.read_text(encoding="utf-8"))["mcpServers"]["memory"]
    assert (bundle / "hooks.json").read_bytes() == hooks
    result = runner.invoke(cli, ["doctor", "antigravity", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["hosts"]["antigravity"]["checks"]["hooks"]["status"] == "ok"

    (bundle / "hooks.json").write_text("{}", encoding="utf-8")
    result = runner.invoke(cli, ["doctor", "antigravity", "--json"])
    assert result.exit_code == 1, result.output
    assert json.loads(result.output)["hosts"]["antigravity"]["checks"]["hooks"]["status"] == "failed"


@pytest.fixture
def lifecycle_server():
    bootstrap_content = "Bootstrap context"
    recall_content = "Recalled context"
    requests = []
    replies = {
        "/v1/scope-bindings/resolve": {"scope_id": "scope:test"},
        "/v1/scope-bindings": {"scope_id": "scope:test"},
        "/v1/context/bootstrap": {
            "schema": "powercontext.bootstrap-context.v1",
            "status": "ready",
            "reason": None,
            "profile": "powercontext.scope-bootstrap.v1",
            "content": bootstrap_content,
            "content_bytes": len(bootstrap_content),
            "package_digest": "sha256:" + sha256(bootstrap_content.encode()).hexdigest(),
            "items": [
                {
                    "kind": "atomic-memory",
                    "scope_id": "scope:test",
                    "artifact": {"family": "atomic-memory", "artifact_id": "memory", "revision": 3},
                    "content_digest": "sha256:" + "a" * 64,
                    "truncated": False,
                }
            ],
            "truncated": False,
            "receipt": {"receipt_id": "bcr_test", "state": "pending"},
        },
        "/v1/context/bootstrap/receipts": {"receipt_id": "bcr_test", "state": "injected"},
        "/v1/context/prepare": {
            "schema": "powercontext.prepared-context.v1",
            "status": "ready",
            "content": recall_content,
            "content_bytes": len(recall_content),
        },
        "/v1/sources/content": {},
    }

    class Handler(BaseHTTPRequestHandler):
        def respond(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append((self.command, self.path, payload, self.headers.get("Authorization")))
            body = json.dumps(replies[self.path]).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        do_POST = do_PUT = respond

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}", requests
        finally:
            server.shutdown()
            thread.join()


def test_installed_antigravity_hooks_inject_and_capture_once_per_prompt(
    tmp_path, monkeypatch, server_checks, lifecycle_server
):
    endpoint, requests = lifecycle_server
    monkeypatch.setenv("POWERCONTEXT_ANTIGRAVITY_AUTHORIZATION", "fixture-token")
    runner, cli = CliRunner(), create_cli()
    result = runner.invoke(cli, ["setup", "antigravity", "--source", str(ROOT), "--server-url", endpoint, "--json"])
    assert result.exit_code == 0, result.output
    bundle = tmp_path / ".gemini/config/plugins/powercontext"
    group = json.loads((bundle / "hooks.json").read_text(encoding="utf-8"))["powercontext"]
    workspace = tmp_path / "workspace with spaces"
    workspace.mkdir()
    session = {"conversationId": "first", "workspacePaths": [str(workspace)]}

    def invoke(event, payload):
        command = group[event][0]["command"]
        arguments = command if os.name == "nt" else shlex.split(command)
        result = subprocess.run(
            arguments, cwd=bundle, input=json.dumps(payload), text=True, capture_output=True, timeout=10, check=False
        )
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)

    def inject(content):
        return {"injectSteps": [{"systemMessage": {"systemMessage": content}}]}

    assert invoke("SessionStart", session) == inject("Bootstrap context")
    first_key = {"integration": "antigravity", "kind": "session", "external_id": "first"}
    assert requests[1] == (
        "PUT",
        "/v1/scope-bindings",
        {"key": first_key, "scope_id": "scope:test"},
        "Bearer fixture-token",
    )
    assert invoke("PreInvocation", {**session, "lastUserInput": "Recall this", "invocationNum": 0}) == inject(
        "Recalled context"
    )
    assert requests[-1][2]["bootstrap_receipt_id"] == "bcr_test"
    assert all(path != "/v1/sources/content" for _, path, _, _ in requests)

    result = runner.invoke(cli, ["config", "agent", "antigravity", "--capture-prompts", "--json"])
    assert result.exit_code == 0, result.output
    prompt = {**session, "lastUserInput": "Implement the change", "invocationNum": 1}
    assert invoke("PreInvocation", prompt) == inject("Recalled context")
    assert requests[-1][1] == "/v1/sources/content"
    assert requests[-1][2]["content"] == prompt["lastUserInput"]
    assert requests[-1][2]["metadata"]["cwd"] == str(workspace)
    before = len(requests)
    assert invoke("PreInvocation", {**prompt, "invocationNum": 2}) == {}
    assert invoke("PreInvocation", {**session, "invocationNum": 3}) == {}
    assert invoke("SessionStart", {"conversationId": "no-workspace"}) == {}
    assert len(requests) == before

    assert invoke("PreInvocation", {**prompt, "conversationId": "another"}) == inject("Recalled context")
    before = len(requests)
    sensitive = {**session, "lastUserInput": "Diagnose token fixture-token", "invocationNum": 4}
    assert invoke("PreInvocation", sensitive) == inject("Recalled context")
    recent = requests[before:]
    assert [path for _, path, _, _ in recent] == ["/v1/scope-bindings/resolve", "/v1/context/prepare"]
    assert "fixture-token" not in recent[-1][2]["query"]
    assert "Diagnose token" in recent[-1][2]["query"]
    assert invoke("PreInvocation", {**prompt, "invocationNum": 5}) == inject("Recalled context")
    assert requests[-1][2]["metadata"]["prompt_id"] == "5"

    monkeypatch.setenv("POWERCONTEXT_ANTIGRAVITY_PROMPT_RECALL", "false")
    result = runner.invoke(cli, ["config", "agent", "antigravity", "--no-capture-prompts", "--json"])
    assert result.exit_code == 0, result.output
    before = len(requests)
    assert invoke("PreInvocation", {**session, "lastUserInput": "Gateway turn", "invocationNum": 6}) == {}
    assert len(requests) == before
