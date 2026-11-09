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

"""Adapt native Gemini requests to an existing Gemini CLI Code Assist account."""

from __future__ import annotations

import json
import math
import os
import re
import time
from contextlib import AsyncExitStack
from pathlib import Path
from uuid import uuid4

import httpx2
from google.genai.types import HttpRetryOptions
from pydantic_ai.exceptions import UserError
from pydantic_ai.providers.google import GoogleProvider
from typing_extensions import override

_ENDPOINT = "https://cloudcode-pa.googleapis.com/v1internal:"


class GeminiCliTransport(httpx2.AsyncBaseTransport):
    """Translate the Code Assist envelope; leave Gemini serialization to its SDK.

    The CLI owns login, onboarding and renewal. This adapter only reads its
    unencrypted OAuth cache; ADC and API keys are different contracts.
    """

    def __init__(self, path: Path | None = None, *, client: httpx2.AsyncClient) -> None:
        self._path = path or Path(os.getenv("GEMINI_CLI_AUTH_PATH") or "~/.gemini/oauth_creds.json").expanduser()
        self._client = client

    @override
    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        match = re.fullmatch(r"/v1(?:beta)?/models/([^/]+):generateContent", request.url.path)
        if (
            request.method != "POST"
            or request.url.scheme != "https"
            or request.url.host != "generativelanguage.googleapis.com"
            or request.url.port not in {None, 443}
            or request.url.userinfo
            or not match
        ):
            message = "Gemini CLI integration supports non-streaming generateContent only"
            raise UserError(message)
        try:
            credentials = json.loads(self._path.read_text(encoding="utf-8"))
            token = credentials["access_token"]
            if (
                not isinstance(token, str)
                or not token
                or not all(32 < ord(char) < 127 for char in token)
                or credentials.get("type") not in {None, "authorized_user"}
                or not math.isfinite(float(credentials["expiry_date"]))
                or float(credentials["expiry_date"]) <= time.time() * 1000
            ):
                raise ValueError  # noqa: TRY301 - normalize all credential format failures
        except (OSError, ValueError, KeyError, TypeError, OverflowError):
            message = "Gemini CLI OAuth credentials are unavailable or expired; renew the original CLI login"
            raise UserError(message) from None
        headers = {"Authorization": f"Bearer {token}"}
        project = os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("GOOGLE_CLOUD_PROJECT_ID")
        loaded = await self._client.post(
            _ENDPOINT + "loadCodeAssist",
            headers=headers,
            json={
                "cloudaicompanionProject": project,
                "metadata": {
                    "duetProject": project,
                    "ideType": "IDE_UNSPECIFIED",
                    "platform": "PLATFORM_UNSPECIFIED",
                    "pluginType": "GEMINI",
                },
            },
        )
        if not loaded.is_success:
            return httpx2.Response(loaded.status_code, content=loaded.content, headers=loaded.headers)
        try:
            account = loaded.json()
            project = account.get("cloudaicompanionProject") or project
            if (
                not isinstance(project, str)
                or not project
                or not (account.get("currentTier") or account.get("paidTier"))
            ):
                raise ValueError  # noqa: TRY301 - onboarding remains owned by the CLI
        except (ValueError, TypeError, AttributeError):
            message = "Gemini Code Assist account is not onboarded; complete setup in the original CLI"
            raise UserError(message) from None
        body = json.loads(await request.aread())
        response = await self._client.post(
            _ENDPOINT + "generateContent",
            headers=headers,
            json={"model": match[1], "project": project, "user_prompt_id": str(uuid4()), "request": body},
        )
        if not response.is_success:
            return httpx2.Response(response.status_code, content=response.content, headers=response.headers)
        try:
            wrapped = response.json()
            content = wrapped["response"]
            if not isinstance(content, dict):
                raise TypeError  # noqa: TRY301 - malformed upstream envelopes are not model responses
        except (ValueError, KeyError, TypeError):
            message = "Gemini Code Assist returned a malformed response"
            raise UserError(message) from None
        return httpx2.Response(200, json=content)


class GeminiCliProvider(GoogleProvider):
    """Keep Code Assist history identity separate from the API-key Gemini API."""

    @property
    @override
    def name(self) -> str:
        return "gemini-cli"

    @property
    @override
    def canonical_system(self) -> str:
        return "gemini-cli"


def gemini_cli_provider(resources: AsyncExitStack) -> GeminiCliProvider:
    wire = httpx2.AsyncClient(follow_redirects=False, timeout=30)
    resources.push_async_callback(wire.aclose)
    client = httpx2.AsyncClient(transport=GeminiCliTransport(client=wire), follow_redirects=False)
    resources.push_async_callback(client.aclose)
    return GeminiCliProvider(
        api_key="externally-managed", http_client=client, retry_options=HttpRetryOptions(attempts=1)
    )
