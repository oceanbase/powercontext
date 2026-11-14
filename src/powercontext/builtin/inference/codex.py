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

"""Read subscription credentials without taking ownership of the CLI's refresh grant."""

from __future__ import annotations

import json
import os
from collections.abc import Generator
from contextlib import AsyncExitStack
from pathlib import Path

import httpx2
from openai import AsyncOpenAI
from pydantic_ai.exceptions import UserError
from pydantic_ai.providers.openai_codex import OpenAICodexCredentials, OpenAICodexProvider
from typing_extensions import override


class CodexCliAuth(httpx2.Auth):
    """Let the CLI own renewal; every request reads its current complete credential set.

    Refreshing a snapshot here would spend the CLI's single-use refresh token without
    updating its store. Multiple Server/Worker processes must remain read-only peers.
    """

    def __init__(self, path: Path) -> None:
        self._path = path

    @override
    def auth_flow(self, request: httpx2.Request) -> Generator[httpx2.Request, httpx2.Response, None]:
        if request.url.scheme != "https" or request.url.host != "chatgpt.com" or request.url.port not in {None, 443}:
            message = "Codex subscription credentials require the default HTTPS service endpoint"
            raise UserError(message)
        try:
            credentials = OpenAICodexCredentials.from_codex_cli_auth(json.loads(self._path.read_text(encoding="utf-8")))
        except (OSError, ValueError, UserError):
            # Paths, JSON parse failures and validation inputs can contain secrets.
            message = "Codex CLI credentials are unavailable or malformed; update the CLI login"
            raise UserError(message) from None
        request.headers["Authorization"] = f"Bearer {credentials.access_token}"
        request.headers["chatgpt-account-id"] = credentials.account_id
        request.headers["originator"] = "pydantic-ai"
        yield request


def codex_cli_provider(resources: AsyncExitStack) -> OpenAICodexProvider:
    """Use the SDK wire dialect with externally managed, read-only authentication."""

    directory = Path(os.getenv("CODEX_HOME") or Path.home() / ".codex")
    http_client = httpx2.AsyncClient(auth=CodexCliAuth(directory / "auth.json"), follow_redirects=False)
    client = AsyncOpenAI(
        base_url="https://chatgpt.com/backend-api/codex",
        api_key="externally-managed",
        http_client=http_client,
    )
    resources.push_async_callback(client.close)
    # Supplying an SDK client explicitly opts out of Pydantic AI's in-memory refresh.
    return OpenAICodexProvider(openai_client=client)
