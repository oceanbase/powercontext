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

"""Actual `codex exec` with the PowerContext plugin against a real PowerContext Server.

`powercontext setup codex` installs the plugin into an isolated Codex home from this checkout and saves the Server
credential there. The hooks are then trusted through Codex's own app-server, as `/hooks` does in the TUI. The host
model is a streamed Responses API fake and the Server's generation model a Chat Completions fake. These cases
establish the local Codex, plugin, and Server chain and the plugin's documented behavior; they do not qualify
live-model behavior, the TUI, or Codex Desktop.
"""

from __future__ import annotations

import hashlib
import json
import os
import queue
import subprocess
import sys
import threading
from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import AnyHttpUrl, SecretStr

from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import InferenceConfig, RuntimeConfig
from powercontext.server.factory import create_server_app
from powercontext.server.settings import BearerAuthConfig, ServerSettings
from tests.e2e.host_models.responses import ResponsesModelFixture
from tests.e2e.zcode_acceptance.protocol import HostModelFixture, ObserveServer, WireEvidence
from tests.e2e.zcode_acceptance.runner import serve

REPOSITORY = Path(__file__).resolve().parents[2]
TOKEN = "codex-host-service-chain-token"  # noqa: S105 - non-secret test credential.
FACT_MARKER = "teal-728"
FACT = f"In the synthetic orchid project, the verification color is {FACT_MARKER}."
QUESTION = "Which verification color does the synthetic orchid project use?"


@dataclass
class CodexTurn:
    thread_id: str
    events: list[dict[str, Any]]

    def tool_calls(self) -> list[dict[str, Any]]:
        """MCP tool calls as `codex exec` finished them."""
        return [
            event["item"]
            for event in self.events
            if event["type"] == "item.completed" and event["item"]["type"] == "mcp_tool_call"
        ]


@dataclass
class CodexStack:
    executable: str
    workspace: Path
    environment: dict[str, str]
    log: Path
    model: ResponsesModelFixture
    generation: HostModelFixture
    wire: WireEvidence
    server: httpx.Client
    plugin_id: str

    def run(self, prompt: str, *, resume: str | None = None, **settings: str | None) -> CodexTurn:
        """Run one `codex exec` turn, in a new session or resuming `resume`; None removes a plugin setting."""
        environment = dict(self.environment)
        for name, value in settings.items():
            if value is None:
                environment.pop(name, None)
            else:
                environment[name] = value
        command = [self.executable, "exec", "--json", "--cd", str(self.workspace)]
        if resume is not None:
            command += ["resume", resume]
        command.append(prompt)
        try:
            completed = subprocess.run(
                command,
                cwd=self.workspace,
                env=environment,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
        except subprocess.TimeoutExpired as timeout:
            # Keep what Codex printed before it hung; CI uploads this log on failure.
            with self.log.open("a", encoding="utf-8") as log:
                log.write(f"$ codex {' '.join(command[1:])}\n{_text(timeout.stdout)}\n{_text(timeout.stderr)}\n")
            raise
        with self.log.open("a", encoding="utf-8") as log:
            log.write(f"$ codex {' '.join(command[1:])}\n{completed.stdout}\n{completed.stderr}\n")
        assert completed.returncode == 0, f"codex_exec_exit_{completed.returncode}; see {self.log}"
        try:
            events = [json.loads(line) for line in completed.stdout.splitlines() if line.startswith("{")]
        except ValueError:
            pytest.fail(f"codex_exec_invalid_event; see {self.log}", pytrace=False)
        assert any(event["type"] == "turn.completed" for event in events), f"codex_turn_incomplete; see {self.log}"
        (thread_id,) = [event["thread_id"] for event in events if event["type"] == "thread.started"]
        return CodexTurn(thread_id, events)

    def approve_tool(self, tool: str) -> None:
        """Persist the user's approval of one PowerContext MCP tool in the Codex configuration."""
        approval = {"mcp_servers": {"powercontext": {"tools": {tool: {"approval_mode": "approve"}}}}}
        with _app_server(self.executable, self.environment, self.workspace, self.log) as call:
            call(
                "config/batchWrite",
                {"edits": [{"keyPath": "plugins", "mergeStrategy": "upsert", "value": {self.plugin_id: approval}}]},
            )

    def create_scope(self, title: str) -> str:
        response = self.server.post(
            "/v1/scopes",
            json={"title": title, "summary": "Disposable Codex host acceptance.", "idempotency_key": title},
        )
        response.raise_for_status()
        return response.json()["scope_id"]

    def bind_workspace(self, scope_id: str) -> None:
        # The plugin keys a workspace by the SHA-256 of its resolved Git root.
        external_id = hashlib.sha256(str(self.workspace.resolve()).encode()).hexdigest()
        response = self.server.put(
            "/v1/scope-bindings",
            json={
                "key": {"integration": "codex", "kind": "workspace", "external_id": external_id},
                "scope_id": scope_id,
            },
        )
        response.raise_for_status()

    def session_scope(self, thread_id: str) -> str:
        """The Scope that Codex session `thread_id` is bound to on the Server."""
        response = self.server.post(
            "/v1/scope-bindings/resolve",
            json={"binding_keys": [{"integration": "codex", "kind": "session", "external_id": thread_id}]},
        )
        response.raise_for_status()
        return response.json()["scope_id"]

    def remember(self, scope_id: str, text: str) -> str:
        response = self.server.post(
            "/v1/memory/remember", json={"scope_id": scope_id, "kind": "decision", "text": text}
        )
        response.raise_for_status()
        return response.json()["records"][0]["artifact"]["artifact_id"]

    def flush(self, scope_id: str) -> None:
        """Process every captured Source, as the Server's schedule would; this stack runs none."""
        for _ in range(8):
            response = self.server.post("/v1/memory/flush", json={"scope_id": scope_id})
            response.raise_for_status()
            result = response.json()
            if result["current_cursor"] >= result["high_watermark"] and not result.get("remaining_work"):
                return
        raise AssertionError("memory_flush_did_not_drain")

    def memories(self, scope_id: str) -> dict[str, dict[str, Any]]:
        response = self.server.post("/v1/memory/entries/list", json={"scope_id": scope_id})
        response.raise_for_status()
        return {entry["text"]: entry for entry in response.json()["entries"]}

    def sources(self, scope_id: str) -> list[dict[str, Any]]:
        response = self.server.get(f"/v1/scopes/{scope_id}/sources")
        response.raise_for_status()
        return response.json()["items"]

    def records(self, path: str) -> list[dict[str, Any]]:
        return [record for record in self.wire.records if record["path"] == path]


@pytest.fixture
def codex_stack(tmp_path: Path) -> Iterator[CodexStack]:
    executable = os.environ.get("CODEX_CLI_BIN")
    if not executable:
        pytest.skip("Set CODEX_CLI_BIN to a real Codex CLI; CI runs this in the codex-host job")
    assert Path(executable).is_file(), "CODEX_CLI_BIN must name an existing Codex CLI"
    workspace = tmp_path / "workspace"
    home = tmp_path / "home"
    codex_home = home / ".codex"
    for directory in (workspace, codex_home, home / "config", home / "data", home / "cache", home / "state"):
        directory.mkdir(parents=True)
    # The plugin keys workspace bindings by Git root.
    subprocess.run(["git", "init", "--quiet", str(workspace)], check=True)  # noqa: S607 - Git from PATH.
    model = ResponsesModelFixture()
    generation = HostModelFixture()
    wire = WireEvidence()
    with ExitStack() as stack:
        model_url = stack.enter_context(serve(model.app))
        generation_url = stack.enter_context(serve(generation.app))
        settings = ServerSettings(
            _env_file=None,  # ty: ignore[unknown-argument] BaseSettings runtime option.
            workspace=workspace,
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'server.db'}"),
            auth=BearerAuthConfig(enabled=True, token=SecretStr(TOKEN)),
            # No memory schedule: captured prompts become Memory only when a case flushes them.
            runtime=RuntimeConfig(artifact_processing_families=("memory",)),
            inference=InferenceConfig(
                generation_model="openai-chat:memory-fixture",
                generation_base_url=AnyHttpUrl(generation_url + "/v1"),
                generation_headers={"Authorization": SecretStr("Bearer synthetic-generation-only")},
            ),
        )
        app = create_server_app(settings=settings, scheduler_path=tmp_path / "scheduler")
        base_url = stack.enter_context(serve(ObserveServer(app, wire)))
        server = stack.enter_context(
            httpx.Client(base_url=base_url, headers={"Authorization": f"Bearer {TOKEN}"}, timeout=15)
        )
        (codex_home / "config.toml").write_text(_codex_config(model_url), encoding="utf-8")
        environment = _isolated_environment(home, codex_home, tmp_path)
        log = tmp_path / "codex.log"
        _install_plugin(executable, environment, base_url, tmp_path, log)
        plugin_id = _trust_plugin_hooks(executable, environment, workspace, log)
        yield CodexStack(executable, workspace, environment, log, model, generation, wire, server, plugin_id)


def _text(output: str | bytes | None) -> str:
    return output.decode(errors="replace") if isinstance(output, bytes) else output or ""


def _codex_config(model_url: str) -> str:
    # Codex 0.167 accepts only the Responses wire API for a custom provider. Plain HTTP streaming and no retries keep
    # a fixture failure from turning into a long wait. The plugin's MCP server is optional, and Codex otherwise lists
    # the first turn's tools after one second whether or not it has connected. A grace of 0 makes Codex wait for it,
    # up to the server's 10 s startup timeout, before the first model request.
    return f"""\
model = "fixture"
model_provider = "fixture"
approval_policy = "never"
sandbox_mode = "read-only"
web_search = "disabled"
check_for_update_on_startup = false
mcp_optional_startup_grace_ms = 0

[analytics]
enabled = false

[model_providers.fixture]
name = "fixture"
base_url = "{model_url}/v1"
wire_api = "responses"
env_key = "CODEX_FIXTURE_MODEL_KEY"
requires_openai_auth = false
supports_websockets = false
request_max_retries = 0
stream_max_retries = 0
"""


def _isolated_environment(home: Path, codex_home: Path, tmp_path: Path) -> dict[str, str]:
    # Setup builds the plugin's environment with uv; keep the runner's uv cache so it can do so offline.
    uv_cache = subprocess.run(["uv", "cache", "dir"], capture_output=True, text=True, check=True).stdout.strip()  # noqa: S607
    # Proxies would route the loopback Server and model traffic away from this host.
    prefixes = ("POWERCONTEXT_", "CODEX_", "OPENAI_", "VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT")
    environment = {
        name: value
        for name, value in os.environ.items()
        if not name.upper().startswith(prefixes) and not name.upper().endswith("_PROXY")
    }
    environment.update(
        HOME=str(home),
        USERPROFILE=str(home),
        CODEX_HOME=str(codex_home),
        XDG_CONFIG_HOME=str(home / "config"),
        XDG_DATA_HOME=str(home / "data"),
        XDG_CACHE_HOME=str(home / "cache"),
        XDG_STATE_HOME=str(home / "state"),
        UV_CACHE_DIR=uv_cache,
        # Setup and the hooks both read this, so the hooks reuse the environment setup built; uv rebuilds it for a
        # different interpreter. Without managed interpreters in the isolated home, both use the one running the tests.
        UV_PYTHON=sys.executable,
        POWERCONTEXT_CLIENT_CONFIG_FILE=str(tmp_path / "clients.json"),
        CODEX_FIXTURE_MODEL_KEY="synthetic-only",
        # A loaded runner must not turn a slow Server reply into a silently skipped preparation or capture.
        POWERCONTEXT_CODEX_REQUEST_TIMEOUT_SECONDS="5",
        POWERCONTEXT_CODEX_HTTP_BUDGET_SECONDS="8",
    )
    return environment


def _install_plugin(executable: str, environment: dict[str, str], base_url: str, tmp_path: Path, log: Path) -> None:
    """Run `powercontext setup codex` from this checkout, with the Codex under test first on PATH."""
    setup_environment = dict(environment)
    setup_environment["PATH"] = os.pathsep.join((str(Path(executable).parent), environment.get("PATH", "")))
    # Setup saves this credential for the hooks and the MCP header helper; `codex exec` runs without it.
    setup_environment["POWERCONTEXT_CODEX_AUTHORIZATION"] = f"Bearer {TOKEN}"
    setup_environment["PYTHONPATH"] = str(REPOSITORY / "src")
    script = (
        "import sys; from powercontext.cli.installation import setup; "
        "setup('codex', source=sys.argv[1], server_url=sys.argv[2])"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script, str(REPOSITORY), base_url],
        cwd=tmp_path,
        env=setup_environment,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    log.write_text(f"$ powercontext setup codex\n{completed.stdout}\n{completed.stderr}\n", encoding="utf-8")
    assert completed.returncode == 0, f"powercontext_setup_codex_failed; see {log}"


@contextmanager
def _app_server(
    executable: str, environment: dict[str, str], workspace: Path, log: Path
) -> Iterator[Callable[..., Any]]:
    """Call Codex's own app-server over stdio, as its TUI does."""
    with log.open("a", encoding="utf-8") as stderr:
        process = subprocess.Popen(
            [executable, "app-server"],
            cwd=workspace,
            env=environment,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=stderr,
            text=True,
        )
    assert process.stdin is not None and process.stdout is not None
    stdin, stdout = process.stdin, process.stdout
    lines: queue.Queue[str] = queue.Queue()
    threading.Thread(target=lambda: [lines.put(line) for line in stdout], daemon=True).start()
    identifiers = iter(range(1, 1_000))

    def call(method: str, params: dict[str, Any]) -> Any:
        identifier = next(identifiers)
        stdin.write(json.dumps({"jsonrpc": "2.0", "id": identifier, "method": method, "params": params}) + "\n")
        stdin.flush()
        while True:
            # Notifications may precede the response.
            try:
                message = json.loads(lines.get(timeout=30))
            except queue.Empty:
                raise AssertionError(f"codex_app_server_{method}_timeout") from None
            if message.get("id") == identifier:
                assert "error" not in message, f"codex_app_server_{method}_failed"
                return message["result"]

    try:
        call("initialize", {"clientInfo": {"name": "powercontext-e2e", "version": "0"}})
        stdin.write(json.dumps({"jsonrpc": "2.0", "method": "initialized"}) + "\n")
        yield call
    finally:
        stdin.close()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)


def _trust_plugin_hooks(executable: str, environment: dict[str, str], workspace: Path, log: Path) -> str:
    """Trust each plugin hook at the hash Codex reports, as the TUI's `/hooks` review does; return the plugin id."""
    with _app_server(executable, environment, workspace, log) as call:
        hooks = [
            hook
            for entry in call("hooks/list", {"cwds": [str(workspace)]})["data"]
            for hook in entry["hooks"]
            if (hook.get("pluginId") or "").startswith("powercontext@")
        ]
        assert {hook["eventName"] for hook in hooks} == {"sessionStart", "userPromptSubmit", "preToolUse", "stop"}
        state = {hook["key"]: {"trusted_hash": hook["currentHash"]} for hook in hooks}
        call("config/batchWrite", {"edits": [{"keyPath": "hooks.state", "mergeStrategy": "upsert", "value": state}]})
        trusted = call("hooks/list", {"cwds": [str(workspace)]})["data"]
        assert {hook["trustStatus"] for entry in trusted for hook in entry["hooks"] if hook["key"] in state} == {
            "trusted"
        }
    return hooks[0]["pluginId"]


def test_codex_prompt_is_recalled_in_a_later_session(codex_stack: CodexStack) -> None:
    credential = "sk-codexe2ecredential0123456789"
    first = codex_stack.run(FACT)
    codex_stack.run(f"Deploy the synthetic orchid project with {credential} today.", resume=first.thread_id)
    scope_id = codex_stack.session_scope(first.thread_id)
    # The credential prompt reached the model but was not captured.
    assert [item["content"] for item in codex_stack.sources(scope_id)] == [FACT]
    assert codex_stack.records("/v1/sources/content")[0]["request"]["metadata"]["session_id"] == first.thread_id
    codex_stack.flush(scope_id)
    assert FACT in codex_stack.memories(scope_id)

    start = len(codex_stack.model.requests)
    second = codex_stack.run(QUESTION)
    assert codex_stack.session_scope(second.thread_id) == scope_id
    prepared = codex_stack.records("/v1/context/prepare")[-1]["response"]
    assert prepared["status"] == "ready"
    assert FACT_MARKER in prepared["content"]
    # The new session's first model request carries the prepared context unchanged, after the user's prompt.
    sent = codex_stack.model.texts(codex_stack.model.requests[start])
    assert 0 <= sent.find(QUESTION) < sent.find(prepared["content"])

    queries = [record["request"]["query"] for record in codex_stack.records("/v1/context/prepare")]
    assert queries == [FACT, "Deploy the synthetic orchid project with [REDACTED] today.", QUESTION]
    server_side = [codex_stack.wire.arrivals, codex_stack.wire.records, codex_stack.generation.generation_requests]
    assert credential not in json.dumps(server_side)


def test_codex_resumed_session_keeps_its_scope(codex_stack: CodexStack) -> None:
    first_scope = codex_stack.create_scope("First Codex workspace")
    second_scope = codex_stack.create_scope("Second Codex workspace")
    codex_stack.bind_workspace(first_scope)
    first = codex_stack.run("First synthetic orchid prompt.")

    # A later workspace binding governs new sessions; a resumed one keeps the Scope it resolved at startup.
    codex_stack.bind_workspace(second_scope)
    codex_stack.run("Resumed synthetic orchid prompt.", resume=first.thread_id)
    fresh = codex_stack.run("Fresh synthetic orchid prompt.")

    assert [item["content"] for item in codex_stack.sources(first_scope)] == [
        "First synthetic orchid prompt.",
        "Resumed synthetic orchid prompt.",
    ]
    assert [item["content"] for item in codex_stack.sources(second_scope)] == ["Fresh synthetic orchid prompt."]
    assert (codex_stack.session_scope(first.thread_id), codex_stack.session_scope(fresh.thread_id)) == (
        first_scope,
        second_scope,
    )
    bootstraps = [record["request"]["lifecycle"] for record in codex_stack.records("/v1/context/bootstrap")]
    assert bootstraps == ["startup", "resume", "startup"]


def test_codex_bootstrap_context_reaches_the_first_model_request(codex_stack: CodexStack) -> None:
    scope_id = codex_stack.create_scope("Codex bootstrap")
    codex_stack.bind_workspace(scope_id)
    bootstrap = "The synthetic orchid project always runs its release checklist before tagging."
    artifact_id = codex_stack.remember(scope_id, bootstrap)
    path = f"/v1/scopes/{scope_id}/artifacts/atomic-memory/{artifact_id}/tags"
    tags = codex_stack.server.get(path)
    tags.raise_for_status()
    codex_stack.server.put(
        path, json={"tags": ["bootstrap-context"]}, headers={"If-Match": tags.headers["ETag"]}
    ).raise_for_status()

    prompt = "Start the synthetic orchid release."
    codex_stack.run(prompt, POWERCONTEXT_CODEX_BOOTSTRAP_CONTEXT="true")
    # Startup context precedes the first prompt, and the turn's own preparation does not repeat it.
    sent = codex_stack.model.texts(codex_stack.model.requests[-1])
    assert sent.count(bootstrap) == 1
    assert 0 <= sent.find(bootstrap) < sent.find(prompt)
    receipts = codex_stack.records("/v1/context/bootstrap/receipts")
    assert [record["request"]["outcome"] for record in receipts] == ["injected"]
    prepared = codex_stack.records("/v1/context/prepare")[-1]
    assert prepared["request"]["bootstrap_receipt_id"] == receipts[0]["request"]["receipt_id"]
    assert prepared["response"]["status"] == "empty"

    # Bootstrap context is opt-in; without it only prompt-time recall supplies the Memory, after the prompt.
    codex_stack.run(prompt)
    sent = codex_stack.model.texts(codex_stack.model.requests[-1])
    assert 0 <= sent.find(prompt) < sent.find(bootstrap)
    assert "BEGIN_POWERCONTEXT_BOOTSTRAP_CONTEXT_V1" not in sent
    assert "bootstrap_receipt_id" not in codex_stack.records("/v1/context/prepare")[-1]["request"]


def test_codex_memory_tools_write_to_the_session_scope_after_approval(codex_stack: CodexStack) -> None:
    other_scope = codex_stack.create_scope("Another Codex project")
    decision = "The synthetic kestrel project uses coral-512 as its verification color."
    # The model names another Scope; the plugin binds every data-plane call to the session's own Scope.
    remember = ("remember_memory", {"kind": "decision", "text": decision, "scope_id": other_scope})
    codex_stack.model.plan([remember])
    declined = codex_stack.run("Save the kestrel verification color decision.")
    scope_id = codex_stack.session_scope(declined.thread_id)
    # A Memory write needs approval, which `codex exec` cannot ask for, so nothing reaches the Server.
    assert [(call["tool"], call["status"]) for call in declined.tool_calls()] == [("remember_memory", "failed")]
    assert codex_stack.wire.calls("remember_memory") == []
    assert codex_stack.memories(scope_id) == {}

    codex_stack.approve_tool("remember_memory")
    codex_stack.model.plan([remember, ("search_memory", {"query": "kestrel verification color", "mode": "fts"})])
    approved = codex_stack.run("Yes, save it and check that it is there.", resume=declined.thread_id)
    assert [(call["tool"], call["status"]) for call in approved.tool_calls()] == [
        ("remember_memory", "completed"),
        ("search_memory", "completed"),
    ]
    assert decision in codex_stack.memories(scope_id)
    assert codex_stack.memories(other_scope) == {}
    found = codex_stack.wire.result("search_memory")
    assert decision in json.dumps(found)


def test_codex_recall_and_capture_settings_are_independent(codex_stack: CodexStack) -> None:
    turn = codex_stack.run(FACT, POWERCONTEXT_CODEX_PROMPT_RECALL="false")
    scope_id = codex_stack.session_scope(turn.thread_id)
    assert codex_stack.records("/v1/context/prepare") == []
    assert [item["content"] for item in codex_stack.sources(scope_id)] == [FACT]

    codex_stack.run(QUESTION, POWERCONTEXT_CODEX_CAPTURE_PROMPTS="false")
    assert [record["request"]["query"] for record in codex_stack.records("/v1/context/prepare")] == [QUESTION]
    assert [item["content"] for item in codex_stack.sources(scope_id)] == [FACT]

    unsent = "A synthetic orchid prompt that stays on this host."
    codex_stack.run(unsent, POWERCONTEXT_CODEX_PROMPT_RECALL="false", POWERCONTEXT_CODEX_CAPTURE_PROMPTS="false")
    assert unsent in codex_stack.model.texts(codex_stack.model.requests[-1])
    assert unsent not in json.dumps([codex_stack.wire.arrivals, codex_stack.wire.records])
