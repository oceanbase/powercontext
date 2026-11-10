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
import os
import sys
from contextlib import AsyncExitStack
from pathlib import Path

import httpx2
import pytest
from pydantic import BaseModel
from pydantic_ai import Agent
from pydantic_ai.exceptions import UserError

from powercontext.builtin.inference import gemini_cli
from powercontext.builtin.inference.claude_cli import ClaudeCliModel
from powercontext.builtin.inference.errors import InvalidInferenceOutputError
from powercontext.builtin.inference.pydantic_ai import (
    InferenceLimits,
    PydanticAIStructuredGenerator,
)
from powercontext.builtin.processing_diagnostics import ProcessingFailureReason, classify_processing_error
from powercontext.builtin.runtime.composition import _open_pydantic_ai_model


class _Input(BaseModel):
    text: str


class _Output(BaseModel):
    value: str


async def _generate(model) -> _Output:
    generator = PydanticAIStructuredGenerator(
        model=model,
        instructions="Extract the value as JSON",
        input_type=_Input,
        output_type=_Output,
        limits=InferenceLimits(timeout_seconds=10, max_requests=1, allow_continuations=False),
    )
    result = await generator.generate(_Input(text="synthetic-input"))
    return result.output


def test_gemini_cli_code_assist_is_consumed_by_structured_generator(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "oauth_creds.json"
    original = json.dumps({"access_token": "synthetic-google", "refresh_token": "cli-owned", "expiry_date": 9e12})
    path.write_text(original)
    monkeypatch.setenv("GEMINI_CLI_AUTH_PATH", str(path))
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT_ID", raising=False)
    seen = []

    def response(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        assert request.url.host == "cloudcode-pa.googleapis.com"
        assert request.headers["authorization"] == "Bearer synthetic-google"
        assert "x-goog-api-key" not in request.headers
        if request.url.path == "/v1internal:loadCodeAssist":
            return httpx2.Response(
                200, json={"currentTier": {"id": "premium-tier"}, "cloudaicompanionProject": "synthetic-project"}
            )
        assert request.url.path == "/v1internal:generateContent"
        body = json.loads(request.content)
        assert body["project"] == "synthetic-project"
        assert body["model"] == "gemini-test"
        assert "synthetic-input" in json.dumps(body["request"]["contents"])
        return httpx2.Response(
            200,
            json={
                "response": {
                    "candidates": [
                        {
                            "content": {"parts": [{"text": '{"value":"synthetic-output"}'}], "role": "model"},
                            "finishReason": "STOP",
                        }
                    ],
                    "usageMetadata": {"promptTokenCount": 2, "candidatesTokenCount": 3, "totalTokenCount": 5},
                }
            },
        )

    class Client(httpx2.AsyncClient):
        def __init__(self, **kwargs):
            kwargs.setdefault("transport", httpx2.MockTransport(response))
            super().__init__(**kwargs)

    monkeypatch.setattr(gemini_cli.httpx2, "AsyncClient", Client)

    async def run() -> None:
        async with AsyncExitStack() as resources:
            _, model = await _open_pydantic_ai_model(
                "gemini-cli:gemini-test",
                base_url=None,
                headers={},
                resources=resources,
                instrumentation=None,
            )
            assert await _generate(model) == _Output(value="synthetic-output")

    asyncio.run(run())
    assert len(seen) == 2
    assert path.read_text() == original


@pytest.fixture
def claude_executable(tmp_path: Path, monkeypatch):
    executable = tmp_path / "claude"
    record = tmp_path / "calls.jsonl"
    executable.write_text(f"""#!{sys.executable}
import json, os, sys
from pathlib import Path
with Path({str(record)!r}).open("a") as handle:
    handle.write(json.dumps({{"argv":sys.argv[1:], "cwd":os.getcwd(), "stdin":sys.stdin.read()}})+"\\n")
if sys.argv[1:] == ["auth", "status"]:
    print(json.dumps({{"authMethod":os.getenv("SYNTHETIC_CLAUDE_AUTH", "claude.ai")}}))
else:
    print(json.dumps({{"is_error":False,"result":'{{"value":"synthetic-output"}}',
        "stop_reason":os.getenv("SYNTHETIC_CLAUDE_STOP_REASON", "end_turn"),
        "usage":{{"input_tokens":2,"output_tokens":3}}}}))
""")
    executable.chmod(0o700)
    for key in (
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_BASE_URL",
        "CLAUDE_CODE_USE_BEDROCK",
        "CLAUDE_CODE_USE_VERTEX",
        "CLAUDE_CODE_USE_FOUNDRY",
        "CLAUDE_CODE_OAUTH_TOKEN",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("CLAUDE_CLI_EXECUTABLE", str(executable))
    return executable, record


@pytest.mark.parametrize("stop_reason", ["end_turn", "max_tokens", "model_context_window_exceeded"])
def test_claude_cli_real_process_is_consumed_by_structured_generator(
    claude_executable, monkeypatch, stop_reason
) -> None:
    _, record = claude_executable
    monkeypatch.setenv("SYNTHETIC_CLAUDE_STOP_REASON", stop_reason)

    async def run() -> None:
        async with AsyncExitStack() as resources:
            _, model = await _open_pydantic_ai_model(
                "claude-cli:sonnet",
                base_url=None,
                headers={},
                resources=resources,
                instrumentation=None,
            )
            if stop_reason == "end_turn":
                assert await _generate(model) == _Output(value="synthetic-output")
            else:
                with pytest.raises(InvalidInferenceOutputError) as error:
                    await _generate(model)
                diagnostics = classify_processing_error(error.value)
                assert diagnostics is not None
                assert diagnostics.reason is ProcessingFailureReason.OUTPUT_TRUNCATED
                assert diagnostics.finish_reason == "length"

    asyncio.run(run())
    calls = [json.loads(line) for line in record.read_text().splitlines()]
    assert len(calls) == 2
    assert calls[0]["argv"] == ["auth", "status"]
    assert "synthetic-input" in calls[1]["stdin"]
    args = calls[1]["argv"]
    assert args[args.index("--tools") + 1] == ""
    assert "--no-session-persistence" in args
    assert not Path(calls[1]["cwd"]).exists()


def test_claude_cli_rejects_console_auth_before_inference(claude_executable, monkeypatch) -> None:
    _, record = claude_executable
    monkeypatch.setenv("SYNTHETIC_CLAUDE_AUTH", "api_key")

    async def run() -> None:
        with pytest.raises(UserError, match="Claude account login"):
            await Agent(ClaudeCliModel("sonnet")).run("synthetic-input")

    asyncio.run(run())
    assert len(record.read_text().splitlines()) == 1


def test_claude_cli_rejects_api_key_override_before_start(claude_executable, monkeypatch) -> None:
    _, record = claude_executable
    monkeypatch.setenv("ANTHROPIC_API_KEY", "synthetic-secret")

    async def run() -> None:
        with pytest.raises(UserError, match="without API or cloud credential overrides"):
            await Agent(ClaudeCliModel("sonnet")).run("synthetic-input")

    asyncio.run(run())
    assert not record.exists()


@pytest.mark.parametrize("failure", ["expired", "not_onboarded", "upstream_500"])
def test_gemini_cli_failure_never_falls_back_to_api_key_or_replays(failure, tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "oauth_creds.json"
    original = json.dumps({
        "access_token": "synthetic-google",
        "refresh_token": "cli-owned",
        "expiry_date": 0 if failure == "expired" else 9e12,
    })
    path.write_text(original)
    monkeypatch.setenv("GEMINI_CLI_AUTH_PATH", str(path))
    monkeypatch.setenv("GOOGLE_API_KEY", "synthetic-key-not-used")
    requests = []

    def response(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        if failure == "not_onboarded":
            return httpx2.Response(200, json={"allowedTiers": [{"id": "free-tier"}]})
        return httpx2.Response(500, json={"error": {"code": 500, "message": "synthetic-upstream-error"}})

    class Client(httpx2.AsyncClient):
        def __init__(self, **kwargs):
            kwargs.setdefault("transport", httpx2.MockTransport(response))
            super().__init__(**kwargs)

    monkeypatch.setattr(gemini_cli.httpx2, "AsyncClient", Client)

    async def run() -> None:
        from pydantic_ai.exceptions import ModelHTTPError

        async with AsyncExitStack() as resources:
            _, model = await _open_pydantic_ai_model(
                "gemini-cli:gemini-test",
                base_url=None,
                headers={},
                resources=resources,
                instrumentation=None,
            )
            with pytest.raises(ModelHTTPError if failure == "upstream_500" else UserError):
                await Agent(model).run("synthetic-input")

    asyncio.run(run())
    assert len(requests) == (0 if failure == "expired" else 1)
    assert path.read_text() == original


@pytest.mark.skipif(os.name != "posix", reason="POSIX subprocess lifecycle boundary")
def test_claude_cli_cancellation_reaps_real_process(claude_executable, tmp_path: Path) -> None:
    executable, record = claude_executable
    executable.write_text(f"""#!{sys.executable}
import json, os, sys, time
from pathlib import Path
if sys.argv[1:] == ["auth", "status"]:
    print(json.dumps({{"authMethod":"claude.ai"}}))
else:
    Path({str(record)!r}).write_text(str(os.getpid()))
    time.sleep(300)
""")

    async def run() -> None:
        task = asyncio.create_task(Agent(ClaudeCliModel("sonnet")).run("synthetic-input"))
        try:
            async with asyncio.timeout(10):
                while not record.exists():
                    if task.done():
                        await task
                    await asyncio.sleep(0.01)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            with pytest.raises(ProcessLookupError):
                os.kill(int(record.read_text()), 0)
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    asyncio.run(run())
