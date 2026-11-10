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

"""Grok CLI OAuth through HTTPX auth and Pydantic AI's Responses interface.

The credential format and refresh grant follow republic's Grok provider. This
HTTP integration does not implement Magpie's CLI-driven subscription bridge.
"""

from __future__ import annotations

import json
import math
import os
import time
from collections.abc import AsyncGenerator, Callable, Generator
from contextlib import AsyncExitStack
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import httpx2
from authlib.integrations.base_client import OAuthError
from authlib.integrations.httpx_client import AsyncOAuth2Client
from filelock import AsyncFileLock, AsyncSoftFileLock, Timeout
from openai import AsyncOpenAI
from pydantic_ai.exceptions import UserError
from pydantic_ai.profiles.grok import grok_model_profile
from pydantic_ai.providers.openai_compatible import OpenAICompatibleProvider
from typing_extensions import override

from powercontext.builtin.inference.oauth import apply_oauth_token, atomic_save_json

_ISSUER = "https://auth.x.ai"
_CLIENT_ID = "b1a00492-073a-47ea-816f-4c329264a828"
_SCOPE = f"{_ISSUER}::{_CLIENT_ID}"


class GrokCliAuth(httpx2.Auth):
    """Read each request's login; serialize renewal with the CLI's OS file lock.

    Use an async HTTPX client. Cancellation propagates through the OAuth HTTP
    exchange and releases the lock without a background credential writer.
    """

    def __init__(self, path: Path | None = None, *, clock: Callable[[], float] = time.time) -> None:
        home = Path(os.getenv("GROK_HOME") or "~/.grok").expanduser()
        self._path = Path(path or os.getenv("GROK_AUTH_PATH") or home / "auth.json").expanduser()
        self._clock = clock

    @override
    def sync_auth_flow(self, request: httpx2.Request) -> Generator[httpx2.Request, httpx2.Response, None]:
        message = "Grok CLI OAuth requires an async HTTP client"
        raise UserError(message)

    @override
    async def async_auth_flow(self, request: httpx2.Request) -> AsyncGenerator[httpx2.Request, httpx2.Response]:
        if (
            request.url.scheme != "https"
            or request.url.host != "api.x.ai"
            or request.url.port not in {None, 443}
            or request.url.userinfo
        ):
            message = "Grok CLI credentials require the default HTTPS service endpoint"
            raise UserError(message)
        try:
            data, stored = self._read()
            if self._expires_at(stored) <= self._clock() + 60:
                lock = AsyncFileLock(
                    self._path.with_name("auth.json.lock"), timeout=30, mode=0o600, run_in_executor=False
                )
                if isinstance(lock, AsyncSoftFileLock):
                    message = "Grok credential renewal requires an OS file lock"
                    raise UserError(message)
                async with lock:
                    # Another process may have spent the single-use refresh grant.
                    data, stored = self._read()
                    if self._expires_at(stored) <= self._clock() + 60:
                        replacement = await self._refresh(stored)
                        data[_SCOPE] = replacement
                        self._save(data)
                        stored = replacement
        except (OSError, Timeout, ValueError, TypeError, OverflowError):
            message = "Grok CLI credentials are unavailable or malformed; run grok login"
            raise UserError(message) from None
        apply_oauth_token(request, {"access_token": stored["key"], "token_type": "Bearer"})
        yield request

    def _read(self) -> tuple[dict[str, Any], dict[str, Any]]:
        data = json.loads(self._path.read_text(encoding="utf-8"))
        stored = data.get(_SCOPE) if isinstance(data, dict) else None
        if (
            not isinstance(stored, dict)
            or stored.get("oidc_issuer") != _ISSUER
            or stored.get("oidc_client_id") != _CLIENT_ID
            or not _bearer_token(stored.get("key"))
        ):
            message = "Grok credentials must contain an xAI OAuth login; run grok login"
            raise UserError(message)
        self._expires_at(stored)
        return data, stored

    @staticmethod
    def _expires_at(stored: dict[str, Any]) -> float:
        value = stored.get("expires_at")
        if isinstance(value, str):
            expiry = datetime.fromisoformat(value)
            if expiry.tzinfo is None:
                message = "Missing expiry timezone"
                raise ValueError(message)
            value = expiry.timestamp()
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            message = "Invalid token expiry"
            raise ValueError(message)
        return value

    async def _refresh(self, stored: dict[str, Any]) -> dict[str, Any]:
        if not _bearer_token(stored.get("refresh_token")):
            message = "Grok token has expired; run grok login"
            raise UserError(message)
        principal = {key: stored[key] for key in ("principal_type", "principal_id") if stored.get(key)}
        try:
            async with AsyncOAuth2Client(
                client_id=_CLIENT_ID,
                token_endpoint_auth_method="none",  # noqa: S106 - public OAuth client
                token={"access_token": stored["key"], "refresh_token": stored["refresh_token"]},
                timeout=30,
                follow_redirects=False,
            ) as client:
                client.register_compliance_hook("refresh_token_response", lambda response: response.raise_for_status())
                token = await client.refresh_token(f"{_ISSUER}/oauth2/token", **principal)
            lifetime = float(token["expires_in"])
            if (
                not _bearer_token(token.get("access_token"))
                or not _bearer_token(token.get("refresh_token"))
                or not math.isfinite(lifetime)
                or lifetime <= 0
                or isinstance(token["expires_in"], bool)
                or token.get("token_type", "Bearer").casefold() != "bearer"
            ):
                message = "Invalid refreshed credential set"
                raise ValueError(message)  # noqa: TRY301 - sanitize validation and OAuth errors together
            return {
                **stored,
                "key": token["access_token"],
                "refresh_token": token["refresh_token"],
                "expires_at": datetime.fromtimestamp(self._clock() + lifetime, UTC).isoformat(),
            }
        except (OAuthError, httpx.HTTPError, KeyError, ValueError, TypeError, AttributeError, OverflowError):
            message = "Cannot refresh the Grok token; run grok login"
            raise UserError(message) from None

    def _save(self, data: dict[str, Any]) -> None:
        atomic_save_json(self._path, data)


def _bearer_token(value: Any) -> bool:
    return isinstance(value, str) and bool(value) and all(32 < ord(char) < 127 for char in value)


def grok_cli_provider(resources: AsyncExitStack) -> OpenAICompatibleProvider:
    """Use xAI OAuth independently of the API-key-based native xAI SDK."""

    http_client = httpx2.AsyncClient(auth=GrokCliAuth(), follow_redirects=False)
    # Auth failures must not spend the grant again; inference failures must not
    # replay a potentially billable request inside the SDK.
    client = AsyncOpenAI(
        base_url="https://api.x.ai/v1", api_key="externally-managed", http_client=http_client, max_retries=0
    )
    resources.push_async_callback(client.close)
    return OpenAICompatibleProvider("grok", openai_client=client, profile=grok_model_profile)
