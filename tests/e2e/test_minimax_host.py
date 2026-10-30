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

"""Opt-in qualification against a real MiniMax CLI and authenticated SQLite Server."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import socket
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import httpx
import pytest
import uvicorn
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from pydantic import SecretStr
from typer.testing import CliRunner

from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.cli.app import create_cli
from powercontext.server.factory import create_server_app
from powercontext.server.settings import BearerAuthConfig, ServerSettings

COMMAND = os.environ.get("POWERCONTEXT_MINIMAX_ACCEPTANCE_COMMAND", "")
pytestmark = pytest.mark.skipif(
    not COMMAND, reason="Set POWERCONTEXT_MINIMAX_ACCEPTANCE_COMMAND to a real mcode launcher"
)
ROOT = Path(__file__).parents[2]


def _tool_listing(body):
    """Read tool names from JSON or streamable HTTP SSE, excluding tool results."""
    text = body.decode("utf-8").replace("\r\n", "\n")
    documents = [text]
    if text.startswith(("event:", "data:", ":")):
        documents = [
            "\n".join(line[5:].lstrip(" ") for line in event.splitlines() if line.startswith("data:"))
            for event in text.split("\n\n")
        ]
    for document in documents:
        try:
            value = json.loads(document)
        except ValueError:
            continue
        if isinstance(value, dict) and isinstance(value.get("result"), dict):
            tools = value["result"].get("tools")
            if isinstance(tools, list):
                return [tool["name"] for tool in tools]
    return None


class MCPTrace:
    """Retain protocol timing without recording headers, arguments, or results."""

    def __init__(self, app, events):
        self.app = app
        self.events = events

    @staticmethod
    def record_request(event, body):
        try:
            document = json.loads(body)
        except ValueError:
            return
        event["rpc_method"] = document.get("method")
        if document.get("method") == "tools/call":
            event["tool_name"] = document.get("params", {}).get("name")

    @staticmethod
    def finish_response(event, response):
        event["response_finished_at_ms"] = round(time.monotonic() * 1000, 2)
        if event.get("rpc_method") == "tools/list":
            names = _tool_listing(response)
            if names is not None:
                event["response_tool_count"] = len(names)
                event["response_has_list_scopes"] = "list_scopes" in names

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope["path"].startswith("/mcp"):
            return await self.app(scope, receive, send)
        started = time.monotonic()
        event = {"http_method": scope["method"], "started_at_ms": round(started * 1000, 2)}
        self.events.append(event)
        body = bytearray()
        response = bytearray()

        async def observed_receive():
            message = await receive()
            if message["type"] == "http.request":
                body.extend(message.get("body", b""))
                if not message.get("more_body", False):
                    self.record_request(event, body)
            return message

        async def observed_send(message):
            if message["type"] == "http.response.start":
                event["http_status"] = message["status"]
            if message["type"] == "http.response.body" and event.get("rpc_method") == "tools/list":
                response.extend(message.get("body", b""))
            await send(message)
            if message["type"] == "http.response.body" and not message.get("more_body", False):
                self.finish_response(event, response)

        try:
            await self.app(scope, observed_receive, observed_send)
        finally:
            event["elapsed_ms"] = round((time.monotonic() - started) * 1000, 2)


@pytest.fixture
def mcp_trace():
    return []


@pytest.fixture
def host(tmp_path, monkeypatch, request, mcp_trace):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setenv("POWERCONTEXT_HOME", str(tmp_path / "pc"))
    monkeypatch.setenv("POWERCONTEXT_CLIENT_CONFIG_FILE", str(tmp_path / "clients.json"))
    authenticated = getattr(request, "param", True)
    if authenticated:
        monkeypatch.setenv("POWERCONTEXT_MINIMAX_AUTHORIZATION", "acceptance-only-token")
    else:
        monkeypatch.delenv("POWERCONTEXT_MINIMAX_AUTHORIZATION", raising=False)
    for name in ("MINIMAX_DATA_DIR", "MAVIS_DATA_DIR", "MAVIS_PROFILE"):
        monkeypatch.delenv(name, raising=False)
    app = create_server_app(
        settings=ServerSettings(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'server.db'}"),
            auth=BearerAuthConfig(
                enabled=authenticated, token=SecretStr("acceptance-only-token") if authenticated else None
            ),
        ),
        scheduler_path=tmp_path / "scheduler",
    )
    app.add_middleware(MCPTrace, events=mcp_trace)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 15
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert server.started
        yield tmp_path, f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=15)
        assert not thread.is_alive()


def invoke(*arguments):
    return CliRunner().invoke(create_cli(), list(arguments))


@pytest.mark.parametrize("selection", ["explicit", "minimax_env", "mavis_env"])
def test_native_discovery_repeat_install_and_readonly_doctor(host, monkeypatch, selection):
    root, endpoint = host
    data = root / "MiniMax 原生 profile"
    options = ["--minimax-data-dir", str(data)] if selection == "explicit" else []
    if selection != "explicit":
        monkeypatch.setenv("MINIMAX_DATA_DIR" if selection == "minimax_env" else "MAVIS_DATA_DIR", str(data))
    arguments = [
        "setup",
        "minimax",
        "--source",
        str(ROOT),
        "--minimax-command",
        COMMAND,
        "--server-url",
        endpoint,
        "--json",
        *options,
    ]
    for _ in range(2):
        result = invoke(*arguments)
        assert result.exit_code == 0, result.output
        row = json.loads(result.output)["hosts"]["minimax"]
        assert row["checks"]["native"]["status"] == "ok"
        assert row["checks"]["server_access"]["status"] == "ok"
    private = data / "mcp.json"
    assert "acceptance-only-token" in private.read_text()
    before = {path: path.read_bytes() for path in data.rglob("*") if path.is_file()}
    result = invoke("doctor", "minimax", "--json")
    assert result.exit_code == 0, result.output
    assert {path: path.read_bytes() for path in data.rglob("*") if path.is_file()} == before
    assert "acceptance-only-token" not in result.output
    environment = dict(os.environ, MINIMAX_DATA_DIR=str(data))
    disabled = subprocess.run(
        [COMMAND, "plugin", "disable", "powercontext@local", "--json"],
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert disabled.returncode == 0, disabled.stderr
    before = {path: path.read_bytes() for path in data.rglob("*") if path.is_file()}
    result = invoke("doctor", "minimax", "--json")
    assert result.exit_code == 1
    assert "disabled" in json.loads(result.output)["hosts"]["minimax"]["checks"]["native"]["detail"]
    assert {path: path.read_bytes() for path in data.rglob("*") if path.is_file()} == before
    # Repeated setup must respect native disablement, rather than silently enabling it.
    result = invoke(*arguments)
    assert result.exit_code == 1
    assert "disabled" in json.loads(result.output)["hosts"]["minimax"]["checks"]["native"]["detail"]


def test_native_loader_omission_and_private_authorization_failure(host):
    root, endpoint = host
    data = root / "omitted"
    result = invoke(
        "setup",
        "minimax",
        "--source",
        str(ROOT),
        "--minimax-command",
        COMMAND,
        "--minimax-data-dir",
        str(data),
        "--server-url",
        endpoint,
        "--json",
    )
    assert result.exit_code == 0, result.output
    private = data / "mcp.json"
    config = json.loads(private.read_text())
    config["mcpServers"]["powercontext"]["headers"]["Authorization"] = "Bearer wrong-acceptance-token"
    private.write_text(json.dumps(config))
    manifest_path = data / "plugins/powercontext/.minimax-plugin/plugin.json"
    manifest = json.loads(manifest_path.read_bytes())
    # Local resource checks pass, but the native loader rejects a known field's invalid type.
    manifest["displayName"] = 123
    manifest_path.write_text(json.dumps(manifest))
    result = invoke("doctor", "minimax", "--json")
    assert result.exit_code == 1
    checks = json.loads(result.output)["hosts"]["minimax"]["checks"]
    assert checks["native"]["status"] == "failed"
    assert "omitted" in checks["native"]["detail"]
    assert checks["server_access"]["status"] == "failed"
    assert "401" in checks["server_access"]["detail"]
    assert "wrong-acceptance-token" not in result.output


@pytest.mark.parametrize("host", [False], indirect=True, ids=["anonymous-server"])
@pytest.mark.parametrize("authorization", [False, True], ids=["without-authorization", "with-authorization"])
def test_native_setup_preserves_disabled_private_override_and_probes_bundle(host, authorization):
    root, endpoint = host
    source = root / "bundle"
    shutil.copytree(ROOT / "integrations/minimax/plugins/powercontext", source)
    bundled_path = source / "powercontext.mcp.json"
    bundled = json.loads(bundled_path.read_bytes())
    bundled["mcpServers"]["powercontext"]["url"] = endpoint + "/mcp"
    bundled_path.write_text(json.dumps(bundled))
    data = root / "disabled-private"
    data.mkdir()
    private = data / "mcp.json"
    entry = {"type": "streamable-http", "url": "https://inactive.example/mcp", "enabled": False}
    if authorization:
        entry["headers"] = {"Authorization": "Bearer inactive-private-token"}
    original = (
        json.dumps({"note": "保留格式和禁用连接", "mcpServers": {"powercontext": entry}}, ensure_ascii=False, indent=4)
        + "\n"
    ).encode()
    private.write_bytes(original)
    for _ in range(2):
        result = invoke(
            "setup",
            "minimax",
            "--source",
            str(source),
            "--minimax-command",
            COMMAND,
            "--minimax-data-dir",
            str(data),
            "--json",
        )
        assert result.exit_code == 0, result.output
        row = json.loads(result.output)["hosts"]["minimax"]
        assert row["checks"]["native"]["status"] == "ok"
        assert row["checks"]["server_access"]["status"] == "ok"
        assert endpoint in row["checks"]["server_liveness"]["detail"]
        assert row["result"]["private_mcp_state"] == "disabled"
        assert private.read_bytes() == original
        assert "inactive-private-token" not in result.output
    result = invoke("doctor", "minimax", "--json")
    assert result.exit_code == 0, result.output
    assert private.read_bytes() == original
    assert endpoint in json.loads(result.output)["hosts"]["minimax"]["checks"]["server_liveness"]["detail"]


def _objects(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _objects(child)
    elif isinstance(value, list):
        for child in value:
            yield from _objects(child)
    elif isinstance(value, str):
        # Native results can contain prose or JSON followed by structured content.
        for block in value.split("\n[MCP structured content]\n"):
            try:
                decoded, _ = json.JSONDecoder().raw_decode(block.lstrip())
            except ValueError:
                continue
            yield from _objects(decoded)


class ModelGateway(ThreadingHTTPServer):
    def __init__(self, *, allow_multiple_scope_tools=False):
        super().__init__(("127.0.0.1", 0), ModelHandler)
        self.allow_multiple_scope_tools = allow_multiple_scope_tools
        self.requests: list[dict[str, Any]] = []
        self.request_observations: list[dict[str, Any]] = []
        self.invoked_tools: list[str] = []
        self.scope_call_ids: set[str] = set()
        self.errors: list[str] = []

    def scope_tool(self, names):
        matches = sorted({name for name in names if name.endswith("list_scopes")})
        if not matches or (len(matches) != 1 and not self.allow_multiple_scope_tools):
            expected = "at least one" if self.allow_multiple_scope_tools else "one isolated"
            self.errors.append(f"Expected {expected} list_scopes tool, discovered {matches!r}; tools={names!r}")
            return None
        return matches[0]


class ModelHandler(BaseHTTPRequestHandler):
    server: ModelGateway

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - match the HTTP handler interface.
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        # Provider negotiation also probes formats this controlled endpoint does not implement.
        if "messages" not in body:
            self.send_error(404, "Only OpenAI chat completions are supported")
            return
        self.server.request_observations.append({
            "received_at_ms": round(time.monotonic() * 1000, 2),
            "has_tool_catalog": "tools" in body,
            "tools": [tool["function"]["name"] for tool in body.get("tools", [])],
        })
        self.server.requests.append(body)
        messages = body["messages"]
        tool_results = [message for message in messages if message["role"] == "tool"]
        names = [tool["function"]["name"] for tool in body.get("tools", [])]
        selected = None
        arguments = {}
        call_id = f"call-{len(tool_results)}"
        if not tool_results and "tool_search" in names:
            selected = "tool_search"
            arguments = {"query": "list_scopes", "top_k": 20}
        elif tool_results and len(tool_results) == 1 and "mcp_invoke" in names:
            match = self.server.scope_tool([
                item["name"] for item in _objects(tool_results[0]["content"]) if isinstance(item.get("name"), str)
            ])
            if match:
                selected = "mcp_invoke"
                arguments = {"tool_name": match, "arguments": {"limit": 10}}
                self.server.invoked_tools.append(match)
                self.server.scope_call_ids.add(call_id)
        elif not tool_results and "tools" in body:
            selected = self.server.scope_tool(names)
            arguments = {"limit": 10}
            if selected:
                self.server.invoked_tools.append(selected)
                self.server.scope_call_ids.add(call_id)
        delta: dict[str, Any] = {
            "content": "MINIMAX_MCP_ACCEPTANCE_FAILED" if self.server.errors else "MINIMAX_MCP_ACCEPTANCE_OK"
        }
        finish = "stop"
        if selected:
            delta = {
                "tool_calls": [
                    {
                        "index": 0,
                        "id": call_id,
                        "type": "function",
                        "function": {"name": selected, "arguments": json.dumps(arguments)},
                    }
                ]
            }
            finish = "tool_calls"
        self.send_response(200)
        if body.get("stream"):
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for content in [
                {
                    "id": "acceptance",
                    "object": "chat.completion.chunk",
                    "created": 0,
                    "model": "fixture",
                    "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
                },
                {
                    "id": "acceptance",
                    "object": "chat.completion.chunk",
                    "created": 0,
                    "model": "fixture",
                    "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
                },
                {
                    "id": "acceptance",
                    "object": "chat.completion.chunk",
                    "created": 0,
                    "model": "fixture",
                    "choices": [{"index": 0, "delta": {}, "finish_reason": finish}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
                },
            ]:
                self.wfile.write(f"data: {json.dumps(content)}\n\n".encode())
            self.wfile.write(b"data: [DONE]\n\n")
        else:
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(
                json.dumps({
                    "id": "acceptance",
                    "object": "chat.completion",
                    "model": "fixture",
                    "choices": [{"index": 0, "message": {"role": "assistant", **delta}, "finish_reason": finish}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
                }).encode()
            )


@pytest.mark.parametrize("route", ["private-mcp", "plugin-mcp", "installed-default"])
@pytest.mark.parametrize("startup", ["cold", "preflight"])
def test_real_host_invokes_powercontext_mcp_with_controlled_inference(host, route, startup, mcp_trace):
    root, endpoint = host
    data = root / "mcp-tool"
    result = invoke(
        "setup",
        "minimax",
        "--source",
        str(ROOT),
        "--minimax-command",
        COMMAND,
        "--minimax-data-dir",
        str(data),
        "--server-url",
        endpoint,
        "--json",
    )
    assert result.exit_code == 0, result.output
    environment = dict(os.environ, MINIMAX_DATA_DIR=str(data), MCODE_PROVIDER_API_KEY="synthetic-model-token")
    private_path = data / "mcp.json"
    private = json.loads(private_path.read_bytes())
    private_entry = private["mcpServers"]["powercontext"]
    bundle = json.loads((data / "plugins/powercontext/powercontext.mcp.json").read_bytes())
    bundle_entry = bundle["mcpServers"]["powercontext"]
    assert private_entry["url"] == bundle_entry["url"] == endpoint + "/mcp"
    assert private_entry["headers"] == bundle_entry["headers"] == {"Authorization": "Bearer acceptance-only-token"}
    if route == "plugin-mcp":
        del private["mcpServers"]["powercontext"]
        private_path.write_text(json.dumps(private), encoding="utf-8")
        assert "powercontext" not in json.loads(private_path.read_bytes())["mcpServers"]
    elif route == "private-mcp":
        disabled = subprocess.run(
            [COMMAND, "plugin", "disable", "powercontext@local", "--json"],
            env=environment,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert disabled.returncode == 0, disabled.stdout + disabled.stderr
    if route != "plugin-mcp":
        assert private_entry.get("enabled", True) is True
    discovered = subprocess.run(
        [COMMAND, "plugin", "list", "--json"],
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert discovered.returncode == 0, discovered.stdout + discovered.stderr
    plugins = [
        item
        for item in json.loads(discovered.stdout)["installed"]
        if item["name"] == "powercontext" and item["marketplaceName"] == "local"
    ]
    assert len(plugins) == 1 and plugins[0]["enabled"] is (route != "private-mcp"), discovered.stdout
    marker = "MiniMax native MCP scope evidence"
    with httpx.Client(
        base_url=endpoint, headers={"Authorization": "Bearer acceptance-only-token"}, timeout=10
    ) as client:
        response = client.post(
            "/v1/scopes",
            json={"title": marker, "summary": "Isolated native MCP acceptance", "idempotency_key": "minimax-scope"},
        )
        assert response.status_code == 201, response.text
        scope_id = response.json()["scope_id"]

    async def discover_server_tools():
        transport = StreamableHttpTransport(bundle_entry["url"], headers=bundle_entry["headers"])
        async with Client(transport, timeout=10, init_timeout=10) as client:
            return sorted(tool.name for tool in await client.list_tools())

    preflight: dict[str, Any] = {"performed": startup == "preflight"}
    if startup == "preflight":
        started = time.monotonic()
        server_tools = asyncio.run(discover_server_tools())
        preflight.update(tools=server_tools, elapsed_ms=round((time.monotonic() - started) * 1000, 2))
        assert "list_scopes" in server_tools, preflight
    diagnostics = root / "native-diagnostics"
    evidence = root / "acceptance-evidence.json"
    gateway = ModelGateway(allow_multiple_scope_tools=route == "installed-default")
    scope_result_matched = False
    thread = threading.Thread(target=gateway.serve_forever, daemon=True)
    thread.start()
    try:
        added = subprocess.run(
            [
                COMMAND,
                "provider",
                "add",
                "--name",
                "acceptance",
                "--base-url",
                f"http://127.0.0.1:{gateway.server_port}/v1",
                "--api-format",
                "openai-completions",
                "--model",
                "fixture",
                "--use",
            ],
            env=environment,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert added.returncode == 0, added.stderr
        mcp_trace.clear()
        executed = subprocess.run(
            [
                COMMAND,
                "exec",
                "List PowerContext scopes using MCP; return the scope titles.",
                "--permission",
                "full",
                "--max-steps",
                "5",
                "--timeout",
                "40s",
                "--output-format",
                "json",
                "--cwd",
                str(root),
                "--diagnostics-dir",
                str(diagnostics),
            ],
            env=environment,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        assert executed.returncode == 0, executed.stdout + executed.stderr
        assert not gateway.errors, {
            "errors": gateway.errors,
            "evidence": str(evidence),
            "native_stderr": executed.stderr,
        }
        observed = [
            message
            for request in gateway.requests
            for message in request.get("messages", [])
            if message["role"] == "tool" and message.get("tool_call_id") in gateway.scope_call_ids
        ]
        scope_result_matched = any(
            item.get("scope_id") == scope_id and item.get("title") == marker
            for message in observed
            for item in _objects(message["content"])
        )
        assert scope_result_matched, {
            "stdout": executed.stdout,
            "stderr": executed.stderr,
            "tools": gateway.invoked_tools,
            "results": observed,
            "evidence": str(evidence),
        }
        assert any(event.get("response_has_list_scopes") is True for event in mcp_trace), str(evidence)
        assert len(set(gateway.invoked_tools)) == 1, gateway.invoked_tools
        if route != "private-mcp":
            assert any("powercontext-project-context" in json.dumps(request) for request in gateway.requests)
    finally:
        gateway.shutdown()
        gateway.server_close()
        thread.join(timeout=5)
        evidence.write_text(
            json.dumps(
                {
                    "route": route,
                    "startup": startup,
                    "server_preflight": preflight,
                    "native_mcp_requests": mcp_trace,
                    "model_requests": gateway.request_observations,
                    "invoked_tools": gateway.invoked_tools,
                    "scope_result_matched": scope_result_matched,
                    "errors": gateway.errors,
                    "native_diagnostics": str(diagnostics),
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )


def test_real_doctor_does_not_initialize_an_uninstalled_native_target(host):
    root, _ = host
    data = root / "uninstalled native profile"
    result = invoke("doctor", "minimax", "--minimax-data-dir", str(data), "--minimax-command", COMMAND, "--json")
    assert result.exit_code == 1
    assert not data.exists()
