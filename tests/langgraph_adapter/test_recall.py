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

"""Behavior tests for the PowerContext LangGraph adapter.

Each test drives ``create_react_agent`` with a recording chat model against an in-process ``create_server_app``
instance, and asserts externally observable behavior only, per ``AGENTS.md``: what the model receives, and what
remains in the persisted history. Call counts, ordering, and internal structure are not frozen.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.prebuilt import create_react_agent
from pydantic import Field

from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import InferenceConfig
from powercontext.client import PowerContextClient
from powercontext.http import RememberMemoryRequest
from powercontext.server.factory import create_server_app
from powercontext.server.settings import McpConfig, ServerSettings

pytest.importorskip("powercontext_langgraph")

from powercontext_langgraph import MissingScopeError, PowerContextRecall, PowerContextScope

UNTRUSTED_LABEL = "untrusted historical evidence"
SCOPE = "project:langgraph-adapter-test"
DEPLOY_MEMORY = "Deploy database migrations before rollout."


class _RecordingModel(BaseChatModel):
    """A chat model that records each model input and returns a fixed acknowledgement.

    ``inputs`` holds the message list handed to the model at every step, which is where ``PowerContextRecall``'s
    ephemeral ``llm_input_messages`` becomes observable — it never lands in the persisted history.
    """

    inputs: list[list[BaseMessage]] = Field(default_factory=list)

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:  # type: ignore[no-untyped-def]
        self.inputs.append(list(messages))
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="acknowledged"))])

    @property
    def _llm_type(self) -> str:
        return "recording"


def _build_agent(model: _RecordingModel, *, checkpointer=None):  # type: ignore[no-untyped-def]
    return create_react_agent(
        model,
        tools=[],
        pre_model_hook=PowerContextRecall(),
        context_schema=PowerContextScope,
        checkpointer=checkpointer,
    )


def _server_app(tmp_path: Path) -> FastAPI:
    return create_server_app(
        settings=ServerSettings(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'runtime.db'}"),
            inference=InferenceConfig(generation_model="test"),
            mcp=McpConfig(enabled=False),
        )
    )


def _run(app: FastAPI, scenario: Callable[[PowerContextClient], Awaitable[None]]) -> None:
    async def driver() -> None:
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as transport,
        ):
            client = PowerContextClient("http://testserver", http_client=transport, trust_transport_security=True)
            from powercontext_langgraph.client import shared_http_client

            with shared_http_client(transport, trust_transport_security=True):
                await scenario(client)

    asyncio.run(driver())


def _system_texts(messages: list[BaseMessage]) -> list[str]:
    return [message.text for message in messages if getattr(message, "type", None) == "system"]


def _system_positions(messages: list[BaseMessage]) -> list[int]:
    return [index for index, message in enumerate(messages) if getattr(message, "type", None) == "system"]


async def _seed(client: PowerContextClient) -> None:
    await client.remember_memory(
        RememberMemoryRequest(scope_id=SCOPE, kind="decision", text=DEPLOY_MEMORY, reason="seeded for the recall test")
    )


def test_recall_supplies_no_prefix_when_context_is_empty(tmp_path: Path) -> None:
    model = _RecordingModel()
    agent = _build_agent(model)
    app = _server_app(tmp_path)

    async def scenario(_: PowerContextClient) -> None:
        await agent.ainvoke(
            {"messages": [HumanMessage(content="How do we deploy the database?")]},
            context=PowerContextScope(scope_id=SCOPE),
        )
        assert model.inputs, "the model was invoked"
        assert _system_texts(model.inputs[-1]) == []

    _run(app, scenario)


def test_recall_injects_context_labelled_untrusted(tmp_path: Path) -> None:
    model = _RecordingModel()
    agent = _build_agent(model)
    app = _server_app(tmp_path)

    async def scenario(client: PowerContextClient) -> None:
        await _seed(client)
        result = await agent.ainvoke(
            {"messages": [HumanMessage(content="How do we deploy the database migrations?")]},
            context=PowerContextScope(scope_id=SCOPE),
        )
        model_input = model.inputs[-1]
        system_texts = _system_texts(model_input)
        assert len(system_texts) == 1
        assert UNTRUSTED_LABEL in system_texts[0]
        assert "migrations" in system_texts[0]
        # The recall message leads the model input and never enters the persisted history.
        assert model_input[0].type == "system"
        assert _system_texts(result["messages"]) == []

    _run(app, scenario)


def test_recall_does_not_accumulate_across_turns(tmp_path: Path) -> None:
    # Regression for the checkpointed multi-turn case: recalled context must ride on the ephemeral llm_input_messages
    # channel so it never persists. Otherwise each turn leaves a recall system message in history, producing the
    # non-consecutive system messages that providers such as ChatAnthropic reject on the following turn.
    model = _RecordingModel()
    agent = _build_agent(model, checkpointer=InMemorySaver())
    app = _server_app(tmp_path)

    async def scenario(client: PowerContextClient) -> None:
        await _seed(client)
        config = {"configurable": {"thread_id": "regression"}}
        await agent.ainvoke(
            {"messages": [HumanMessage(content="How do we deploy the database migrations?")]},
            context=PowerContextScope(scope_id=SCOPE),
            config=config,
        )
        result = await agent.ainvoke(
            {"messages": [HumanMessage(content="And what should we double-check about the migrations first?")]},
            context=PowerContextScope(scope_id=SCOPE),
            config=config,
        )

        # Every model input carries at most one system message, always at the front: consecutive-system-safe input.
        for model_input in model.inputs:
            assert _system_positions(model_input) in ([], [0])
        # Recall fired at least once, so the invariant above is meaningful rather than vacuous.
        assert any(model_input and model_input[0].type == "system" for model_input in model.inputs)
        # Two turns leave no recall system message behind in the persisted history.
        assert _system_texts(result["messages"]) == []
        human_texts = [message.text for message in result["messages"] if message.type == "human"]
        assert human_texts == [
            "How do we deploy the database migrations?",
            "And what should we double-check about the migrations first?",
        ]

    _run(app, scenario)


def test_recall_completes_graph_for_over_limit_prompt(tmp_path: Path) -> None:
    # Regression: a human turn longer than the public query limit (8192 chars) must not abort the graph. The hook
    # clamps the query to that limit before building the request, so preparation stays best-effort and the graph
    # reaches its end instead of raising a request-validation error.
    model = _RecordingModel()
    agent = _build_agent(model)
    app = _server_app(tmp_path)
    long_prompt = "How do we deploy the database migrations? " + ("context padding " * 2000)
    assert len(long_prompt) > 8192

    async def scenario(client: PowerContextClient) -> None:
        await _seed(client)
        result = await agent.ainvoke(
            {"messages": [HumanMessage(content=long_prompt)]},
            context=PowerContextScope(scope_id=SCOPE),
        )
        # The graph completed: the model ran and produced an answer despite the over-limit prompt.
        assert model.inputs
        assert any(message.type == "ai" for message in result["messages"])
        # The clamped query is still a valid recall query, so recalled context reaches the model input.
        assert _system_texts(model.inputs[-1]), "recall should still fire on the clamped query"
        # Recalled context never persists, and the model never saw a raised error in its place.
        assert _system_texts(result["messages"]) == []

    _run(app, scenario)


def test_recall_isolates_cached_context_across_scopes(tmp_path: Path) -> None:
    # Regression for the multi-tenant case: one shared PowerContextRecall serves many runs. Two runs carrying the same
    # human turn (identical id and text) but different scopes must each receive only their own scope's context. If the
    # per-turn cache keyed on the turn alone, the first scope's prepared content would be replayed to the second.
    scope_alpha = "project:tenant-alpha"
    scope_bravo = "project:tenant-bravo"
    marker_alpha = "ALPHA_SECRET_RUNBOOK"
    marker_bravo = "BRAVO_SECRET_RUNBOOK"
    turn = HumanMessage(content="How do we deploy the database migrations?", id="shared-turn")

    model = _RecordingModel()
    # A single hook instance is reused across both invocations, exercising the shared-instance cache path.
    agent = create_react_agent(
        model,
        tools=[],
        pre_model_hook=PowerContextRecall(),
        context_schema=PowerContextScope,
    )
    app = _server_app(tmp_path)

    async def scenario(client: PowerContextClient) -> None:
        await client.remember_memory(
            RememberMemoryRequest(
                scope_id=scope_alpha,
                kind="decision",
                text=f"Deploy database migrations before rollout. {marker_alpha}",
                reason="seeded for the isolation test",
            )
        )
        await client.remember_memory(
            RememberMemoryRequest(
                scope_id=scope_bravo,
                kind="decision",
                text=f"Deploy database migrations before rollout. {marker_bravo}",
                reason="seeded for the isolation test",
            )
        )

        await agent.ainvoke({"messages": [turn]}, context=PowerContextScope(scope_id=scope_alpha))
        alpha_systems = "\n".join(_system_texts(model.inputs[-1]))

        await agent.ainvoke({"messages": [turn]}, context=PowerContextScope(scope_id=scope_bravo))
        bravo_systems = "\n".join(_system_texts(model.inputs[-1]))

        # Each run sees only its own tenant's memory; the shared cache never leaks one scope's content to the other.
        assert marker_alpha in alpha_systems
        assert marker_bravo not in alpha_systems
        assert marker_bravo in bravo_systems
        assert marker_alpha not in bravo_systems

    _run(app, scenario)


def test_agent_reaches_end_when_server_unreachable() -> None:
    model = _RecordingModel()
    agent = _build_agent(model)

    async def driver() -> None:
        # No shared client is installed, so the recall hook opens a real client against a closed port.
        result = await agent.ainvoke(
            {"messages": [HumanMessage(content="anything at all")]},
            context=PowerContextScope(scope_id=SCOPE, base_url="http://127.0.0.1:9", timeout=2.0),
        )
        assert model.inputs and _system_texts(model.inputs[-1]) == []
        assert any(message.type == "ai" for message in result["messages"])

    asyncio.run(driver())


def test_missing_scope_outside_repository_raises(tmp_path: Path) -> None:
    from powercontext_langgraph import resolve_scope_id

    with pytest.raises(MissingScopeError):
        resolve_scope_id(None, cwd=str(tmp_path))
