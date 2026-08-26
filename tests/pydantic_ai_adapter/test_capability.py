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
from pydantic_ai.messages import ModelResponse, SystemPromptPart, TextPart, ToolCallPart, ToolReturnPart
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

    async def respond(messages, _info):
        model_contexts.append([
            part.content
            for message in messages
            for part in message.parts
            if isinstance(part, SystemPromptPart) and CONTEXT_MARKER in part.content
        ])
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

    asyncio.run(scenario())

    assert len(RecordingClient.instances) == 3
    assert [len(client.prepare_requests) for client in RecordingClient.instances] == [1, 1, 1]
    assert len(model_contexts) == 6
    expected_contexts = ["context-one", "context-one", "context-two", "context-two", "context-three", "context-three"]
    assert all(len(contexts) == 1 for contexts in model_contexts)
    assert all(expected in contexts[0] for contexts, expected in zip(model_contexts, expected_contexts, strict=True))


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
    assert len(RecordingClient.instances[0].prepare_requests) == 1


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
