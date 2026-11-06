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

from __future__ import annotations

import asyncio
import logging
from typing import Any

import powercontext_pydantic_ai.toolset as toolset_module
import pytest
from powercontext_pydantic_ai import PowerContext, PowerContextSettings
from powercontext_pydantic_ai.capability import CONTEXT_MARKER
from pydantic import SecretStr
from pydantic_ai import Agent, RunContext
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    SystemPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import FunctionModel

from powercontext.client import ServerResponseError, TransportError
from tests.pydantic_ai_adapter.fakes import RecordingClient, prepared_response


def test_context_is_replaced_once_per_run_when_reusing_old_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    RecordingClient.reset()
    RecordingClient.prepare_result = prepared_response("context-one")
    monkeypatch.setattr(toolset_module, "PowerContextClient", RecordingClient)
    model_contexts: list[list[str]] = []
    stored_contexts: list[list[str]] = []

    async def respond(messages, _info):
        # Recalled context reaches the model as a leading `SystemPromptPart` on every step, including the
        # tool step whose request is rebuilt from persisted history.
        model_contexts.append([
            part.content
            for message in messages
            for part in message.parts
            if isinstance(part, SystemPromptPart) and CONTEXT_MARKER in part.content
        ])
        assert isinstance(messages[0].parts[0], SystemPromptPart)
        if isinstance(messages[-1].parts[-1], ToolReturnPart):
            return ModelResponse(parts=[TextPart("complete")])
        return ModelResponse(parts=[ToolCallPart("powercontext_search", {"query": "context"}, "search-1")])

    async def scenario() -> None:
        agent = Agent(FunctionModel(respond), capabilities=[PowerContext(scope_id="project:context")])
        first = await agent.run("first prompt")
        RecordingClient.prepare_result = prepared_response("context-two")
        second = await agent.run("second prompt", message_history=first.all_messages())
        RecordingClient.prepare_result = prepared_response("context-three")
        third = await agent.run("third prompt", message_history=second.all_messages())
        assert third.output == "complete"
        for result in (first, second, third):
            stored_contexts.append([
                part.content
                for message in result.all_messages()
                for part in message.parts
                if isinstance(part, SystemPromptPart) and CONTEXT_MARKER in part.content
            ])

    asyncio.run(scenario())

    assert len(model_contexts) == 6
    expected_contexts = ["context-one", "context-one", "context-two", "context-two", "context-three", "context-three"]
    assert all(len(contexts) == 1 for contexts in model_contexts)
    assert all(expected in contexts[0] for contexts, expected in zip(model_contexts, expected_contexts, strict=True))
    # Persisted history stores recalled context as a `SystemPromptPart`, not as user text.
    assert len(stored_contexts) == 3
    assert all(len(contexts) == 1 for contexts in stored_contexts)
    assert [contexts[0].split("\n\n", 1)[1].split('"')[0] for contexts in stored_contexts] == [
        "context-one",
        "context-two",
        "context-three",
    ]


def test_recalled_context_preserves_new_messages_boundary_across_turns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    RecordingClient.reset()
    RecordingClient.prepare_result = prepared_response("boundary-one")
    monkeypatch.setattr(toolset_module, "PowerContextClient", RecordingClient)

    turns: list[int] = []

    async def respond(messages, _info):
        if not turns or any(isinstance(part, ToolReturnPart) for part in messages[-1].parts):
            turns.append(len(turns) + 1)
            return ModelResponse(parts=[TextPart(f"answer {turns[-1]}")])
        return ModelResponse(parts=[ToolCallPart("powercontext_search", {"query": "q"}, "search-1")])

    def prompts_and_calls(messages: list[Any]) -> list[tuple[str, str]]:
        return [
            (type(part).__name__, str(getattr(part, "content", getattr(part, "tool_name", ""))))
            for message in messages
            for part in message.parts
            if isinstance(part, (UserPromptPart, SystemPromptPart, ToolCallPart, ToolReturnPart, TextPart))
        ]

    async def scenario() -> tuple[list[Any], list[Any], list[Any], list[Any]]:
        agent = Agent(FunctionModel(respond), capabilities=[PowerContext(scope_id="project:boundary")])
        first = await agent.run("first prompt")
        RecordingClient.prepare_result = prepared_response("boundary-two")
        second = await agent.run("second prompt", message_history=first.all_messages())
        # Resume from an explicit history that ends in a `ModelRequest`, not a completed response.
        history_before_resume = [*second.all_messages(), ModelRequest(parts=[UserPromptPart("unanswered prompt")])]
        RecordingClient.prepare_result = prepared_response("boundary-three")
        snapshot = list(history_before_resume)
        third = await agent.run(message_history=history_before_resume)
        # The caller's objects are never rewritten in place; only the documented context replacement happens
        # inside the result's own copy.
        assert prompts_and_calls(history_before_resume) == prompts_and_calls(snapshot)
        return second.new_messages(), third.new_messages(), third.all_messages()

    second_new, third_new, third_all = asyncio.run(scenario())

    def text(messages: list[Any]) -> str:
        return "\n".join(value for _, value in prompts_and_calls(messages))

    # A new prompt with prior history: only this run's prompt and its own recalled context are new.
    assert "second prompt" in text(second_new)
    assert "first prompt" not in text(second_new)
    assert "boundary-two" in text(second_new)
    assert "boundary-one" not in text(second_new)
    # A multi-step tool turn stays inside the boundary with exactly one call/return pair, and appending
    # `new_messages()` to the caller's history reproduces the run's history instead of duplicating a turn.
    assert [value for name, value in prompts_and_calls(second_new) if name == "ToolCallPart"] == ["powercontext_search"]
    assert [
        part.tool_call_id
        for message in second_new
        for part in message.parts
        if isinstance(part, (ToolCallPart, ToolReturnPart))
    ] == ["search-1", "search-1"]
    second_run_ids = {message.run_id for message in second_new}
    assert len(second_run_ids) == 1 and None not in second_run_ids
    assert text(second_new).count("second prompt") == 1
    assert text(second_new).count("answer 2") == 1

    # A history-only continuation resumes the trailing `ModelRequest`, which the framework treats as prior
    # context, so no earlier prompt, answer, or tool return is reported as new either.
    assert "second prompt" not in text(third_new)
    assert "first prompt" not in text(third_new)
    assert "unanswered prompt" not in text(third_new)
    assert "answer 1" not in text(third_new)
    assert "answer 2" not in text(third_new)
    assert "boundary-two" not in text(third_new)
    assert [value for name, value in prompts_and_calls(third_new) if name == "ToolCallPart"] == ["powercontext_search"]

    # The prior turns and the reviewed prompt survive exactly once in the result: none duplicated, none dropped.
    assert text(third_all).count("first prompt") == 1
    assert text(third_all).count("second prompt") == 1
    assert text(third_all).count("unanswered prompt") == 1
    # Recalled context is stored once for this run, replacing the prior run's context instead of accumulating it.
    assert text(third_all).count("boundary-three") == 1
    assert "boundary-one" not in text(third_all)


def test_empty_context_is_not_injected(monkeypatch: pytest.MonkeyPatch) -> None:
    RecordingClient.reset()
    RecordingClient.prepare_result = prepared_response(None)
    monkeypatch.setattr(toolset_module, "PowerContextClient", RecordingClient)
    seen_markers: list[str] = []

    async def respond(messages, _info):
        seen_markers.extend(
            part.content
            for message in messages
            for part in message.parts
            if isinstance(part, SystemPromptPart) and CONTEXT_MARKER in part.content
        )
        return ModelResponse(parts=[TextPart("no context needed")])

    async def scenario() -> str:
        agent = Agent(FunctionModel(respond), capabilities=[PowerContext(scope_id="project:empty")])
        return (await agent.run("new prompt")).output

    assert asyncio.run(scenario()) == "no context needed"
    assert seen_markers == []


def test_unreachable_server_fails_open_for_recall(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    RecordingClient.reset()
    RecordingClient.prepare_result = TransportError("/v1/context/prepare")
    monkeypatch.setattr(toolset_module, "PowerContextClient", RecordingClient)

    async def respond(_messages, _info):
        return ModelResponse(parts=[TextPart("model still completes")])

    async def scenario() -> str:
        settings = PowerContextSettings(capture_events=True)
        agent = Agent(
            FunctionModel(respond),
            capabilities=[PowerContext(settings=settings, scope_id="project:offline")],
        )
        return (await agent.run("continue while offline")).output

    with caplog.at_level(logging.DEBUG, logger="powercontext_pydantic_ai.capability"):
        assert asyncio.run(scenario()) == "model still completes"

    failures = [record for record in caplog.records if "context preparation failed open" in record.getMessage()]
    assert len(failures) == 1
    assert failures[0].exc_info is not None


def test_authentication_failure_logs_one_credential_free_configuration_warning(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    RecordingClient.reset()
    RecordingClient.prepare_result = ServerResponseError(status_code=401, request_id="request-1")
    monkeypatch.setattr(toolset_module, "PowerContextClient", RecordingClient)
    token = "never-log-this-token"  # noqa: S105 - synthetic logging sentinel.

    async def noop(ctx: RunContext[object]) -> str:
        del ctx
        return "ok"

    async def respond(messages: list[Any], _info: Any) -> ModelResponse:
        if isinstance(messages[-1].parts[-1], ToolReturnPart):
            return ModelResponse(parts=[TextPart("finished")])
        return ModelResponse(parts=[ToolCallPart("noop", {}, "noop-1")])

    async def scenario() -> str:
        settings = PowerContextSettings(token=SecretStr(token))
        agent: Agent[object, str] = Agent(
            FunctionModel(respond),
            output_type=str,
            deps_type=object,
            tools=[noop],
            capabilities=[PowerContext[object](settings=settings, scope_id="project:auth")],
        )
        return (await agent.run("two recall attempts")).output

    with caplog.at_level(logging.WARNING, logger="powercontext_pydantic_ai.toolset"):
        assert asyncio.run(scenario()) == "finished"

    warnings = [record.getMessage() for record in caplog.records if "HTTP 401" in record.getMessage()]
    assert len(warnings) == 1
    assert "POWERCONTEXT_PYDANTIC_AI_TOKEN" in warnings[0]
    assert token not in caplog.text
