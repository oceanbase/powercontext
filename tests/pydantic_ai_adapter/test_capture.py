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
import json
from pathlib import Path
from typing import Any

import powercontext_pydantic_ai.toolset as toolset_module
import pytest
from powercontext_pydantic_ai import PowerContext, PowerContextSettings
from powercontext_pydantic_ai.capability import CAPTURE_SCHEMA
from pydantic_ai import Agent, RunContext
from pydantic_ai.messages import ModelResponse, TextPart, ThinkingPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel

from powercontext.client import TransportError
from powercontext.client.capture import render_capture_event
from tests.pydantic_ai_adapter.fakes import RecordingClient, prepared_response


def test_capture_disabled_makes_no_source_or_flush_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    RecordingClient.reset()
    RecordingClient.prepare_result = prepared_response(None)
    monkeypatch.setattr(toolset_module, "PowerContextClient", RecordingClient)

    async def respond(_messages, _info):
        return ModelResponse(parts=[TextPart("done")])

    async def scenario() -> None:
        agent = Agent(FunctionModel(respond), capabilities=[PowerContext(scope_id="project:no-capture")])
        await agent.run("private prompt")

    asyncio.run(scenario())

    client = RecordingClient.instances[0]
    assert client.capture_requests == []
    assert client.flush_requests == []


def test_capture_is_bounded_redacted_checkpointed_and_serialized_under_parallel_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    RecordingClient.reset()
    RecordingClient.prepare_result = prepared_response(None)
    monkeypatch.setattr(toolset_module, "PowerContextClient", RecordingClient)
    secret = "provider-secret-sentinel"  # noqa: S105 - synthetic redaction sentinel.
    hidden_thinking = "hidden-thinking-must-not-be-captured"
    monkeypatch.setenv("PROVIDER_API_TOKEN", secret)

    async def leak_one(ctx: RunContext[object], api_key: str) -> dict[str, str]:
        del ctx
        return {"authorization": api_key, "body": "x" * 2000}

    async def leak_two(ctx: RunContext[object], password: str) -> dict[str, str]:
        del ctx
        return {"cookie": password, "body": "y" * 2000}

    async def respond(messages, _info):
        if isinstance(messages[-1].parts[-1], ToolReturnPart):
            return ModelResponse(parts=[ThinkingPart(hidden_thinking), TextPart("visible complete")])
        return ModelResponse(
            parts=[
                ThinkingPart(hidden_thinking),
                ToolCallPart("leak_one", {"api_key": secret}, "leak-1"),
                ToolCallPart("leak_two", {"password": secret}, "leak-2"),
            ]
        )

    async def scenario() -> str:
        settings = PowerContextSettings(
            capture_events=True,
            capture_checkpoint_every=4,
            capture_max_bytes=512,
        )
        agent: Agent[object, str] = Agent(
            FunctionModel(respond),
            output_type=str,
            deps_type=object,
            tools=[leak_one, leak_two],
            capabilities=[PowerContext[object](settings=settings, scope_id="project:capture")],
        )
        return (await agent.run("Run both tools and report visible output.")).output

    assert asyncio.run(scenario()) == "visible complete"

    client = RecordingClient.instances[0]
    assert len(client.capture_requests) == 5
    assert len(client.flush_requests) == 2
    assert [request.metadata["sequence"] for request in client.capture_requests] == [1, 2, 3, 4, 5]
    assert len({request.source_id for request in client.capture_requests}) == 5
    assert all(request.source_id.startswith("pydantic-ai-event:") for request in client.capture_requests)
    assert {request.metadata["event"] for request in client.capture_requests} >= {
        "user_prompt",
        "model_response",
        "tool_result",
    }
    assert [request.metadata["event"] for request in client.capture_requests].count("tool_result") == 2

    for request in client.capture_requests:
        assert len(request.content.encode()) <= 512
        assert secret not in request.content
        assert hidden_thinking not in request.content
        event = json.loads(request.content)
        assert event["schema"] == CAPTURE_SCHEMA
        assert request.metadata["schema"] == CAPTURE_SCHEMA
        assert request.metadata["origin"] == "pydantic-ai"
        assert request.metadata["kind"] == "agent-trajectory"
        assert request.metadata["run_id"]
        assert request.metadata["conversation_id"]
    serialized = "\n".join(request.content for request in client.capture_requests)
    assert "[REDACTED]" in serialized


def test_shared_capture_redacts_codex_auth_values_outside_sensitive_keys(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    secret = "codex-auth-secret-sentinel"  # noqa: S105 - synthetic redaction sentinel.
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    (codex_home / "auth.json").write_text(json.dumps({"tokens": {"access_token": secret}}))
    monkeypatch.setenv("CODEX_HOME", str(codex_home))

    content = render_capture_event(
        "tool_result",
        1,
        {"result": f"provider echoed {secret}"},
        8192,
        schema=CAPTURE_SCHEMA,
    )

    assert secret not in content
    assert "[REDACTED]" in content


def test_capture_failure_does_not_change_tool_or_model_results(monkeypatch: pytest.MonkeyPatch) -> None:
    RecordingClient.reset()
    RecordingClient.prepare_result = prepared_response(None)
    RecordingClient.capture_error = TransportError("/v1/sources/content")
    monkeypatch.setattr(toolset_module, "PowerContextClient", RecordingClient)

    async def useful_tool(ctx: RunContext[object], value: int) -> dict[str, int]:
        del ctx
        return {"value": value * 2}

    async def respond(messages, _info):
        returns = [
            part
            for message in messages
            for part in message.parts
            if isinstance(part, ToolReturnPart) and part.tool_name == "useful_tool"
        ]
        if returns:
            assert returns[-1].content == {"value": 42}
            return ModelResponse(parts=[TextPart("tool result preserved")])
        return ModelResponse(parts=[ToolCallPart("useful_tool", {"value": 21}, "useful-1")])

    async def scenario() -> str:
        settings = PowerContextSettings(capture_events=True, capture_checkpoint_every=1)
        agent: Agent[object, str] = Agent(
            FunctionModel(respond),
            output_type=str,
            deps_type=object,
            tools=[useful_tool],
            capabilities=[PowerContext[object](settings=settings, scope_id="project:capture-failure")],
        )
        return (await agent.run("run useful tool")).output

    assert asyncio.run(scenario()) == "tool result preserved"
    client = RecordingClient.instances[0]
    assert len(client.capture_requests) >= 1
    assert client.flush_requests == []


def test_flush_failure_does_not_change_model_result(monkeypatch: pytest.MonkeyPatch) -> None:
    RecordingClient.reset()
    RecordingClient.prepare_result = prepared_response(None)
    RecordingClient.flush_error = TransportError("/v1/memory/flush")
    monkeypatch.setattr(toolset_module, "PowerContextClient", RecordingClient)

    async def respond(_messages: list[Any], _info: Any) -> ModelResponse:
        return ModelResponse(parts=[TextPart("flush failed open")])

    async def scenario() -> str:
        settings = PowerContextSettings(capture_events=True, capture_checkpoint_every=1)
        agent = Agent(
            FunctionModel(respond),
            capabilities=[PowerContext(settings=settings, scope_id="project:flush-failure")],
        )
        return (await agent.run("finish despite flush failure")).output

    assert asyncio.run(scenario()) == "flush failed open"
    assert RecordingClient.instances[0].flush_requests
