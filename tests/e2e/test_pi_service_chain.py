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

"""Actual Pi RPC with the Pi package's PowerContext extension against a real PowerContext Server.

Pi loads the extension directly with a pinned Scope, so package installation, bundled skills, and Scope resolution are
outside these cases. The host model and the Server's generation model are deterministic local fixtures. These cases
establish the local Pi, extension, and Server chain and the package's documented lifecycle; they do not qualify
live-model behavior.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import httpx
import pytest
from pydantic import AnyHttpUrl, SecretStr
from starlette.types import ASGIApp, Receive, Scope, Send

from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import InferenceConfig, RuntimeConfig
from powercontext.server.factory import create_server_app
from powercontext.server.settings import BearerAuthConfig, ServerSettings
from tests.e2e.cross_host_work_continuity.pi import PiHost
from tests.e2e.zcode_acceptance.protocol import HostModelFixture, ObserveServer, WireEvidence, strings
from tests.e2e.zcode_acceptance.runner import serve

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PI_PLUGIN = PROJECT_ROOT / "integrations" / "pi" / "plugins" / "powercontext"
PI_CLI = PI_PLUGIN / "node_modules" / "@earendil-works" / "pi-coding-agent" / "dist" / "cli.js"
# PiHost always selects this provider and its "fixture" model.
PI_PROVIDER = "continuity"
TOKEN = "pi-service-chain-token"  # noqa: S105 - non-secret test credential.
PENDING_FACT_MARKER = "amber-314"
PENDING_FACT = f"In the synthetic heron project, the verification color is {PENDING_FACT_MARKER}."


class FlushOutage:
    """Fault only the Server's flush route at the HTTP boundary; every other request reaches the real Server."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self.mode: Literal["off", "unavailable", "stalled"] = "off"
        self.released = threading.Event()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        mode = self.mode
        if mode == "off" or scope["type"] != "http" or scope["path"] != "/v1/memory/flush":
            await self.app(scope, receive, send)
            return
        if mode == "stalled":
            # Hold the request past the host's closing budget, then answer without processing anything.
            await asyncio.to_thread(self.released.wait, 60)
        body = b'{"detail":"synthetic flush outage"}'
        await send({
            "type": "http.response.start",
            "status": 503,
            "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())],
        })
        await send({"type": "http.response.body", "body": body})


@dataclass
class PiStack:
    node: str
    workspace: Path
    environment: dict[str, str]
    model: HostModelFixture
    wire: WireEvidence
    outage: FlushOutage
    server: httpx.Client
    scope_id: str
    diagnostics: Path

    def open(self, **settings: str) -> PiHost:
        """Start a Pi process, which is also a new Pi session."""
        return PiHost(self.node, PI_CLI, PI_PLUGIN, self.workspace, {**self.environment, **settings})

    def memories(self) -> dict[str, dict[str, Any]]:
        response = self.server.post("/v1/memory/entries/list", json={"scope_id": self.scope_id})
        response.raise_for_status()
        return {entry["text"]: entry for entry in response.json()["entries"]}

    def sources(self) -> list[Any]:
        response = self.server.get(f"/v1/scopes/{self.scope_id}/sources")
        response.raise_for_status()
        return [item["content"] for item in response.json()["items"]]

    def flush_statuses(self) -> list[int]:
        return [record["status"] for record in self.wire.records if record["path"] == "/v1/memory/flush"]

    def missed_budgets(self) -> list[str]:
        """Lifecycle boundaries whose flush budget expired, from the package's content-free diagnostics."""
        text = self.diagnostics.read_text(encoding="utf-8") if self.diagnostics.exists() else ""
        # A line Pi is still appending has no newline yet.
        events = [json.loads(line) for line in text.split("\n")[:-1] if line.strip()]
        return [event["lifecycle_event"] for event in events if event.get("outcome") == "deadline_exceeded"]


@pytest.fixture
def pi_stack(tmp_path: Path) -> Iterator[PiStack]:
    node = shutil.which("node")
    if node is None or not PI_CLI.is_file():
        reason = "Pi host acceptance needs Node and the Pi package dependencies installed by make pi-test"
        if os.environ.get("POWERCONTEXT_TEST_PI_HOST") == "1":
            pytest.fail(reason, pytrace=False)
        pytest.skip(f"{reason}; CI runs it in the pi-package job")
    workspace = tmp_path / "workspace"
    agent = tmp_path / "pi-agent"
    home = tmp_path / "home"
    for directory in (workspace, agent, home):
        directory.mkdir()
    model = HostModelFixture()
    wire = WireEvidence()
    with ExitStack() as stack:
        model_url = stack.enter_context(serve(model.app))
        settings = ServerSettings(
            _env_file=None,  # ty: ignore[unknown-argument] BaseSettings runtime option.
            workspace=workspace,
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'server.db'}"),
            auth=BearerAuthConfig(enabled=True, token=SecretStr(TOKEN)),
            # No memory schedule: only the package's own flush calls turn captured prompts into Memory.
            runtime=RuntimeConfig(artifact_processing_families=("memory",)),
            inference=InferenceConfig(
                generation_model="openai-chat:memory-fixture",
                generation_base_url=AnyHttpUrl(model_url + "/v1"),
                generation_headers={"Authorization": SecretStr("Bearer synthetic-generation-only")},
            ),
        )
        outage = FlushOutage(create_server_app(settings=settings))
        base_url = stack.enter_context(serve(ObserveServer(outage, wire)))
        # Let a held flush answer before the Server stops.
        stack.callback(outage.released.set)
        server = stack.enter_context(
            httpx.Client(base_url=base_url, headers={"Authorization": f"Bearer {TOKEN}"}, timeout=15)
        )
        response = server.post(
            "/v1/scopes",
            json={
                "title": "Pi service chain",
                "summary": "Disposable Pi host acceptance.",
                "idempotency_key": "pi-service-chain",
            },
        )
        response.raise_for_status()
        scope_id = response.json()["scope_id"]
        (agent / "models.json").write_text(
            json.dumps({
                "providers": {
                    PI_PROVIDER: {
                        "baseUrl": model_url + "/v1",
                        "api": "openai-completions",
                        "apiKey": "synthetic-only",
                        "models": [{"id": "fixture", "contextWindow": 65536, "maxTokens": 4096}],
                    }
                }
            }),
            encoding="utf-8",
        )
        # Compact only on request, and allow a short conversation to be compacted.
        (agent / "settings.json").write_text(
            json.dumps({"compaction": {"enabled": False, "keepRecentTokens": 1}}), encoding="utf-8"
        )
        diagnostics = tmp_path / "pi-diagnostics.jsonl"
        environment = {key: value for key, value in os.environ.items() if not key.startswith(("POWERCONTEXT_", "PI_"))}
        environment.update(
            HOME=str(home),
            USERPROFILE=str(home),
            PI_CODING_AGENT_DIR=str(agent),
            PI_OFFLINE="1",
            POWERCONTEXT_PI_BASE_URL=base_url,
            POWERCONTEXT_PI_AUTHORIZATION=f"Bearer {TOKEN}",
            POWERCONTEXT_PI_SCOPE_ID=scope_id,
            POWERCONTEXT_PI_DIAGNOSTICS=str(diagnostics),
        )
        yield PiStack(node, workspace, environment, model, wire, outage, server, scope_id, diagnostics)


def test_pi_prompt_is_recalled_in_a_later_pi_session(pi_stack: PiStack) -> None:
    fact = "In the synthetic orchid project, the verification color is teal-728."
    credential = "sk-pie2ecredential0123456789"
    first = pi_stack.open()
    try:
        first.prompt(fact)
        first.prompt(f"Before the release, rotate the deploy key api_key={credential}.")
    finally:
        first.close()
    # The Server runs no memory schedule, so only the package's lifecycle flush can extract the captured Source.
    _wait_until(lambda: fact in pi_stack.memories(), "pi_lifecycle_flush_missing")
    assert pi_stack.sources() == [fact]
    # Pi's own model receives the credential prompt; neither the Server nor its generation model may receive it.
    server_side = json.dumps([pi_stack.wire.arrivals, pi_stack.wire.records, pi_stack.model.generation_requests])
    assert credential not in server_side

    question = "What is the verification color in the synthetic orchid project?"
    start = len(pi_stack.model.requests)
    second = pi_stack.open()
    try:
        second.prompt(question)
    finally:
        second.close()
    prepares = [
        record["response"]
        for record in pi_stack.wire.records
        if record["path"] == "/v1/context/prepare" and question in record["request"].get("query", "")
    ]
    assert prepares, "pi_recall_prepare_missing"
    prepared = prepares[-1]
    assert prepared["status"] == "ready"
    assert fact in prepared["content"]
    # The new session's first model call carries the Server's prepared content unchanged, after the package's label.
    sent = "\n".join(strings(pi_stack.model.requests[start]["messages"]))
    assert prepared["content"] in sent
    assert 0 <= sent.find("PowerContext host-supplied context.") < sent.find(prepared["content"])


def test_pi_compaction_checkpoints_a_pending_prompt_source(pi_stack: PiStack) -> None:
    with _pending_prompt(pi_stack) as host:
        pi_stack.outage.mode = "off"
        assert host.request("compact")["summary"]
        # The checkpoint completed before Pi finished compacting.
        assert PENDING_FACT in pi_stack.memories(), f"missed budgets: {pi_stack.missed_budgets()}"


def test_pi_stalled_compaction_checkpoint_keeps_the_source_pending(pi_stack: PiStack) -> None:
    with _pending_prompt(pi_stack) as host:
        pi_stack.outage.mode = "stalled"
        started = time.monotonic()
        assert host.request("compact")["summary"]
        # The one-second boundary budget, not the 30-second request timeout, ends the held checkpoint.
        assert time.monotonic() - started < 3
        assert PENDING_FACT not in pi_stack.memories()
        assert pi_stack.missed_budgets() == ["session_before_compact"]
        # Diagnostics are content-free and correlate the Scope only by hash.
        recorded = pi_stack.diagnostics.read_text(encoding="utf-8")
        assert PENDING_FACT_MARKER not in recorded
        assert pi_stack.scope_id not in recorded

        # The missed checkpoint keeps the position pending; the next lifecycle boundary flushes it.
        pi_stack.outage.mode = "off"
        pi_stack.outage.released.set()
        assert host.request("new_session") == {"cancelled": False}
        assert PENDING_FACT in pi_stack.memories(), f"missed budgets: {pi_stack.missed_budgets()}"


def test_pi_memory_tools_write_only_after_user_confirmation(pi_stack: PiStack) -> None:
    decision = "The synthetic kestrel project uses coral-512 as its verification color."
    remember = ("pc_remember", {"kind": "decision", "text": decision})
    request = "Save the kestrel verification color decision."
    # Without automatic prompt capture, the explicit tools are the only writers.
    host = pi_stack.open(POWERCONTEXT_PI_CAPTURE_PROMPTS="false")
    try:
        host.allowed = set()
        pi_stack.model.plan([remember])
        before = len(host.events)
        host.prompt(request, allow_tool_errors=True)
        # Pi asked the user to confirm the durable write; the declined write leaves nothing behind.
        assert any(
            event.get("type") == "extension_ui_request" and event.get("method") == "confirm"
            for event in host.events[before:]
        ), "pi_confirmation_not_requested"
        declined = _last_tool_result(pi_stack.model.requests[-1])
        assert declined["ok"] is False
        assert pi_stack.memories() == {}

        host.allowed = {"remember_memory"}
        pi_stack.model.plan([remember])
        host.prompt(request)
        assert host.confirmations == ["remember_memory"]
        entry = pi_stack.memories()[decision]
        assert entry["kind"] == "decision"

        pi_stack.model.plan([("pc_search", {"query": "kestrel verification color"})])
        host.prompt("Which verification color did we decide on for kestrel?")
        found = _last_tool_result(pi_stack.model.requests[-1])
        assert found["ok"] is True
        assert decision in json.dumps(found)
        assert entry["artifact"]["artifact_id"] in json.dumps(found)
    finally:
        host.close()


@contextmanager
def _pending_prompt(pi_stack: PiStack) -> Iterator[PiHost]:
    """Run Pi with one accepted prompt Source left pending behind an unavailable flush route."""
    pi_stack.outage.mode = "unavailable"
    # Generous request limits leave the lifecycle budget as the only bound on a held flush.
    host = pi_stack.open(POWERCONTEXT_PI_REQUEST_TIMEOUT_MS="30000", POWERCONTEXT_PI_HTTP_BUDGET_MS="60000")
    try:
        host.prompt(PENDING_FACT)
        _wait_until(lambda: 503 in pi_stack.flush_statuses(), "pi_agent_end_flush_missing")
        assert PENDING_FACT not in pi_stack.memories()
        yield host
    finally:
        host.close()


def _last_tool_result(request: dict[str, Any]) -> dict[str, Any]:
    """Decode the latest tool result that Pi delivered to the model in one request."""
    results = [message for message in request["messages"] if message.get("role") == "tool"]
    assert results, "pi_tool_result_missing"
    return json.loads("".join(strings(results[-1].get("content"))))


def _wait_until(condition: Callable[[], bool], failure: str, *, timeout: float = 15) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, failure
        time.sleep(0.05)
