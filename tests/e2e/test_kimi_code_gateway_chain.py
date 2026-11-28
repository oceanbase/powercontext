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

"""Real Kimi Code behind the PowerContext gateway, with the plugin's prompt-time recall and capture off.

Kimi calls the Rust gateway's Responses route; the gateway prepares and injects context, checks every request with a
real PowerContext Server over its Unix socket, captures the turn, and calls a deterministic local Responses fixture.
No live model is called. The cases establish the documented Kimi gateway setup; they do not qualify live-model behavior.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
from collections.abc import Iterator
from contextlib import ExitStack
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import pytest
from starlette.requests import Request
from starlette.responses import Response

from powercontext.cli.installation import setup as install_plugin
from powercontext.cli.kimi_code import KimiInstaller
from tests.e2e.host_models.responses import ResponsesModelFixture
from tests.e2e.host_models.script import FINAL_ANSWER
from tests.e2e.test_gateway_authentication import gateway_environment, running_gateway
from tests.e2e.test_server_uds import _TOKEN, _environment, _running_server
from tests.e2e.zcode_acceptance.runner import serve

pytestmark = [
    pytest.mark.skipif(sys.platform not in {"linux", "darwin"}, reason="Gateway Core link requires UDS"),
    pytest.mark.skipif(
        not os.environ.get("KIMI_CLI_BIN"), reason="Set KIMI_CLI_BIN to a real Kimi binary or built main.mjs"
    ),
]

REPOSITORY = Path(__file__).parents[2]
GATEWAY_KEY = "synthetic-kimi-gateway-key"
PROFILE = "kimi-gateway"
MODEL = "kimi-fixture"
MAX_OUTPUT = 4096
FACT = "In the synthetic heron project, the release color is amber-264."
PREPARED_MARKERS = ("BEGIN_POWERCONTEXT_PREPARED_CONTEXT_V1", "BEGIN_POWERCONTEXT_PREPARED_TEXT_V1")
PLUGIN_BINDING = "powercontext.kimi-code.request-binding.v1"
# Headers the gateway accepts as a conversation id (`conversation_id` in gateway ingress).
CONVERSATION_HEADERS = (
    "x-powercontext-conversation-id",
    "x-claude-code-session-id",
    "x-opencode-session-id",
    "thread-id",
)


@dataclass
class HeaderRecorder:
    """Forward Kimi's model requests to the gateway unchanged and keep the headers Kimi sent."""

    gateway: str
    headers: list[dict[str, str]] = field(default_factory=list)
    statuses: list[tuple[int, bytes]] = field(default_factory=list)

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        request = Request(scope, receive)
        self.headers.append({key.lower(): value for key, value in request.headers.items()})
        forwarded = {
            key: value for key, value in request.headers.items() if key.lower() not in {"host", "content-length"}
        }
        async with httpx.AsyncClient(base_url=self.gateway, trust_env=False, timeout=60) as client:
            upstream = await client.request(
                request.method, request.url.path, headers=forwarded, content=await request.body()
            )
        self.statuses.append((upstream.status_code, upstream.content[:2000]))
        response = Response(upstream.content, status_code=upstream.status_code)
        if "content-type" in upstream.headers:
            response.headers["content-type"] = upstream.headers["content-type"]
        await response(scope, receive, send)


@dataclass
class KimiGatewayStack:
    command: list[str]
    workspace: Path
    environment: dict[str, str]
    model: ResponsesModelFixture
    recorder: HeaderRecorder
    core: httpx.Client
    scope_id: str
    status_script: Path

    def run(
        self, prompt: str, *arguments: str, check: bool = True, **environment: str
    ) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            [*self.command, "-p", prompt, "--output-format", "stream-json", *arguments],
            env=self.environment | environment,
            cwd=self.workspace,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
            check=False,
        )
        if check:
            assert result.returncode == 0, result.stdout[-5000:] + result.stderr[-5000:]
            assert FINAL_ANSWER in result.stdout, result.stdout[-5000:] + result.stderr[-5000:]
        return result

    def status(self, session_id: str) -> dict[str, Any]:
        """Return the plugin's latest observation for one Kimi session."""
        result = subprocess.run(
            [self.command[0], str(self.status_script), "--cwd", str(self.workspace), "--session-id", session_id],
            env=self.environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=10,
            check=True,
        )
        return json.loads(result.stdout)

    def sources(self) -> list[dict[str, Any]]:
        response = self.core.get(f"/v1/scopes/{self.scope_id}/sources")
        response.raise_for_status()
        return response.json()["items"]


class AcpSession:
    """One live Kimi session over the Agent Client Protocol, which takes several prompts in turn."""

    def __init__(self, stack: KimiGatewayStack) -> None:
        self._log_path = stack.workspace.with_name("kimi-acp.log")
        self._log = self._log_path.open("w", encoding="utf-8")
        self.process = subprocess.Popen(
            [*stack.command, "acp"],
            env=stack.environment,
            cwd=stack.workspace,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self._log,
            text=True,
            encoding="utf-8",
        )
        assert self.process.stdin is not None and self.process.stdout is not None
        self._input, self._output = self.process.stdin, self.process.stdout
        self._id = 0
        try:
            self.call("initialize", {"protocolVersion": 1, "clientCapabilities": {}})
            self.session_id = self.call("session/new", {"cwd": str(stack.workspace), "mcpServers": []})[0]["sessionId"]
        except BaseException:
            self.close()
            raise

    def call(self, method: str, params: dict[str, Any]) -> tuple[dict[str, Any], str]:
        """Send one request and return its result with the agent's message text streamed meanwhile."""
        self._id += 1
        self._send({"id": self._id, "method": method, "params": params})
        text = ""
        timer = threading.Timer(60, self.process.kill)
        timer.start()
        try:
            for line in self._output:
                message = json.loads(line)
                if message.get("id") == self._id and "method" not in message:
                    assert "result" in message, message
                    return message["result"], text
                update = message.get("params", {}).get("update", {})
                if update.get("sessionUpdate") == "agent_message_chunk":
                    text += update.get("content", {}).get("text", "")
                if message.get("method") == "session/request_permission":
                    allow = next(item for item in message["params"]["options"] if item["kind"].startswith("allow"))
                    self._send({
                        "id": message["id"],
                        "result": {"outcome": {"outcome": "selected", "optionId": allow["optionId"]}},
                    })
        finally:
            timer.cancel()
        log = self._log_path.read_text(encoding="utf-8")[-3000:]
        pytest.fail(f"kimi_acp_exited_during_{method}: {log}")

    def prompt(self, text: str) -> str:
        result, reply = self.call(
            "session/prompt", {"sessionId": self.session_id, "prompt": [{"type": "text", "text": text}]}
        )
        assert result["stopReason"] == "end_turn", result
        return reply

    def close(self) -> None:
        try:
            self._input.close()
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()
        finally:
            self._log.close()

    def _send(self, message: dict[str, Any]) -> None:
        self._input.write(json.dumps({"jsonrpc": "2.0", **message}) + "\n")
        self._input.flush()


def _session_id(output: str) -> str:
    return next(
        event["session_id"]
        for line in output.splitlines()
        if line.startswith("{") and isinstance((event := json.loads(line)).get("session_id"), str)
    )


def _prompt_index(request: dict[str, Any], prompt: str) -> int:
    """Index of the latest user input item that carries the prompt."""
    items = request["input"]
    return max(index for index, item in enumerate(items) if item.get("role") == "user" and prompt in json.dumps(item))


def _reminders_after(request: dict[str, Any], prompt: str) -> list[str]:
    """Kimi's reminder items that follow the prompt."""
    items = request["input"][_prompt_index(request, prompt) + 1 :]
    return [text for item in items if item.get("role") == "user" and "<system-reminder>" in (text := json.dumps(item))]


def _gateway_binary() -> Path:
    cargo = shutil.which("cargo")
    assert cargo, "Kimi gateway acceptance needs the Rust toolchain to build the gateway"
    subprocess.run(
        [cargo, "build", "--locked", "--manifest-path", str(REPOSITORY / "gateway/Cargo.toml"), "-p", "pc-gateway-app"],
        check=True,
        timeout=900,
    )
    return REPOSITORY / "gateway/target/debug/pc-gateway"


@pytest.fixture
def kimi_gateway(tmp_path: Path, short_tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[KimiGatewayStack]:
    cli = Path(os.environ["KIMI_CLI_BIN"])
    node = shutil.which("node")
    assert cli.is_file(), "KIMI_CLI_BIN must be an existing real Kimi CLI"
    assert node, "Node is required by the plugin"
    gateway_binary = _gateway_binary()
    home, kimi_home, workspace = tmp_path / "home", tmp_path / "kimi home", tmp_path / "workspace"
    for directory in (home, kimi_home, workspace):
        directory.mkdir()
    model = ResponsesModelFixture()
    with ExitStack() as stack:
        upstream = stack.enter_context(serve(model.app)) + "/v1/responses"
        server_environment = _environment(tmp_path) | {"POWERCONTEXT_SERVER_MCP_ENABLED": "true"}
        # An enforcing Core admits Kimi's requests: Kimi sends max_output_tokens, and its prompt_cache_key is
        # approved by the provider-default retention.
        server_environment["POWERCONTEXT_SERVER_INFERENCE"] = json.dumps({
            "protection": {
                "mode": "enforcing",
                "policy_version": "kimi-gateway.v1",
                "targets": {
                    PROFILE: {
                        "endpoint": upstream,
                        "model": MODEL,
                        "protocol": "openai-responses",
                        # The UTF-8 bound counts a byte as a token; Kimi's system prompt and tools alone exceed a
                        # small window under it.
                        "context_window_tokens": 1_000_000,
                        "max_output_tokens": MAX_OUTPUT,
                        "validated_utf8_token_bound": True,
                        "allowed_cache_retention": ["provider-default"],
                    }
                },
            }
        })
        _, core = stack.enter_context(_running_server(tmp_path, short_tmp_path / "core.sock", server_environment))
        created = core.post(
            "/v1/scopes",
            json={
                "title": "Kimi gateway",
                "summary": "Disposable Kimi gateway acceptance.",
                "idempotency_key": "kimi-gw",
            },
        )
        assert created.status_code == 201, created.text
        scope_id = created.json()["scope_id"]
        remembered = core.post("/v1/memory/remember", json={"scope_id": scope_id, "kind": "decision", "text": FACT})
        assert remembered.status_code in {200, 201}, remembered.text
        gateway = stack.enter_context(
            running_gateway(
                gateway_binary,
                gateway_environment()
                | {
                    "PC_GATEWAY_CORE_UDS": str(short_tmp_path / "core.sock"),
                    "PC_GATEWAY_CORE_TOKEN": _TOKEN,
                    "PC_GATEWAY_API_KEY": GATEWAY_KEY,
                    "PC_GATEWAY_UPSTREAM_API_KEY": "synthetic-provider-key",
                    "PC_GATEWAY_UPSTREAM_PROFILE": PROFILE,
                    "PC_GATEWAY_UPSTREAM_URL_RESPONSES": upstream,
                    "PC_GATEWAY_SEDIMENT": "on",
                    # No PC_GATEWAY_INJECTION and no default Scope: the combined deployment infers Kimi's conversation,
                    # and Kimi's provider selects the Scope with a static header.
                },
                tmp_path,
            )
        )
        recorder = HeaderRecorder(str(gateway.base_url))
        model_url = stack.enter_context(serve(recorder))
        # The provider the guide documents: the gateway's Responses route, with the gateway key as the API key.
        (kimi_home / "config.toml").write_text(
            'default_model = "fixture"\ntelemetry = false\nauto_session_title = false\n'
            "builtin_product_skills = false\n"
            # A rejected request fails the turn at once instead of being retried with backoff.
            "[loop_control]\nmax_attempts_per_step = 1\n"
            '[providers.gateway]\ntype = "openai_responses"\n'
            f'api_key = "{GATEWAY_KEY}"\nbase_url = "{model_url}/gateway/v1"\n'
            f'[providers.gateway.custom_headers]\n"X-PowerContext-Scope-ID" = "{scope_id}"\n'
            f'[models.fixture]\nprovider = "gateway"\nmodel = "{MODEL}"\nmax_context_size = 131072\n'
            f'max_output_size = {MAX_OUTPUT}\ncapabilities = ["tool_use"]\n'
            # The same model without the gateway, for a session that starts before Kimi is moved behind it.
            '[providers.direct]\ntype = "openai_responses"\napi_key = "synthetic-provider-key"\n'
            f'base_url = "{upstream.removesuffix("/responses")}"\n'
            f'[models.direct]\nprovider = "direct"\nmodel = "{MODEL}"\nmax_context_size = 131072\n'
            f'max_output_size = {MAX_OUTPUT}\ncapabilities = ["tool_use"]\n'
            '[[permission.rules]]\ndecision = "allow"\npattern = "mcp__plugin_powercontext_powercontext__*"\n',
            encoding="utf-8",
        )
        environment = {
            key: value for key, value in os.environ.items() if not key.startswith(("POWERCONTEXT_", "KIMI_"))
        }
        environment.update(
            HOME=str(home),
            USERPROFILE=str(home),
            XDG_CONFIG_HOME=str(home / ".config"),
            XDG_DATA_HOME=str(home / ".local/share"),
            XDG_CACHE_HOME=str(home / ".cache"),
            XDG_STATE_HOME=str(home / ".local/state"),
            KIMI_CODE_HOME=str(kimi_home),
            # A combined gateway also serves the Core API, so the plugin's hook and MCP tools keep one address.
            POWERCONTEXT_KIMI_TOKEN=_TOKEN,
            POWERCONTEXT_KIMI_PROMPT_RECALL="false",
        )
        for key in ("HOME", "KIMI_CODE_HOME", "POWERCONTEXT_KIMI_TOKEN"):
            monkeypatch.setenv(key, environment[key])
        installed = install_plugin(
            "kimi-code",
            source=str(REPOSITORY),
            server_url=str(gateway.base_url),
            scope_id=scope_id,
            capture_prompts=False,
        )
        diagnostics = KimiInstaller().verify(mcp=True)
        assert all(check.ok for check in diagnostics.values()), diagnostics
        command = [node, str(cli)] if cli.suffix in {".mjs", ".js", ".cjs"} else [str(cli)]
        status_script = Path(installed["plugin_path"]) / "scripts/status.mjs"
        yield KimiGatewayStack(command, workspace, environment, model, recorder, core, scope_id, status_script)


def test_kimi_turns_take_context_and_capture_only_from_the_gateway(kimi_gateway: KimiGatewayStack) -> None:
    prompts = [
        "Start a short review of the synthetic heron project.",
        "What is the release color in the synthetic heron project?",
        "Repeat the heron release color once more.",
    ]
    session = AcpSession(kimi_gateway)
    try:
        replies = [session.prompt(prompts[0])]
        # The second turn runs a tool loop through the gateway's Core API forwarding: the model searches, then answers.
        kimi_gateway.model.plan([
            ("search_memory", {"scope_id": kimi_gateway.scope_id, "query": "heron", "mode": "fts"})
        ])
        replies.append(session.prompt(prompts[1]))
        kimi_gateway.model.plan()
        replies.append(session.prompt(prompts[2]))
    finally:
        session.close()
    assert all(FINAL_ANSWER in reply for reply in replies), replies

    requests = kimi_gateway.model.requests
    assert len(requests) == len(kimi_gateway.recorder.headers) == 4
    # Kimi names no conversation in a header, so the gateway infers it from the opening of the full history. The
    # provider's static header selects the Scope.
    assert not any(name in headers for headers in kimi_gateway.recorder.headers for name in CONVERSATION_HEADERS)
    assert all(
        headers.get("x-powercontext-scope-id") == kimi_gateway.scope_id for headers in kimi_gateway.recorder.headers
    )
    for request, prompt in zip(requests, [prompts[0], prompts[1], prompts[1], prompts[2]], strict=True):
        # The gateway always calls its upstream without streaming and keeps Kimi's output bound.
        assert request["stream"] is False
        assert request["max_output_tokens"] == MAX_OUTPUT
        assert PLUGIN_BINDING not in json.dumps(request)
        assert sum(kimi_gateway.model.texts(request).count(marker) for marker in PREPARED_MARKERS) == 1
        # The block lands in the turn's prompt, which the gateway found behind any of Kimi's reminders.
        prompt_item = json.dumps(request["input"][_prompt_index(request, prompt)])
        assert any(marker in prompt_item for marker in PREPARED_MARKERS)
        assert FACT in prompt_item
    # A session's first turn ends with Kimi's date and session-start reminders, which the gateway forwards unchanged.
    reminders = _reminders_after(requests[0], prompts[0])
    assert any("plugin_session_start" in reminder for reminder in reminders), reminders
    assert kimi_gateway.model.function_outputs(requests[2])
    # Kimi's own history never holds a gateway block: the next turn's block is only in its prompt.
    next_turn = requests[3]["input"]
    assert not any(
        marker in json.dumps(next_turn[: _prompt_index(requests[3], prompts[2])]) for marker in PREPARED_MARKERS
    )

    # The gateway, not the plugin, captured each turn in the Scope the header selected, with the prompt as its text.
    turns = [json.loads(item["content"]) for item in kimi_gateway.sources() if item["source_id"].startswith("gw-turn-")]
    assert [item["user"] for item in turns] == prompts
    assert all(item["kind"] == "gateway_turn" for item in turns)
    assert not [item for item in kimi_gateway.sources() if item["source_id"].startswith("kimi-user-prompt:")]
    # The plugin's hook did run on these turns; with recall and capture off it skipped every stage.
    observed = kimi_gateway.status(session.session_id)
    assert observed["status"] == "observed", observed
    assert all(stage == {"state": "skipped", "reason": "disabled"} for stage in observed["stages"].values()), observed


def test_a_session_that_ran_with_prompt_recall_cannot_move_behind_the_gateway(kimi_gateway: KimiGatewayStack) -> None:
    # Before the move: the plugin's recall is on and Kimi calls the model directly.
    session = _session_id(
        kimi_gateway.run(
            "What is the heron release color?", "-m", "direct", POWERCONTEXT_KIMI_PROMPT_RECALL="true"
        ).stdout
    )
    direct = kimi_gateway.model.requests[-1]
    assert sum(json.dumps(direct).count(marker) for marker in PREPARED_MARKERS) == 1
    sent = len(kimi_gateway.model.requests)
    # Kimi stores the hook result in the session and sends it again with every later turn, so Core rejects the
    # resumed session behind the gateway even with the plugin's recall off, and the model is never called.
    rejected = kimi_gateway.run("Repeat the heron release color.", "-S", session, "-m", "fixture", check=False)
    assert FINAL_ANSWER not in rejected.stdout
    assert len(kimi_gateway.model.requests) == sent
    assert [status for status, _ in kimi_gateway.recorder.statuses] == [503]
    assert b"gate_paused" in kimi_gateway.recorder.statuses[0][1]
    # A new session behind the gateway works.
    prompt = "Repeat the heron release color."
    kimi_gateway.run(prompt)
    assert len(kimi_gateway.model.requests) == sent + 1
    fresh = kimi_gateway.model.requests[-1]
    assert FACT in json.dumps(fresh["input"][_prompt_index(fresh, prompt)])
    # A print-mode turn also ends with Kimi's permission-mode reminder; the gateway forwards it and captures the prompt.
    assert any("Auto permission mode" in reminder for reminder in _reminders_after(fresh, prompt))
    turns = [json.loads(item["content"]) for item in kimi_gateway.sources() if item["source_id"].startswith("gw-turn-")]
    assert [item["user"] for item in turns] == [prompt]
