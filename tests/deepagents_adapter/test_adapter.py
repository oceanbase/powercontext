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

"""Observable behavior tests for the PowerContext Deep Agents adapter."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import deepagents
import httpx
import pytest
from fastapi import FastAPI
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import Runnable
from powercontext_deepagents import (
    create_powercontext_deep_agent,
    powercontext_deep_agent_middleware,
    powercontext_subagents,
)
from powercontext_langchain import PowerContextMiddleware, PowerContextScope
from powercontext_langchain.client import shared_http_client
from pydantic import Field

from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import InferenceConfig, RuntimeConfig
from powercontext.client import PowerContextClient
from powercontext.http import RememberMemoryRequest, ResolveScopeBindingRequest
from powercontext.server.factory import create_server_app
from powercontext.server.settings import McpConfig, ServerSettings

CONTEXT_MARKER = "PowerContext host-supplied context"
RECALL_TEXT = "Run the migrations before deploying."


class _ScriptedModel(BaseChatModel):
    """Call the ``task`` tool once, then answer; subagents answer directly."""

    calls: list[list[BaseMessage]] = Field(default_factory=list)

    def bind_tools(
        self,
        tools: Sequence[dict[str, Any] | type | Callable[..., Any]],
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ) -> Runnable:
        return self

    def _generate(self, messages: Sequence[BaseMessage], stop=None, run_manager=None, **kwargs) -> ChatResult:
        self.calls.append(list(messages))
        if any(isinstance(message, ToolMessage) for message in messages):
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content="Deploy after migrating."))])
        if any(message.type == "system" and "You research." in message.text for message in messages):
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content="Researched."))])
        return ChatResult(
            generations=[
                ChatGeneration(
                    message=AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "name": "task",
                                "id": "call-1",
                                "args": {"description": "find deployment steps", "subagent_type": "researcher"},
                                "type": "tool_call",
                            }
                        ],
                    )
                )
            ]
        )

    @property
    def _llm_type(self) -> str:
        return "scripted"


SUBAGENT = {
    "name": "researcher",
    "description": "researches deployment steps",
    "system_prompt": "You research.",
}


def _system_texts(messages: Sequence[BaseMessage]) -> list[str]:
    return [message.text for message in messages if message.type == "system"]


def test_main_agent_middleware_follows_auto_capture() -> None:
    recall_only = powercontext_deep_agent_middleware()
    assert len(recall_only) == 1
    assert isinstance(recall_only[0], PowerContextMiddleware)
    assert recall_only[0].auto_capture is False

    capturing = powercontext_deep_agent_middleware(auto_capture=True)
    assert capturing[0].auto_capture is True


def test_existing_powercontext_middleware_is_reused() -> None:
    existing = PowerContextMiddleware(auto_capture=True)
    combined = powercontext_deep_agent_middleware([existing])
    assert combined == [existing]


def test_declarative_subagents_get_recall_only_middleware() -> None:
    original = dict(SUBAGENT)
    augmented = powercontext_subagents([original])

    assert augmented is not None
    assert len(augmented) == 1
    middleware = augmented[0]["middleware"]
    assert len(middleware) == 1
    assert isinstance(middleware[0], PowerContextMiddleware)
    assert middleware[0].auto_capture is False
    assert "middleware" not in original, "the caller's spec must not be mutated"


def test_compiled_subagents_are_left_alone() -> None:
    compiled = {"name": "compiled", "description": "owns its graph", "runnable": object()}
    assert powercontext_subagents([compiled]) == [compiled]


def test_subagents_with_powercontext_middleware_are_not_duplicated() -> None:
    existing = PowerContextMiddleware(auto_capture=False)
    spec = {**SUBAGENT, "middleware": [existing]}
    augmented = powercontext_subagents([spec])
    assert augmented == [spec]


def test_create_deep_agent_defaults_context_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_create(model: Any = None, tools: Any = None, **kwargs: Any) -> str:
        captured.update(kwargs)
        return "agent"

    monkeypatch.setattr(deepagents, "create_deep_agent", fake_create)

    assert create_powercontext_deep_agent(_ScriptedModel(), subagents=[SUBAGENT]) == "agent"
    assert captured["context_schema"] is PowerContextScope
    assert isinstance(captured["middleware"][0], PowerContextMiddleware)
    assert captured["subagents"][0]["middleware"][0].auto_capture is False


def test_create_deep_agent_keeps_caller_context_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_create(model: Any = None, tools: Any = None, **kwargs: Any) -> str:
        captured.update(kwargs)
        return "agent"

    monkeypatch.setattr(deepagents, "create_deep_agent", fake_create)
    create_powercontext_deep_agent(_ScriptedModel(), context_schema=dict)
    assert captured["context_schema"] is dict


def test_deep_agent_recalls_for_main_agent_and_subagents(monkeypatch: pytest.MonkeyPatch) -> None:
    captured_turns: list[tuple[str, str]] = []

    async def fake_prepare(self: PowerContextMiddleware, query: str, scope: Any) -> str:
        return RECALL_TEXT

    async def fake_capture(self: PowerContextMiddleware, user_text: str, assistant_text: str, scope: Any) -> None:
        captured_turns.append((user_text, assistant_text))

    monkeypatch.setattr(PowerContextMiddleware, "_prepare", fake_prepare)
    monkeypatch.setattr(PowerContextMiddleware, "_capture", fake_capture)

    model = _ScriptedModel()
    agent = create_powercontext_deep_agent(
        model,
        tools=[],
        system_prompt="You are a deployment assistant.",
        subagents=[SUBAGENT],
        auto_capture=True,
    )

    result = agent.invoke(
        {"messages": [{"role": "user", "content": "How should we deploy?"}]},
        context=PowerContextScope(),
    )

    main_systems = [
        texts for messages in model.calls for texts in [_system_texts(messages)] if RECALL_TEXT in "\n".join(texts)
    ]
    assert main_systems, "the injected context must reach the model call"
    assert len(model.calls) >= 3, "the subagent must run as its own model call"

    subagent_calls = [
        messages
        for messages in model.calls
        if any(message.type == "system" and "You research." in message.text for message in messages)
    ]
    assert subagent_calls, "the task tool must invoke the researcher subagent"
    assert all(CONTEXT_MARKER in "\n".join(_system_texts(messages)) for messages in subagent_calls), (
        "subagent model calls must receive bounded recall"
    )

    assert captured_turns == [("How should we deploy?", "Deploy after migrating.")], (
        "only the main agent captures, so a delegated turn is stored once"
    )
    assert all(CONTEXT_MARKER not in message.text for message in result["messages"]), (
        "recalled context must not enter agent state"
    )


def test_deep_agent_skips_capture_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    captured_turns: list[tuple[str, str]] = []

    async def fake_prepare(self: PowerContextMiddleware, query: str, scope: Any) -> None:
        return None

    async def fake_capture(self: PowerContextMiddleware, user_text: str, assistant_text: str, scope: Any) -> None:
        captured_turns.append((user_text, assistant_text))

    monkeypatch.setattr(PowerContextMiddleware, "_prepare", fake_prepare)
    monkeypatch.setattr(PowerContextMiddleware, "_capture", fake_capture)

    agent = create_powercontext_deep_agent(_ScriptedModel(), tools=[], subagents=[SUBAGENT])
    agent.invoke({"messages": [{"role": "user", "content": "hello"}]}, context=PowerContextScope())

    assert captured_turns == [], "capture is disabled by default"


def _server_app(tmp_path: Path) -> FastAPI:
    return create_server_app(
        settings=ServerSettings(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'runtime.db'}"),
            inference=InferenceConfig(generation_model="test"),
            runtime=RuntimeConfig(artifact_processing_families=()),
            mcp=McpConfig(enabled=False),
        )
    )


def test_deep_agent_recalls_server_memory_for_main_agent_and_subagent(tmp_path: Path) -> None:
    memory_text = "Run database migrations before deploying the application."
    model = _ScriptedModel()
    agent = create_powercontext_deep_agent(
        model,
        tools=[],
        system_prompt="You are a deployment assistant.",
        subagents=[SUBAGENT],
    )
    app = _server_app(tmp_path)

    async def scenario() -> None:
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as transport,
        ):
            client = PowerContextClient("http://testserver", http_client=transport, trust_transport_security=True)
            with shared_http_client(transport, trust_transport_security=True):
                scope_id = (await client.resolve_scope_binding(ResolveScopeBindingRequest())).scope_id
                await client.remember_memory(
                    RememberMemoryRequest(
                        scope_id=scope_id,
                        kind="decision",
                        text=memory_text,
                        description="Explicit fixture memory",
                    )
                )
                result = await agent.ainvoke(
                    {"messages": [{"role": "user", "content": "How should we deploy the application?"}]},
                    context=PowerContextScope(scope_id=scope_id),
                )

        assert all(message.type != "system" for message in result["messages"])

    asyncio.run(scenario())

    recalled = [
        messages
        for messages in model.calls
        if any(message.type == "system" and memory_text in message.text for message in messages)
    ]
    assert len(recalled) >= 2, "the main agent and the delegated subagent must both recall server memory"
