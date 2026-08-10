"""PowerContext tools exposed to Bub agents."""

from __future__ import annotations

import json
from typing import Any, TypedDict, cast

from bub import tool
from pydantic import BaseModel, Field

from powercontext.client import PowerContextClient
from powercontext.http import PrepareContextRequest, RememberMemoryRequest, SearchMemoryRequest

from .plugin import STATE_KEY


class ToolSettings(TypedDict):
    base_url: str
    scope_id: str
    timeout: float


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
    request = SearchMemoryRequest(scope_id=settings["scope_id"], query=param.query, limit=param.limit)
    async with PowerContextClient(settings["base_url"], timeout=settings["timeout"]) as client:
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
    request = RememberMemoryRequest(
        scope_id=settings["scope_id"],
        kind=param.kind,
        text=param.text,
        reason=param.reason,
    )
    async with PowerContextClient(settings["base_url"], timeout=settings["timeout"]) as client:
        response = await client.remember_memory(request)
    if response.entry is None:
        return "(PowerContext accepted the memory without an entry receipt)"
    return f"Remembered {response.entry.kind}: {response.entry.text}"


@tool(context=True, name="powercontext.context")
async def prepare_context(query: str, *, context: Any) -> str:
    """Prepare a bounded PowerContext payload for a new question."""

    settings = _settings(context)
    request = PrepareContextRequest(scope_id=settings["scope_id"], query=query)
    async with PowerContextClient(settings["base_url"], timeout=settings["timeout"]) as client:
        response = await client.prepare_context(request)
    return response.content or "(no relevant PowerContext context)"


def _settings(context: Any) -> ToolSettings:
    settings = context.state[STATE_KEY]
    return cast(ToolSettings, settings)
