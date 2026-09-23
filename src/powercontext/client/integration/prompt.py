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

"""Shared prompt operations; adapters supply only native event identities."""

from __future__ import annotations

from collections.abc import Mapping
from hashlib import sha256
from time import monotonic
from typing import Any

from powercontext.client.checkpoints import source_position
from powercontext.client.integration import native
from powercontext.client.integration.diagnostics import Events
from powercontext.client.prepared_context import MAX_CONTEXT_BYTES


def prepare(query: str, scope_id: str, *, settings: Any, deadline: float) -> Any:
    return native.request_json(
        "/v1/context/prepare",
        {
            "scope_id": scope_id,
            "query": query,
            "max_bytes": MAX_CONTEXT_BYTES,
            **({"assembly": settings.context_assembly} if settings.context_assembly is not None else {}),
        },
        settings=settings,
        deadline=deadline,
    )


def capture(
    host: str,
    prompt: str,
    cwd: str,
    scope_id: str,
    session_id: str | None,
    event_id: str | None,
    *,
    event_id_field: str,
    settings: Any,
    deadline: float,
) -> Any:
    identity = "\0".join((scope_id, session_id or "", event_id or "", prompt))
    metadata = {"origin": host, "event": "user_prompt_submit", "cwd": cwd}
    if session_id is not None:
        metadata["session_id"] = session_id
    if event_id is not None:
        metadata[event_id_field] = event_id
    return native.request_json(
        "/v1/sources/content",
        {
            "scope_id": scope_id,
            "source_id": f"{host}-user-prompt:{sha256(identity.encode()).hexdigest()}",
            "content": prompt,
            "metadata": metadata,
        },
        settings=settings,
        deadline=deadline,
    )


def flush_through(
    scope_id: str,
    position: int,
    *,
    settings: Any,
    deadline: float,
) -> None:
    previous = -1
    for _ in range(settings.flush_max_calls):
        result = native.request_json("/v1/memory/flush", {"scope_id": scope_id}, settings=settings, deadline=deadline)
        cursor = result["current_cursor"]
        if cursor >= position:
            return
        if cursor <= previous:
            break
        previous = cursor
    raise RuntimeError


def read_payload() -> dict[str, Any]:
    import json
    import sys

    stream = getattr(sys.stdin, "buffer", sys.stdin)
    return json.loads(stream.read())


def is_prompt_event(value: object) -> bool:
    return isinstance(value, str) and value.replace("_", "").lower() == "userpromptsubmit"


def identifier(payload: Mapping[str, object], *names: str) -> str | None:
    for name in names:
        value = payload.get(name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def deadline(settings: Any) -> float:
    return monotonic() + settings.http_budget_seconds


def _failure(events: Events, operation: str, error: Exception) -> None:
    details: dict[str, Any] = {}
    outcome = "invalid_response"
    if isinstance(error, native.HttpStatusError):
        details.update(http_status=error.status, error_code=error.code)
        if error.status == 401:
            outcome = "authentication_failed"
        elif error.status == 503:
            outcome = "server_unavailable"
        elif error.status == 404 and error.code is None and error.path == "/v1/context/prepare":
            outcome = "version_mismatch"
    elif isinstance(error, native.UnavailableError | TimeoutError):
        outcome = "server_unavailable"
    if outcome == "server_unavailable":
        details["recovery"] = "powercontext doctor"
    events.emit(outcome, event=operation, **details)


def recall(query: str, scope_id: str, *, settings: Any, deadline: float, events: Events) -> str | None:
    prepared = prepare(query, scope_id, settings=settings, deadline=deadline)
    if prepared["status"] == "empty":
        events.emit("empty", http_status=200, context_status="empty", content_bytes=0)
        return None
    return prepared["content"]


def run(
    host: str,
    query: str,
    cwd: str,
    scope_id: str,
    session_id: str | None,
    event_id: str | None,
    *,
    event_id_field: str,
    settings: Any,
    deadline: float,
    events: Events,
) -> str | None:
    """Recall before capture; flush only an acknowledged Source within the same budget."""

    context = None
    try:
        context = recall(query, scope_id, settings=settings, deadline=deadline, events=events)
    except Exception as error:
        _failure(events, "context_prepare", error)
    if not settings.capture_prompts or len(query) > 200_000:
        return context
    try:
        captured = capture(
            host,
            query,
            cwd,
            scope_id,
            session_id,
            event_id,
            event_id_field=event_id_field,
            settings=settings,
            deadline=deadline,
        )
        position = source_position(captured)
    except Exception as error:
        _failure(events, "capture_source", error)
        return context
    if settings.flush_on_capture and position is not None:
        try:
            flush_through(scope_id, position, settings=settings, deadline=deadline)
        except Exception as error:
            _failure(events, "flush_memory", error)
    return context
