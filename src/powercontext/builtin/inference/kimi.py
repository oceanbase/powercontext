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

"""Kimi Code through public Pydantic AI and Authlib interfaces."""

from __future__ import annotations

import os
import platform
import time
from collections.abc import AsyncGenerator, Callable, Generator
from contextlib import AsyncExitStack
from importlib.metadata import version
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import httpx2
from authlib.integrations.base_client import OAuthError
from authlib.integrations.httpx_client import AsyncOAuth2Client
from openai import AsyncOpenAI
from pydantic_ai.exceptions import UserError
from pydantic_ai.providers.openai_compatible import OpenAICompatibleProvider
from typing_extensions import override

from powercontext.builtin.inference.oauth import (
    OAuthCredentialsFile,
    apply_oauth_token,
    authorize_device,
    complete_token_response,
)

_CLIENT_ID = "17e5f671-d194-4dfb-9706-5516cb48c098"
_TOKEN_ENDPOINT = "https://auth.kimi.com/api/oauth/token"  # noqa: S105 - public OAuth endpoint
_BASE_URL = "https://api.kimi.com/coding/v1"


def _path() -> Path:
    return Path(
        os.getenv("POWERCONTEXT_KIMI_CREDENTIALS_PATH") or "~/.config/powercontext/oauth/kimi.json"
    ).expanduser()


def _identity(device_id: str) -> dict[str, str]:
    return {
        "User-Agent": f"PowerContext/{version('powercontext')}",
        "X-Msh-Platform": "kimi_cli",
        "X-Msh-Version": "0.1.0",
        "X-Msh-Device-Name": "powercontext",
        "X-Msh-Device-Model": platform.machine(),
        "X-Msh-Os-Version": platform.system(),
        "X-Msh-Device-Id": device_id,
    }


class KimiOAuthAuth(httpx2.Auth):
    """Inject a pre-refreshed Kimi grant once; never retry a model request."""

    def __init__(self, path: Path | None = None, *, clock: Callable[[], float] = time.time) -> None:
        self.credentials = OAuthCredentialsFile(path or _path(), "kimi", clock=clock)

    @override
    def sync_auth_flow(self, request: httpx2.Request) -> Generator[httpx2.Request, httpx2.Response, None]:
        message = "Kimi subscription OAuth requires an async HTTP client"
        raise UserError(message)

    @override
    async def async_auth_flow(self, request: httpx2.Request) -> AsyncGenerator[httpx2.Request, httpx2.Response]:
        if str(request.url) != _BASE_URL + "/chat/completions":
            message = "Kimi credentials require the default HTTPS Chat Completions endpoint"
            raise UserError(message)
        token = await self.credentials.load_active(self._refresh)
        request.headers.update(_identity(token["device_id"]))
        apply_oauth_token(request, token)
        yield request

    @staticmethod
    async def _refresh(token: dict[str, Any]) -> dict[str, Any]:
        async with AsyncOAuth2Client(
            client_id=_CLIENT_ID,
            token_endpoint_auth_method="none",  # noqa: S106 - public OAuth client
            token=token,
            headers=_identity(token["device_id"]),
            timeout=30,
            follow_redirects=False,
        ) as client:
            client.register_compliance_hook("refresh_token_response", complete_token_response)
            return dict(await client.refresh_token(_TOKEN_ENDPOINT))


class KimiProvider(OpenAICompatibleProvider):
    """Kimi subscription identity with a caller-owned authenticated OpenAI SDK client."""

    def __init__(self, *, openai_client: AsyncOpenAI) -> None:
        super().__init__("kimi", openai_client=openai_client)


def kimi_provider(resources: AsyncExitStack) -> KimiProvider:
    http_client = httpx2.AsyncClient(auth=KimiOAuthAuth(), follow_redirects=False)
    client = AsyncOpenAI(base_url=_BASE_URL, api_key="externally-managed", http_client=http_client, max_retries=0)
    resources.push_async_callback(client.close)
    return KimiProvider(openai_client=client)


async def login_kimi(present: Callable[[str, str], None], *, path: Path | None = None) -> Path:
    """Acquire a new PowerContext-owned grant without borrowing the Kimi CLI's refresh token."""
    credentials = OAuthCredentialsFile(path or _path(), "kimi")
    credentials.require_new()
    device_id = str(uuid4())
    try:
        async with AsyncOAuth2Client(
            client_id=_CLIENT_ID,
            token_endpoint_auth_method="none",  # noqa: S106
            headers=_identity(device_id),
            timeout=30,
            follow_redirects=False,
        ) as client:
            token = await authorize_device(
                client,
                device_endpoint="https://auth.kimi.com/api/oauth/device_authorization",
                token_endpoint=_TOKEN_ENDPOINT,
                verification_host="auth.kimi.com",
                form={"client_id": _CLIENT_ID},
                present=present,
            )
    except (OSError, TimeoutError, ValueError, KeyError, TypeError, OAuthError, httpx.HTTPError):
        message = "Coding subscription device authorization failed"
        raise UserError(message) from None

    return await credentials.create(token, device_id=device_id)
