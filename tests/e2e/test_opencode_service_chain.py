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

"""Actual OpenCode with the PowerContext plugin bundle against a real PowerContext Server.

`opencode serve` loads the checked-in bundle from its global plugin directory, where setup installs it. npm runs offline
against a closed registry, so OpenCode's own dependency install fails at once and the bundle's `@opencode-ai/plugin`
import resolves from the plugin package's development install. The host model and the Server's generation model are
deterministic local fixtures. These cases establish the local OpenCode, plugin, and Server chain and the plugin's
documented behavior; they do not qualify live-model behavior or the TUI.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import threading
import time
from collections.abc import Iterator
from concurrent.futures import Future, ThreadPoolExecutor
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

PLUGIN = Path(__file__).resolve().parents[2] / "integrations" / "opencode" / "plugins" / "powercontext"
TOKEN = "opencode-service-chain-token"  # noqa: S105 - non-secret test credential.
LABEL = "PowerContext host-supplied context."
FACT_MARKER = "teal-728"
FACT = f"In the synthetic orchid project, the verification color is {FACT_MARKER}."
QUESTION = "Which verification color does the synthetic orchid project use?"
MODEL = {"providerID": "fixture", "modelID": "fixture"}


class OpenCodeHost:
    """One `opencode serve` process; its sessions stay in the stack's data directory for the next process."""

    def __init__(self, executable: str, workspace: Path, environment: dict[str, str], log: Path) -> None:
        self._log = log.open("a", encoding="utf-8")
        try:
            self.process = subprocess.Popen(
                [executable, "serve", "--hostname", "127.0.0.1", "--port", "0", "--print-logs"],
                cwd=workspace,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=self._log,
                text=True,
            )
        except BaseException:
            self._log.close()
            raise
        self._turns = ThreadPoolExecutor(max_workers=1)
        address: list[str] = []
        ready = threading.Event()

        def read_stdout() -> None:
            # Keep draining after the address so OpenCode never blocks on a full pipe.
            assert self.process.stdout is not None
            for line in self.process.stdout:
                match = re.search(r"listening on (http://127\.0\.0\.1:\d+)", line)
                if match and not address:
                    address.append(match[1])
                    ready.set()
            ready.set()

        threading.Thread(target=read_stdout, daemon=True).start()
        ready.wait(timeout=30)
        if not address:
            self._stop()
            pytest.fail(f"opencode_serve_not_listening; see {log}", pytrace=False)
        self.client = httpx.Client(base_url=address[0], params={"directory": str(workspace)}, timeout=60)

    def session(self, title: str) -> dict[str, Any]:
        # A titled session also keeps OpenCode from asking the model for a title.
        response = self.client.post("/session", json={"title": title})
        response.raise_for_status()
        return response.json()

    def prompt(self, session_id: str, text: str) -> dict[str, Any]:
        """Run one turn to completion and return OpenCode's last assistant message."""
        response = self.client.post(
            f"/session/{session_id}/message", json={"parts": [{"type": "text", "text": text}], "model": MODEL}
        )
        response.raise_for_status()
        return response.json()

    def start(self, session_id: str, text: str) -> Future[dict[str, Any]]:
        """Run one turn in the background, for a turn that waits on the user."""
        return self._turns.submit(self.prompt, session_id, text)

    def permission(self, turn: Future[dict[str, Any]]) -> dict[str, Any]:
        """Return the permission request that holds `turn`."""
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            response = self.client.get("/permission")
            response.raise_for_status()
            if pending := response.json():
                return pending[0]
            if turn.done():
                turn.result()
                raise AssertionError("opencode_turn_finished_without_permission_request")
            time.sleep(0.05)
        raise AssertionError("opencode_permission_request_missing")

    def reply(self, request: dict[str, Any], reply: str) -> None:
        self.client.post(f"/permission/{request['id']}/reply", json={"reply": reply}).raise_for_status()

    def transcript(self, session_id: str) -> list[dict[str, Any]]:
        response = self.client.get(f"/session/{session_id}/message")
        response.raise_for_status()
        return response.json()

    def tool_output(self, session_id: str, tool: str) -> dict[str, Any]:
        """The latest completed call of `tool` in the session, as the plugin returned it."""
        parts = [
            part
            for message in self.transcript(session_id)
            for part in message["parts"]
            if part.get("type") == "tool" and part.get("tool") == tool and part["state"]["status"] == "completed"
        ]
        assert parts, f"opencode_{tool}_not_completed"
        return json.loads(parts[-1]["state"]["output"])

    def close(self) -> None:
        self._stop()
        self.client.close()

    def _stop(self) -> None:
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=10)
        self._turns.shutdown(wait=True, cancel_futures=True)
        self._log.close()


@dataclass
class OpenCodeStack:
    executable: str
    workspace: Path
    environment: dict[str, str]
    log: Path
    model: HostModelFixture
    wire: WireEvidence
    server: httpx.Client
    scope_id: str

    @contextmanager
    def open(self, **settings: str | None) -> Iterator[OpenCodeHost]:
        """Start OpenCode with plugin settings overridden; None removes a setting."""
        environment = dict(self.environment)
        for name, value in settings.items():
            if value is None:
                environment.pop(name, None)
            else:
                environment[name] = value
        host = OpenCodeHost(self.executable, self.workspace, environment, self.log)
        try:
            yield host
        finally:
            host.close()

    def bind_workspace(self, scope_id: str) -> None:
        # Node hosts key a workspace by the SHA-256 of its resolved path, without resolving symlinks.
        external_id = hashlib.sha256(os.path.abspath(self.workspace).encode()).hexdigest()
        response = self.server.put(
            "/v1/scope-bindings",
            json={
                "key": {"integration": "opencode", "kind": "workspace", "external_id": external_id},
                "scope_id": scope_id,
            },
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

    def memories(self) -> dict[str, dict[str, Any]]:
        response = self.server.post("/v1/memory/entries/list", json={"scope_id": self.scope_id})
        response.raise_for_status()
        return {entry["text"]: entry for entry in response.json()["entries"]}

    def sources(self, scope_id: str | None = None) -> list[Any]:
        response = self.server.get(f"/v1/scopes/{scope_id or self.scope_id}/sources")
        response.raise_for_status()
        return [item["content"] for item in response.json()["items"]]

    def wait_for_sources(self, expected: list[str], scope_id: str | None = None) -> None:
        """Wait until the Scope holds exactly `expected`; a returned turn does not promise its prompt is captured."""
        deadline = time.monotonic() + 10
        while (sources := self.sources(scope_id)) != expected and time.monotonic() < deadline:
            time.sleep(0.05)
        assert sources == expected

    def records(self, path: str) -> list[dict[str, Any]]:
        return [record for record in self.wire.records if record["path"] == path]


@pytest.fixture
def opencode_stack(tmp_path: Path) -> Iterator[OpenCodeStack]:
    executable = os.environ.get("OPENCODE_CLI_BIN")
    if not executable:
        pytest.skip("Set OPENCODE_CLI_BIN to a real OpenCode binary; CI runs this in the opencode-package job")
    assert Path(executable).is_file(), "OPENCODE_CLI_BIN must name an existing OpenCode binary"
    dependencies = PLUGIN / "node_modules"
    assert (dependencies / "@opencode-ai" / "plugin").is_dir(), "make opencode-test installs the plugin dependencies"
    workspace = tmp_path / "workspace"
    home = tmp_path / "home"
    config = home / "config"
    plugins = config / "opencode" / "plugins"
    for directory in (workspace, plugins, home / "data", home / "cache", home / "state", home / "tmp"):
        directory.mkdir(parents=True)
    shutil.copyfile(PLUGIN / "lib" / "index.js", plugins / "powercontext-opencode.js")
    # OpenCode owns config/opencode/node_modules; Node resolves the bundle's imports one level up.
    (config / "node_modules").symlink_to(dependencies, target_is_directory=True)
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
        scope_id = _create_scope(server, "OpenCode service chain")
        (config / "opencode" / "opencode.json").write_text(
            json.dumps({
                "$schema": "https://opencode.ai/config.json",
                "autoupdate": False,
                "share": "disabled",
                "model": "fixture/fixture",
                "provider": {
                    "fixture": {
                        "npm": "@ai-sdk/openai-compatible",
                        "name": "fixture",
                        "options": {"baseURL": model_url + "/v1", "apiKey": "synthetic-only"},
                        "models": {
                            "fixture": {
                                "name": "fixture",
                                "tool_call": True,
                                "limit": {"context": 65536, "output": 4096},
                            }
                        },
                    }
                },
            }),
            encoding="utf-8",
        )
        # Proxies would route the loopback Server and model traffic away from this host.
        prefixes = ("POWERCONTEXT_", "OPENCODE", "NPM_CONFIG_", "BUN_")
        environment = {
            name: value
            for name, value in os.environ.items()
            if not name.upper().startswith(prefixes) and not name.upper().endswith("_PROXY")
        }
        environment.update(
            HOME=str(home),
            TMPDIR=str(home / "tmp"),
            XDG_CONFIG_HOME=str(config),
            XDG_DATA_HOME=str(home / "data"),
            XDG_CACHE_HOME=str(home / "cache"),
            XDG_STATE_HOME=str(home / "state"),
            OPENCODE_TEST_HOME=str(home),
            OPENCODE_DISABLE_AUTOUPDATE="true",
            OPENCODE_DISABLE_MODELS_FETCH="true",
            OPENCODE_DISABLE_DEFAULT_PLUGINS="true",
            OPENCODE_DISABLE_LSP_DOWNLOAD="true",
            OPENCODE_DISABLE_CLAUDE_CODE="true",
            OPENCODE_DISABLE_EXTERNAL_SKILLS="true",
            OPENCODE_DISABLE_PROJECT_CONFIG="true",
            # Before loading plugins, OpenCode installs @opencode-ai/plugin into each config directory with npm.
            # Offline, that install fails at once instead of retrying a closed registry.
            npm_config_offline="true",
            npm_config_cache=str(home / "npm-cache"),
            npm_config_registry="http://127.0.0.1:9/",
            POWERCONTEXT_OPENCODE_BASE_URL=base_url,
            POWERCONTEXT_OPENCODE_AUTHORIZATION=f"Bearer {TOKEN}",
            POWERCONTEXT_OPENCODE_SCOPE_ID=scope_id,
            # A loaded runner must not turn a slow Server reply into a silently skipped preparation or capture.
            POWERCONTEXT_OPENCODE_REQUEST_TIMEOUT_MS="10000",
            POWERCONTEXT_OPENCODE_HTTP_BUDGET_MS="20000",
        )
        yield OpenCodeStack(
            executable, workspace, environment, tmp_path / "opencode.log", model, wire, server, scope_id
        )


def _create_scope(server: httpx.Client, title: str) -> str:
    response = server.post(
        "/v1/scopes", json={"title": title, "summary": "Disposable OpenCode host acceptance.", "idempotency_key": title}
    )
    response.raise_for_status()
    return response.json()["scope_id"]


def test_opencode_prompt_is_recalled_in_a_later_session(opencode_stack: OpenCodeStack) -> None:
    credential = "sk-opencodee2ecredential0123456789"
    with opencode_stack.open() as host:
        first = host.session("Capture")["id"]
        host.prompt(first, FACT)
        host.prompt(first, f"Deploy the synthetic orchid project with {credential} today.")
        opencode_stack.wait_for_sources([FACT])
        opencode_stack.flush()
        assert FACT in opencode_stack.memories()

        second = host.session("Recall")["id"]
        start = len(opencode_stack.model.requests)
        host.prompt(second, QUESTION)

        prepared = opencode_stack.records("/v1/context/prepare")[-1]["response"]
        assert prepared["status"] == "ready"
        assert FACT_MARKER in prepared["content"]
        sent = "\n".join(strings(opencode_stack.model.requests[start]["messages"]))
        assert 0 <= sent.find(LABEL) < sent.find(prepared["content"])
        # The context reached the model for this turn only; OpenCode stored the turn without it.
        stored = json.dumps(host.transcript(second))
        assert QUESTION in stored
        assert LABEL not in stored
        assert FACT_MARKER not in stored

    opencode_stack.wait_for_sources([FACT, QUESTION])
    queries = [record["request"]["query"] for record in opencode_stack.records("/v1/context/prepare")]
    assert queries == [FACT, "Deploy the synthetic orchid project with [REDACTED] today.", QUESTION]
    assert credential not in json.dumps([opencode_stack.wire.records, opencode_stack.model.generation_requests])


def test_opencode_resumed_session_keeps_its_scope(opencode_stack: OpenCodeStack) -> None:
    first_scope = opencode_stack.scope_id
    second_scope = _create_scope(opencode_stack.server, "Second OpenCode workspace")
    opencode_stack.bind_workspace(first_scope)
    unpinned = {"POWERCONTEXT_OPENCODE_SCOPE_ID": None}
    with opencode_stack.open(**unpinned) as host:
        session = host.session("Resume")
        assert session["directory"] == str(opencode_stack.workspace)
        host.prompt(session["id"], "First synthetic orchid prompt.")

    # A later workspace binding governs new sessions; a resumed one keeps the Scope it resolved first.
    opencode_stack.bind_workspace(second_scope)
    with opencode_stack.open(**unpinned) as host:
        host.prompt(session["id"], "Resumed synthetic orchid prompt.")
        host.prompt(host.session("Fresh")["id"], "Fresh synthetic orchid prompt.")

    opencode_stack.wait_for_sources(["First synthetic orchid prompt.", "Resumed synthetic orchid prompt."], first_scope)
    opencode_stack.wait_for_sources(["Fresh synthetic orchid prompt."], second_scope)


def test_opencode_recall_and_capture_settings_are_independent(opencode_stack: OpenCodeStack) -> None:
    with opencode_stack.open(POWERCONTEXT_OPENCODE_PROMPT_RECALL="false") as host:
        host.prompt(host.session("Capture")["id"], FACT)
        opencode_stack.wait_for_sources([FACT])
        opencode_stack.flush()
        assert FACT in opencode_stack.memories()
        start = len(opencode_stack.model.requests)
        host.prompt(host.session("Recall off")["id"], QUESTION)

    assert opencode_stack.records("/v1/context/prepare") == []
    opencode_stack.wait_for_sources([FACT, QUESTION])
    sent = json.dumps(opencode_stack.model.requests[start:])
    assert QUESTION in sent
    assert LABEL not in sent
    assert FACT_MARKER not in sent

    captured = len(opencode_stack.records("/v1/sources/content"))
    uncaptured = "Which verification color should the synthetic orchid release notes mention?"
    with opencode_stack.open(POWERCONTEXT_OPENCODE_CAPTURE_PROMPTS="false") as host:
        host.prompt(host.session("Capture off")["id"], uncaptured)

    assert [record["request"]["query"] for record in opencode_stack.records("/v1/context/prepare")] == [uncaptured]

    unsent = "A synthetic orchid prompt that stays on this host."
    with opencode_stack.open(
        POWERCONTEXT_OPENCODE_PROMPT_RECALL="false", POWERCONTEXT_OPENCODE_CAPTURE_PROMPTS="false"
    ) as host:
        host.prompt(host.session("Recall and capture off")["id"], unsent)

    assert unsent in json.dumps(opencode_stack.model.requests[-1])
    # Neither run captured its prompt, and the last one prepared nothing for its prompt either.
    assert len(opencode_stack.records("/v1/sources/content")) == captured
    assert len(opencode_stack.records("/v1/context/prepare")) == 1
    assert unsent not in json.dumps(opencode_stack.wire.records)


def test_opencode_memory_writes_wait_for_permission(opencode_stack: OpenCodeStack) -> None:
    remembered = "The synthetic orchid release train leaves on Thursdays."
    remember = ("pc_remember", {"kind": "decision", "text": remembered})
    with opencode_stack.open() as host:
        session = host.session("Remember")["id"]
        opencode_stack.model.plan([remember])
        turn = host.start(session, "Remember for later that the release train leaves on Thursdays.")
        # The configuration sets no permission, so only the plugin's default rule makes OpenCode ask.
        request = host.permission(turn)
        assert (request["permission"], request["patterns"]) == ("powercontext", ["remember_memory"])
        assert opencode_stack.records("/v1/memory/remember") == []
        host.reply(request, "reject")
        turn.result(timeout=30)
        assert remembered not in opencode_stack.memories()

        opencode_stack.model.plan([remember, ("pc_search", {"query": "release train", "mode": "fts"})])
        turn = host.start(session, "Yes, remember it.")
        host.reply(host.permission(turn), "once")
        turn.result(timeout=30)

        assert remembered in opencode_stack.memories()
        hits = host.tool_output(session, "pc_search")
        assert hits["ok"] is True
        assert remembered in json.dumps(hits["data"])
