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

"""Actual ZCode CLI behind the PowerContext gateway, with the plugin's prompt-time recall and capture off.

ZCode calls the Rust gateway's Responses route; the gateway prepares and injects context, checks every request with a
real PowerContext Server over its Unix socket, captures the turn, and calls a deterministic local Responses fixture.
The plugin's hooks and MCP tools reach the same Server through the gateway, as in a combined deployment. No live model
is called. The cases establish the documented ZCode gateway setup; they do not qualify live-model behavior.

Set `ZCODE_CLI_BIN` to a built `dist/zcode.cjs` to run them; `POWERCONTEXT_TEST_ZCODE_GATEWAY=1` turns a missing
prerequisite into a failure.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, NoReturn

import httpx
import pytest
import uvicorn
from starlette.background import BackgroundTask
from starlette.requests import Request
from starlette.responses import StreamingResponse

from tests.e2e.host_models.responses import ResponsesModelFixture
from tests.e2e.host_models.script import FINAL_ANSWER
from tests.e2e.test_gateway_authentication import gateway_environment, running_gateway
from tests.e2e.test_server_uds import _TOKEN, _environment, _running_server
from tests.e2e.zcode_acceptance.runner import serve

pytestmark = pytest.mark.skipif(sys.platform not in {"linux", "darwin"}, reason="Gateway Core link requires UDS")

PROJECT_ROOT = Path(__file__).resolve().parents[2]
GATEWAY_KEY = "synthetic-zcode-gateway-key"
PROFILE = "zcode-gateway"
MODEL = "zcode-fixture"
FACT = "In the synthetic heron project, the release color is amber-264."
PREPARED_MARKERS = ("BEGIN_POWERCONTEXT_PREPARED_CONTEXT_V1", "BEGIN_POWERCONTEXT_PREPARED_TEXT_V1")
PLUGIN_BINDING = "PowerContext current-request binding metadata"
STEERING = PROJECT_ROOT / "gateway/crates/pc-gateway-app/src/default_steering.txt"
SEARCH = "mcp__plugin_powercontext_powercontext__search_memory"
# ZCode appends a TodoWrite reminder after ten assistant messages without TodoWrite, and a repeated-call reminder
# after three identical calls; twelve identical searches reach both inside one turn.
TOOL_LOOP_CALLS = 12


@dataclass
class Recorder:
    """Forward requests unchanged to `target` and keep each request's path, headers, and JSON body."""

    target: str
    uds: str | None = None
    # When set, model requests skip the gateway and go to this Responses endpoint, as without the gateway.
    direct: str | None = None
    records: list[dict[str, Any]] = field(default_factory=list)

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            return
        request = Request(scope, receive)
        body = await request.body()
        try:
            parsed = json.loads(body) if body else None
        except ValueError:
            parsed = None
        headers = {key.lower(): value for key, value in request.headers.items()}
        # Recordings keep no credential, even a synthetic one.
        headers |= {key: "<redacted>" for key in ("authorization", "x-api-key") if key in headers}
        record = {"path": request.url.path, "headers": headers, "body": parsed}
        self.records.append(record)
        # Keep the client's Host, which Core uses for its MCP redirect.
        forwarded = {key: value for key, value in request.headers.items() if key.lower() != "content-length"}
        transport = httpx.AsyncHTTPTransport(uds=self.uds) if self.uds else None
        client = httpx.AsyncClient(base_url=self.target, transport=transport, trust_env=False, timeout=120)
        url = request.url.path + (f"?{request.url.query}" if request.url.query else "")
        if self.direct and request.url.path == "/gateway/v1/responses":
            url = self.direct
            forwarded.pop("host", None)
            record["direct"] = True
        upstream = await client.send(
            client.build_request(request.method, url, headers=forwarded, content=body), stream=True
        )
        record["status"] = upstream.status_code
        if upstream.status_code >= 400:
            # Keep an error's body, which names a withhold or gate reason, for the failure report.
            await upstream.aread()
            record["error"] = upstream.content[:2000].decode("utf-8", "replace")

        async def close() -> None:
            await upstream.aclose()
            await client.aclose()

        # Stream the response so a long-lived MCP event stream passes through; an error was already read and decoded.
        dropped = {"content-length", "transfer-encoding", "connection"} | (
            {"content-encoding"} if "error" in record else set()
        )
        headers = {key: value for key, value in upstream.headers.items() if key.lower() not in dropped}
        content = iter([upstream.content]) if "error" in record else upstream.aiter_raw()
        response = StreamingResponse(
            content, status_code=upstream.status_code, headers=headers, background=BackgroundTask(close)
        )
        await response(scope, receive, send)

    def paths(self) -> list[str]:
        return [record["path"] for record in self.records]


@contextmanager
def serve_uds(app: Any, path: Path) -> Iterator[None]:
    server = uvicorn.Server(uvicorn.Config(app, uds=str(path), log_level="error", access_log=False))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 30
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert server.started, "uds_recorder_startup_failed"
    try:
        yield
    finally:
        server.should_exit = True
        thread.join(timeout=15)


@dataclass
class ZCodeGatewayStack:
    node: str
    cli: Path
    workspace: Path
    upstream: str
    environment: dict[str, str]
    model: ResponsesModelFixture
    recorder: Recorder
    core_recorder: Recorder
    core: httpx.Client
    scope_id: str
    default_scope_id: str
    gateway_log: Path

    def process(
        self, prompt: str, *, resume: str | None = None, direct: bool = False, **environment: str
    ) -> subprocess.CompletedProcess[str]:
        """Run one headless ZCode turn; `direct` calls the model without the gateway."""
        self.recorder.direct = self.upstream if direct else None
        args = [str(self.node), str(self.cli), "--cwd", str(self.workspace), "--mode", "yolo", "--no-color"]
        args += ["--output-format", "stream-json", "--prompt", prompt]
        if resume:
            args += ["--resume", resume]
        return subprocess.run(
            args,
            env=self.environment | environment,
            cwd=self.workspace,
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )

    def run(
        self, prompt: str, *, resume: str | None = None, direct: bool = False, answer: bool = True, **environment: str
    ) -> str:
        """Run one turn that must succeed, and return its session ID."""
        completed = self.process(prompt, resume=resume, direct=direct, **environment)
        events = [json.loads(line) for line in completed.stdout.splitlines() if line.startswith("{")]
        results = [event for event in events if event.get("type") == "result"]
        assert completed.returncode == 0 and len(results) == 1, (completed.returncode, completed.stderr[-3000:])
        assert not answer or FINAL_ANSWER in json.dumps(results[0]), results[0]
        return results[0]["sessionId"]

    def model_requests(self) -> list[dict[str, Any]]:
        return [
            record
            for record in self.recorder.records
            if record["path"] == "/gateway/v1/responses" and not record.get("direct")
        ]

    def prepare_queries(self) -> list[str]:
        return [
            record["body"]["query"] for record in self.core_recorder.records if record["path"] == "/v1/context/prepare"
        ]

    def sources(self, scope_id: str) -> list[dict[str, Any]]:
        response = self.core.get(f"/v1/scopes/{scope_id}/sources")
        response.raise_for_status()
        return response.json()["items"]

    def turns(self, scope_id: str) -> list[dict[str, Any]]:
        return [
            json.loads(item["content"]) for item in self.sources(scope_id) if item["source_id"].startswith("gw-turn-")
        ]

    def calls(self, count: int) -> list[dict[str, Any]]:
        """The gateway's call evidence records, in the order the calls finished, once `count` have been written."""
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
    if os.environ.get("POWERCONTEXT_TEST_ZCODE_GATEWAY") == "1":
        pytest.fail(reason, pytrace=False)
    pytest.skip(f"{reason}; CI runs it in the ZCode acceptance gateway-chain job")


def _gateway_binary() -> Path:
    cargo = shutil.which("cargo")
    if cargo is None:
        _required("ZCode gateway acceptance needs the Rust toolchain to build the gateway")
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


def _texts(item: Any) -> str:
    """The text parts of one input message."""
    content = item.get("content") if isinstance(item, dict) else None
    if isinstance(content, str):
        return content
    return "".join(part.get("text", "") for part in content or [] if isinstance(part, dict))


def _user_items(request: dict[str, Any]) -> list[tuple[int, str]]:
    return [
        (index, _texts(item))
        for index, item in enumerate(request["input"])
        if isinstance(item, dict) and item.get("role") == "user"
    ]


@pytest.fixture
def zcode_gateway(request: pytest.FixtureRequest, tmp_path: Path, short_tmp_path: Path) -> Iterator[ZCodeGatewayStack]:
    node = shutil.which("node")
    cli = Path(os.environ.get("ZCODE_CLI_BIN", ""))
    if node is None or not cli.is_file():
        _required("ZCode gateway acceptance needs Node and a built ZCode CLI in ZCODE_CLI_BIN")
    gateway_binary = _gateway_binary()
    workspace, home = tmp_path / "workspace", tmp_path / "home"
    workspace.mkdir()
    home.mkdir()
    subprocess.run([shutil.which("git") or "git", "init", "--quiet", str(workspace)], check=True, timeout=30)
    model = ResponsesModelFixture()
    with ExitStack() as stack:
        upstream = stack.enter_context(serve(model.app)) + "/v1/responses"
        server_environment = _environment(tmp_path) | {"POWERCONTEXT_SERVER_MCP_ENABLED": "true"}
        # The guide's non-enforcing Core, unless a case asks for another mode.
        server_environment["POWERCONTEXT_SERVER_INFERENCE"] = json.dumps({
            "protection": {
                "mode": getattr(request, "param", "advisory"),
                "policy_version": "zcode-gateway.v1",
                "targets": {PROFILE: {"endpoint": upstream, "model": MODEL, "protocol": "openai-responses"}},
            }
        })
        _, core = stack.enter_context(_running_server(tmp_path, short_tmp_path / "core.sock", server_environment))
        scopes = []
        for key in ("zcode-gw", "zcode-gw-default"):
            created = core.post("/v1/scopes", json={"title": key, "summary": "ZCode gateway.", "idempotency_key": key})
            assert created.status_code == 201, created.text
            scopes.append(created.json()["scope_id"])
        scope_id, default_scope_id = scopes
        remembered = core.post("/v1/memory/remember", json={"scope_id": scope_id, "kind": "decision", "text": FACT})
        assert remembered.status_code in {200, 201}, remembered.text
        # Record the gateway's Core calls on the socket between them, to read each prepare query.
        core_recorder = Recorder("http://localhost", uds=str(short_tmp_path / "core.sock"))
        stack.enter_context(serve_uds(core_recorder, short_tmp_path / "gateway-core.sock"))
        gateway = stack.enter_context(
            running_gateway(
                gateway_binary,
                gateway_environment()
                | {
                    "PC_GATEWAY_CORE_UDS": str(short_tmp_path / "gateway-core.sock"),
                    "PC_GATEWAY_CORE_TOKEN": _TOKEN,
                    "PC_GATEWAY_API_KEY": GATEWAY_KEY,
                    "PC_GATEWAY_UPSTREAM_API_KEY": "synthetic-provider-key",
                    "PC_GATEWAY_UPSTREAM_PROFILE": PROFILE,
                    "PC_GATEWAY_UPSTREAM_URL_RESPONSES": upstream,
                    # ZCode's provider header selects the Scope; the default must not receive its turns.
                    "PC_GATEWAY_DEFAULT_SCOPE": default_scope_id,
                    "PC_GATEWAY_SEDIMENT": "on",
                },
                tmp_path,
            )
        )
        recorder = Recorder(str(gateway.base_url))
        gateway_url = stack.enter_context(serve(recorder))
        # The provider the guide documents, in ZCode's personal provider file: the gateway's Responses route, the
        # gateway key as the API key, and the Scope as a static header.
        provider_file = home / ".zcode/v2/provider_config.json"
        provider_file.parent.mkdir(parents=True)
        provider_file.write_text(
            json.dumps({
                "schemaVersion": 1,
                "config": {
                    "providerConfigRules": {
                        "providerRules": [
                            {
                                "providerId": "powercontext-gateway",
                                "config": {
                                    "group": "standard-personal",
                                    "access": {"type": "api-key", "apiKey": GATEWAY_KEY},
                                    "api": {
                                        "type": "openai-responses",
                                        "baseUrl": gateway_url + "/gateway/v1",
                                        "headers": {"X-PowerContext-Scope-ID": scope_id},
                                    },
                                    "personalModelIds": [MODEL],
                                    "modelOrder": [MODEL],
                                },
                            }
                        ]
                    },
                    "modelConfigRules": {
                        "providerModelRules": [
                            {
                                "providerId": "powercontext-gateway",
                                "modelId": MODEL,
                                "config": {"properties": {"contextWindow": 131072}},
                            }
                        ],
                        "manualProviderModelRules": [],
                    },
                    "defaultModelSelection": {
                        "providerId": "powercontext-gateway",
                        "modelId": MODEL,
                        "options": {"reasoningLevel": "high"},
                    },
                },
            }),
            encoding="utf-8",
        )
        (home / ".zcode/cli").mkdir()
        (home / ".zcode/cli/config.json").write_text(
            json.dumps({"locale": "en", "plugins": {"enabled": True, "dirs": []}}), encoding="utf-8"
        )
        environment = {
            key: value for key, value in os.environ.items() if not key.startswith(("POWERCONTEXT_", "ZCODE_"))
        }
        environment.update(
            HOME=str(home),
            USERPROFILE=str(home),
            ZCODE_STORAGE_DIR=str(home / ".zcode"),
            ZCODE_DATA_BASE_DIR=str(home),
            ZCODE_CLI_BIN=str(cli.resolve()),
            POWERCONTEXT_ZCODE_AUTHORIZATION=f"Bearer {_TOKEN}",
            POWERCONTEXT_ZCODE_SCOPE_ID=scope_id,
            POWERCONTEXT_CLIENT_CONFIG_FILE=str(tmp_path / "client-settings.json"),
            PYTHONPATH=str(PROJECT_ROOT / "src"),
        )
        # A combined gateway also serves the Core API and MCP, so the plugin keeps one address.
        installed = subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys; from powercontext.cli.installation import setup; "
                'setup("zcode", source=sys.argv[1], ref="master", server_url=sys.argv[2])',
                str(PROJECT_ROOT),
                gateway_url,
            ],
            env=environment,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        assert installed.returncode == 0, installed.stderr[-2000:]
        environment.update(POWERCONTEXT_ZCODE_PROMPT_RECALL="false", POWERCONTEXT_ZCODE_CAPTURE_PROMPTS="false")
        stack_value = ZCodeGatewayStack(
            node,
            cli,
            workspace,
            upstream,
            environment,
            model,
            recorder,
            core_recorder,
            core,
            scope_id,
            default_scope_id,
            tmp_path / "gateway.log",
        )
        try:
            yield stack_value
        finally:
            (tmp_path / "recordings.json").write_text(
                json.dumps(
                    {"zcode": recorder.records, "core": core_recorder.records, "upstream": model.requests},
                    indent=1,
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )


def test_zcode_turns_take_context_and_capture_only_from_the_gateway(zcode_gateway: ZCodeGatewayStack) -> None:
    first = "What is the release color in the synthetic heron project?"
    second = "Search memory for the heron release color until you are sure, then answer."
    session = zcode_gateway.run(first)
    zcode_gateway.model.plan(
        [("search_memory", {"scope_id": zcode_gateway.scope_id, "query": "heron", "mode": "fts"})] * TOOL_LOOP_CALLS
    )
    assert zcode_gateway.run(second, resume=session) == session

    sent = zcode_gateway.model_requests()
    assert len(sent) == len(zcode_gateway.model.requests) == 1 + TOOL_LOOP_CALLS + 1
    # ZCode names its session, but in no header the gateway reads, so the gateway infers the conversation.
    assert {headers["headers"].get("x-session-id") for headers in sent} == {session.removeprefix("sess_")}
    assert all("x-powercontext-conversation-id" not in record["headers"] for record in sent)
    assert {record["headers"].get("x-powercontext-scope-id") for record in sent} == {zcode_gateway.scope_id}

    # The gateway queried Core once per turn, with the turn's prompt; the tool loop replays the first step's block.
    assert zcode_gateway.prepare_queries() == [first, second]
    steering = STEERING.read_text(encoding="utf-8").strip()
    for request in zcode_gateway.model.requests:
        text = zcode_gateway.model.texts(request)
        assert PLUGIN_BINDING not in text
        assert sum(text.count(marker) for marker in PREPARED_MARKERS) == 1, "gateway_block_count"
        assert FACT in text
        assert steering in text
        assert "store" not in request

    loop = zcode_gateway.model.requests[1:]
    blocks = [
        next(item for item in request["input"] if any(marker in json.dumps(item) for marker in PREPARED_MARKERS))
        for request in loop
    ]
    # The block is prepended to the prompt message, which every step carries unchanged.
    assert all(block == blocks[0] for block in blocks)
    assert _texts(blocks[0]).endswith(second)
    # Some steps end with ZCode's own reminders after the tool results; the gateway forwards them unchanged.
    reminders = [
        text
        for request in loop
        for index, text in _user_items(request)
        if index == len(request["input"]) - 1 and text.startswith("<system-reminder>")
    ]
    assert any("TodoWrite" in text for text in reminders), "todo_reminder_missing"
    assert any("same input" in text for text in reminders), "repeat_reminder_missing"
    # The next turn's history holds no block from the turn before.
    assert sum(json.dumps(loop[0]["input"]).count(marker) for marker in PREPARED_MARKERS) == 1

    # The gateway, not the plugin, captured both turns, in the Scope ZCode's header named.
    turns = zcode_gateway.turns(zcode_gateway.scope_id)
    assert [turn["user"] for turn in turns] == [first, second]
    assert all(turn["kind"] == "gateway_turn" for turn in turns)
    assert not zcode_gateway.turns(zcode_gateway.default_scope_id)
    assert not [
        item for item in zcode_gateway.sources(zcode_gateway.scope_id) if item["source_id"].startswith("zcode-user-")
    ]
    # With recall and capture off, the plugin's hook sent no request; ZCode still used the plugin's MCP tools.
    plugin_paths = set(zcode_gateway.recorder.paths()) - {"/gateway/v1/responses"}
    assert not plugin_paths & {"/v1/context/prepare", "/v1/sources/content", "/v1/memory/flush"}, plugin_paths
    assert "/mcp" in plugin_paths
    assert [call["internal"] for call in zcode_gateway.calls(len(sent))] == [False] * len(sent)


def test_zcode_compaction_reaches_the_gateway_as_a_turn(zcode_gateway: ZCodeGatewayStack) -> None:
    first, last = (
        "What is the release color in the synthetic heron project?",
        "Repeat the heron release color once more.",
    )
    session = zcode_gateway.run(first)
    zcode_gateway.run("/compact", resume=session, answer=False)
    zcode_gateway.run(last, resume=session)

    # ZCode's compaction request is shaped and labeled like a turn, and its hooks cannot mark it internal.
    sent = zcode_gateway.model_requests()
    assert len(sent) == len(zcode_gateway.model.requests) == 3
    assert [record["headers"].get("x-zcode-session-type") for record in sent] == ["main"] * 3
    assert all("x-powercontext-turn" not in record["headers"] for record in sent)
    assert [call["internal"] for call in zcode_gateway.calls(3)] == [False] * 3
    instruction = _texts(sent[1]["body"]["input"][-1])
    assert instruction.startswith("CRITICAL: Respond with TEXT ONLY.")
    # So the gateway prepares with the compaction instruction as the query, adds its steering, and captures the
    # instruction and summary as a turn.
    assert zcode_gateway.prepare_queries() == [first, instruction, last]
    steering = STEERING.read_text(encoding="utf-8").strip()
    assert steering in zcode_gateway.model.texts(zcode_gateway.model.requests[1])
    turns = zcode_gateway.turns(zcode_gateway.scope_id)
    assert [turn["user"] for turn in turns] == [first, instruction, last]
    # After compaction, the summary arrives as ZCode's own user message before the prompt, and the gateway prepares
    # for that prompt as usual.
    after = zcode_gateway.model.requests[2]["input"]
    assert _texts(after[-2]).startswith("This session is being continued from a previous conversation")
    assert _texts(after[-1]).endswith(last)
    assert sum(json.dumps(after).count(marker) for marker in PREPARED_MARKERS) == 1


def test_a_session_that_ran_with_prompt_recall_can_continue_behind_the_gateway(
    zcode_gateway: ZCodeGatewayStack,
) -> None:
    # Before the move: the plugin's recall is on and ZCode calls the model directly.
    first = "What is the heron release color?"
    session = zcode_gateway.run(first, direct=True, POWERCONTEXT_ZCODE_PROMPT_RECALL="true")
    direct = zcode_gateway.model.texts(zcode_gateway.model.requests[-1])
    assert PLUGIN_BINDING in direct
    assert sum(direct.count(marker) for marker in PREPARED_MARKERS) == 1
    # The plugin prepared through the combined deployment's Core API; the gateway prepared nothing.
    plugin = [
        record["body"]["query"] for record in zcode_gateway.recorder.records if record["path"] == "/v1/context/prepare"
    ]
    assert plugin == zcode_gateway.prepare_queries() == [first]
    # ZCode does not send a turn's hook context again with later turns, so the resumed session carries no block the
    # gateway did not prepare, and Core admits it.
    resumed = "Repeat the heron release color."
    assert zcode_gateway.run(resumed, resume=session) == session
    behind = zcode_gateway.model.texts(zcode_gateway.model.requests[-1])
    assert PLUGIN_BINDING not in behind
    assert sum(behind.count(marker) for marker in PREPARED_MARKERS) == 1
    assert zcode_gateway.prepare_queries() == [first, resumed]
    assert [record["status"] for record in zcode_gateway.model_requests()] == [200]


def test_the_plugins_prompt_recall_behind_the_gateway_pauses_every_request(zcode_gateway: ZCodeGatewayStack) -> None:
    prompt = "What is the heron release color?"
    # ZCode retries a 503 ten times over about six minutes by default; its own settings shorten that to three attempts.
    completed = zcode_gateway.process(
        prompt,
        POWERCONTEXT_ZCODE_PROMPT_RECALL="true",
        ZCODE_MODEL_RETRY_MAX_RETRIES="2",
        ZCODE_MODEL_RETRY_BASE_DELAY_MS="100",
    )
    # The plugin prepared through the combined deployment's Core API and put its block in the prompt.
    plugin = [
        record["body"]["query"] for record in zcode_gateway.recorder.records if record["path"] == "/v1/context/prepare"
    ]
    assert plugin == [prompt]
    # Core refuses a block the gateway did not bind, so the gateway pauses every attempt and the turn fails.
    sent = zcode_gateway.model_requests()
    assert len(sent) == 3
    assert [(record["status"], json.loads(record["error"])["decision"]) for record in sent] == [
        (503, "gate_paused")
    ] * len(sent)
    checks = [
        record["status"] for record in zcode_gateway.core_recorder.records if record["path"] == "/v1/context/check"
    ]
    assert checks == [422] * len(sent)
    assert not zcode_gateway.model.requests
    assert completed.returncode != 0


@pytest.mark.parametrize("zcode_gateway", ["enforcing"], indirect=True)
def test_an_enforcing_core_withholds_every_zcode_request(zcode_gateway: ZCodeGatewayStack) -> None:
    completed = zcode_gateway.process("What is the heron release color?")
    # ZCode sends no `store: false`, which an enforcing Core requires on the Responses route, and does not retry a 400.
    assert completed.returncode != 0
    assert [(record["status"], json.loads(record["error"])) for record in zcode_gateway.model_requests()] == [
        (400, {"decision": "withhold", "reason_codes": ["provider_state_unsupported"]})
    ]
    assert not zcode_gateway.model.requests
