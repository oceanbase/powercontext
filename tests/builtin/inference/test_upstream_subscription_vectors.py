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

"""Upstream vectors and expected assertions; see fixtures/oauth/upstream/README.md."""

from __future__ import annotations

import asyncio
import json
import sys
from functools import partial
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import httpx2
import pytest
from pydantic_ai import Agent
from pydantic_ai.exceptions import UserError

from powercontext.builtin.inference import coding_oauth
from powercontext.builtin.inference.claude_cli import ClaudeCliModel
from powercontext.builtin.inference.gemini_cli import GeminiCliTransport

_VECTORS = json.loads((Path(__file__).parents[2] / "fixtures/oauth/upstream/provider-vectors.json").read_text())


def test_goose_codeassist_wrap_and_unwrap_vectors(tmp_path: Path, monkeypatch) -> None:
    vector = _VECTORS["gemini"]
    path = tmp_path / "oauth_creds.json"
    path.write_text(json.dumps({"access_token": "synthetic", "expiry_date": 9e12}))
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT_ID", raising=False)
    sent = []

    def respond(request: httpx2.Request) -> httpx2.Response:
        sent.append(request)
        if request.url.path == "/v1internal:loadCodeAssist":
            return httpx2.Response(
                200, json={"currentTier": {"id": "test-tier"}, "cloudaicompanionProject": "project-123"}
            )
        assert request.url.path == "/v1internal:generateContent"
        actual = json.loads(request.content)
        for field, expected in vector["expected_request"].items():
            assert actual[field] == expected
        return httpx2.Response(200, json=vector["response"])

    async def run() -> None:
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as wire:
            transport = GeminiCliTransport(path, client=wire)
            async with httpx2.AsyncClient(transport=transport) as client:
                response = await client.post(
                    "https://generativelanguage.googleapis.com/v1/models/gemini-2.5-pro:generateContent",
                    json=vector["request"],
                )
                assert response.json() == vector["expected_response"]

    asyncio.run(run())
    assert len(sent) == 2


@pytest.mark.parametrize("subscription", ["qwen", "kimi"])
def test_official_coding_refresh_vectors_restore_rotated_tokens(subscription, tmp_path: Path, monkeypatch) -> None:
    vector = _VECTORS[subscription]
    path = tmp_path / "tokens.json"
    store = {
        "schema": "powercontext.oauth.v1",
        "subscription": subscription,
        "device_id": "test-device-id",
        "access_token": "old-at",
        "refresh_token": vector["initial_refresh_token"],
        "expires_at": 0,
        "token_type": "Bearer",
    }
    path.write_text(json.dumps(store))
    responses = iter(vector["responses"])
    forms = []
    resource_tokens = []

    def token_response(request: httpx.Request) -> httpx.Response:
        forms.append({key: value[0] for key, value in parse_qs(request.content.decode()).items()})
        return httpx.Response(200, json=next(responses))

    def resource(request: httpx2.Request) -> httpx2.Response:
        resource_tokens.append(request.headers["authorization"])
        return httpx2.Response(200, json={})

    monkeypatch.setattr(
        coding_oauth,
        "AsyncOAuth2Client",
        partial(
            coding_oauth.AsyncOAuth2Client,
            transport=httpx.MockTransport(token_response),
        ),
    )

    async def run() -> None:
        for _ in vector["responses"]:
            # The upstream API tests refresh twice explicitly. Drive the same grants
            # through our file-backed request hook, reopening the auth each time.
            saved = json.loads(path.read_text())
            saved["expires_at"] = 0
            path.write_text(json.dumps(saved))
            auth = coding_oauth.CodingOAuthAuth(subscription, path, clock=lambda: 2_000_000_000)
            async with httpx2.AsyncClient(auth=auth, transport=httpx2.MockTransport(resource)) as client:
                await client.post(auth.base_url + "/chat/completions")
        assert json.loads(path.read_text())["refresh_token"] == vector["expected_refresh_token"]

    asyncio.run(run())
    assert forms == vector["expected_forms"]
    assert resource_tokens == [f"Bearer {response['access_token']}" for response in vector["responses"]]


@pytest.mark.parametrize("subscription", ["qwen", "kimi"])
def test_official_missing_refresh_token_vectors_do_not_replace_store(subscription, tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "tokens.json"
    original = json.dumps({
        "schema": "powercontext.oauth.v1",
        "subscription": subscription,
        "device_id": "test-device-id",
        "access_token": "old-at",
        "refresh_token": "old-rt",
        "expires_at": 0,
        "token_type": "Bearer",
    })
    path.write_text(original)
    monkeypatch.setattr(
        coding_oauth,
        "AsyncOAuth2Client",
        partial(
            coding_oauth.AsyncOAuth2Client,
            transport=httpx.MockTransport(
                lambda _: httpx.Response(200, json=_VECTORS[subscription]["malformed_token"])
            ),
        ),
    )

    async def run() -> None:
        auth = coding_oauth.CodingOAuthAuth(subscription, path)
        async with httpx2.AsyncClient(
            auth=auth, transport=httpx2.MockTransport(lambda _: pytest.fail("inference sent"))
        ) as client:
            with pytest.raises(UserError, match="renewal failed"):
                await client.post(auth.base_url + "/chat/completions")

    asyncio.run(run())
    assert path.read_text() == original


def test_qwen_zero_interval_vector_fails_before_presenting_or_polling(tmp_path: Path, monkeypatch) -> None:
    seen = []

    def response(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=_VECTORS["qwen"]["malformed_device"])

    monkeypatch.setattr(
        coding_oauth,
        "AsyncOAuth2Client",
        partial(
            coding_oauth.AsyncOAuth2Client,
            transport=httpx.MockTransport(response),
        ),
    )

    async def run() -> None:
        with pytest.raises(UserError, match="device authorization failed"):
            await coding_oauth.login_coding_subscription(
                "qwen", lambda *_: pytest.fail("invalid device displayed"), path=tmp_path / "tokens.json"
            )

    asyncio.run(run())
    assert len(seen) == 1
    assert not (tmp_path / "tokens.json").exists()


@pytest.mark.parametrize("failure", [None, 0, 1])
def test_goose_claude_terminal_usage_and_error_vectors(failure, tmp_path: Path, monkeypatch) -> None:
    vector = _VECTORS["claude"]
    result = (
        {**vector["adapter_wrapper"], "usage": vector["usage_input"]} if failure is None else vector["errors"][failure]
    )
    response_file = tmp_path / "terminal.json"
    response_file.write_text(json.dumps(result))
    executable = tmp_path / "claude"
    executable.write_text(f"""#!{sys.executable}
import json, sys
from pathlib import Path
if sys.argv[1:] == ["auth", "status"]:
    print(json.dumps({{"authMethod":"claude.ai"}}))
else:
    sys.stdin.read()
    print(Path({str(response_file)!r}).read_text())
""")
    executable.chmod(0o700)
    for name in (
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_BASE_URL",
        "CLAUDE_CODE_USE_BEDROCK",
        "CLAUDE_CODE_USE_VERTEX",
        "CLAUDE_CODE_USE_FOUNDRY",
        "CLAUDE_CODE_OAUTH_TOKEN",
    ):
        monkeypatch.delenv(name, raising=False)

    async def run() -> None:
        agent = Agent(ClaudeCliModel("sonnet", executable=str(executable)))
        if failure is not None:
            with pytest.raises(UserError, match="did not complete a text response"):
                await agent.run("hello")
        else:
            output = await agent.run("hello")
            assert output.output == vector["adapter_wrapper"]["result"]
            assert output.usage.input_tokens == vector["expected"]["input_tokens"]
            assert output.usage.output_tokens == vector["expected"]["output_tokens"]

    asyncio.run(run())


def test_republic_codex_forced_stream_vector_through_native_model(tmp_path: Path) -> None:
    from openai import AsyncOpenAI
    from pydantic_ai.models.openai_codex import OpenAICodexModel
    from pydantic_ai.providers.openai_codex import OpenAICodexProvider

    from powercontext.builtin.inference.codex import CodexCliAuth

    vector = _VECTORS["codex"]
    path = tmp_path / "auth.json"
    credentials = json.loads(json.dumps(vector["credentials"]))
    credentials["tokens"].update(vector["adapter_credentials"])
    original = json.dumps(credentials)
    path.write_text(original)
    data = "".join(
        f"data: {json.dumps({**vector['adapter_event_defaults'], **event}) if isinstance(event, dict) else event}\n\n"
        for event in vector["events"]
    )

    def response(request: httpx2.Request) -> httpx2.Response:
        assert str(request.url) == "https://chatgpt.com/backend-api/codex/responses"
        assert request.headers["authorization"] == "Bearer dummy-access"
        body = json.loads(request.content)
        assert body["stream"] is True and body["store"] is False
        return httpx2.Response(200, content=data, headers={"content-type": "text/event-stream"})

    async def run() -> None:
        async with (
            httpx2.AsyncClient(auth=CodexCliAuth(path), transport=httpx2.MockTransport(response)) as http,
            AsyncOpenAI(
                api_key="externally-managed", base_url="https://chatgpt.com/backend-api/codex", http_client=http
            ) as client,
        ):
            agent = Agent(OpenAICodexModel("test", provider=OpenAICodexProvider(openai_client=client)))
            output = await agent.run("hello")
            assert output.output == vector["expected_text"]
            assert output.usage.input_tokens == vector["expected_usage"]["input_tokens"]
            assert output.usage.output_tokens == vector["expected_usage"]["output_tokens"]

    asyncio.run(run())
    assert path.read_text() == original


def test_republic_copilot_direct_token_vector_through_native_model() -> None:
    from pydantic_ai.models.github_copilot import GitHubCopilotModel
    from pydantic_ai.providers.github_copilot import GitHubCopilotProvider

    vector = _VECTORS["copilot"]

    def response(request: httpx2.Request) -> httpx2.Response:
        assert str(request.url) == vector["expected_endpoint"]
        assert request.headers["authorization"] == f"Bearer {vector['token']}"
        assert json.loads(request.content)["messages"] == vector["expected_messages"]
        body = {**vector["adapter_response_defaults"], **vector["response"]}
        body["choices"] = [
            {**choice, "message": {**vector["adapter_message_defaults"], **choice["message"]}}
            for choice in body["choices"]
        ]
        return httpx2.Response(200, json=body)

    async def run() -> None:
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(response)) as client:
            model = GitHubCopilotModel(
                "test", provider=GitHubCopilotProvider(api_key=vector["token"], http_client=client)
            )
            output = await Agent(model).run("hello")
            assert output.output == vector["expected_text"]

    asyncio.run(run())
