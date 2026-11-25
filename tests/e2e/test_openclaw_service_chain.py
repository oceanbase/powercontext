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

"""Actual OpenClaw with the PowerContext memory plugin against a real PowerContext Server.

A real `openclaw gateway run` process loads the plugin's built bundle through `openclaw plugins install --link` and
serves every turn, which `openclaw agent` submits as an ordinary Gateway client. The Gateway owns the plugin, as in a
real installation: OpenClaw runs `agent_end` hooks after the turn returns, and a one-shot `agent --local` process can
exit before the plugin's capture request is sent. The host model and the Server's generation model are deterministic
local fixtures. These cases establish the local OpenClaw, plugin, and Server chain and the plugin's documented
behavior; they do not qualify live-model behavior, channels, compaction modes other than the one the compaction case
selects, or the TUI.
"""

from __future__ import annotations

import hashlib
import json
import os
import socket
import subprocess
import time
from collections.abc import Iterator
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
from tests.e2e.zcode_acceptance.protocol import HostModelFixture, ObserveServer, WireEvidence, strings
from tests.e2e.zcode_acceptance.runner import serve

PLUGIN = Path(__file__).resolve().parents[2] / "integrations" / "openclaw" / "plugins" / "memory-powercontext"
TOKEN = "openclaw-service-chain-token"  # noqa: S105 - non-secret test credential.
GATEWAY_TOKEN = "openclaw-service-chain-gateway"  # noqa: S105 - non-secret test credential.
# The plugin wraps the Server's prepared context, which carries its own untrusted-history label.
WRAPPER = "<powercontext_memory>"
LABEL = "PowerContext prepared untrusted historical context."
FACT_MARKER = "teal-728"
FACT = f"In the synthetic orchid project, the verification color is {FACT_MARKER}."
QUESTION = "Which verification color does the synthetic orchid project use?"
# `powercontext setup openclaw` adds these to `tools.alsoAllow`.
TOOLS = (
    "powercontext_memory_search",
    "powercontext_memory_get",
    "powercontext_memory_store",
    "powercontext_memory_revise",
    "powercontext_memory_retire",
)


class OpenClawGateway:
    """One `openclaw gateway run` process; its sessions stay in the stack's state directory for the next process."""

    def __init__(self, stack: OpenClawStack, port: int) -> None:
        self._stack = stack
        self._log = stack.log.open("a", encoding="utf-8")
        try:
            self.process = subprocess.Popen(
                [stack.executable, "gateway", "run", "--port", str(port)],
                cwd=stack.workspace,
                env=stack.environment,
                stdin=subprocess.DEVNULL,
                stdout=self._log,
                stderr=subprocess.STDOUT,
            )
        except BaseException:
            self._log.close()
            raise
        try:
            deadline = time.monotonic() + 90
            while self.process.poll() is None and time.monotonic() < deadline:
                try:
                    socket.create_connection(("127.0.0.1", port), timeout=1).close()
                except OSError:
                    time.sleep(0.1)
                    continue
                if self._call("health").returncode == 0:
                    return
                time.sleep(0.2)
        except BaseException:
            self.close()
            raise
        self.close()
        pytest.fail(f"openclaw_gateway_not_ready; see {stack.log}", pytrace=False)

    def turn(self, session: str, text: str) -> dict[str, Any]:
        """Run one agent turn through the Gateway and return the CLI's JSON result."""
        completed = self._run("agent", "--session-key", session, "--message", text, "--json", "--timeout", "120")
        assert completed.returncode == 0, f"openclaw_agent_failed; see {self._stack.log}"
        return json.loads(completed.stdout)

    def history(self, session: str) -> Any:
        """The session's stored messages, as the Gateway serves them to chat clients."""
        completed = self._call("chat.history", {"sessionKey": self._stack.session_key(session)})
        assert completed.returncode == 0, f"openclaw_chat_history_failed; see {self._stack.log}"
        return json.loads(completed.stdout)

    def reset(self, session: str) -> None:
        """Reset the session the way a chat client does, which ends its current OpenClaw session."""
        completed = self._call("sessions.reset", {"key": self._stack.session_key(session)})
        assert completed.returncode == 0, f"openclaw_session_reset_failed; see {self._stack.log}"

    def close(self) -> None:
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=10)
        self._log.close()

    def _call(self, method: str, params: dict[str, Any] | None = None) -> subprocess.CompletedProcess[str]:
        arguments = ["gateway", "call", method, "--json"]
        if params is not None:
            arguments += ["--params", json.dumps(params)]
        return self._run(*arguments)

    def _run(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        completed = subprocess.run(
            [self._stack.executable, *arguments],
            cwd=self._stack.workspace,
            env=self._stack.environment,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
        with self._stack.log.open("a", encoding="utf-8") as log:
            log.write(f"$ openclaw {arguments[0]} {arguments[1]} -> {completed.returncode}\n{completed.stderr}")
        return completed


@dataclass
class OpenClawStack:
    executable: str
    workspace: Path
    config_path: Path
    config: dict[str, Any]
    environment: dict[str, str]
    log: Path
    model: HostModelFixture
    wire: WireEvidence
    server: httpx.Client
    base_url: str
    scope_id: str

    @contextmanager
    def open(self, **settings: Any) -> Iterator[OpenClawGateway]:
        """Start the Gateway with plugin settings overridden; None removes a setting."""
        config = json.loads(json.dumps(self.config))
        plugin = config["plugins"]["entries"]["memory-powercontext"]["config"]
        for name, value in settings.items():
            if value is None:
                plugin.pop(name, None)
            else:
                plugin[name] = value
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        config["gateway"]["port"] = port
        self.config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
        gateway = OpenClawGateway(self, port)
        try:
            yield gateway
        finally:
            gateway.close()

    @staticmethod
    def session_key(name: str) -> str:
        return f"agent:main:{name}"

    def bind(self, kind: str, identity: str, scope_id: str) -> None:
        """Provision a durable OpenClaw binding the way an operator would; the plugin only resolves them."""
        external_id = identity if kind == "agent" else hashlib.sha256(identity.encode()).hexdigest()
        response = self.server.put(
            "/v1/scope-bindings",
            json={"key": {"integration": "openclaw", "kind": kind, "external_id": external_id}, "scope_id": scope_id},
        )
        response.raise_for_status()

    def flush(self) -> None:
        """Process every captured Source, as the Server's schedule would; this stack runs none."""
        for _ in range(8):
            response = self.server.post("/v1/memory/flush", json={"scope_id": self.scope_id})
            response.raise_for_status()
            result = response.json()
            if result["current_cursor"] >= result["high_watermark"] and not result.get("remaining_work"):
                return
        raise AssertionError("memory_flush_did_not_drain")

    def memories(self, scope_id: str | None = None) -> dict[str, dict[str, Any]]:
        response = self.server.post("/v1/memory/entries/list", json={"scope_id": scope_id or self.scope_id})
        response.raise_for_status()
        return {entry["text"]: entry for entry in response.json()["entries"]}

    def sources(self, scope_id: str | None = None) -> list[Any]:
        response = self.server.get(f"/v1/scopes/{scope_id or self.scope_id}/sources")
        response.raise_for_status()
        return [item["content"] for item in response.json()["items"]]

    def wait_for_sources(self, expected: list[str], scope_id: str | None = None) -> None:
        """Wait until the Scope holds exactly `expected`; OpenClaw runs `agent_end` after the turn returns."""
        expected = [f"User: {text}" for text in expected]
        deadline = time.monotonic() + 15
        while (sources := self.sources(scope_id)) != expected and time.monotonic() < deadline:
            time.sleep(0.05)
        assert sources == expected

    def records(self, path: str) -> list[dict[str, Any]]:
        return [record for record in self.wire.records if record["path"] == path]

    def queries(self) -> list[str]:
        return [record["request"]["query"] for record in self.records("/v1/context/prepare")]


@pytest.fixture
def openclaw_stack(tmp_path: Path) -> Iterator[OpenClawStack]:
    executable = os.environ.get("OPENCLAW_CLI_BIN")
    if not executable:
        pytest.skip("Set OPENCLAW_CLI_BIN to a real OpenClaw CLI; CI runs this in the openclaw-package job")
    assert Path(executable).is_file(), "OPENCLAW_CLI_BIN must name an existing OpenClaw CLI"
    assert (PLUGIN / "dist" / "index.js").is_file(), "make openclaw-plugin-test builds the plugin bundle"
    workspace = tmp_path / "workspace"
    home = tmp_path / "home"
    state = home / ".openclaw"
    for directory in (workspace, state, home / "tmp"):
        directory.mkdir(parents=True)
    log = tmp_path / "openclaw.log"
    model = HostModelFixture()
    wire = WireEvidence()
    with ExitStack() as stack:
        model_url = stack.enter_context(serve(model.app))
        settings = ServerSettings(
            _env_file=None,  # ty: ignore[unknown-argument] BaseSettings runtime option.
            workspace=workspace,
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'server.db'}"),
            auth=BearerAuthConfig(enabled=True, token=SecretStr(TOKEN)),
            # No memory schedule: captured prompts become Memory only when a case flushes them.
            runtime=RuntimeConfig(artifact_processing_families=("memory",)),
            inference=InferenceConfig(
                generation_model="openai-chat:memory-fixture",
                generation_base_url=AnyHttpUrl(model_url + "/v1"),
                generation_headers={"Authorization": SecretStr("Bearer synthetic-generation-only")},
            ),
        )
        app = create_server_app(settings=settings)
        base_url = stack.enter_context(serve(ObserveServer(app, wire)))
        server = stack.enter_context(
            httpx.Client(base_url=base_url, headers={"Authorization": f"Bearer {TOKEN}"}, timeout=15)
        )
        scope_id = _create_scope(server, "OpenClaw service chain")
        # Proxies would route the loopback Server, Gateway, and model traffic away from this host.
        prefixes = ("POWERCONTEXT_", "OPENCLAW_", "CLAWDBOT_", "NODE_OPTIONS", "NPM_CONFIG_")
        environment = {
            name: value
            for name, value in os.environ.items()
            if not name.upper().startswith(prefixes) and not name.upper().endswith("_PROXY")
        }
        environment.update(
            HOME=str(home),
            TMPDIR=str(home / "tmp"),
            XDG_CONFIG_HOME=str(home / "config"),
            XDG_DATA_HOME=str(home / "data"),
            XDG_CACHE_HOME=str(home / "cache"),
            XDG_STATE_HOME=str(home / "state"),
            OPENCLAW_STATE_DIR=str(state),
            OPENCLAW_GATEWAY_TOKEN=GATEWAY_TOKEN,
            OPENCLAW_DISABLE_BONJOUR="1",
            OPENCLAW_SKIP_CHANNELS="1",
            OPENCLAW_SKIP_CRON="1",
            OPENCLAW_NO_AUTO_UPDATE="1",
            OPENCLAW_NO_RESPAWN="1",
            POWERCONTEXT_CLIENT_CONFIG_FILE=str(home / "powercontext-clients.json"),
            # The plugin's default `tokenEnv`, read by the Gateway process.
            POWERCONTEXT_CLIENT_API_TOKEN=TOKEN,
        )
        # The README's install path: a linked local plugin, enabled and selected for the memory slot.
        install = subprocess.run(
            [executable, "plugins", "install", "--link", "--force", "--accept-capabilities", str(PLUGIN)],
            cwd=workspace,
            env=environment,
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
        log.write_text(f"$ openclaw plugins install\n{install.stdout}{install.stderr}", encoding="utf-8")
        assert install.returncode == 0, f"openclaw_plugin_install_failed; see {log}"
        config_path = state / "openclaw.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        assert config["plugins"]["slots"]["memory"] == "memory-powercontext"
        entry = config["plugins"]["entries"]["memory-powercontext"]
        entry["hooks"] = {"allowConversationAccess": True}
        entry["config"] = {
            "endpoint": base_url,
            "scopeId": scope_id,
            # A loaded runner must not turn a slow Server reply into a silently skipped preparation or capture.
            "timeoutMs": 10000,
        }
        config.update(
            gateway={"mode": "local", "bind": "loopback", "auth": {"mode": "token"}},
            # The project identity is the workspace path, independent of any repository above the temporary directory.
            agents={
                "defaults": {
                    "model": {"primary": "fixture/fixture"},
                    "workspace": str(workspace),
                    "repoRoot": str(workspace),
                }
            },
            models={
                "catalogRefresh": {"enabled": False},
                "providers": {
                    "fixture": {
                        "baseUrl": model_url + "/v1",
                        "apiKey": "synthetic-only",
                        "api": "openai-completions",
                        "models": [
                            {
                                "id": "fixture",
                                "name": "fixture",
                                "reasoning": False,
                                "input": ["text"],
                                "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
                                "contextWindow": 65536,
                                "maxTokens": 4096,
                            }
                        ],
                    }
                },
            },
            tools={"alsoAllow": list(TOOLS)},
            update={"checkOnStart": False},
            logging={"file": str(tmp_path / "openclaw-file.log")},
        )
        yield OpenClawStack(
            executable, workspace, config_path, config, environment, log, model, wire, server, base_url, scope_id
        )


def _create_scope(server: httpx.Client, title: str) -> str:
    response = server.post(
        "/v1/scopes", json={"title": title, "summary": "Disposable OpenClaw host acceptance.", "idempotency_key": title}
    )
    response.raise_for_status()
    return response.json()["scope_id"]


def _sent(request: dict[str, Any]) -> str:
    """Everything one host model request carried apart from OpenClaw's system prompt."""
    return "\n".join(strings([message for message in request["messages"] if message.get("role") != "system"]))


def test_openclaw_prompt_is_recalled_in_a_later_session(openclaw_stack: OpenClawStack) -> None:
    with openclaw_stack.open() as gateway:
        gateway.turn("capture", FACT)
        openclaw_stack.wait_for_sources([FACT])
        openclaw_stack.flush()
        assert FACT in openclaw_stack.memories()

        start = len(openclaw_stack.model.requests)
        gateway.turn("recall", QUESTION)
        prepared = openclaw_stack.records("/v1/context/prepare")[-1]
        assert prepared["request"]["query"] == QUESTION
        assert prepared["response"]["status"] == "ready"
        content = prepared["response"]["content"]
        assert content.startswith(LABEL)
        assert FACT_MARKER in content
        sent = _sent(openclaw_stack.model.requests[start])
        assert 0 <= sent.find(WRAPPER) < sent.find(content) < sent.find("</powercontext_memory>") < sent.find(QUESTION)

        # The context reached the model for this turn only; OpenClaw stored the turn without it.
        stored = json.dumps(gateway.history("recall"))
        assert QUESTION in stored
        assert "powercontext_memory" not in stored
        assert LABEL not in stored
        assert FACT_MARKER not in stored
        openclaw_stack.wait_for_sources([FACT, QUESTION])

    assert openclaw_stack.queries() == [FACT, QUESTION]


def test_openclaw_scope_follows_explicit_selection_then_durable_bindings(openclaw_stack: OpenClawStack) -> None:
    agent_scope = _create_scope(openclaw_stack.server, "OpenClaw agent")
    project_scope = _create_scope(openclaw_stack.server, "OpenClaw project")
    session_scope = _create_scope(openclaw_stack.server, "OpenClaw session")
    openclaw_stack.bind("agent", "main", agent_scope)
    with openclaw_stack.open(scopeId=None) as gateway:
        gateway.turn("first", "First synthetic orchid prompt.")
        openclaw_stack.wait_for_sources(["First synthetic orchid prompt."], agent_scope)

        # Bindings are resolved on every operation: an ordered active project wins over the agent identity, and the
        # OpenClaw session wins over both.
        openclaw_stack.bind("project", f"path:{openclaw_stack.workspace}", project_scope)
        gateway.turn("second", "Second synthetic orchid prompt.")
        openclaw_stack.wait_for_sources(["Second synthetic orchid prompt."], project_scope)
        openclaw_stack.bind("session", openclaw_stack.session_key("third"), session_scope)
        gateway.turn("third", "Third synthetic orchid prompt.")
        openclaw_stack.wait_for_sources(["Third synthetic orchid prompt."], session_scope)

    # An explicit `scopeId` selects its Scope for every operation, ahead of any binding.
    with openclaw_stack.open() as gateway:
        gateway.turn("third", "Pinned synthetic orchid prompt.")
        openclaw_stack.wait_for_sources(["Pinned synthetic orchid prompt."])
    openclaw_stack.wait_for_sources(["Third synthetic orchid prompt."], session_scope)
    prepared = [record["request"]["scope_id"] for record in openclaw_stack.records("/v1/context/prepare")]
    assert prepared == [agent_scope, project_scope, session_scope, openclaw_stack.scope_id]


def test_openclaw_recall_and_capture_settings_are_independent(openclaw_stack: OpenClawStack) -> None:
    with openclaw_stack.open(autoRecall=False) as gateway:
        gateway.turn("capture", FACT)
        openclaw_stack.wait_for_sources([FACT])
        openclaw_stack.flush()
        assert FACT in openclaw_stack.memories()
        start = len(openclaw_stack.model.requests)
        gateway.turn("recall-off", QUESTION)
        openclaw_stack.wait_for_sources([FACT, QUESTION])

    assert openclaw_stack.records("/v1/context/prepare") == []
    sent = json.dumps(openclaw_stack.model.requests[start:])
    assert QUESTION in sent
    assert "powercontext_memory>" not in sent
    assert FACT_MARKER not in sent

    captured = len(openclaw_stack.records("/v1/sources/content"))
    uncaptured = "Which verification color should the synthetic orchid release notes mention?"
    with openclaw_stack.open(autoCapture=False) as gateway:
        start = len(openclaw_stack.model.requests)
        gateway.turn("capture-off", uncaptured)

    assert openclaw_stack.queries() == [uncaptured]
    assert FACT_MARKER in _sent(openclaw_stack.model.requests[start])

    unsent = "A synthetic orchid prompt that stays on this host."
    remembered = "The synthetic orchid release train leaves on Thursdays."
    with openclaw_stack.open(autoRecall=False, autoCapture=False) as gateway:
        start = len(openclaw_stack.model.requests)
        gateway.turn("both-off", unsent)
        # Explicit tools stay available when both automatic paths are off.
        openclaw_stack.model.plan([("powercontext_memory_store", {"text": remembered, "kind": "decision"})])
        gateway.turn("both-off", "Remember for later that the release train leaves on Thursdays.")

    assert unsent in _sent(openclaw_stack.model.requests[start])
    # Neither run captured a prompt, and the last one prepared nothing for its prompts either.
    assert len(openclaw_stack.records("/v1/sources/content")) == captured
    assert openclaw_stack.queries() == [uncaptured]
    assert unsent not in json.dumps(openclaw_stack.wire.records)
    assert remembered in openclaw_stack.memories()


def test_openclaw_memory_tools_reach_the_bound_scope(openclaw_stack: OpenClawStack) -> None:
    bound = _create_scope(openclaw_stack.server, "OpenClaw bound session")
    openclaw_stack.bind("session", openclaw_stack.session_key("remember"), bound)
    remembered = "The synthetic orchid release train leaves on Thursdays."
    openclaw_stack.model.plan([
        ("powercontext_memory_store", {"text": remembered, "kind": "decision"}),
        ("powercontext_memory_search", {"query": "release train"}),
    ])
    with openclaw_stack.open(scopeId=None, autoRecall=False, autoCapture=False) as gateway:
        gateway.turn("remember", "Remember for later that the release train leaves on Thursdays, then look it up.")

    assert remembered in openclaw_stack.memories(bound)
    assert remembered not in openclaw_stack.memories()
    assert [record["request"]["scope_id"] for record in openclaw_stack.records("/v1/memory/remember")] == [bound]
    assert [record["request"]["scope_id"] for record in openclaw_stack.records("/v1/memory/search")] == [bound]
    # The search result OpenClaw returned to the model came from the bound Scope.
    results = [
        json.loads(message["content"] if isinstance(message["content"], str) else message["content"][0]["text"])
        for message in openclaw_stack.model.requests[-1]["messages"]
        if message.get("role") == "tool"
    ]
    assert results[0]["status"] == "stored"
    assert remembered in [hit["text"] for hit in results[1]["results"]]


def test_openclaw_group_and_incognito_sessions_are_excluded(openclaw_stack: OpenClawStack) -> None:
    with openclaw_stack.open() as gateway:
        gateway.turn("capture", FACT)
        openclaw_stack.wait_for_sources([FACT])
        openclaw_stack.flush()
        for session in ("group:orchid", "dashboard:incognito-orchid"):
            start = len(openclaw_stack.model.requests)
            gateway.turn(session, QUESTION)
            assert FACT_MARKER not in _sent(openclaw_stack.model.requests[start])

    # Only the private session reached the Server; its capture remains the Scope's only Source.
    assert openclaw_stack.queries() == [FACT]
    openclaw_stack.wait_for_sources([FACT])
    assert QUESTION not in json.dumps(openclaw_stack.wire.records)


def test_openclaw_checkpoints_the_prompt_before_in_session_compaction(openclaw_stack: OpenClawStack) -> None:
    agent_scope = _create_scope(openclaw_stack.server, "OpenClaw compaction agent")
    project_scope = _create_scope(openclaw_stack.server, "OpenClaw compaction project")
    openclaw_stack.bind("agent", "main", agent_scope)
    openclaw_stack.bind("project", f"path:{openclaw_stack.workspace}", project_scope)
    # OpenClaw's default `safeguard` mode turns off the built-in runner's in-session auto-compaction. In `default` mode
    # the runner starts a compaction once a reply reports more usage than the context window less its reserve, and it
    # reports that compaction to `before_compaction` with only the session key and no active projects. This transcript
    # is too short for OpenClaw to compact anything, so the case covers the checkpoint, not the compaction itself.
    openclaw_stack.config["agents"]["defaults"]["compaction"] = {"mode": "default"}
    openclaw_stack.config["models"]["providers"]["fixture"]["models"][0]["contextWindow"] = 32000
    openclaw_stack.model.streamed_prompt_tokens = 31000
    with openclaw_stack.open(scopeId=None) as gateway:
        gateway.turn("compact", FACT)

        # The checkpoint captures the prompt under its own event identity, beside the turn's `agent_end` capture, and
        # flushes the project Scope that the turn's recall read while the Gateway is still running.
        def checkpointed() -> bool:
            captured = openclaw_stack.sources(project_scope).count(f"User: {FACT}")
            return captured >= 2 and FACT in openclaw_stack.memories(project_scope)

        deadline = time.monotonic() + 15
        while not checkpointed() and time.monotonic() < deadline:
            time.sleep(0.1)
        assert checkpointed()
    # OpenClaw builds the prompt again after the compaction; every preparation reads the project Scope.
    prepared = {record["request"]["scope_id"] for record in openclaw_stack.records("/v1/context/prepare")}
    assert prepared == {project_scope}
    flushed = {record["request"]["scope_id"] for record in openclaw_stack.records("/v1/memory/flush")}
    assert flushed == {project_scope}
    assert openclaw_stack.sources(agent_scope) == []


def test_openclaw_flushes_captured_prompts_when_a_session_ends_and_the_gateway_stops(
    openclaw_stack: OpenClawStack,
) -> None:
    agent_scope = _create_scope(openclaw_stack.server, "OpenClaw session end")
    openclaw_stack.bind("agent", "main", agent_scope)
    later = "In the synthetic heron project, the verification color is amber-415."
    with openclaw_stack.open(scopeId=None) as gateway:
        gateway.turn("ended", FACT)
        openclaw_stack.wait_for_sources([FACT], agent_scope)
        assert openclaw_stack.records("/v1/memory/flush") == []

        # OpenClaw runs a turn's hooks on the plugin registration of the run's registry, and reports the reset to
        # `session_end` on the Gateway's own registration of the plugin. That registration flushes the turn's capture.
        gateway.reset("ended")
        deadline = time.monotonic() + 15
        while FACT not in openclaw_stack.memories(agent_scope) and time.monotonic() < deadline:
            time.sleep(0.1)
        assert FACT in openclaw_stack.memories(agent_scope)
        assert {record["request"]["scope_id"] for record in openclaw_stack.records("/v1/memory/flush")} == {agent_scope}

        gateway.turn("stopped", later)
        openclaw_stack.wait_for_sources([FACT, later], agent_scope)
        flushed = len(openclaw_stack.records("/v1/memory/flush"))

    # Stopping the Gateway reports `gateway_stop` and then `session_end` for each open session; both reach the Gateway's
    # own registration, and the first one to run flushes the later capture.
    assert len(openclaw_stack.records("/v1/memory/flush")) > flushed
    assert {record["request"]["scope_id"] for record in openclaw_stack.records("/v1/memory/flush")} == {agent_scope}
    assert later in openclaw_stack.memories(agent_scope)


def test_openclaw_turn_proceeds_when_the_server_is_unavailable(openclaw_stack: OpenClawStack) -> None:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        closed = f"http://127.0.0.1:{probe.getsockname()[1]}"
    with openclaw_stack.open(endpoint=closed, timeoutMs=1000) as gateway:
        result = gateway.turn("offline", QUESTION)

    assert "Synthetic operation complete." in json.dumps(result)
    sent = _sent(openclaw_stack.model.requests[-1])
    assert QUESTION in sent
    assert "powercontext_memory>" not in sent
    # The plugin handled the outage itself: it reported its content-free diagnostic, and OpenClaw neither caught a hook
    # failure nor timed the hook out.
    log = openclaw_stack.log.read_text(encoding="utf-8")
    diagnostics = [
        json.loads(line[line.index("{") :]) for line in log.splitlines() if '"powercontext.openclaw"' in line
    ]
    expected = {"event": "context_prepare", "outcome": "server_unavailable"}.items()
    assert any(expected <= diagnostic.items() for diagnostic in diagnostics)
    assert "handler from memory-powercontext failed" not in log
