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

"""Bub recalls with what the user sent: each inbound message's text, or the end of a long prompt."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar

import httpx2
import pytest
import republic
from bub.channels.message import ChannelMessage
from bub.framework import BubFramework
from bub.hooks import hookimpl
from bub.hooks.interception import LLMCallRequest, LLMCallResult
from powercontext_bub import plugin as plugin_module
from powercontext_bub.plugin import PowerContextPlugin, PowerContextSettings

_ROOT = Path(__file__).resolve().parents[3]
FIXTURE = json.loads((_ROOT / "tests/fixtures/recall_query_bound.json").read_text(encoding="utf-8"))
QUESTION = "How should we deploy the migrations?"


def _expand(segments: list[dict[str, Any]]) -> str:
    return "".join(segment["text"] * segment.get("repeat", 1) for segment in segments)


class RecordingClient:
    prepared: ClassVar[list[Any]] = []

    def __init__(self, base_url: str, **options: object) -> None:
        del base_url, options

    async def __aenter__(self) -> RecordingClient:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        del exc_info

    async def resolve_scope_binding(self, request: Any) -> SimpleNamespace:
        del request
        return SimpleNamespace(scope_id="scp_00000000000000000000000000")

    async def prepare_context(self, request: Any) -> SimpleNamespace:
        self.prepared.append(request)
        return SimpleNamespace(content="remembered context", content_bytes=18, status=SimpleNamespace(value="ready"))


def _cli_message(content: str, **fields: Any) -> ChannelMessage:
    return ChannelMessage(
        session_id="cli:local",
        channel="cli",
        chat_id="local",
        content=content,
        context={"sender_id": "human"},
        **fields,
    )


@pytest.fixture
def framework(monkeypatch, tmp_path: Path) -> BubFramework:
    """Bub's builtin hooks with the PowerContext plugin registered after them, as Bub loads it."""
    RecordingClient.prepared.clear()
    settings = PowerContextSettings(base_url="http://127.0.0.1:8000")
    monkeypatch.setattr(plugin_module, "ensure_config", lambda _: settings)
    monkeypatch.setattr(plugin_module, "PowerContextClient", RecordingClient)
    monkeypatch.chdir(tmp_path)
    framework = BubFramework(config_file=tmp_path / "bub.yml")
    framework.load_builtin_hooks()
    framework.plugin_manager.register(PowerContextPlugin(framework), name="powercontext")
    return framework


def _plugin(framework: BubFramework) -> PowerContextPlugin:
    plugin = framework.plugin_manager.get_plugin("powercontext")
    assert isinstance(plugin, PowerContextPlugin)
    return plugin


def _turn_state(framework: BubFramework, session_id: str) -> dict[str, Any]:
    # The builtin load_state binds the session and its agent; this keeps the turn to the plugin's state.
    state = {"session_id": session_id, **_plugin(framework).load_state(message={}, session_id=session_id)}
    return {**state, "_runtime_agent": SimpleNamespace(command_prefix=",")}


def _prepared_queries(framework: BubFramework, state: dict[str, Any], prompt: list[Any]) -> list[str]:
    request = LLMCallRequest(run_id="run-1", model="test-model", messages=[republic.user(*prompt)])

    result = asyncio.run(_plugin(framework).before_llm_call(request, state))

    assert (result is not None) == bool(RecordingClient.prepared)
    return [prepared.query for prepared in RecordingClient.prepared]


def _recall_queries(framework: BubFramework, message: Any) -> list[str]:
    session_id = str(message.session_id) if isinstance(message, ChannelMessage) else "cli:local"
    state = _turn_state(framework, session_id)
    prompt = asyncio.run(framework.build_prompt(message, session_id, state))
    return _prepared_queries(framework, state, prompt)


@pytest.mark.parametrize("case", FIXTURE["cases"], ids=[case["id"] for case in FIXTURE["cases"]])
def test_recall_query_keeps_the_end_of_the_prompt(framework: BubFramework, case: dict[str, Any]) -> None:
    state = _turn_state(framework, "cli:local")

    assert _prepared_queries(framework, state, [_expand(case["prompt"])]) == [_expand(case["query"])]


@pytest.mark.parametrize(
    "message",
    [
        _cli_message(QUESTION),
        ChannelMessage(
            session_id="chat:room-7",
            channel="chat",
            chat_id="room-7",
            content=f"{QUESTION}\nKeep the rollout order.",
            context={"sender_id": "42", "username": "ada"},
        ),
        _cli_message('{"message": "report", "constraint": "database migrations"}'),
    ],
    ids=["cli", "chat-channel", "json-text"],
)
def test_recall_query_is_the_inbound_content_without_the_bub_frame(
    framework: BubFramework, message: ChannelMessage
) -> None:
    assert _recall_queries(framework, message) == [message.content]


def test_recall_query_is_the_sender_text_when_the_content_wraps_it(framework: BubFramework) -> None:
    # Bub's Telegram channel wraps the sender's words in JSON with message metadata and keeps them as ``text``.
    message = {
        "session_id": "telegram:42",
        "channel": "telegram",
        "chat_id": "42",
        "content": json.dumps({"message": QUESTION, "message_id": 7, "username": "ada"}),
        "text": QUESTION,
    }

    assert _recall_queries(framework, message) == [QUESTION]


def test_recall_skips_a_message_without_text(framework: BubFramework) -> None:
    assert _recall_queries(framework, _cli_message("")) == []


def test_recall_maps_a_prompt_another_plugin_builds(framework: BubFramework) -> None:
    class SummaryPrompt:
        @hookimpl
        async def build_prompt(self, message: Any) -> str:
            return f"Summarize for the on-call engineer:\n{message.content}"

    framework.plugin_manager.register(SummaryPrompt(), name="summary")

    assert _recall_queries(framework, _cli_message(QUESTION)) == [QUESTION]


def test_subagent_recalls_with_its_own_prompt(framework: BubFramework) -> None:
    parent_state = _turn_state(framework, "cli:local")
    asyncio.run(framework.build_prompt(_cli_message("Plan the release."), "cli:local", parent_state))
    # Bub runs a subagent with a copy of the parent's turn state under the subagent's own session.
    subagent_state = {**parent_state, "session_id": "cli:local/subagent-1"}

    assert _prepared_queries(framework, subagent_state, ["Check the migration order."]) == [
        "Check the migration order."
    ]


def test_steered_follow_up_recalls_with_its_own_text(monkeypatch, tmp_path: Path, framework: BubFramework) -> None:
    follow_up = "What is the support escalation policy?"

    class SteerOnce:
        steered = False

        @hookimpl
        async def after_llm_call(self, request: LLMCallRequest, result: LLMCallResult, state: dict[str, Any]) -> None:
            del request, result
            if not self.steered:
                self.steered = True
                await framework.steer_message(message=_cli_message(follow_up), session_id="cli:local", state=state)

    replies = ["one", "two"]

    def model_reply(request: httpx2.Request) -> httpx2.Response:
        del request
        events = [{"choices": [{"delta": {"content": replies.pop(0)}, "finish_reason": "stop"}]}, "[DONE]"]
        body = "".join(f"data: {event if isinstance(event, str) else json.dumps(event)}\n\n" for event in events)
        return httpx2.Response(200, headers={"content-type": "text/event-stream"}, text=body)

    client = httpx2.AsyncClient
    monkeypatch.setattr(
        httpx2, "AsyncClient", lambda **options: client(**{"transport": httpx2.MockTransport(model_reply), **options})
    )
    for name, value in {
        "BUB_HOME": str(tmp_path / "home"),
        "BUB_MODEL": "openai:test-model",
        "BUB_API_KEY": "test",
        "BUB_CLIENT_ARGS": '{"api_format": "chat"}',
    }.items():
        monkeypatch.setenv(name, value)
    framework.plugin_manager.register(SteerOnce(), name="steer-once")

    async def run_turn() -> None:
        async with framework.running():
            await framework.process_inbound(_cli_message(QUESTION))

    asyncio.run(run_turn())

    assert replies == []
    assert [prepared.query for prepared in RecordingClient.prepared] == [QUESTION, follow_up]
