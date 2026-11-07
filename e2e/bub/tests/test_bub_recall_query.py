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

"""Bub recalls with what the user sent: the inbound content, or the end of a long prompt."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar

import pytest
import republic
from bub.builtin.hook_impl import BuiltinImpl
from bub.channels.message import ChannelMessage
from bub.hooks.interception import LLMCallRequest
from powercontext_bub import plugin as plugin_module
from powercontext_bub.plugin import PowerContextPlugin, PowerContextSettings

_ROOT = Path(__file__).resolve().parents[3]
FIXTURE = json.loads((_ROOT / "tests/fixtures/recall_query_bound.json").read_text(encoding="utf-8"))


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


def _plugin(monkeypatch, tmp_path: Path) -> PowerContextPlugin:
    RecordingClient.prepared.clear()
    settings = PowerContextSettings(base_url="http://127.0.0.1:8000")
    monkeypatch.setattr(plugin_module, "ensure_config", lambda _: settings)
    monkeypatch.setattr(plugin_module, "PowerContextClient", RecordingClient)
    return PowerContextPlugin(SimpleNamespace(workspace=tmp_path))


def _prepared_queries(plugin: PowerContextPlugin, state: dict[str, Any], prompt: str) -> list[str]:
    request = LLMCallRequest(run_id="run-1", model="test-model", messages=[republic.user(prompt)])

    result = asyncio.run(plugin.before_llm_call(request, state))

    assert (result is not None) == bool(RecordingClient.prepared)
    return [prepared.query for prepared in RecordingClient.prepared]


def _recall_queries(
    monkeypatch, tmp_path: Path, prompt: str, *, message: ChannelMessage | None = None, session_id: str = "session-1"
) -> list[str]:
    plugin = _plugin(monkeypatch, tmp_path)
    # Bub's builtin load_state supplies the session id beside the plugin's state.
    state = {"session_id": session_id, **plugin.load_state(message=message, session_id=session_id)}
    return _prepared_queries(plugin, state, prompt)


def _bub_prompt(message: ChannelMessage) -> str:
    agent = SimpleNamespace(command_prefix=",")
    prompt = asyncio.run(
        BuiltinImpl(framework=None).build_prompt(message, message.session_id, {"_runtime_agent": agent})
    )
    assert len(prompt) == 1
    assert isinstance(prompt[0], str)
    return prompt[0]


@pytest.mark.parametrize("case", FIXTURE["cases"], ids=[case["id"] for case in FIXTURE["cases"]])
def test_recall_query_keeps_the_end_of_the_prompt(monkeypatch, tmp_path: Path, case: dict[str, Any]) -> None:
    assert _recall_queries(monkeypatch, tmp_path, _expand(case["prompt"])) == [_expand(case["query"])]


@pytest.mark.parametrize(
    "message",
    [
        ChannelMessage(
            session_id="cli:local",
            channel="cli",
            chat_id="local",
            content="How should we deploy the migrations?",
            context={"sender_id": "human"},
        ),
        ChannelMessage(
            session_id="chat:room-7",
            channel="chat",
            chat_id="room-7",
            content="How should we deploy the migrations?\nKeep the rollout order.",
            context={"sender_id": "42", "username": "ada"},
        ),
        ChannelMessage(
            session_id="cli:local",
            channel="cli",
            chat_id="local",
            content='{"message": "report", "constraint": "database migrations"}',
        ),
    ],
    ids=["cli", "chat-channel", "json-text"],
)
def test_recall_query_is_the_inbound_content_without_the_bub_frame(
    monkeypatch, tmp_path: Path, message: ChannelMessage
) -> None:
    prompt = _bub_prompt(message)
    assert prompt != message.content

    assert _recall_queries(monkeypatch, tmp_path, prompt, message=message, session_id=message.session_id) == [
        message.content
    ]


def test_recall_skips_a_message_without_text(monkeypatch, tmp_path: Path) -> None:
    message = ChannelMessage(session_id="cli:local", channel="cli", chat_id="local", content="")

    assert _recall_queries(monkeypatch, tmp_path, _bub_prompt(message), message=message, session_id="cli:local") == []


def test_subagent_recalls_with_its_own_prompt(monkeypatch, tmp_path: Path) -> None:
    message = ChannelMessage(session_id="cli:local", channel="cli", chat_id="local", content="Plan the release.")
    plugin = _plugin(monkeypatch, tmp_path)
    parent_state = {"session_id": "cli:local", **plugin.load_state(message=message, session_id="cli:local")}
    # Bub runs a subagent with a copy of the parent's turn state under the subagent's own session.
    subagent_state = {**parent_state, "session_id": "cli:local/subagent-1"}

    assert _prepared_queries(plugin, subagent_state, "Check the migration order.") == ["Check the migration order."]
