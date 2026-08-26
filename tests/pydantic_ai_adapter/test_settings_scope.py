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
import hashlib
import os
from pathlib import Path
from typing import Any, cast

import powercontext_pydantic_ai.scope as scope_module
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


@pytest.mark.parametrize(
    ("remote", "expected"),
    [
        ("https://github.com/OceanBase/powercontext.git", "github.com/OceanBase/powercontext"),
        ("ssh://git@github.com/OceanBase/powercontext.git", "github.com/OceanBase/powercontext"),
        ("git@github.com:OceanBase/powercontext.git", "github.com/OceanBase/powercontext"),
    ],
)
def test_scope_reuses_codex_remote_normalization(remote: str, expected: str) -> None:
    assert scope_module.normalize_git_remote(remote) == expected


def test_scope_uses_normalized_git_origin_then_local_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def git_remote(_cwd: str, *arguments: str) -> str | None:
        if arguments == ("rev-parse", "--show-toplevel"):
            return str(tmp_path)
        if arguments == ("config", "--get", "remote.origin.url"):
            return "https://token@GitHub.com/OceanBase/powercontext.git"
        return None

    monkeypatch.setattr(scope_module, "_git_value", git_remote)
    assert scope_module.derive_scope_id(tmp_path) == "git:github.com/OceanBase/powercontext"

    monkeypatch.setattr(scope_module, "_git_value", lambda *_args: None)
    digest = hashlib.sha256(os.fsencode(tmp_path.resolve())).hexdigest()
    assert scope_module.derive_scope_id(tmp_path) == f"local:{digest}"


def test_explicit_scope_skips_git_and_is_deterministically_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        scope_module,
        "_git_value",
        lambda *_args: pytest.fail("explicit scope must not invoke Git"),
    )
    configured = "scope:" + "x" * 300
    settings = PowerContextSettings(scope_id=configured)
    ctx = cast(RunContext[Any], object())

    resolved = scope_module.resolve_scope_id(ctx, None, settings.scope_id)

    assert resolved == f"sha256:{hashlib.sha256(configured.encode()).hexdigest()}"
    assert len(resolved) <= 256


def test_constructor_scope_callback_wins_and_runs_once_per_agent_run(monkeypatch: pytest.MonkeyPatch) -> None:
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

    assert len(calls) == 1
    assert RecordingClient.instances[0].prepare_requests
    assert {request.scope_id for request in RecordingClient.instances[0].prepare_requests} == {"constructor:scope"}
