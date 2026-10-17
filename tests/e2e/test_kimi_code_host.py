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

"""Real Kimi CLI + real Server/SQLite, with controlled inference at both model boundaries."""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import httpx
import pytest
import uvicorn
from pydantic import AnyHttpUrl, SecretStr

from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime.config import InferenceConfig, RuntimeConfig
from powercontext.cli.kimi_code import install_kimi_plugin, run_kimi_diagnostics
from powercontext.server.factory import create_server_app
from powercontext.server.settings import BearerAuthConfig, ServerSettings

REPOSITORY = Path(__file__).parents[2]
EXPLICIT = "Explicit Kimi deployment code is amber-582."
AUTOMATIC = "Kimi acceptance staging color is cobalt-731."


def _objects(value: Any) -> Iterator[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _objects(child)
    elif isinstance(value, list):
        for child in value:
            yield from _objects(child)
    elif isinstance(value, str):
        try:
            decoded = json.loads(value)
        except ValueError:
            try:
                decoded, _ = json.JSONDecoder().raw_decode(value[value.index("{") :])
            except ValueError:
                return
        if isinstance(decoded, (dict, list)):
            yield from _objects(decoded)


class ModelGateway(ThreadingHTTPServer):
    def __init__(self, scope_id):
        super().__init__(("127.0.0.1", 0), ModelHandler)
        self.scope_id = scope_id
        self.requests = []
        self.generation_requests = []


class ModelHandler(BaseHTTPRequestHandler):
    server: ModelGateway

    def do_POST(self):  # noqa: C901 - two controlled inference protocols.
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if body.get("model") == "memory-fixture":
            self.generate_memory(body)
            return
        self.server.requests.append(body)
        messages = body["messages"]
        user_text = json.dumps([m for m in messages if m["role"] == "user"], ensure_ascii=False)
        tool_messages = [m for m in messages if m["role"] == "tool"]
        binding = next(
            (obj for obj in _objects(messages) if obj.get("schema") == "powercontext.kimi-code.request-binding.v1"),
            {},
        )
        scope_id = binding.get("scope_id")
        workspace_text = EXPLICIT + (
            " first-workspace."
            if "WORKSPACE_FIRST" in user_text
            else " second-workspace."
            if "WORKSPACE_SECOND" in user_text
            else ""
        )
        tool = None
        arguments = None
        if "PC_KIMI_SAVE" in user_text:
            if not tool_messages:
                tool = "remember_memory"
                arguments = {"scope_id": scope_id, "kind": "fact", "text": workspace_text}
            elif len(tool_messages) == 1:
                record = next(
                    (
                        obj
                        for obj in _objects(tool_messages[-1]["content"])
                        if isinstance(obj.get("artifact"), dict) and obj["artifact"].get("family") == "atomic-memory"
                    ),
                    None,
                )
                if record is not None:
                    artifact = record["artifact"]
                    tool = "get_artifact_revision"
                    arguments = {"scope_id": scope_id, **artifact}
        elif "PC_KIMI_SEARCH" in user_text and not tool_messages:
            tool = "search_memory"
            arguments = {"scope_id": scope_id, "query": "amber", "mode": "fts"}
        delta = {"content": "PC_KIMI_OK"}
        finish = "stop"
        if tool:
            names = [item["function"]["name"] for item in body.get("tools", [])]
            name = next((name for name in names if name.endswith("__" + tool)), None)
            if name:
                delta = {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "call-" + tool,
                            "type": "function",
                            "function": {"name": name, "arguments": json.dumps(arguments)},
                        }
                    ]
                }
                finish = "tool_calls"
            else:
                delta = {"content": "PC_KIMI_MISSING_TOOL:" + tool}
        self.send_response(200)
        if body.get("stream"):
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for payload in [
                {"choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
                {
                    "choices": [{"index": 0, "delta": {}, "finish_reason": finish}],
                    "usage": {"prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110},
                },
            ]:
                payload = {
                    "id": "kimi-controlled",
                    "object": "chat.completion.chunk",
                    "model": "kimi-fixture",
                    **payload,
                }
                self.wfile.write(("data: " + json.dumps(payload) + "\n\n").encode())
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        else:
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(
                json.dumps({
                    "id": "kimi-controlled",
                    "object": "chat.completion",
                    "model": "kimi-fixture",
                    "choices": [{"index": 0, "message": {"role": "assistant", **delta}, "finish_reason": finish}],
                    "usage": {"prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110},
                }).encode()
            )

    def generate_memory(self, body):
        self.server.generation_requests.append(body)
        payload: dict[str, Any] = next(
            (obj for obj in _objects(body["messages"]) if "evidence" in obj), {"evidence": []}
        )
        if "proposal" in payload:
            proposal = payload["proposal"]
            duplicate = next((item for item in payload["related"] if item["text"] == proposal["text"]), None)
            value = {
                "action": "noop" if duplicate else "create",
                "compared_ids": [item["item_id"] for item in payload["related"]],
                "target_ids": [duplicate["item_id"]] if duplicate else [],
                "content": None if duplicate else {"kind": proposal["kind"], "text": proposal["text"]},
                "evidence_ids": [] if duplicate else proposal["evidence_ids"],
                "reason": "Synthetic evidence reconciliation",
            }
        else:
            value = {
                "candidates": [
                    {"kind": "fact", "text": AUTOMATIC, "evidence_ids": [item["evidence_id"]]}
                    for item in payload["evidence"]
                    if AUTOMATIC in json.dumps(item["content"])
                ]
            }
        output = json.dumps(value)
        tools = body.get("tools", [])
        message = {"role": "assistant", "content": output}
        if tools:
            message = {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "generation-result",
                        "type": "function",
                        "function": {"name": tools[0]["function"]["name"], "arguments": output},
                    }
                ],
            }
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(
            json.dumps({
                "id": "generation-fixture",
                "object": "chat.completion",
                "created": 1,
                "model": "memory-fixture",
                "choices": [{"index": 0, "message": message, "finish_reason": "tool_calls" if tools else "stop"}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 30, "total_tokens": 130},
            }).encode()
        )

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - stdlib signature.
        pass


@contextmanager
def serving(app):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 15
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert server.started
        yield server, f"http://127.0.0.1:{port}", thread
    finally:
        server.should_exit = True
        thread.join(timeout=10)


@pytest.fixture
def model_gateway():
    gateway = ModelGateway(None)
    thread = threading.Thread(target=gateway.serve_forever, daemon=True)
    thread.start()
    try:
        yield gateway
    finally:
        gateway.shutdown()
        thread.join(timeout=5)
        gateway.server_close()


@pytest.mark.skipif(
    not os.environ.get("KIMI_CLI_BIN"), reason="Set KIMI_CLI_BIN to a real Kimi binary or built main.mjs"
)
def test_real_kimi_memory_capture_recall_and_fail_open(tmp_path: Path, monkeypatch, model_gateway) -> None:
    cli = Path(os.environ["KIMI_CLI_BIN"])
    assert cli.is_file(), "KIMI_CLI_BIN must be an existing real Kimi CLI"
    node = shutil.which("node")
    assert node, "Node is required by the plugin"
    home = tmp_path / "kimi home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    home.mkdir()
    monkeypatch.setenv("KIMI_CODE_HOME", str(home))
    monkeypatch.setenv("POWERCONTEXT_KIMI_TOKEN", "kimi-synthetic-server-token")
    gateway = model_gateway
    app = create_server_app(
        settings=ServerSettings(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'kimi.db'}"),
            auth=BearerAuthConfig(enabled=True, token=SecretStr("kimi-synthetic-server-token")),
            runtime=RuntimeConfig(memory_schedule_seconds=0.25, artifact_processing_families=("memory",)),
            inference=InferenceConfig(
                generation_model="openai-chat:memory-fixture",
                generation_base_url=AnyHttpUrl(f"http://127.0.0.1:{gateway.server_port}/v1"),
                generation_headers={"Authorization": SecretStr("Bearer synthetic-generation")},
            ),
        ),
        scheduler_path=tmp_path / "scheduler",
    )
    with (
        serving(app) as (server, endpoint, server_thread),
        httpx.Client(
            base_url=endpoint,
            headers={"Authorization": "Bearer kimi-synthetic-server-token"},
            timeout=10,
        ) as client,
    ):
        scope = client.post(
            "/v1/scopes",
            json={
                "title": "Kimi acceptance",
                "summary": "Disposable controlled host run",
                "idempotency_key": "kimi-host",
            },
        )
        scope.raise_for_status()
        scope_id = scope.json()["scope_id"]
        gateway.scope_id = scope_id
        (home / "config.toml").write_text(
            'default_model = "fixture"\ntelemetry = false\nauto_session_title = false\n'
            'builtin_product_skills = false\n[providers.fixture]\ntype = "openai"\napi_key = "synthetic"\n'
            f'base_url = "http://127.0.0.1:{gateway.server_port}/v1"\n'
            '[models.fixture]\nprovider = "fixture"\nmodel = "kimi-fixture"\nmax_context_size = 131072\n'
            'capabilities = ["tool_use"]\n'
            '[[permission.rules]]\ndecision = "allow"\npattern = "mcp__plugin_powercontext_powercontext__*"\n',
            encoding="utf-8",
        )
        installed = install_kimi_plugin(source=REPOSITORY, server_url=endpoint, scope_id=scope_id)
        install_kimi_plugin(source=REPOSITORY)
        diagnostics = run_kimi_diagnostics()
        assert all(check.ok for check in diagnostics.values()), diagnostics
        command = [node, str(cli)] if cli.suffix in {".mjs", ".js", ".cjs"} else [str(cli)]
        version = subprocess.run([*command, "--version"], capture_output=True, text=True, check=True, timeout=15)

        def invoke(prompt, token: str | None = "kimi-synthetic-server-token", *, cwd: Path = workspace):  # noqa: S107 - disposable fixture credential.
            environment = dict(os.environ)
            if token is None:
                environment.pop("POWERCONTEXT_KIMI_TOKEN", None)
            else:
                environment["POWERCONTEXT_KIMI_TOKEN"] = token
            result = subprocess.run(
                [*command, "-p", prompt, "--output-format", "stream-json"],
                env=environment,
                cwd=cwd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=45,
                check=False,
            )
            assert result.returncode == 0, result.stdout[-5000:] + result.stderr[-5000:]
            assert "PC_KIMI_OK" in result.stdout, result.stdout[-5000:] + result.stderr[-5000:]
            return result

        start = len(gateway.requests)
        invoke("PC_KIMI_SAVE: Save this project decision explicitly.")
        requests = gateway.requests[start:]
        tool_results = [m for request in requests for m in request["messages"] if m["role"] == "tool"]
        assert any(
            EXPLICIT in json.dumps(m) and m.get("tool_call_id") == "call-get_artifact_revision" for m in tool_results
        ), tool_results
        assert any(
            "get_artifact_revision" in json.dumps(request)
            and any(m["role"] == "tool" and EXPLICIT in json.dumps(m) for m in request["messages"])
            for request in requests
        ), requests
        start = len(gateway.requests)
        invoke("PC_KIMI_SEARCH: Search the saved deployment code.")
        assert any(
            EXPLICIT in json.dumps(m)
            for request in gateway.requests[start:]
            for m in request["messages"]
            if m["role"] == "tool"
        )

        invoke(AUTOMATIC)
        sources = client.get(f"/v1/scopes/{scope_id}/sources")
        sources.raise_for_status()
        captured = [item for item in sources.json()["items"] if item["source_id"].startswith("kimi-user-prompt:")]
        assert captured
        generated = None
        entries = []
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            entries = client.post("/v1/memory/entries/list", json={"scope_id": scope_id}).json()["entries"]
            generated = next((item for item in entries if item["text"] == AUTOMATIC), None)
            if generated:
                break
            time.sleep(0.2)
        assert generated is not None, entries
        assert gateway.generation_requests, "Real background worker must call the inference service"
        artifact = generated["artifact"]
        revision = client.get(
            f"/v1/scopes/{scope_id}/artifacts/{artifact['family']}/"
            f"{artifact['artifact_id']}/revisions/{artifact['revision']}"
        )
        revision.raise_for_status()
        evidence_ids = {item["source_id"] for item in revision.json()["sources"]}
        assert evidence_ids & {item["source_id"] for item in captured}
        start = len(gateway.requests)
        recall = invoke("What was the Kimi acceptance staging color?")
        assert any(
            "cobalt-731" in json.dumps(request["messages"])
            and "powercontext.kimi-code.request-binding.v1" in json.dumps(request["messages"])
            for request in gateway.requests[start:]
        )

        session_id = next(
            event["session_id"]
            for line in recall.stdout.splitlines()
            if line.startswith("{") and isinstance((event := json.loads(line)).get("session_id"), str)
        )
        history = subprocess.run(
            [
                node,
                str(Path(installed.plugin_path) / "scripts/status.mjs"),
                "--cwd",
                str(workspace),
                "--session-id",
                session_id,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=True,
            timeout=5,
        )
        observed = json.loads(history.stdout)
        assert observed["status"] == "observed", observed
        assert observed["selection"] == "session"
        assert observed["stages"]["prepare"]["state"] == "ready"
        assert observed["stages"]["capture"]["state"] == "accepted"
        assert observed["stages"]["injection"]["state"] == "emitted"
        diagnostics = run_kimi_diagnostics(runtime=True, cwd=workspace)
        assert diagnostics["runtime"].ok, diagnostics
        assert diagnostics["runtime"].checks is not None
        assert diagnostics["runtime"].checks["capture"] == "accepted"

        install_kimi_plugin(source=REPOSITORY, capture_prompts=False)
        count = len(client.get(f"/v1/scopes/{scope_id}/sources").json()["items"])
        invoke("An ordinary task with capture disabled.")
        assert len(client.get(f"/v1/scopes/{scope_id}/sources").json()["items"]) == count
        invoke("Ordinary task with rejected credentials.", token="wrong-synthetic-token")  # noqa: S106
        invoke("Ordinary task with missing credentials.", token=None)
        monkeypatch.delenv("POWERCONTEXT_KIMI_TOKEN")
        assert run_kimi_diagnostics()["credentials"].detail == "authentication_failed"
        monkeypatch.setenv("POWERCONTEXT_KIMI_TOKEN", "wrong-synthetic-token")
        assert run_kimi_diagnostics()["scope"].detail == "authentication_failed"
        monkeypatch.setenv("POWERCONTEXT_KIMI_TOKEN", "kimi-synthetic-server-token")
        server.should_exit = True
        server_thread.join(timeout=10)
        assert not server_thread.is_alive(), "PowerContext Server must actually stop before outage acceptance"
        invoke("An ordinary task during a PowerContext outage.")
        assert run_kimi_diagnostics()["server"].detail == "server_unavailable"
        basic_app = create_server_app(
            settings=ServerSettings(
                database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'basic.db'}"),
            )
        )
        with serving(basic_app) as (_, basic_endpoint, _), httpx.Client(base_url=basic_endpoint, timeout=10) as basic:
            new_scope = basic.post(
                "/v1/scopes",
                json={
                    "title": "Basic Kimi memory",
                    "summary": "No Generation or authentication",
                    "idempotency_key": "kimi-basic",
                },
            )
            new_scope.raise_for_status()
            gateway.scope_id = new_scope.json()["scope_id"]
            monkeypatch.delenv("POWERCONTEXT_KIMI_TOKEN")
            install_kimi_plugin(source=REPOSITORY, server_url=basic_endpoint, scope_id=gateway.scope_id)
            assert all(check.ok for check in run_kimi_diagnostics().values())
            start = len(gateway.requests)
            invoke("PC_KIMI_SAVE: Save explicitly without Generation.", token=None)
            assert any(
                EXPLICIT in json.dumps(m) and m.get("tool_call_id") == "call-get_artifact_revision"
                for request in gateway.requests[start:]
                for m in request["messages"]
                if m["role"] == "tool"
            )
            invoke("PC_KIMI_SEARCH: Search in basic memory mode.", token=None)

            install_kimi_plugin(source=REPOSITORY, workspace_scope=True, capture_prompts=True)
            default = basic.put("/v1/scopes/default", json={"scope_id": gateway.scope_id})
            default.raise_for_status()
            second_scope = basic.post(
                "/v1/scopes",
                json={
                    "title": "Second Kimi workspace",
                    "summary": "Isolated workspace",
                    "idempotency_key": "kimi-second",
                },
            )
            second_scope.raise_for_status()
            other_scope_id = second_scope.json()["scope_id"]
            other_workspace = tmp_path / "other workspace"
            other_workspace.mkdir()
            scope_script = Path(installed.plugin_path) / "scripts/scope.mjs"

            def scope_action(action, cwd, target=None):
                arguments = [node, str(scope_script), action, "--cwd", str(cwd)]
                if target is not None:
                    arguments.extend(["--scope-id", target])
                result = subprocess.run(
                    arguments, capture_output=True, text=True, encoding="utf-8", check=True, timeout=6
                )
                return json.loads(result.stdout)

            for cwd, target, label, other_label in (
                (workspace, gateway.scope_id, "FIRST", "second-workspace"),
                (other_workspace, other_scope_id, "SECOND", "first-workspace"),
            ):
                bound = scope_action("bind", cwd, target)
                assert bound["binding"]["status"] == "saved"
                assert bound["scope_id"] == target
                invoke(f"PC_KIMI_SAVE WORKSPACE_{label}: Save this workspace decision.", token=None, cwd=cwd)
                start = len(gateway.requests)
                recall = invoke("PC_KIMI_SEARCH: Find this workspace decision.", token=None, cwd=cwd)
                tool_results = [
                    message
                    for request in gateway.requests[start:]
                    for message in request["messages"]
                    if message["role"] == "tool"
                ]
                serialized = json.dumps(tool_results)
                assert label.lower() + "-workspace" in serialized, serialized
                assert other_label not in serialized, serialized
                assert all(
                    obj.get("scope_id", target) == target
                    for request in gateway.requests[start:]
                    for obj in _objects(request["messages"])
                    if obj.get("schema") == "powercontext.kimi-code.request-binding.v1"
                )
                observed = run_kimi_diagnostics(runtime=True, cwd=cwd)
                assert observed["scope"].checks is not None
                assert observed["scope"].checks["scope_id"] == target
                assert observed["runtime"].checks is not None
                assert observed["runtime"].checks["scope_id"] == target
                assert observed["runtime"].checks["capture"] == "accepted"
                captured = basic.get(f"/v1/scopes/{target}/sources")
                captured.raise_for_status()
                assert any(item["source_id"].startswith("kimi-user-prompt:") for item in captured.json()["items"])
            install_kimi_plugin(source=REPOSITORY, scope_id=gateway.scope_id)
            shadowed = scope_action("bind", other_workspace, other_scope_id)
            assert shadowed["binding"]["status"] == "saved"
            assert shadowed["bound_scope_is_current"] is False
            assert shadowed["scope_id"] == gateway.scope_id
            install_kimi_plugin(source=REPOSITORY, workspace_scope=True)
            assert scope_action("resolve", other_workspace)["scope_id"] == other_scope_id
            assert scope_action("unbind", other_workspace)["binding"]["status"] == "cleared"
            assert scope_action("resolve", other_workspace)["scope_id"] == gateway.scope_id
            assert scope_action("resolve", workspace)["scope_id"] == gateway.scope_id
        (tmp_path / "evidence.json").write_text(
            json.dumps(
                {
                    "host_version": version.stdout.strip(),
                    "platform": os.name,
                    "inference": "controlled",
                    "installation": installed.plugin_path,
                    "explicit_memory": True,
                    "exact_readback": True,
                    "source_generation": True,
                    "fresh_session_injection": True,
                    "runtime_observation": True,
                    "workspace_scope_isolation": True,
                    "workspace_scope_bind_unbind": True,
                    "capture_disabled": True,
                    "wrong_token_fail_open": True,
                    "outage_fail_open": True,
                    "basic_memory_without_generation_or_token": True,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
