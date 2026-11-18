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

"""Actual `bub run` with the PowerContext plugin against a real PowerContext Server.

Bub runs from the replay harness environment that `make harness-sync` builds, where Bub 0.6.1 discovers the plugin
through its `bub` entry point. That environment installs only the PowerContext client, so the Server stays in this
process. The host model and the Server's generation model are deterministic local fixtures. These cases establish the
local Bub, plugin, and Server chain and the plugin's documented behavior; they do not qualify live-model behavior or
Bub's chat and gateway channels.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from collections.abc import Callable, Iterator
from contextlib import ExitStack
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

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BUB_ENVIRONMENT = PROJECT_ROOT / "e2e" / "bub" / ".venv"
BUB = BUB_ENVIRONMENT / ("Scripts/bub.exe" if os.name == "nt" else "bin/bub")
TOKEN = "bub-service-chain-token"  # noqa: S105 - non-secret test credential.
LABEL = "PowerContext host-supplied context"
FACT = "In the synthetic orchid project, the verification color is teal-728."
QUESTION = "Which verification color does the synthetic orchid project use?"


@dataclass
class BubStack:
    root: Path
    environment: dict[str, str]
    model: HostModelFixture
    wire: WireEvidence
    server: httpx.Client
    log: Path

    def workspace(self, name: str) -> Path:
        path = self.root / name
        path.mkdir(exist_ok=True)
        # Bub loads the nearest `.env` from its working directory upward; an empty one here stops that search
        # before it reaches a checkout's own `.env`.
        (path / ".env").touch()
        return path

    def run(self, workspace: Path, text: str, *, session_id: str = "e2e:session", **settings: str) -> int:
        """Run one `bub run` turn to completion; return the wire position where its Server requests begin."""
        start = len(self.wire.records)
        try:
            completed = subprocess.run(
                [str(BUB), "--workspace", str(workspace), "run", text, "--session-id", session_id],
                cwd=workspace,
                env={**self.environment, **settings},
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
        except subprocess.TimeoutExpired as expired:
            self._log(session_id, "timeout", expired.stdout, expired.stderr)
            pytest.fail(f"bub_run_timeout; see {self.log}", pytrace=False)
        self._log(session_id, completed.returncode, completed.stdout, completed.stderr)
        assert completed.returncode == 0, f"bub_run_failed; see {self.log}"
        return start

    def _log(self, session_id: str, outcome: object, stdout: str | bytes | None, stderr: str | bytes | None) -> None:
        output = "".join(
            part.decode(errors="replace") if isinstance(part, bytes) else part or "" for part in (stdout, stderr)
        )
        with self.log.open("a", encoding="utf-8") as log:
            log.write(f"$ bub run ({session_id}) -> {outcome}\n{output}\n")

    def calls(self, path: str, start: int = 0) -> list[dict[str, Any]]:
        return [record for record in self.wire.records[start:] if record["path"] == path]

    def scope_of(self, start: int) -> str:
        """The single Scope that one Bub turn's prepare used."""
        scopes = {record["request"]["scope_id"] for record in self.calls("/v1/context/prepare", start)}
        assert len(scopes) == 1, "bub_turn_scope_ambiguous"
        return scopes.pop()

    def create_scope(self, title: str) -> str:
        response = self.server.post(
            "/v1/scopes", json={"title": title, "summary": "Disposable Bub host acceptance.", "idempotency_key": title}
        )
        response.raise_for_status()
        return response.json()["scope_id"]

    def bind(self, start: int, scope_id: str) -> None:
        """Bind the workspace key that one Bub turn presented to `scope_id`, as an operator would."""
        resolved = self.calls("/v1/scope-bindings/resolve", start)
        assert resolved, "bub_scope_resolution_missing"
        (key,) = resolved[0]["request"]["binding_keys"]
        self.server.put("/v1/scope-bindings", json={"key": key, "scope_id": scope_id}).raise_for_status()

    def remember(self, scope_id: str, text: str) -> None:
        response = self.server.post("/v1/memory/remember", json={"scope_id": scope_id, "kind": "fact", "text": text})
        response.raise_for_status()

    def memories(self, scope_id: str) -> dict[str, dict[str, Any]]:
        response = self.server.post("/v1/memory/entries/list", json={"scope_id": scope_id})
        response.raise_for_status()
        return {entry["text"]: entry for entry in response.json()["entries"]}

    def sources(self, scope_id: str) -> list[Any]:
        response = self.server.get(f"/v1/scopes/{scope_id}/sources")
        response.raise_for_status()
        return [item["content"] for item in response.json()["items"]]


@pytest.fixture
def bub_stack(tmp_path: Path) -> Iterator[BubStack]:
    if not BUB.is_file():
        reason = "Bub host acceptance needs the Bub 0.6.1 environment installed by make harness-sync"
        if os.environ.get("POWERCONTEXT_TEST_BUB_HOST") == "1":
            pytest.fail(reason, pytrace=False)
        pytest.skip(f"{reason}; CI runs it in the bub-host job")
    home = tmp_path / "home"
    home.mkdir()
    model = HostModelFixture()
    wire = WireEvidence()
    with ExitStack() as stack:
        model_url = stack.enter_context(serve(model.app))
        settings = ServerSettings(
            _env_file=None,  # ty: ignore[unknown-argument] BaseSettings runtime option.
            workspace=tmp_path,
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'server.db'}"),
            auth=BearerAuthConfig(enabled=True, token=SecretStr(TOKEN)),
            # No memory schedule: only the plugin's own flush calls turn captured events into Memory.
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
        environment = {key: value for key, value in os.environ.items() if not key.startswith(("POWERCONTEXT_", "BUB_"))}
        environment.update(
            HOME=str(home),
            USERPROFILE=str(home),
            BUB_HOME=str(home / ".bub"),
            BUB_MODEL="openai:fixture",
            BUB_API_BASE=model_url + "/v1",
            BUB_API_KEY="synthetic-model-key-only",
            BUB_CLIENT_ARGS=json.dumps({"api_format": "chat"}),
            POWERCONTEXT_BUB_BASE_URL=base_url,
            POWERCONTEXT_BUB_API_TOKEN=TOKEN,
            # Every endpoint is a loopback fixture; never route it through the runner's proxy.
            NO_PROXY="127.0.0.1,localhost",
            no_proxy="127.0.0.1,localhost",
        )
        yield BubStack(tmp_path, environment, model, wire, server, tmp_path / "bub.log")


def test_bub_prompt_is_recalled_in_a_later_bub_session(bub_stack: BubStack) -> None:
    workspace = bub_stack.workspace("workspace")
    start = bub_stack.run(workspace, FACT, session_id="e2e:first", POWERCONTEXT_BUB_CAPTURE_EVENTS="true")
    scope_id = bub_stack.scope_of(start)
    # The Server runs no memory schedule, so only the plugin's end-of-turn flush can extract the captured prompt.
    _wait_until(lambda: FACT in bub_stack.memories(scope_id), "bub_turn_flush_missing")

    # A short question: lexical recall must match on the user's few words alone.
    question = "What is the orchid verification color?"
    model_start = len(bub_stack.model.requests)
    start = bub_stack.run(workspace, question, session_id="e2e:second")
    prepares = [record["response"] for record in bub_stack.calls("/v1/context/prepare", start)]
    assert prepares, "bub_recall_prepare_missing"
    prepared = prepares[0]
    assert prepared["status"] == "ready"
    assert FACT in prepared["content"]
    # The new session's first model call carries the Server's prepared content unchanged, after the plugin's label.
    sent = "\n".join(strings(bub_stack.model.requests[model_start]["messages"]))
    assert prepared["content"] in sent
    assert 0 <= sent.find(LABEL) < sent.find(prepared["content"])


def test_bub_capture_removes_known_credential_values(bub_stack: BubStack) -> None:
    credential = "sk-bube2ecredential0123456789"
    workspace = bub_stack.workspace("workspace")
    model_start = len(bub_stack.model.requests)
    # Bub's own provider key is a credential environment value of the Bub process.
    start = bub_stack.run(
        workspace,
        f"Rotate the model provider key {credential} before the orchid release.",
        POWERCONTEXT_BUB_CAPTURE_EVENTS="true",
        BUB_API_KEY=credential,
    )
    # Bub's own model received the prompt as written.
    assert credential in json.dumps(bub_stack.model.requests[model_start]["messages"])
    captures = [record["request"] for record in bub_stack.calls("/v1/sources/content", start)]
    prompts = [capture["content"] for capture in captures if capture["metadata"]["event"] == "user_prompt"]
    assert len(prompts) == 1
    assert (
        "Rotate the model provider key [REDACTED] before the orchid release."
        in json.loads(prompts[0])["payload"]["text"]
    )
    assert bub_stack.calls("/v1/memory/flush", start), "bub_turn_flush_missing"
    # Neither the captured Sources nor the Server's generation model receive the credential.
    stored = bub_stack.sources(bub_stack.scope_of(start))
    assert credential not in json.dumps([captures, stored, bub_stack.model.generation_requests])


def test_bub_workspace_binding_holds_across_a_resumed_session(bub_stack: BubStack) -> None:
    orchid = bub_stack.create_scope("bub-orchid")
    kestrel = bub_stack.create_scope("bub-kestrel")
    bub_stack.remember(orchid, FACT)
    bound = bub_stack.workspace("bound")
    other = bub_stack.workspace("other")

    greeting = "Hello from the orchid workspace."
    start = bub_stack.run(bound, greeting, session_id="e2e:resume")
    default = bub_stack.scope_of(start)
    assert default not in {orchid, kestrel}
    bub_stack.bind(start, orchid)

    # Resuming the session reaches the bound Scope and recalls from it.
    model_start = len(bub_stack.model.requests)
    start = bub_stack.run(bound, QUESTION, session_id="e2e:resume")
    assert bub_stack.scope_of(start) == orchid
    sent = "\n".join(strings(bub_stack.model.requests[model_start]["messages"]))
    # Bub replays the session's earlier turn, and the plugin still injects context for the new one.
    assert greeting in sent
    assert 0 <= sent.find(LABEL) < sent.find(FACT)

    # Another workspace keeps the Server default and does not see the bound Scope's Memory.
    start = bub_stack.run(other, QUESTION, session_id="e2e:resume")
    assert bub_stack.scope_of(start) == default
    assert bub_stack.calls("/v1/context/prepare", start)[0]["response"]["status"] == "empty"

    # An explicit Scope takes precedence over the workspace binding.
    start = bub_stack.run(bound, QUESTION, session_id="e2e:resume", POWERCONTEXT_BUB_SCOPE_ID=kestrel)
    assert bub_stack.scope_of(start) == kestrel

    # Only an opaque binding key leaves Bub; workspace paths never reach the Server.
    arrivals = "\n".join(strings(bub_stack.wire.arrivals))
    for workspace in (bound, other):
        assert str(workspace) not in arrivals
        assert str(workspace.resolve()) not in arrivals


def test_bub_capture_setting_leaves_recall_running(bub_stack: BubStack) -> None:
    workspace = bub_stack.workspace("workspace")
    # Capture is off by default; the turn still prepares context.
    start = bub_stack.run(workspace, QUESTION)
    assert len(bub_stack.calls("/v1/context/prepare", start)) == 1
    assert not bub_stack.calls("/v1/sources/content", start)
    assert not bub_stack.calls("/v1/memory/flush", start)

    start = bub_stack.run(workspace, QUESTION, POWERCONTEXT_BUB_CAPTURE_EVENTS="true")
    assert len(bub_stack.calls("/v1/context/prepare", start)) == 1
    events = [record["request"]["metadata"]["event"] for record in bub_stack.calls("/v1/sources/content", start)]
    assert "user_prompt" in events
    assert bub_stack.calls("/v1/memory/flush", start)


def test_bub_memory_tools_reach_the_bound_scope(bub_stack: BubStack) -> None:
    kestrel = bub_stack.create_scope("bub-kestrel")
    workspace = bub_stack.workspace("workspace")
    start = bub_stack.run(workspace, "Hello from the kestrel workspace.")
    default = bub_stack.scope_of(start)
    bub_stack.bind(start, kestrel)

    decision = "The synthetic kestrel project uses coral-512 as its verification color."
    bub_stack.model.plan([("powercontext_remember", {"kind": "decision", "text": decision})])
    bub_stack.run(workspace, "Save the kestrel verification color decision.")
    assert bub_stack.memories(kestrel)[decision]["kind"] == "decision"
    assert decision not in bub_stack.memories(default)

    bub_stack.model.plan([("powercontext_search", {"query": "kestrel verification color"})])
    bub_stack.run(workspace, "Search PowerContext for the kestrel verification color.")
    assert decision in _last_tool_result(bub_stack.model.requests[-1])

    bub_stack.model.plan([("powercontext_context", {"query": "kestrel verification color"})])
    bub_stack.run(workspace, "Prepare PowerContext context about the kestrel verification color.")
    assert decision in _last_tool_result(bub_stack.model.requests[-1])


def _last_tool_result(request: dict[str, Any]) -> str:
    """The latest tool result that Bub delivered to the model in one request."""
    results = [message for message in request["messages"] if message.get("role") == "tool"]
    assert results, "bub_tool_result_missing"
    return "".join(strings(results[-1].get("content")))


def _wait_until(condition: Callable[[], bool], failure: str, *, timeout: float = 15) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, failure
        time.sleep(0.05)
