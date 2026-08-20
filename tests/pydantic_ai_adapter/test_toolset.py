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
from typing import Any

import powercontext_pydantic_ai.toolset as toolset_module
import pytest
from powercontext_pydantic_ai import PowerContextSettings, PowerContextToolset
from pydantic import SecretStr
from pydantic_ai import Agent
from pydantic_ai.messages import ModelResponse, RetryPromptPart, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel

from powercontext.client import TransportError
from tests.pydantic_ai_adapter.fakes import RecordingClient, prepared_response, remember_response, search_response


def test_toolset_exposes_exact_schemas_instructions_request_mapping_and_full_responses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    RecordingClient.reset()
    monkeypatch.setattr(toolset_module, "PowerContextClient", RecordingClient)
    model_calls: list[list[Any]] = []
    definitions: dict[str, Any] = {}

    async def respond(messages, info):
        model_calls.append(messages)
        definitions.update({tool.name: tool for tool in info.function_tools})
        if any(isinstance(part, ToolReturnPart) for part in messages[-1].parts):
            return ModelResponse(parts=[TextPart("complete")])
        return ModelResponse(
            parts=[
                ToolCallPart(
                    "powercontext_search",
                    {"query": "public response", "limit": 4, "mode": "fts"},
                    "search-1",
                ),
                ToolCallPart(
                    "powercontext_remember",
                    {"text": "Use the public client.", "kind": "decision", "reason": "shared contract"},
                    "remember-1",
                ),
                ToolCallPart("powercontext_context", {"query": "what is current?"}, "context-1"),
            ]
        )

    async def scenario() -> Any:
        agent = Agent(
            FunctionModel(respond),
            toolsets=[PowerContextToolset(scope_id="project:tools")],
        )
        return await agent.run("Use all memory tools")

    result = asyncio.run(scenario())

    assert result.output == "complete"
    assert set(definitions) == {
        "powercontext_search",
        "powercontext_remember",
        "powercontext_context",
    }
    search_schema = definitions["powercontext_search"].parameters_json_schema
    assert search_schema["properties"]["limit"] == {
        "default": 10,
        "maximum": 50,
        "minimum": 1,
        "type": "integer",
    }
    assert search_schema["properties"]["mode"]["enum"] == ["auto", "fts", "vector", "hybrid"]
    assert "untrusted historical evidence" in (model_calls[0][-1].instructions or "")

    client = RecordingClient.instances[0]
    assert client.search_requests[0].model_dump(mode="json") == {
        "scope_id": "project:tools",
        "query": "public response",
        "limit": 4,
        "mode": "fts",
    }
    assert client.remember_requests[0].model_dump(mode="json") == {
        "scope_id": "project:tools",
        "kind": "decision",
        "text": "Use the public client.",
        "reason": "shared contract",
        "expected_revision": None,
    }
    assert client.prepare_requests[0].model_dump(mode="json") == {
        "scope_id": "project:tools",
        "query": "what is current?",
        "max_bytes": 8000,
    }

    returns = {
        part.tool_name: part.content
        for message in model_calls[1]
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    }
    assert returns["powercontext_search"] == search_response().model_dump(mode="json", by_alias=True)
    assert returns["powercontext_remember"] == remember_response().model_dump(mode="json", by_alias=True)
    assert returns["powercontext_context"] == prepared_response().model_dump(mode="json", by_alias=True)


def test_toolset_converts_client_failure_to_model_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    RecordingClient.reset()
    RecordingClient.search_result = TransportError("/v1/memory/search")
    monkeypatch.setattr(toolset_module, "PowerContextClient", RecordingClient)
    retry_parts: list[RetryPromptPart] = []

    async def respond(messages, _info):
        retry_parts.extend(part for message in messages for part in message.parts if isinstance(part, RetryPromptPart))
        if retry_parts:
            return ModelResponse(parts=[TextPart("recovered from retry")])
        return ModelResponse(parts=[ToolCallPart("powercontext_search", {"query": "missing"}, "search-failure")])

    async def scenario() -> str:
        agent = Agent(FunctionModel(respond), toolsets=[PowerContextToolset(scope_id="project:retry")])
        return (await agent.run("search memory")).output

    assert asyncio.run(scenario()) == "recovered from retry"
    assert len(retry_parts) == 1
    assert "PowerContext search failed" in str(retry_parts[0].content)
    assert RecordingClient.instances[0].search_requests


def test_toolset_uses_one_raw_token_client_per_run_and_closes_it(monkeypatch: pytest.MonkeyPatch) -> None:
    RecordingClient.reset()
    monkeypatch.setattr(toolset_module, "PowerContextClient", RecordingClient)
    token = "bare-token-sentinel"  # noqa: S105 - synthetic client-boundary sentinel.

    async def respond(_messages, _info):
        return ModelResponse(parts=[TextPart("done")])

    async def scenario() -> None:
        toolset = PowerContextToolset(
            settings=PowerContextSettings(token=SecretStr(token)),
            scope_id="project:lifecycle",
        )
        assert toolset.id == "powercontext"
        agent = Agent(FunctionModel(respond), toolsets=[toolset])
        await agent.run("first")
        await agent.run("second")

    asyncio.run(scenario())

    assert len(RecordingClient.instances) == 2
    assert [client.token for client in RecordingClient.instances] == [token, token]
    assert all(client.closed for client in RecordingClient.instances)
