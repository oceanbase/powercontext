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

"""Application-owned Qwen and Kimi subscription device grants using standard OAuth."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import tempfile
import time
from collections.abc import AsyncGenerator, Callable, Generator
from contextlib import AsyncExitStack
from importlib.metadata import version
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

import httpx
import httpx2
from authlib.integrations.base_client import OAuthError
from authlib.integrations.httpx_client import AsyncOAuth2Client
from filelock import AsyncFileLock, AsyncSoftFileLock
from openai import AsyncOpenAI
from pydantic import BaseModel, Field, ValidationError, field_validator
from pydantic_ai.exceptions import UserError
from pydantic_ai.providers.openai_compatible import OpenAICompatibleProvider
from typing_extensions import override

Subscription = Literal["qwen", "kimi"]
_SCHEMA = "powercontext.oauth.v1"


class _TokenFields(BaseModel):
    access_token: str = Field(min_length=1)
    refresh_token: str = Field(min_length=1)
    token_type: Literal["Bearer"] = "Bearer"  # noqa: S105 - OAuth token type

    @field_validator("access_token", "refresh_token")
    @classmethod
    def valid_token(cls, value: str) -> str:
        if not all(32 < ord(char) < 127 for char in value):
            raise ValueError
        return value


class _TokenResponse(_TokenFields):
    expires_in: float = Field(gt=0, allow_inf_nan=False, strict=True)


class _StoredCredentials(_TokenFields):
    expires_at: float = Field(allow_inf_nan=False, strict=True)
    device_id: str = Field(min_length=1)


class _DeviceAuthorization(BaseModel):
    device_code: str = Field(min_length=1)
    user_code: str = Field(min_length=1)
    verification_uri: str | None = None
    verification_uri_complete: str | None = None
    expires_in: float = Field(gt=0, allow_inf_nan=False, strict=True)
    interval: float = Field(default=5, gt=0, allow_inf_nan=False, strict=True)


def _settings(subscription: Subscription) -> tuple[str, str, str, str]:
    if subscription == "qwen":
        return (
            "https://auth.qwen.ai/api/oauth/",
            "qwen-code-cli",
            "device/code",
            "https://api.qwen.ai/compatible-mode/v1",
        )
    if subscription == "kimi":
        return (
            "https://auth.kimi.com/api/oauth/",
            "17e5f671-d194-4dfb-9706-5516cb48c098",
            "device_authorization",
            "https://api.kimi.com/coding/v1",
        )
    message = "Unsupported coding subscription"
    raise UserError(message)


def _default_path(subscription: Subscription) -> Path:
    configured = os.getenv(f"POWERCONTEXT_{subscription.upper()}_CREDENTIALS_PATH")
    return Path(configured or f"~/.config/powercontext/oauth/{subscription}.json").expanduser()


def _save(path: Path, value: dict[str, Any]) -> None:
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        try:
            json.dump(value, handle, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
            handle.close()
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def _token_response(response: httpx.Response) -> httpx.Response:
    if response.status_code != 400:
        response.raise_for_status()
    if response.is_success:
        _TokenResponse.model_validate(response.json())
    return response


class CodingOAuthAuth(httpx2.Auth):
    """A standard async auth hook with application-owned, locked token persistence."""

    def __init__(
        self, subscription: Subscription, path: Path | None = None, *, clock: Callable[[], float] = time.time
    ) -> None:
        if issubclass(AsyncFileLock, AsyncSoftFileLock):
            message = "Coding subscription OAuth requires operating-system file locks"
            raise UserError(message)
        self.subscription = subscription
        self._issuer, self._client_id, self._device_path, self.base_url = _settings(subscription)
        self.path = path or _default_path(subscription)
        self._clock = clock

    @override
    def sync_auth_flow(self, request: httpx2.Request) -> Generator[httpx2.Request, httpx2.Response, None]:
        message = "Coding subscription OAuth requires an async HTTP client"
        raise UserError(message)

    def _identity(self, data: dict[str, Any]) -> dict[str, str]:
        release = version("powercontext")
        headers = {"User-Agent": f"PowerContext/{release}"}
        if self.subscription == "kimi":
            headers.update({
                "X-Msh-Platform": "kimi_cli",
                "X-Msh-Version": "0.1.0",
                "X-Msh-Device-Name": "powercontext",
                "X-Msh-Device-Model": platform.machine(),
                "X-Msh-Os-Version": platform.system(),
                "X-Msh-Device-Id": data["device_id"],
            })
        return headers

    def _read(self) -> dict[str, Any]:
        data = json.loads(self.path.read_text(encoding="utf-8"))
        if data["schema"] != _SCHEMA or data["subscription"] != self.subscription:
            raise ValueError
        _StoredCredentials.model_validate(data)
        if not all(32 < ord(char) < 127 for char in data["access_token"] + data["device_id"]):
            raise ValueError
        return data

    @override
    async def async_auth_flow(self, request: httpx2.Request) -> AsyncGenerator[httpx2.Request, httpx2.Response]:
        if str(request.url) != self.base_url + "/chat/completions":
            message = "Coding subscription credentials require the default HTTPS Chat Completions endpoint"
            raise UserError(message)
        try:
            data = self._read()
            if data["expires_at"] <= self._clock() + 60:
                async with AsyncFileLock(self.path.with_suffix(".lock"), timeout=30, mode=0o600, run_in_executor=False):
                    data = self._read()
                    if data["expires_at"] <= self._clock() + 60:
                        async with AsyncOAuth2Client(
                            client_id=self._client_id,
                            token_endpoint_auth_method="none",  # noqa: S106
                            token=data,
                            headers=self._identity(data),
                            timeout=30,
                            follow_redirects=False,
                        ) as client:
                            client.register_compliance_hook("refresh_token_response", _token_response)
                            token = await client.refresh_token(self._issuer + "token")
                        data.update(token)
                        data["expires_at"] = self._clock() + float(token["expires_in"])
                        _save(self.path, data)
        except (OSError, TimeoutError, ValueError, KeyError, TypeError, OAuthError, httpx.HTTPError):
            message = "Coding subscription credentials are unavailable or renewal failed; run the device login again"
            raise UserError(message) from None
        request.headers.update(self._identity(data))
        request.headers["Authorization"] = f"Bearer {data['access_token']}"
        yield request


async def login_coding_subscription(
    subscription: Subscription, present: Callable[[str, str], None], *, path: Path | None = None
) -> Path:
    """Authorize a new application-owned store; never import or overwrite a CLI grant."""

    auth = CodingOAuthAuth(subscription, path)
    if auth.path.exists():
        message = "The coding subscription credential file already exists; select a new path for a new login"
        raise UserError(message)
    data = {"schema": _SCHEMA, "subscription": subscription, "device_id": str(uuid4())}
    try:
        async with AsyncOAuth2Client(
            client_id=auth._client_id,
            token_endpoint_auth_method="none",  # noqa: S106
            headers=auth._identity(data),
            timeout=30,
            follow_redirects=False,
        ) as client:
            form = {"client_id": auth._client_id}
            if subscription == "qwen":
                form["scope"] = "model:invoke profile:read"
            issued = await client.request("POST", auth._issuer + auth._device_path, data=form, withhold_token=True)
            issued.raise_for_status()
            device = _DeviceAuthorization.model_validate(issued.json()).model_dump()
            uri = device.get("verification_uri_complete") or device["verification_uri"]
            url = httpx.URL(uri)
            host = "chat.qwen.ai" if subscription == "qwen" else "auth.kimi.com"
            if url.scheme != "https" or url.host != host or url.port not in {None, 443} or url.userinfo:
                raise ValueError  # noqa: TRY301 - do not display a foreign verification URL
            present(uri, device["user_code"])
            interval = device["interval"]
            client.register_compliance_hook("access_token_response", _token_response)
            async with asyncio.timeout(min(900, float(device["expires_in"]))):
                while True:
                    await asyncio.sleep(interval)
                    try:
                        token = await client.fetch_token(
                            auth._issuer + "token",
                            grant_type="urn:ietf:params:oauth:grant-type:device_code",
                            device_code=device["device_code"],
                        )
                        break
                    except OAuthError as error:
                        if error.error == "slow_down":
                            interval += 5
                        elif error.error != "authorization_pending":
                            raise
        data.update(token)
        data["expires_at"] = time.time() + float(token["expires_in"])
        auth.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        async with AsyncFileLock(auth.path.with_suffix(".lock"), timeout=30, mode=0o600, run_in_executor=False):
            if auth.path.exists():
                raise FileExistsError
            _save(auth.path, data)
    except (OSError, TimeoutError, ValueError, KeyError, TypeError, OAuthError, httpx.HTTPError, ValidationError):
        message = "Coding subscription device authorization failed"
        raise UserError(message) from None
    return auth.path


def coding_oauth_provider(subscription: Subscription, resources: AsyncExitStack) -> OpenAICompatibleProvider:
    auth = CodingOAuthAuth(subscription)
    http_client = httpx2.AsyncClient(auth=auth, follow_redirects=False)
    client = AsyncOpenAI(base_url=auth.base_url, api_key="externally-managed", http_client=http_client, max_retries=0)
    resources.push_async_callback(client.close)
    # The generic OpenAI-compatible profile handles model families without claiming
    # these coding services are the underlying vendor's public API.
    return OpenAICompatibleProvider(subscription, openai_client=client)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Authorize a PowerContext-owned coding subscription")
    parser.add_argument("subscription", choices=("qwen", "kimi"))
    arguments = parser.parse_args()
    asyncio.run(login_coding_subscription(arguments.subscription, lambda uri, code: print(f"{uri}\nCode: {code}")))
