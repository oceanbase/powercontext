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

"""Application-owned credential transactions and Authlib/HTTPX2 interoperability.

Pydantic AI's public Provider accepts the authenticated SDK client. Its private
OAuth lifecycle also refreshes and replays on 401, and does not provide an OS
transaction around load/refresh/save. These helpers preserve our stricter
no-replay contract without depending on that private lifecycle.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import time
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from typing import Any, Literal

import httpx
import httpx2
from authlib.integrations.base_client import OAuthError
from authlib.integrations.httpx_client import AsyncOAuth2Client, OAuth2Auth
from filelock import AsyncFileLock, AsyncSoftFileLock
from pydantic import BaseModel, Field, field_validator
from pydantic_ai.exceptions import UserError

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


def apply_oauth_token(request: httpx2.Request, token: Mapping[str, Any]) -> None:
    """Use Authlib's public signer without converting HTTPX2's request to HTTPX 0.x.

    Authlib 1.7's OAuth2Auth.auth_flow builds a legacy HTTPX Request. Only the
    default header placement is needed here; prepare() leaves URL/body intact.
    Refresh is explicitly performed before signing, never in a response hook.
    """
    _, headers, _ = OAuth2Auth(dict(token)).prepare(str(request.url), {}, b"")
    request.headers.update(headers)


def atomic_save_json(path: Path, value: Mapping[str, Any]) -> None:
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        try:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
            handle.close()
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def complete_token_response(response: httpx.Response) -> httpx.Response:
    """Reject partial rotation before Authlib can reuse an omitted refresh token."""
    if response.status_code != 400:
        response.raise_for_status()
    if response.is_success:
        _TokenResponse.model_validate(response.json())
    return response


class OAuthCredentialsFile:
    """The PowerContext-owned file format shared by the coding device grants.

    This is an application transaction, not a Pydantic AI CredentialSource or
    provider registry. The refresh callback runs under the same OS lock as the
    reread and atomic replacement; provider-specific wire policy stays outside.
    """

    def __init__(self, path: Path, subscription: str, *, clock: Callable[[], float] = time.time) -> None:
        if issubclass(AsyncFileLock, AsyncSoftFileLock):
            message = "Coding subscription OAuth requires operating-system file locks"
            raise UserError(message)
        self.path = path
        self._subscription = subscription
        self._clock = clock

    def _read(self) -> dict[str, Any]:
        data = json.loads(self.path.read_text(encoding="utf-8"))
        if data["schema"] != _SCHEMA or data["subscription"] != self._subscription:
            raise ValueError
        _StoredCredentials.model_validate(data)
        if not all(32 < ord(char) < 127 for char in data["device_id"]):
            raise ValueError
        return data

    def require_new(self) -> None:
        if self.path.exists():
            message = "The coding subscription credential file already exists; select a new path for a new login"
            raise UserError(message)

    async def load_active(self, refresh: Callable[[dict[str, Any]], Awaitable[Mapping[str, Any]]]) -> dict[str, Any]:
        try:
            data = self._read()
            if data["expires_at"] <= self._clock() + 60:
                async with AsyncFileLock(self.path.with_suffix(".lock"), timeout=30, mode=0o600, run_in_executor=False):
                    data = self._read()
                    if data["expires_at"] <= self._clock() + 60:
                        token = await refresh(data)
                        _TokenResponse.model_validate(token)
                        data.update(token)
                        data["expires_at"] = self._clock() + float(token["expires_in"])
                        atomic_save_json(self.path, data)
        except (OSError, TimeoutError, ValueError, KeyError, TypeError, OAuthError, httpx.HTTPError):
            message = "Coding subscription credentials are unavailable or renewal failed; run the device login again"
            raise UserError(message) from None
        return data

    async def create(self, token: Mapping[str, Any], *, device_id: str) -> Path:
        try:
            _TokenResponse.model_validate(token)
            data = {
                **token,
                "schema": _SCHEMA,
                "subscription": self._subscription,
                "device_id": device_id,
                "expires_at": self._clock() + float(token["expires_in"]),
            }
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            async with AsyncFileLock(self.path.with_suffix(".lock"), timeout=30, mode=0o600, run_in_executor=False):
                self.require_new()
                atomic_save_json(self.path, data)
        except (OSError, TimeoutError, ValueError, KeyError, TypeError):
            message = "Coding subscription device authorization failed"
            raise UserError(message) from None
        return self.path


async def authorize_device(
    client: AsyncOAuth2Client,
    *,
    device_endpoint: str,
    token_endpoint: str,
    verification_host: str,
    form: dict[str, str],
    present: Callable[[str, str], None],
) -> Mapping[str, Any]:
    """Poll RFC 8628 grants with Authlib's public token exchange.

    Authlib has no async device-polling client. Keep only the bounded polling
    and pending/slow_down handling here; form signing and token parsing are SDK
    responsibilities. No model request passes through this OAuth client.
    """
    try:
        issued = await client.request("POST", device_endpoint, data=form, withhold_token=True)
        issued.raise_for_status()
        device = _DeviceAuthorization.model_validate(issued.json())
        uri = device.verification_uri_complete or device.verification_uri
        if uri is None:
            raise ValueError  # noqa: TRY301 - a verification target is required
        url = httpx.URL(uri)
        if url.scheme != "https" or url.host != verification_host or url.port not in {None, 443} or url.userinfo:
            raise ValueError  # noqa: TRY301 - never present a foreign verification URL
        present(str(uri), device.user_code)
        interval = device.interval
        client.register_compliance_hook("access_token_response", complete_token_response)
        async with asyncio.timeout(min(900, device.expires_in)):
            while True:
                await asyncio.sleep(interval)
                try:
                    return await client.fetch_token(
                        token_endpoint,
                        grant_type="urn:ietf:params:oauth:grant-type:device_code",
                        device_code=device.device_code,
                    )
                except OAuthError as error:
                    if error.error == "slow_down":
                        interval += 5
                    elif error.error != "authorization_pending":
                        raise
    except (OSError, TimeoutError, ValueError, KeyError, TypeError, OAuthError, httpx.HTTPError):
        message = "Coding subscription device authorization failed"
        raise UserError(message) from None
