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

"""``prompt_recall=false`` leaves prompt-time context to the PowerContext gateway."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar

import httpx2
from bub.channels.message import ChannelMessage
from bub.framework import BubFramework
from powercontext_bub import plugin as plugin_module
from powercontext_bub.plugin import CONTEXT_MARKER, PowerContextPlugin, PowerContextSettings

PROMPT = "Which verification color does the orchid project use?"
ANSWER = "Noted."
CONTEXT = "The orchid project uses teal-728."


class RecordingClient:
    prepared: ClassVar[list[Any]] = []
    searched: ClassVar[list[Any]] = []
    captured: ClassVar[list[Any]] = []
    flushed: ClassVar[list[Any]] = []

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
        return SimpleNamespace(content=CONTEXT, content_bytes=len(CONTEXT), status=SimpleNamespace(value="ready"))

    async def search_memory(self, request: Any) -> SimpleNamespace:
        self.searched.append(request)
        return SimpleNamespace(hits=[])

    async def capture_content_source(self, request: Any) -> SimpleNamespace:
        self.captured.append(request)
        return SimpleNamespace(position=len(self.captured))

    async def flush_memory(self, request: Any) -> SimpleNamespace:
        self.flushed.append(request)
        position = len(self.captured)
        return SimpleNamespace(
            status=SimpleNamespace(value="completed"),
            previous_cursor=0,
            current_cursor=position,
            high_watermark=position,
            processed_source_count=position,
            remaining_work=False,
        )


def _run_turn(
    monkeypatch, tmp_path: Path, *, tool_calls: Sequence[tuple[str, dict[str, Any]]] = (), **settings: Any
) -> list[dict[str, Any]]:
    """Run one message through Bub's own turn; return the Chat Completions requests the stub model received.

    The model calls ``tool_calls`` in its first response, then answers.
    """
    for calls in (
        RecordingClient.prepared,
        RecordingClient.searched,
        RecordingClient.captured,
        RecordingClient.flushed,
    ):
        calls.clear()
    configured = PowerContextSettings(base_url="http://127.0.0.1:8000", **settings)
    monkeypatch.setattr(plugin_module, "ensure_config", lambda _: configured)
    monkeypatch.setattr(plugin_module, "PowerContextClient", RecordingClient)
    requests: list[dict[str, Any]] = []

    def model_reply(request: httpx2.Request) -> httpx2.Response:
        requests.append(json.loads(request.content))
        if tool_calls and len(requests) == 1:
            calls = [
                {
                    "index": index,
                    "id": f"call-{index}",
                    "type": "function",
                    "function": {"name": name, "arguments": json.dumps(arguments)},
                }
                for index, (name, arguments) in enumerate(tool_calls)
            ]
            events = [{"choices": [{"delta": {"tool_calls": calls}, "finish_reason": "tool_calls"}]}, "[DONE]"]
        else:
            events = [{"choices": [{"delta": {"content": ANSWER}, "finish_reason": "stop"}]}, "[DONE]"]
        body = "".join(f"data: {event if isinstance(event, str) else json.dumps(event)}\n\n" for event in events)
        return httpx2.Response(200, headers={"content-type": "text/event-stream"}, text=body)

    client = httpx2.AsyncClient
    monkeypatch.setattr(
        httpx2, "AsyncClient", lambda **options: client(**{"transport": httpx2.MockTransport(model_reply), **options})
    )
    for name, value in {
        "BUB_HOME": str(tmp_path / "home"),
        "BUB_MODEL": "openai:test-model",
        "BUB_API_KEY": "stub-model-credential",
        "BUB_CLIENT_ARGS": '{"api_format": "chat"}',
    }.items():
        monkeypatch.setenv(name, value)
    monkeypatch.chdir(tmp_path)
    framework = BubFramework(config_file=tmp_path / "bub.yml")
    framework.load_builtin_hooks()
    framework.plugin_manager.register(PowerContextPlugin(framework), name="powercontext")
    message = ChannelMessage(
        session_id="cli:local", channel="cli", chat_id="local", content=PROMPT, context={"sender_id": "human"}
    )

    async def turn() -> None:
        async with framework.running():
            await framework.process_inbound(message)

    asyncio.run(turn())
    return requests


def _offered_tools(request: dict[str, Any]) -> set[str]:
    return {tool["function"]["name"] for tool in request.get("tools", [])}


def _tool_results(request: dict[str, Any]) -> dict[str, str]:
    return {message["tool_call_id"]: message["content"] for message in request["messages"] if message["role"] == "tool"}


def test_recall_off_sends_no_preparation_and_still_captures_and_flushes(monkeypatch, tmp_path: Path) -> None:
    requests = _run_turn(monkeypatch, tmp_path, prompt_recall=False, capture_events=True)

    assert RecordingClient.prepared == []
    assert CONTEXT_MARKER not in json.dumps(requests)
    assert CONTEXT not in json.dumps(requests)
    assert [captured.metadata["event"] for captured in RecordingClient.captured] == ["user_prompt", "llm_result"]
    assert len(RecordingClient.flushed) == 1


def test_recall_off_neither_offers_nor_dispatches_the_prepare_tool(monkeypatch, tmp_path: Path) -> None:
    requests = _run_turn(
        monkeypatch,
        tmp_path,
        tool_calls=[("powercontext_context", {"query": "orchid"}), ("powercontext_search", {"query": "orchid"})],
        prompt_recall=False,
    )

    assert {"powercontext_search", "powercontext_remember"} <= _offered_tools(requests[0])
    assert "powercontext_context" not in _offered_tools(requests[0])
    # The model's call to the withheld tool is answered as an unknown tool, and the other tools still run.
    results = _tool_results(requests[1])
    assert "does not exist" in results["call-0"]
    assert RecordingClient.prepared == []
    assert [request.query for request in RecordingClient.searched] == ["orchid"]
    assert CONTEXT not in json.dumps(requests)


def test_recall_on_by_default_injects_context_and_dispatches_the_prepare_tool(monkeypatch, tmp_path: Path) -> None:
    requests = _run_turn(monkeypatch, tmp_path, tool_calls=[("powercontext_context", {"query": "orchid"})])

    assert "powercontext_context" in _offered_tools(requests[0])
    assert CONTEXT_MARKER in json.dumps(requests[0]["messages"])
    queries = [request.query for request in RecordingClient.prepared]
    assert queries[0] == PROMPT
    assert "orchid" in queries
    assert _tool_results(requests[1])["call-0"] == CONTEXT
