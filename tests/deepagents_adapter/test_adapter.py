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
from deepagents.middleware.subagents import DEFAULT_SUBAGENT_PROMPT
from fastapi import FastAPI
from langchain_core.language_models import BaseChatModel
from langchain_core.language_models.base import LanguageModelInput
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool, tool
from langchain_core.utils.function_calling import convert_to_openai_tool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
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
from powercontext.http import CreateScopeRequest, RememberMemoryRequest, ResolveScopeBindingRequest
from powercontext.server.factory import create_server_app
from powercontext.server.settings import McpConfig, ServerSettings

CONTEXT_MARKER = "PowerContext host-supplied context"
RECALL_TEXT = "Run the migrations before deploying."


class _ScriptedModel(BaseChatModel):
    """Call the ``task`` tool once, then answer; subagents answer directly."""

    calls: list[list[BaseMessage]] = Field(default_factory=list)

    def bind_tools(
        self,
        tools: Sequence[dict[str, Any] | type | Callable[..., Any] | BaseTool],
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ) -> Runnable[LanguageModelInput, AIMessage]:
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


GENERAL_PURPOSE_PROBE = DEFAULT_SUBAGENT_PROMPT.split("\n", 1)[0]


class _DelegatingModel(_ScriptedModel):
    """Delegate once to ``target_subagent``, then answer; the target answers directly."""

    target_subagent: str = "general-purpose"

    def _generate(self, messages: Sequence[BaseMessage], stop=None, run_manager=None, **kwargs) -> ChatResult:
        self.calls.append(list(messages))
        if GENERAL_PURPOSE_PROBE in "\n".join(_system_texts(messages)):
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content="Delegated answer."))])
        if any(isinstance(message, ToolMessage) for message in messages):
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content="Final answer."))])
        return ChatResult(
            generations=[
                ChatGeneration(
                    message=AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "name": "task",
                                "id": "call-gp",
                                "args": {"description": "find deployment steps", "subagent_type": self.target_subagent},
                                "type": "tool_call",
                            }
                        ],
                    )
                )
            ]
        )


class _AnsweringModel(BaseChatModel):
    """Record the tools it is bound with, then answer."""

    tool_names: list[str] = Field(default_factory=list)

    def bind_tools(
        self,
        tools: Sequence[dict[str, Any] | type | Callable[..., Any] | BaseTool],
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ) -> Runnable[LanguageModelInput, AIMessage]:
        self.tool_names = [convert_to_openai_tool(item)["function"]["name"] for item in tools]
        return self

    def _generate(self, messages: Sequence[BaseMessage], stop=None, run_manager=None, **kwargs) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="ok"))])

    @property
    def _llm_type(self) -> str:
        return "answering"


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

    assert [spec["name"] for spec in augmented] == ["general-purpose", "researcher"]
    middleware = augmented[1]["middleware"]
    assert len(middleware) == 1
    assert isinstance(middleware[0], PowerContextMiddleware)
    assert middleware[0].auto_capture is False
    assert "middleware" not in original, "the caller's spec must not be mutated"


def test_default_general_purpose_subagent_gets_recall() -> None:
    """Deep Agents creates this subagent implicitly, so it must be supplied with recall."""
    augmented = powercontext_subagents(None)

    assert [spec["name"] for spec in augmented] == ["general-purpose"]
    general = augmented[0]
    assert general["description"], "the built-in description must be preserved"
    assert general["system_prompt"], "the built-in system prompt must be preserved"
    assert [item.auto_capture for item in general["middleware"]] == [False]
    assert "model" not in general, "Deep Agents must inherit the parent model"
    assert "tools" not in general, "Deep Agents must inherit the parent tools"


def test_disabled_general_purpose_profile_is_respected() -> None:
    """Opting into PowerContext must not re-enable delegation that the profile disabled."""
    from deepagents import GeneralPurposeSubagentProfile, HarnessProfile, register_harness_profile
    from deepagents.profiles.harness import harness_profiles

    registry = harness_profiles._HARNESS_PROFILES
    snapshot = dict(registry)
    try:
        register_harness_profile(
            "_scriptedmodel",
            HarnessProfile(general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False)),
        )
        model = _ScriptedModel()
        assert powercontext_subagents(None, model=model) == []
        assert [spec["name"] for spec in powercontext_subagents([SUBAGENT], model=model)] == ["researcher"]

        captured: dict[str, Any] = {}

        def fake_create(inner_model: Any = None, tools: Any = None, **kwargs: Any) -> str:
            captured.update(kwargs)
            return "agent"

        with pytest.MonkeyPatch.context() as patcher:
            patcher.setattr(deepagents, "create_deep_agent", fake_create)
            assert create_powercontext_deep_agent(model) == "agent"
        assert captured["subagents"] == [], "no general-purpose spec may be forwarded"
    finally:
        registry.clear()
        registry.update(snapshot)


def test_disabled_general_purpose_profile_keeps_delegation_disabled() -> None:
    """The compiled agent must not offer ``task`` when the profile disabled it."""
    from deepagents import GeneralPurposeSubagentProfile, HarnessProfile, register_harness_profile
    from deepagents.profiles.harness import harness_profiles

    registry = harness_profiles._HARNESS_PROFILES
    snapshot = dict(registry)
    try:
        register_harness_profile(
            "_answeringmodel",
            HarnessProfile(general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False)),
        )
        model = _AnsweringModel()
        agent = create_powercontext_deep_agent(model, tools=[])
        agent.invoke({"messages": [{"role": "user", "content": "hi"}]}, context=PowerContextScope())
        assert "task" not in model.tool_names
    finally:
        registry.clear()
        registry.update(snapshot)


def test_disabled_general_purpose_profile_applies_to_the_default_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """The deprecated default-model path must resolve the same profile as Deep Agents."""
    from deepagents import GeneralPurposeSubagentProfile, HarnessProfile, register_harness_profile
    from deepagents.profiles.harness import harness_profiles

    registry = harness_profiles._HARNESS_PROFILES
    snapshot = dict(registry)
    try:
        register_harness_profile(
            "_answeringmodel",
            HarnessProfile(general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False)),
        )
        model = _AnsweringModel()
        monkeypatch.setattr("deepagents.graph._build_default_model", lambda: model)

        agent = create_powercontext_deep_agent(tools=[])
        agent.invoke({"messages": [{"role": "user", "content": "hi"}]}, context=PowerContextScope())

        assert "task" not in model.tool_names
    finally:
        registry.clear()
        registry.update(snapshot)


def test_explicit_general_purpose_subagent_is_not_duplicated() -> None:
    spec = {"name": "general-purpose", "description": "custom", "system_prompt": "custom prompt"}
    augmented = powercontext_subagents([spec])

    assert [item["name"] for item in augmented] == ["general-purpose"]
    assert augmented[0]["middleware"][0].auto_capture is False


def test_capture_middleware_on_a_subagent_is_normalized_to_recall() -> None:
    """A delegated turn must never be captured, even if the caller asked for it."""
    capturing = PowerContextMiddleware(auto_capture=True, trace_content=True)
    spec = {**SUBAGENT, "middleware": [capturing]}
    augmented = powercontext_subagents([spec])

    normalized = augmented[1]["middleware"][0]
    assert isinstance(normalized, PowerContextMiddleware)
    assert normalized.auto_capture is False
    assert normalized.trace_content is True, "unrelated options must survive normalization"
    assert normalized is not capturing
    assert capturing.auto_capture is True, "the caller-owned middleware must not be mutated"
    assert spec["middleware"] == [capturing], "the caller-owned spec must not be mutated"


def test_compiled_subagents_are_left_alone() -> None:
    compiled = {"name": "compiled", "description": "owns its graph", "runnable": object()}
    augmented = powercontext_subagents([compiled])

    assert augmented[1] == compiled
    assert [spec["name"] for spec in augmented] == ["general-purpose", "compiled"]


def test_compiled_general_purpose_subagent_is_not_duplicated() -> None:
    compiled = {"name": "general-purpose", "description": "owns its graph", "runnable": object()}
    assert powercontext_subagents([compiled]) == [compiled]


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


def test_default_general_purpose_delegation_recalls(monkeypatch: pytest.MonkeyPatch) -> None:
    """Deep Agents' implicit general-purpose subagent must receive bounded recall too."""

    async def fake_prepare(self: PowerContextMiddleware, query: str, scope: Any) -> str:
        return RECALL_TEXT

    monkeypatch.setattr(PowerContextMiddleware, "_prepare", fake_prepare)

    model = _DelegatingModel()
    agent = create_powercontext_deep_agent(model, tools=[], system_prompt="You are a deployment assistant.")
    result = agent.invoke(
        {"messages": [{"role": "user", "content": "How should we deploy?"}]},
        context=PowerContextScope(),
    )

    general_purpose_calls = [
        messages for messages in model.calls if GENERAL_PURPOSE_PROBE in "\n".join(_system_texts(messages))
    ]
    assert general_purpose_calls, "the task tool must invoke the built-in general-purpose subagent"
    assert all(CONTEXT_MARKER in "\n".join(_system_texts(messages)) for messages in general_purpose_calls), (
        "the delegated general-purpose model call must receive bounded recall"
    )
    assert result["messages"][-1].text == "Final answer."


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


@tool
def _lookup(topic: str) -> str:
    """Look up a deployment topic."""
    return f"steps for {topic}"


class _ToolLoopModel(_ScriptedModel):
    """Call one tool, then answer."""

    calls: list[list[BaseMessage]] = Field(default_factory=list)

    def _generate(self, messages: Sequence[BaseMessage], stop=None, run_manager=None, **kwargs) -> ChatResult:
        self.calls.append(list(messages))
        if any(isinstance(message, ToolMessage) for message in messages):
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content="Final answer."))])
        return ChatResult(
            generations=[
                ChatGeneration(
                    message=AIMessage(
                        content="",
                        tool_calls=[
                            {"name": "_lookup", "id": "call-1", "args": {"topic": "deploy"}, "type": "tool_call"}
                        ],
                    )
                )
            ]
        )


def test_repeated_model_calls_do_not_accumulate_context(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_prepare(self: PowerContextMiddleware, query: str, scope: Any) -> str:
        return RECALL_TEXT

    async def fake_capture(self: PowerContextMiddleware, user_text: str, assistant_text: str, scope: Any) -> None:
        return None

    monkeypatch.setattr(PowerContextMiddleware, "_prepare", fake_prepare)
    monkeypatch.setattr(PowerContextMiddleware, "_capture", fake_capture)

    model = _ToolLoopModel()
    agent = create_powercontext_deep_agent(model, tools=[_lookup], subagents=[SUBAGENT])
    agent.invoke({"messages": [{"role": "user", "content": "deploy?"}]}, context=PowerContextScope())

    assert len(model.calls) >= 2, "the tool loop must produce more than one model call"
    for messages in model.calls:
        system_text = "\n".join(_system_texts(messages))
        assert system_text.count(CONTEXT_MARKER) == 1, "each model request gets exactly one recall block"


async def _stream_agent(model: BaseChatModel, agent: Any, scope: PowerContextScope) -> list[Any]:
    chunks = []
    async for chunk in agent.astream(
        {"messages": [{"role": "user", "content": "How should we deploy?"}]},
        context=scope,
        stream_mode="values",
    ):
        chunks.append(chunk)
    return chunks


def test_streaming_agent_recalls_and_captures_once(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[tuple[str, str]] = []

    async def fake_prepare(self: PowerContextMiddleware, query: str, scope: Any) -> str:
        return RECALL_TEXT

    async def fake_capture(self: PowerContextMiddleware, user_text: str, assistant_text: str, scope: Any) -> None:
        captured.append((user_text, assistant_text))

    monkeypatch.setattr(PowerContextMiddleware, "_prepare", fake_prepare)
    monkeypatch.setattr(PowerContextMiddleware, "_capture", fake_capture)

    model = _ScriptedModel()
    agent = create_powercontext_deep_agent(model, tools=[], subagents=[SUBAGENT], auto_capture=True)

    chunks = asyncio.run(_stream_agent(model, agent, PowerContextScope()))

    assert chunks, "astream must yield state"
    assert all(message.type != "system" for message in chunks[-1]["messages"])
    assert RECALL_TEXT in "\n".join(_system_texts(model.calls[-1]))
    assert captured == [("How should we deploy?", "Deploy after migrating.")]


def test_interrupt_and_resume_capture_once(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[tuple[str, str]] = []
    executed: list[str] = []

    @tool
    def risky(action: str) -> str:
        """Run a risky deployment action."""
        executed.append(action)
        return f"done:{action}"

    async def fake_prepare(self: PowerContextMiddleware, query: str, scope: Any) -> None:
        return None

    async def fake_capture(self: PowerContextMiddleware, user_text: str, assistant_text: str, scope: Any) -> None:
        captured.append((user_text, assistant_text))

    monkeypatch.setattr(PowerContextMiddleware, "_prepare", fake_prepare)
    monkeypatch.setattr(PowerContextMiddleware, "_capture", fake_capture)

    class _RiskyModel(BaseChatModel):
        calls: int = 0

        def bind_tools(
            self, tools: Sequence[Any], *, tool_choice: str | None = None, **kwargs: Any
        ) -> Runnable[LanguageModelInput, AIMessage]:
            return self

        def _generate(self, messages: Sequence[BaseMessage], stop=None, run_manager=None, **kwargs) -> ChatResult:
            self.calls += 1
            if any(isinstance(message, ToolMessage) for message in messages):
                return ChatResult(generations=[ChatGeneration(message=AIMessage(content="Approved and done."))])
            return ChatResult(
                generations=[
                    ChatGeneration(
                        message=AIMessage(
                            content="",
                            tool_calls=[
                                {"name": "risky", "id": "call-1", "args": {"action": "deploy"}, "type": "tool_call"}
                            ],
                        )
                    )
                ]
            )

        @property
        def _llm_type(self) -> str:
            return "risky"

    agent = create_powercontext_deep_agent(
        _RiskyModel(),
        tools=[risky],
        subagents=[SUBAGENT],
        auto_capture=True,
        interrupt_on={"risky": True},
        checkpointer=InMemorySaver(),
    )
    config = {"configurable": {"thread_id": "interrupt-1"}}

    interrupted = asyncio.run(
        agent.ainvoke(
            {"messages": [{"role": "user", "content": "deploy it"}]}, context=PowerContextScope(), config=config
        )
    )
    assert interrupted.get("__interrupt__"), "the risky tool call must pause the run"
    assert executed == [], "the tool must not run before approval"
    assert captured == [], "an interrupted run is not a completed turn"

    resumed = asyncio.run(
        agent.ainvoke(Command(resume={"decisions": [{"type": "approve"}]}), context=PowerContextScope(), config=config)
    )

    assert executed == ["deploy"]
    assert resumed["messages"][-1].text == "Approved and done."
    assert captured == [("deploy it", "Approved and done.")], "resume captures the completed turn exactly once"


def test_scopes_are_isolated_between_runs(tmp_path: Path) -> None:
    first_memory = "Run database migrations before deploying the application."
    second_memory = "Deploy only after draining traffic from the load balancer."
    model = _ScriptedModel()
    agent = create_powercontext_deep_agent(model, tools=[], system_prompt="You are a deployment assistant.")
    app = _server_app(tmp_path)
    runs: list[tuple[int, int]] = []

    async def scenario() -> None:
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as transport,
        ):
            client = PowerContextClient("http://testserver", http_client=transport, trust_transport_security=True)
            with shared_http_client(transport, trust_transport_security=True):
                scope_a = (
                    await client.create_scope(
                        CreateScopeRequest(title="scope-a", summary="scope a", idempotency_key="scope-a")
                    )
                ).scope_id
                scope_b = (
                    await client.create_scope(
                        CreateScopeRequest(title="scope-b", summary="scope b", idempotency_key="scope-b")
                    )
                ).scope_id
                for scope_id, text in ((scope_a, first_memory), (scope_b, second_memory)):
                    await client.remember_memory(
                        RememberMemoryRequest(scope_id=scope_id, kind="decision", text=text, description="fixture")
                    )

                for scope in (scope_a, scope_b):
                    start = len(model.calls)
                    await agent.ainvoke(
                        {"messages": [{"role": "user", "content": "How should we deploy the application?"}]},
                        context=PowerContextScope(scope_id=scope),
                    )
                    runs.append((start, len(model.calls)))

    asyncio.run(scenario())

    first_run = "\n".join(
        system for messages in model.calls[runs[0][0] : runs[0][1]] for system in _system_texts(messages)
    )
    second_run = "\n".join(
        system for messages in model.calls[runs[1][0] : runs[1][1]] for system in _system_texts(messages)
    )

    assert first_memory in first_run, first_run
    assert second_memory not in first_run, "scope B memory must not leak into scope A"
    assert second_memory in second_run, second_run
    assert first_memory not in second_run, "scope A memory must not leak into scope B"
