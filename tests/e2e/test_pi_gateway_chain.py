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

"""Actual Pi RPC behind the PowerContext gateway, with the Pi package's prompt-time recall and capture off.

Pi calls the Rust gateway's Responses route; the gateway prepares and injects context, checks every request with a
real PowerContext Server over its Unix socket, captures the turn, and calls a deterministic local Responses fixture.
No live model is called. The cases establish the documented Pi gateway setup; they do not qualify live-model behavior.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import ExitStack
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, NoReturn

import httpx
import pytest
from starlette.requests import Request
from starlette.responses import Response

from tests.e2e.cross_host_work_continuity.pi import PiHost
from tests.e2e.host_models.responses import ResponsesModelFixture
from tests.e2e.host_models.script import FINAL_ANSWER
from tests.e2e.test_gateway_authentication import gateway_environment, running_gateway
from tests.e2e.test_server_uds import _TOKEN, _environment, _running_server
from tests.e2e.zcode_acceptance.runner import serve

pytestmark = pytest.mark.skipif(sys.platform not in {"linux", "darwin"}, reason="Gateway Core link requires UDS")

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PI_PLUGIN = PROJECT_ROOT / "integrations" / "pi" / "plugins" / "powercontext"
PI_CLI = PI_PLUGIN / "node_modules" / "@earendil-works" / "pi-coding-agent" / "dist" / "cli.js"
# PiHost always selects this provider and its "fixture" model.
PI_PROVIDER = "continuity"
GATEWAY_KEY = "synthetic-pi-gateway-key"
PROFILE = "pi-gateway"
MODEL = "fixture"
FACT = "In the synthetic plover project, the verification color is cobalt-417."
PLUGIN_LABEL = "PowerContext host-supplied context."
PREPARED_MARKERS = ("BEGIN_POWERCONTEXT_PREPARED_CONTEXT_V1", "BEGIN_POWERCONTEXT_PREPARED_TEXT_V1")
STEERING = PROJECT_ROOT / "gateway/crates/pc-gateway-app/src/default_steering.txt"


@dataclass
class GatewayRecorder:
    """Forward Pi's requests to the gateway unchanged and keep the request headers Pi sent for each path."""

    gateway: str
    headers: list[tuple[str, dict[str, str]]] = field(default_factory=list)
    statuses: list[tuple[str, int, bytes]] = field(default_factory=list)

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        request = Request(scope, receive)
        self.headers.append((request.url.path, {key.lower(): value for key, value in request.headers.items()}))
        forwarded = {
            key: value for key, value in request.headers.items() if key.lower() not in {"host", "content-length"}
        }
        async with httpx.AsyncClient(base_url=self.gateway, trust_env=False, timeout=60) as client:
            upstream = await client.request(
                request.method,
                request.url.path + (f"?{request.url.query}" if request.url.query else ""),
                headers=forwarded,
                content=await request.body(),
            )
        self.statuses.append((request.url.path, upstream.status_code, upstream.content[:2000]))
        response = Response(upstream.content, status_code=upstream.status_code)
        for key in ("content-type", "www-authenticate"):
            if key in upstream.headers:
                response.headers[key] = upstream.headers[key]
        await response(scope, receive, send)

    def model_requests(self) -> list[dict[str, str]]:
        return [headers for path, headers in self.headers if path == "/gateway/v1/responses"]


@dataclass
class PiGatewayStack:
    node: str
    workspace: Path
    environment: dict[str, str]
    model: ResponsesModelFixture
    recorder: GatewayRecorder
    core: httpx.Client
    scope_id: str
    gateway_log: Path

    def open(self) -> PiHost:
        """Start a Pi process, which is also a new Pi session."""
        return PiHost(self.node, PI_CLI, PI_PLUGIN, self.workspace, self.environment)

    def sources(self) -> list[dict[str, Any]]:
        response = self.core.get(f"/v1/scopes/{self.scope_id}/sources")
        response.raise_for_status()
        return response.json()["items"]

    def calls(self, count: int) -> list[dict[str, Any]]:
        """The gateway's call evidence records, in the order the calls finished, once `count` have been written."""
        # The gateway writes evidence asynchronously, so the last call's record can trail its response.
        deadline = time.monotonic() + 10
        while True:
            records = []
            for line in self.gateway_log.read_text(encoding="utf-8").splitlines():
                if line.startswith("{"):
                    record = json.loads(line)
                    if record.get("operation") == "gateway.model_call" and record.get("event") == "call":
                        records.append(record["evidence"])
            if len(records) >= count or time.monotonic() >= deadline:
                return records
            time.sleep(0.1)


def _required(reason: str) -> NoReturn:
    if os.environ.get("POWERCONTEXT_TEST_PI_HOST") == "1":
        pytest.fail(reason, pytrace=False)
    pytest.skip(f"{reason}; CI runs it in the pi-package job")


def _gateway_binary() -> Path:
    cargo = shutil.which("cargo")
    if cargo is None:
        _required("Pi gateway acceptance needs the Rust toolchain to build the gateway")
    subprocess.run(
        [
            cargo,
            "build",
            "--locked",
            "--manifest-path",
            str(PROJECT_ROOT / "gateway/Cargo.toml"),
            "-p",
            "pc-gateway-app",
        ],
        check=True,
        timeout=900,
    )
    return PROJECT_ROOT / "gateway/target/debug/pc-gateway"


@pytest.fixture
def pi_gateway(tmp_path: Path, short_tmp_path: Path) -> Iterator[PiGatewayStack]:
    node = shutil.which("node")
    if node is None or not PI_CLI.is_file():
        _required("Pi host acceptance needs Node and the Pi package dependencies installed by make pi-test")
    gateway_binary = _gateway_binary()
    workspace = tmp_path / "workspace"
    agent = tmp_path / "pi-agent"
    home = tmp_path / "home"
    for directory in (workspace, agent, home):
        directory.mkdir()
    model = ResponsesModelFixture()
    with ExitStack() as stack:
        upstream = stack.enter_context(serve(model.app)) + "/v1/responses"
        server_environment = _environment(tmp_path)
        # An enforcing Core admits Pi's requests: Pi sends max_output_tokens, and its prompt_cache_key is approved here.
        server_environment["POWERCONTEXT_SERVER_INFERENCE"] = json.dumps({
            "protection": {
                "mode": "enforcing",
                "policy_version": "pi-gateway.v1",
                "targets": {
                    PROFILE: {
                        "endpoint": upstream,
                        "model": MODEL,
                        "protocol": "openai-responses",
                        # The UTF-8 bound counts a byte as a token, and Pi's requests exceed the fixture model's
                        # 64 Ki-token window under it.
                        "context_window_tokens": 1_000_000,
                        "max_output_tokens": 4096,
                        "validated_utf8_token_bound": True,
                        "allowed_cache_retention": ["provider-default"],
                    }
                },
            }
        })
        _, core = stack.enter_context(_running_server(tmp_path, short_tmp_path / "core.sock", server_environment))
        created = core.post(
            "/v1/scopes",
            json={"title": "Pi gateway", "summary": "Disposable Pi gateway acceptance.", "idempotency_key": "pi-gw"},
        )
        assert created.status_code == 201, created.text
        scope_id = created.json()["scope_id"]
        granted = core.post(
            "/v1/access/bindings/create",
            json={
                "subject": {"type": "service", "id": "server-token"},
                "resource": {"type": "scope", "scope_id": scope_id},
                "role": "gateway.user",
                "idempotency_key": "pi-gw-gateway-user",
            },
        )
        assert granted.status_code == 201, granted.text
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
                    "PC_GATEWAY_UPSTREAM_API_KEY_RESPONSES": "synthetic-provider-key",
                    "PC_GATEWAY_UPSTREAM_PROFILE_RESPONSES": PROFILE,
                    "PC_GATEWAY_DEFAULT_SCOPE": scope_id,
                    "PC_GATEWAY_SEDIMENT": "on",
                },
                tmp_path,
            )
        )
        # Compact only on request, and allow a short conversation to be compacted.
        (agent / "settings.json").write_text(
            json.dumps({"compaction": {"enabled": False, "keepRecentTokens": 1}}), encoding="utf-8"
        )
        recorder = GatewayRecorder(str(gateway.base_url))
        gateway_url = stack.enter_context(serve(recorder))
        # The provider the guide documents: the gateway's Responses route, with the gateway key as the API key.
        (agent / "models.json").write_text(
            json.dumps({
                "providers": {
                    PI_PROVIDER: {
                        "baseUrl": gateway_url + "/gateway/v1",
                        "api": "openai-responses",
                        "apiKey": GATEWAY_KEY,
                        "models": [{"id": MODEL, "reasoning": True, "contextWindow": 65536, "maxTokens": 4096}],
                    }
                }
            }),
            encoding="utf-8",
        )
        environment = {key: value for key, value in os.environ.items() if not key.startswith(("POWERCONTEXT_", "PI_"))}
        environment.update(
            HOME=str(home),
            USERPROFILE=str(home),
            XDG_CONFIG_HOME=str(home / ".config"),
            XDG_DATA_HOME=str(home / ".local/share"),
            XDG_CACHE_HOME=str(home / ".cache"),
            XDG_STATE_HOME=str(home / ".local/state"),
            PI_CODING_AGENT_DIR=str(agent),
            PI_OFFLINE="1",
            # A combined gateway also serves the Core API, so the package keeps one address.
            POWERCONTEXT_PI_BASE_URL=gateway_url,
            POWERCONTEXT_PI_AUTHORIZATION=f"Bearer {_TOKEN}",
            POWERCONTEXT_PI_SCOPE_ID=scope_id,
            POWERCONTEXT_PI_PROMPT_RECALL="false",
            POWERCONTEXT_PI_CAPTURE_PROMPTS="false",
        )
        yield PiGatewayStack(node, workspace, environment, model, recorder, core, scope_id, tmp_path / "gateway.log")


def test_pi_turns_take_context_and_capture_only_from_the_gateway(pi_gateway: PiGatewayStack) -> None:
    first = "What is the verification color in the synthetic plover project?"
    second = "Repeat the plover verification color once more."
    host = pi_gateway.open()
    try:
        # The first turn runs a tool loop: the model searches, then answers.
        pi_gateway.model.plan([("pc_search", {"query": "plover verification color"})])
        session = host.prompt(first)
        host.prompt(second)
        # Compaction asks the model for a summary, which Pi marks as not a turn of the gateway conversation.
        assert host.request("compact")["summary"]
    finally:
        host.close()

    # Pi received the fixture's answer for both turns through the gateway's streamed replay.
    replies = [json.dumps(event) for event in host.events if event.get("type") == "agent_end"]
    assert len(replies) == 2
    assert all(FINAL_ANSWER in reply and "errorMessage" not in reply for reply in replies), pi_gateway.recorder.statuses

    # Every turn request named the Pi session as the gateway conversation.
    sent = pi_gateway.recorder.model_requests()
    # Compaction splits the second turn, so Pi asks for a history summary and a turn-prefix summary.
    assert len(sent) == len(pi_gateway.model.requests) == 5
    assert {headers.get("x-powercontext-conversation-id") for headers in sent[:3]} == {session}
    assert all("x-powercontext-turn" not in headers for headers in sent[:3])
    # The summary requests name no conversation and are marked internal, so the gateway does not prepare, inject,
    # steer, or capture them.
    assert all("x-powercontext-conversation-id" not in headers for headers in sent[3:])
    assert [headers.get("x-powercontext-turn") for headers in sent[3:]] == ["internal", "internal"]
    assert [call["internal"] for call in pi_gateway.calls(5)] == [False, False, False, True, True]
    steering = STEERING.read_text(encoding="utf-8").strip()
    turn_requests, summary_requests = pi_gateway.model.requests[:3], pi_gateway.model.requests[3:]
    for request in summary_requests:
        text = pi_gateway.model.texts(request)
        assert PLUGIN_LABEL not in text
        assert not any(marker in text for marker in PREPARED_MARKERS), "gateway_block_count"
        assert steering not in text

    for request in turn_requests:
        text = pi_gateway.model.texts(request)
        assert PLUGIN_LABEL not in text
        assert sum(text.count(marker) for marker in PREPARED_MARKERS) == 1, "gateway_block_count"
        assert FACT in text
        assert steering in text
        # The gateway injects context itself and replaces a prepare result with a placeholder, so Pi does not offer it.
        offered = {tool.get("name") for tool in request.get("tools", [])}
        assert "pc_search" in offered
        assert "pc_prepare_context" not in offered
    first_step, tool_step, next_turn = (request["input"] for request in turn_requests)
    # The tool-loop step replays the first step's injected user message; Pi's own history never holds the block.
    assert pi_gateway.model.function_outputs(pi_gateway.model.requests[1])
    assert tool_step[1] == first_step[1]
    assert not any(marker in json.dumps(next_turn[:-1]) for marker in PREPARED_MARKERS)
    assert any(marker in json.dumps(next_turn[-1]) for marker in PREPARED_MARKERS)
    # The next turn's Memory comes from the gateway's block, not only from the earlier pc_search output.
    assert FACT in json.dumps(next_turn[-1], ensure_ascii=False)

    # The gateway, not the package, captured both turns in its configured Scope, and no summary as a turn.
    turns = [json.loads(item["content"]) for item in pi_gateway.sources() if item["source_id"].startswith("gw-turn-")]
    assert [turn["user"] for turn in turns] == [first, second]
    assert all(turn["kind"] == "gateway_turn" for turn in turns)
    assert not [item for item in pi_gateway.sources() if item["source_id"].startswith("pi-user-prompt:")]
