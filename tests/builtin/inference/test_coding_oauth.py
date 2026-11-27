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
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import httpx2
import pytest
from pydantic import BaseModel
from pydantic_ai.exceptions import UserError

from powercontext.builtin.inference.pydantic_ai import InferenceLimits, PydanticAIStructuredGenerator
from powercontext.builtin.inference.qwen import QwenOAuthAuth
from powercontext.builtin.runtime.composition import _open_pydantic_ai_model


class _Value(BaseModel):
    value: str


def test_device_login_subscription_model_and_persisted_rotation(
    coding_subscription, oauth_http, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    subscription, auth_type, login, endpoint = coding_subscription
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
    oauth_http(oauth_response)

    async def run() -> None:
        await login(lambda uri, code: presented.append((uri, code)), path=path)
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
        async with httpx2.AsyncClient(auth=auth_type(path)) as client:
            await client.post(endpoint)

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
def test_failed_coding_refresh_preserves_owned_store(response, tmp_path: Path, oauth_http) -> None:
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
    oauth_http(lambda _: httpx.Response(400 if "error" in response else 200, json=response))

    async def run() -> None:
        async with httpx2.AsyncClient(
            auth=QwenOAuthAuth(path),
            transport=httpx2.MockTransport(lambda _: pytest.fail("inference sent")),
        ) as client:
            with pytest.raises(UserError) as error:
                await client.post("https://api.qwen.ai/compatible-mode/v1/chat/completions")
            assert "synthetic-secret-error" not in "".join(traceback.format_exception(error.value))
            assert path.read_text() == original

    asyncio.run(run())


@pytest.mark.parametrize("status", [401, 500])
def test_coding_native_provider_does_not_refresh_or_replay_model_failure(
    coding_subscription, oauth_http, tmp_path: Path, monkeypatch, status
) -> None:
    from pydantic_ai import Agent
    from pydantic_ai.exceptions import ModelHTTPError

    subscription, _, _, _ = coding_subscription
    path = tmp_path / "tokens.json"
    original = json.dumps({
        "schema": "powercontext.oauth.v1",
        "subscription": subscription,
        "device_id": "synthetic-device",
        "access_token": "synthetic-current",
        "refresh_token": "synthetic-refresh",
        "expires_at": 9e12,
        "token_type": "Bearer",
    })
    path.write_text(original)
    monkeypatch.setenv(f"POWERCONTEXT_{subscription.upper()}_CREDENTIALS_PATH", str(path))
    oauth_http(lambda _: pytest.fail("model failure must not exchange a refresh grant"))
    requests = []

    def response(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(status, json={"error": {"message": "synthetic failure"}})

    class Client(httpx2.AsyncClient):
        def __init__(self, **kwargs):
            super().__init__(**kwargs, transport=httpx2.MockTransport(response))

    monkeypatch.setattr(httpx2, "AsyncClient", Client)

    async def run() -> None:
        async with AsyncExitStack() as resources:
            _, model = await _open_pydantic_ai_model(
                f"{subscription}:synthetic-model", base_url=None, headers={}, resources=resources, instrumentation=None
            )
            with pytest.raises(ModelHTTPError) as error:
                await Agent(model).run("synthetic prompt")
            assert error.value.status_code == status

    asyncio.run(run())
    assert len(requests) == 1
    assert requests[0].headers["authorization"] == "Bearer synthetic-current"
    assert path.read_text() == original


@pytest.mark.parametrize("cancel_first_refresh", [False, True])
def test_coding_workers_serialize_rotation_and_release_cancelled_refresh(
    coding_subscription, oauth_http, tmp_path: Path, cancel_first_refresh
) -> None:
    subscription, auth_type, _, endpoint = coding_subscription
    path = tmp_path / "tokens.json"
    original = json.dumps({
        "schema": "powercontext.oauth.v1",
        "subscription": subscription,
        "device_id": "synthetic-device",
        "access_token": "synthetic-expired",
        "refresh_token": "synthetic-refresh",
        "expires_at": 0,
        "token_type": "Bearer",
    })
    path.write_text(original)
    exchanges = []
    resource_tokens = []

    async def run() -> None:
        started = asyncio.Event()
        finish = asyncio.Event()

        async def refresh(request: httpx.Request) -> httpx.Response:
            exchanges.append(request)
            assert parse_qs(request.content.decode())["refresh_token"] == ["synthetic-refresh"]
            if len(exchanges) == 1:
                started.set()
                await finish.wait()
            return httpx.Response(
                200,
                json={
                    "access_token": "synthetic-renewed",
                    "refresh_token": "synthetic-rotated",
                    "expires_in": 3600,
                    "token_type": "Bearer",
                },
            )

        def resource(request: httpx2.Request) -> httpx2.Response:
            resource_tokens.append(request.headers["authorization"])
            return httpx2.Response(200, json={})

        oauth_http(refresh)
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(resource)) as client:
            first = asyncio.create_task(client.post(endpoint, auth=auth_type(path)))
            await started.wait()
            if cancel_first_refresh:
                first.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await first
                assert path.read_text() == original
                assert not resource_tokens
                # A different auth instance must reacquire the abandoned lock and
                # recover from the unchanged store, without a background writer.
                await asyncio.wait_for(client.post(endpoint, auth=auth_type(path)), timeout=5)
            else:
                second = asyncio.create_task(client.post(endpoint, auth=auth_type(path)))
                await asyncio.sleep(0.05)
                finish.set()
                await asyncio.gather(first, second)
        assert json.loads(path.read_text())["refresh_token"] == "synthetic-rotated"  # noqa: S105 - synthetic credential

    asyncio.run(run())
    assert len(exchanges) == (2 if cancel_first_refresh else 1)
    assert resource_tokens == ["Bearer synthetic-renewed"] * (1 if cancel_first_refresh else 2)
