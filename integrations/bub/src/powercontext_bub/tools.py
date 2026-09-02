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

"""PowerContext tools exposed to Bub agents."""

from __future__ import annotations

import json
from contextlib import AbstractAsyncContextManager
from typing import Any, TypedDict, cast

from bub import tool
from pydantic import BaseModel, Field

from powercontext.client import PowerContextClient
from powercontext.http import PrepareContextRequest, RememberMemoryRequest, ScopeBindingKey, SearchMemoryRequest

from .plugin import STATE_KEY, open_client
from .scope import resolve_scope_id


class ToolSettings(TypedDict):
    base_url: str
    scope_id: str | None
    explicit_scope_id: str | None
    binding_keys: list[ScopeBindingKey]
    timeout: float
    trust_transport_security: bool


class SearchInput(BaseModel):
    query: str = Field(description="Question or topic to recall from durable project memory.")
    limit: int = Field(5, ge=1, le=20, description="Maximum number of memory entries to return.")


class RememberInput(BaseModel):
    text: str = Field(description="Durable decision, preference, constraint, or procedure to remember.")
    kind: str = Field("agent-note", description="Stable category for the memory entry.")
    reason: str | None = Field(None, description="Why this information should remain available across sessions.")


@tool(context=True, name="powercontext.search", model=SearchInput)
async def search_memory(param: SearchInput, *, context: Any) -> str:
    """Search durable PowerContext memory."""

    settings = _settings(context)
    async with _client(settings) as client:
        scope_id = await _scope_id(settings, client)
        request = SearchMemoryRequest(scope_id=scope_id, query=param.query, limit=param.limit)
        response = await client.search_memory(request)
    if not response.hits:
        return "(no matching PowerContext memory)"

    return json.dumps(
        [
            {
                "matched_by": [value.value for value in hit.matched_by],
                "score": hit.score,
                "text": hit.text,
            }
            for hit in response.hits
        ],
        ensure_ascii=True,
        sort_keys=True,
    )


@tool(context=True, name="powercontext.remember", model=RememberInput)
async def remember_memory(param: RememberInput, *, context: Any) -> str:
    """Save one explicit durable memory for later agent sessions."""

    settings = _settings(context)
    async with _client(settings) as client:
        scope_id = await _scope_id(settings, client)
        request = RememberMemoryRequest(
            scope_id=scope_id,
            kind=param.kind,
            text=param.text,
            reason=param.reason,
        )
        response = await client.remember_memory(request)
    if response.entry is None:
        return "(PowerContext accepted the memory without an entry receipt)"
    return f"Remembered {response.entry.kind}: {response.entry.text}"


@tool(context=True, name="powercontext.context")
async def prepare_context(query: str, *, context: Any) -> str:
    """Prepare a bounded PowerContext payload for a new question."""

    settings = _settings(context)
    async with _client(settings) as client:
        scope_id = await _scope_id(settings, client)
        request = PrepareContextRequest(scope_id=scope_id, query=query)
        response = await client.prepare_context(request)
    return response.content or "(no relevant PowerContext context)"


def _settings(context: Any) -> ToolSettings:
    settings = context.state[STATE_KEY]
    return cast(ToolSettings, settings)


def _client(settings: ToolSettings) -> AbstractAsyncContextManager[PowerContextClient]:
    return open_client(
        settings["base_url"],
        timeout=settings["timeout"],
        trust_transport_security=settings["trust_transport_security"],
    )


async def _scope_id(settings: ToolSettings, client: PowerContextClient) -> str:
    cached_scope_id = settings["scope_id"]
    if cached_scope_id is not None:
        return cached_scope_id
    resolved_scope_id = await resolve_scope_id(
        client,
        explicit_scope_id=settings["explicit_scope_id"],
        binding_keys=settings["binding_keys"],
    )
    settings["scope_id"] = resolved_scope_id
    return resolved_scope_id
