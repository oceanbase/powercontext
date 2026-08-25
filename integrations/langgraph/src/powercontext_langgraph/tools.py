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

"""PowerContext Memory tools for LangGraph agents.

Each tool talks to the Server through the public client. On a client failure the error is returned as the tool
result rather than raised, because a model that invoked a search tool and received an empty result would conclude
that no relevant memory exists. Returning the error lets the model retry or select a different strategy.
"""

from __future__ import annotations

import json

from langchain_core.tools import BaseTool, tool
from pydantic import BaseModel, Field, ValidationError

from powercontext.client import ClientError
from powercontext.http import PrepareContextRequest, RememberMemoryRequest, SearchMemoryRequest

from .client import open_client, resolve_config
from .runtime import current_scope

# The public ``query`` contract caps a search or prepare request at this many characters. A model can emit more,
# so the query is clamped before the request is built, keeping the tool best-effort rather than raising.
_MAX_QUERY_CHARS = 8192


class _SearchInput(BaseModel):
    query: str = Field(description="Question or topic to recall from durable project memory.")
    limit: int = Field(5, ge=1, le=20, description="Maximum number of memory entries to return.")


class _RememberInput(BaseModel):
    text: str = Field(description="Durable decision, preference, constraint, or procedure to remember.")
    kind: str = Field("agent-note", description="Stable category for the memory entry.")
    reason: str | None = Field(None, description="Why this should remain available across sessions.")


@tool("powercontext_search", args_schema=_SearchInput)
async def powercontext_search(query: str, limit: int = 5) -> str:
    """Search durable PowerContext memory for a question or topic."""

    try:
        config = resolve_config(current_scope())
        request = SearchMemoryRequest(scope_id=config.scope_id, query=query[:_MAX_QUERY_CHARS], limit=limit)
        async with open_client(config) as client:
            response = await client.search_memory(request)
    except ValidationError:
        return _invalid_arguments()
    except ClientError as exc:
        return _error(exc)
    except Exception:
        return _unavailable()
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


@tool("powercontext_remember", args_schema=_RememberInput)
async def powercontext_remember(text: str, kind: str = "agent-note", reason: str | None = None) -> str:
    """Save one explicit durable memory for later agent sessions."""

    try:
        config = resolve_config(current_scope())
        request = RememberMemoryRequest(scope_id=config.scope_id, kind=kind, text=text, reason=reason)
        async with open_client(config) as client:
            response = await client.remember_memory(request)
    except ValidationError:
        return _invalid_arguments()
    except ClientError as exc:
        return _error(exc)
    except Exception:
        return _unavailable()
    if response.entry is None:
        return "(PowerContext accepted the memory without an entry receipt)"
    return f"Remembered {response.entry.kind}: {response.entry.text}"


@tool("powercontext_context")
async def powercontext_context(query: str) -> str:
    """Prepare a bounded PowerContext payload for a new question."""

    try:
        config = resolve_config(current_scope())
        request = PrepareContextRequest(
            scope_id=config.scope_id, query=query[:_MAX_QUERY_CHARS], max_bytes=config.max_bytes
        )
        async with open_client(config) as client:
            response = await client.prepare_context(request)
    except ValidationError:
        return _invalid_arguments()
    except ClientError as exc:
        return _error(exc)
    except Exception:
        return _unavailable()
    return response.content or "(no relevant PowerContext context)"


def powercontext_tools() -> list[BaseTool]:
    """Return the PowerContext Memory tools for use in a ``ToolNode`` or any tool list."""

    return [powercontext_search, powercontext_remember, powercontext_context]


def _error(exc: ClientError) -> str:
    return f"(PowerContext unavailable: {type(exc).__name__})"


def _invalid_arguments() -> str:
    # A model-supplied argument fell outside the public request contract (e.g. an empty or over-length field).
    # Report it as a tool result so the model can retry with corrected arguments rather than aborting the graph.
    return "(PowerContext rejected the request: an argument was empty or out of range)"


def _unavailable() -> str:
    # A configuration or unexpected fault (an unresolvable scope, a malformed base URL, or any other client error
    # outside ClientError) prevented the call. Report it as a tool result so the model can proceed rather than
    # aborting the graph.
    return "(PowerContext unavailable: the request could not be completed)"
