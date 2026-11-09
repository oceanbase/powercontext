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
import traceback
from contextlib import AsyncExitStack
from functools import partial
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import httpx2
import pytest
from pydantic import BaseModel
from pydantic_ai.exceptions import UserError

from powercontext.builtin.inference import coding_oauth
from powercontext.builtin.inference.pydantic_ai import InferenceLimits, PydanticAIStructuredGenerator
from powercontext.builtin.runtime.composition import _open_pydantic_ai_model


class _Value(BaseModel):
    value: str


@pytest.mark.parametrize("subscription", ["qwen", "kimi"])
def test_device_login_subscription_model_and_persisted_rotation(
    subscription, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "tokens.json"
    monkeypatch.setenv(f"POWERCONTEXT_{subscription.upper()}_CREDENTIALS_PATH", str(path))
    oauth_requests = []
    resource_requests = []
    presented = []
    polled = 0

    def oauth_response(request: httpx.Request) -> httpx.Response:
        nonlocal polled
        oauth_requests.append(request)
        assert "authorization" not in request.headers
        form = parse_qs(request.content.decode())
        expected_host = "auth.qwen.ai" if subscription == "qwen" else "auth.kimi.com"
        assert request.url.host == expected_host
        expected_id = "qwen-code-cli" if subscription == "qwen" else "17e5f671-d194-4dfb-9706-5516cb48c098"
        assert form["client_id"] == [expected_id]
        if subscription == "kimi":
            assert request.headers["x-msh-platform"] == "kimi_cli"
            assert request.headers["x-msh-device-id"]
        if "device" in request.url.path:
            if subscription == "qwen":
                assert form["scope"] == ["model:invoke profile:read"]
            return httpx.Response(
                200,
                json={
                    "device_code": "synthetic-device",
                    "user_code": "ABCD",
                    "expires_in": 600,
                    "interval": 1,
                    "verification_uri_complete": f"https://{'chat.qwen.ai' if subscription == 'qwen' else 'auth.kimi.com'}/authorize",
                },
            )
        assert request.url.path == "/api/oauth/token"
        if form["grant_type"] == ["urn:ietf:params:oauth:grant-type:device_code"]:
            assert form["device_code"] == ["synthetic-device"]
            polled += 1
            if polled == 1:
                return httpx.Response(400, json={"error": "authorization_pending"})
            return httpx.Response(
                200,
                json={
                    "access_token": "synthetic-initial",
                    "refresh_token": "synthetic-refresh",
                    "expires_in": 3600,
                    "token_type": "Bearer",
                },
            )
        assert form["grant_type"] == ["refresh_token"]
        assert form["refresh_token"] == ["synthetic-refresh"]
        return httpx.Response(
            200,
            json={
                "access_token": "synthetic-renewed",
                "refresh_token": "synthetic-rotated",
                "expires_in": 3600,
                "token_type": "Bearer",
            },
        )

    def resource_response(request: httpx2.Request) -> httpx2.Response:
        resource_requests.append(request)
        expected = (
            "https://api.qwen.ai/compatible-mode/v1/chat/completions"
            if subscription == "qwen"
            else "https://api.kimi.com/coding/v1/chat/completions"
        )
        assert str(request.url) == expected
        return httpx2.Response(
            200,
            json={
                "id": "synthetic-response",
                "object": "chat.completion",
                "model": "synthetic-model",
                "created": 0,
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": '{"value":"extracted"}'},
                    }
                ],
                "usage": {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5},
            },
        )

    class Client(httpx2.AsyncClient):
        def __init__(self, **kwargs):
            super().__init__(**kwargs, transport=httpx2.MockTransport(resource_response))

    monkeypatch.setattr(httpx2, "AsyncClient", Client)
    monkeypatch.setattr(
        coding_oauth,
        "AsyncOAuth2Client",
        partial(coding_oauth.AsyncOAuth2Client, transport=httpx.MockTransport(oauth_response)),
    )

    async def run() -> None:
        await coding_oauth.login_coding_subscription(
            subscription, lambda uri, code: presented.append((uri, code)), path=path
        )
        async with AsyncExitStack() as resources:
            _, model = await _open_pydantic_ai_model(
                f"{subscription}:synthetic-model",
                base_url=None,
                headers={},
                resources=resources,
                instrumentation=None,
            )
            generator = PydanticAIStructuredGenerator(
                model=model,
                instructions="Extract JSON",
                input_type=_Value,
                output_type=_Value,
                limits=InferenceLimits(timeout_seconds=10, max_requests=1),
            )
            assert (await generator.generate(_Value(value="synthetic-input"))).output.value == "extracted"
            saved = json.loads(path.read_text())
            saved["expires_at"] = 0
            path.write_text(json.dumps(saved))
            assert (await generator.generate(_Value(value="synthetic-input"))).output.value == "extracted"
        saved = json.loads(path.read_text())
        assert saved["refresh_token"] == "synthetic-rotated"  # noqa: S105 - synthetic credential
        # A newly opened auth instance must use the persisted rotation without another exchange.
        async with httpx2.AsyncClient(auth=coding_oauth.CodingOAuthAuth(subscription, path)) as client:
            await client.post(coding_oauth.CodingOAuthAuth(subscription, path).base_url + "/chat/completions")

    asyncio.run(run())
    assert presented[0][1] == "ABCD"
    assert len(oauth_requests) == 4
    assert [request.headers["authorization"] for request in resource_requests] == [
        "Bearer synthetic-initial",
        "Bearer synthetic-renewed",
        "Bearer synthetic-renewed",
    ]


@pytest.mark.parametrize(
    "response",
    [
        {"error": "invalid_grant", "error_description": "synthetic-secret-error"},
        {"access_token": "synthetic-new", "expires_in": 3600},
    ],
)
def test_failed_coding_refresh_preserves_owned_store(response, tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "tokens.json"
    original = json.dumps({
        "schema": "powercontext.oauth.v1",
        "subscription": "qwen",
        "device_id": "synthetic-device",
        "access_token": "synthetic-old",
        "refresh_token": "synthetic-refresh",
        "expires_at": 0,
        "token_type": "Bearer",
    })
    path.write_text(original)
    monkeypatch.setattr(
        coding_oauth,
        "AsyncOAuth2Client",
        partial(
            coding_oauth.AsyncOAuth2Client,
            transport=httpx.MockTransport(lambda _: httpx.Response(400 if "error" in response else 200, json=response)),
        ),
    )

    async def run() -> None:
        async with httpx2.AsyncClient(
            auth=coding_oauth.CodingOAuthAuth("qwen", path),
            transport=httpx2.MockTransport(lambda _: pytest.fail("inference sent")),
        ) as client:
            with pytest.raises(UserError) as error:
                await client.post("https://api.qwen.ai/compatible-mode/v1/chat/completions")
            assert "synthetic-secret-error" not in "".join(traceback.format_exception(error.value))
            assert path.read_text() == original

    asyncio.run(run())
