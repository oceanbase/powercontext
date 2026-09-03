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

import powercontext_pydantic_ai.toolset as toolset_module
import pytest
from powercontext_pydantic_ai import PowerContext, PowerContextSettings
from pydantic import SecretStr, ValidationError
from pydantic_ai import Agent, RunContext
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel

from tests.pydantic_ai_adapter.fakes import RecordingClient, prepared_response


def test_settings_load_all_environment_values_and_keep_token_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    token = "raw-token-sentinel"  # noqa: S105 - synthetic redaction sentinel.
    monkeypatch.setenv("POWERCONTEXT_PYDANTIC_AI_BASE_URL", "https://memory.example/api/")
    monkeypatch.setenv("POWERCONTEXT_PYDANTIC_AI_TOKEN", token)
    monkeypatch.setenv("POWERCONTEXT_PYDANTIC_AI_SCOPE_ID", "project:test")
    monkeypatch.setenv("POWERCONTEXT_PYDANTIC_AI_TIMEOUT", "4.5")
    monkeypatch.setenv("POWERCONTEXT_PYDANTIC_AI_MAX_BYTES", "4096")
    monkeypatch.setenv("POWERCONTEXT_PYDANTIC_AI_CAPTURE_EVENTS", "true")
    monkeypatch.setenv("POWERCONTEXT_PYDANTIC_AI_CAPTURE_CHECKPOINT_EVERY", "3")
    monkeypatch.setenv("POWERCONTEXT_PYDANTIC_AI_CAPTURE_MAX_BYTES", "1024")

    settings = PowerContextSettings()

    assert settings.base_url == "https://memory.example/api"
    assert settings.token is not None and settings.token.get_secret_value() == token
    assert settings.scope_id == "project:test"
    assert settings.timeout == 4.5
    assert settings.max_bytes == 4096
    assert settings.capture_events is True
    assert settings.capture_checkpoint_every == 3
    assert settings.capture_max_bytes == 1024
    assert token not in repr(settings)
    assert token not in settings.model_dump_json()


@pytest.mark.parametrize(
    "value",
    [
        "ftp://memory.example",
        "https://user:password@memory.example",
        "https://memory.example?token=secret",
        "https://memory.example#fragment",
    ],
)
def test_settings_reject_unsafe_server_urls(value: str) -> None:
    with pytest.raises(ValidationError):
        PowerContextSettings(base_url=value)


def test_settings_require_a_bare_token_without_leaking_invalid_input() -> None:
    token = "Bearer secret-token-sentinel"  # noqa: S105 - synthetic validation sentinel.

    with pytest.raises(ValidationError) as exc_info:
        PowerContextSettings(token=SecretStr(token))

    assert token not in str(exc_info.value)


def test_settings_reject_scope_id_over_contract_limit_instead_of_rewriting_it() -> None:
    with pytest.raises(ValidationError):
        PowerContextSettings(scope_id="scope:" + "x" * 300)


def test_server_default_scope_is_used_for_the_run(monkeypatch: pytest.MonkeyPatch) -> None:
    RecordingClient.reset()
    RecordingClient.default_scope_id = "server:chosen-default"
    monkeypatch.delenv("POWERCONTEXT_PYDANTIC_AI_SCOPE_ID", raising=False)
    monkeypatch.setattr(toolset_module, "PowerContextClient", RecordingClient)

    async def respond(_messages, _info):
        return ModelResponse(parts=[TextPart("complete")])

    async def scenario() -> None:
        agent = Agent(FunctionModel(respond), capabilities=[PowerContext()])
        result = await agent.run("use the Server default Scope")
        assert result.output == "complete"

    asyncio.run(scenario())

    client = RecordingClient.instances[0]
    assert client.resolve_scope_requests
    assert {request.explicit_scope_id for request in client.resolve_scope_requests} == {None}
    assert {request.scope_id for request in client.prepare_requests} == {"server:chosen-default"}


def test_constructor_scope_callback_selects_the_scope_for_the_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    RecordingClient.reset()
    RecordingClient.prepare_result = prepared_response(None)
    monkeypatch.setattr(toolset_module, "PowerContextClient", RecordingClient)
    calls: list[str | None] = []

    def scope_id(ctx: RunContext[object]) -> str:
        calls.append(ctx.run_id)
        return "constructor:scope"

    async def noop(ctx: RunContext[object]) -> str:
        del ctx
        return "done"

    async def respond(messages, _info):
        if isinstance(messages[-1].parts[-1], ToolReturnPart):
            return ModelResponse(parts=[TextPart("complete")])
        return ModelResponse(parts=[ToolCallPart("noop", {}, "noop-1")])

    async def scenario() -> None:
        settings = PowerContextSettings(scope_id="environment:scope")
        agent: Agent[object, str] = Agent(
            FunctionModel(respond),
            output_type=str,
            deps_type=object,
            tools=[noop],
            capabilities=[PowerContext[object](settings=settings, scope_id=scope_id)],
        )
        result = await agent.run("exercise two model rounds")
        assert result.output == "complete"

    asyncio.run(scenario())

    assert calls
    client = RecordingClient.instances[0]
    assert {request.explicit_scope_id for request in client.resolve_scope_requests} == {"constructor:scope"}
    assert {request.scope_id for request in client.prepare_requests} == {"constructor:scope"}
