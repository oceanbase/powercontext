#!/usr/bin/env python3
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

# Adapted for WorkBuddy from the PowerContext Claude Code plugin hook
# (integrations/claude-code/plugins/powercontext/hooks/user_prompt_submit.py).

"""Recall memory and capture the current WorkBuddy prompt without blocking."""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping
from contextlib import suppress
from hashlib import sha256
from pathlib import Path
from time import monotonic
from typing import Any, Protocol, cast
from urllib.error import HTTPError
from urllib.request import Request

_HOOKS_ROOT = Path(__file__).resolve().parent
_PLUGIN_ROOT = _HOOKS_ROOT.parent
_SCRIPTS_ROOT = _PLUGIN_ROOT / "scripts"
sys.path.insert(0, str(_PLUGIN_ROOT))
sys.path.insert(0, str(_HOOKS_ROOT))
sys.path.insert(0, str(_SCRIPTS_ROOT))

import prepared_context as _prepared_context  # noqa: E402
from workbuddy_settings import WorkBuddyPluginSettings  # noqa: E402

if (_HOOKS_ROOT / "powercontext_scope_binding.py").is_file():
    from powercontext_scope_binding import bind_response_deadline, open_bounded, resolve_scope_id
else:
    from workspace_scope import bind_response_deadline, open_bounded, resolve_scope_id

_MAX_CONTEXT_BYTES = _prepared_context.MAX_CONTEXT_BYTES
_InvalidResponseError = _prepared_context.InvalidPreparedContextResponse
_validate_prepared_context = _prepared_context.validate_prepared_context
_MAX_RESPONSE_BYTES = 1_048_576
_MAX_SOURCE_LENGTH = 200_000
_READ_CHUNK_BYTES = 65_536
_USER_QUERY_OPEN = "<user_query>"
_USER_QUERY_CLOSE = "</user_query>"
_CLOSING_TAG_PREFIX = "</"
_HOST_BLOCK_OPEN_PREFIXES = (
    "<cb_summary",
    "<conversation_history_summary",
    "<system-reminder",
    "<task-notification",
)
# The request contract bounds a query in characters. The byte bound is the hook's own
# margin, so a query stays acceptable to a server that still measures the storage layer's
# limit as well.
_MAX_QUERY_CHARACTERS = 8192
_MAX_QUERY_BYTES = 8192
_REQUEST_HEADERS = {
    "Accept": "application/json",
    "Content-Type": "application/json",
    "User-Agent": "powercontext-workbuddy-plugin/0.1.0",
}


class _Response(Protocol):
    status: int

    def __enter__(self) -> _Response: ...

    def __exit__(self, *args: object) -> object: ...

    def read(self, amount: int = -1) -> bytes: ...


class _HttpStatusError(RuntimeError):
    def __init__(self, status: int) -> None:
        self.status = status
        super().__init__(f"PowerContext returned HTTP {status}")


class _ServerUnavailableError(RuntimeError):
    pass


def main(settings: WorkBuddyPluginSettings | None = None) -> int:
    """Process one WorkBuddy hook payload and fail open."""

    try:
        settings = WorkBuddyPluginSettings.from_environment() if settings is None else settings
        payload = _read_payload()
        if not _is_user_prompt_submit(payload.get("hook_event_name")):
            return 0
        prompt = _prompt(payload)
        cwd = payload.get("cwd")
        context = None
        if prompt is not None and prompt.strip() and isinstance(cwd, str):
            query = _recall_query(prompt)
            http_deadline = monotonic() + settings.http_budget_seconds
            try:
                scope_id = resolve_scope_id(
                    cwd,
                    session_id=_payload_identifier(payload, "session_id"),
                    settings=settings,
                    deadline=http_deadline,
                )
            except Exception:
                scope_id = None
            if scope_id:
                # Only the recall depends on a usable query. The request contract requires a
                # query of at least one non-whitespace character, so a turn that reduces to
                # nothing has nothing to retrieve. The Source still records what the host
                # submitted, so capture keeps its own guard.
                if query:
                    with suppress(Exception):
                        context = _recall_context(
                            query,
                            scope_id,
                            settings=settings,
                            deadline=http_deadline,
                        )

                if settings.capture_prompts and len(prompt) <= _MAX_SOURCE_LENGTH:
                    with suppress(Exception):
                        captured = _capture_prompt(
                            payload,
                            prompt=prompt,
                            cwd=cwd,
                            scope_id=scope_id,
                            settings=settings,
                            deadline=http_deadline,
                        )
                        if settings.flush_on_capture:
                            _flush_through(
                                scope_id,
                                _source_position(captured),
                                settings=settings,
                                deadline=http_deadline,
                            )

        json.dump(
            {
                "hookSpecificOutput": {
                    "hookEventName": "UserPromptSubmit",
                    "additionalContext": context or "",
                }
            },
            sys.stdout,
            separators=(",", ":"),
        )
        sys.stdout.write("\n")
    except Exception:
        return 0
    return 0


def _read_payload() -> dict[str, Any]:
    stdin = sys.stdin
    if hasattr(stdin, "buffer"):
        return cast(dict[str, Any], json.loads(stdin.buffer.read().decode("utf-8")))
    return cast(dict[str, Any], json.load(stdin))


def _prompt(payload: Mapping[str, object]) -> str | None:
    prompt = payload.get("prompt")
    if isinstance(prompt, str):
        return prompt
    fallback = payload.get("user_prompt")
    return fallback if isinstance(fallback, str) else None


def _recall_query(prompt: str) -> str:
    """Reduce a host prompt to the question PowerContext should retrieve for.

    WorkBuddy joins every user message of the session into one prompt, and each of those
    messages carries its injected context block, so the joined text describes the whole
    conversation rather than the turn being submitted. Retrieving with it both exceeds the
    request's query bound and dilutes the query with earlier turns, so the most recent
    ``<user_query>`` element the host wrapped is preferred: that element is the turn in hand.

    The element is only trusted where its boundaries match the host's wrapper, because the
    same tags appear verbatim wherever a user or a host-written summary quotes this code. A
    prompt no verified element can be read from falls back to the joined text, trimmed to the
    bounds above so the request stays acceptable to any server version.
    """

    extracted = _last_user_query(prompt)
    query = prompt if extracted is None else extracted
    bounded, truncated = _query_within_bounds(query.strip())
    if extracted is not None or truncated:
        _emit_query_event(
            source="user_query" if extracted is not None else "joined_prompt",
            truncated=truncated,
            characters=len(bounded),
            utf8_bytes=len(bounded.encode("utf-8")),
        )
    return bounded


def _last_user_query(prompt: str) -> str | None:
    """Read the most recent ``<user_query>`` element that carries the host's wrapper.

    Candidates are tried from the end of the prompt backwards. The most recent host-wrapped
    opener holds the turn in hand, and where the host appends messages that carry no turn of
    their own, such as task notifications, the turn before them is the most recent one the user
    submitted.
    """

    spans = _wrapper_spans(prompt)
    for index in range(len(spans) - 1, -1, -1):
        opened, closed = spans[index]
        # A literal pair inside the turn — one a fenced example puts at the start of a line —
        # can carry the wrapper boundaries too. The element that encloses it holds the turn, so
        # a candidate enclosed by an earlier one gives way to its container. The nearest
        # candidate is tested first, which keeps the scan linear for the shapes the host sends.
        if any(
            outer_opened < opened and outer_closed >= closed for outer_opened, outer_closed in reversed(spans[:index])
        ):
            continue
        return prompt[opened + len(_USER_QUERY_OPEN) : closed]
    return None


def _wrapper_spans(prompt: str) -> list[tuple[int, int]]:
    """Return every element that could be the host's wrapper, in the order it opens."""

    spans: list[tuple[int, int]] = []
    opened = prompt.find(_USER_QUERY_OPEN)
    while opened >= 0:
        # The host gives the element a line of its own. A pair written inside a sentence does
        # not start one, so it is never a candidate however it closes.
        if opened == 0 or prompt[opened - 1] == "\n":
            closed = _host_wrapper_close(prompt, opened)
            if closed is not None:
                spans.append((opened, closed))
        opened = prompt.find(_USER_QUERY_OPEN, opened + len(_USER_QUERY_OPEN))
    return spans


def _host_wrapper_close(prompt: str, opened: int) -> int | None:
    """Return the closing offset when the opener carries the host's wrapper boundaries.

    The host closes the element where the message ends, so what follows its closing tag is the
    start of the next host block or the end of the prompt. A pair quoted in prose or inside
    another element leaves that element's own closing tag, or nothing, on that side instead.

    The opener's own close decides, because a literal pair the turn contains closes before the
    turn does. Accepting any close that merely ends at a boundary would pair a literal opener
    with the host's closing tag and send the fragment between them as the query.
    """

    closed = _matched_user_query_close(prompt, opened)
    if closed is None:
        # An unclosed literal opener leaves the element unbalanced, and then no close belongs to
        # it. The host still closes the wrapper, so the first close that reaches a host boundary
        # is the boundary of the turn, and the text before it is what the user wrote.
        closed = _first_host_boundary_close(prompt, opened)
    if closed is None or not _ends_at_host_boundary(prompt, closed):
        return None
    return closed


def _matched_user_query_close(prompt: str, opened: int) -> int | None:
    """Return the close that balances the opener, counting nested pairs as turn content."""

    depth = 1
    index = opened + len(_USER_QUERY_OPEN)
    while index < len(prompt):
        next_open = prompt.find(_USER_QUERY_OPEN, index)
        next_close = prompt.find(_USER_QUERY_CLOSE, index)
        if next_close < 0:
            return None
        if 0 <= next_open < next_close:
            depth += 1
            index = next_open + len(_USER_QUERY_OPEN)
            continue
        depth -= 1
        if depth == 0:
            return next_close
        index = next_close + len(_USER_QUERY_CLOSE)
    return None


def _first_host_boundary_close(prompt: str, opened: int) -> int | None:
    """Return the first close after the opener that reaches a host boundary."""

    closed = prompt.find(_USER_QUERY_CLOSE, opened + len(_USER_QUERY_OPEN))
    while closed >= 0:
        if _ends_at_host_boundary(prompt, closed):
            return closed
        closed = prompt.find(_USER_QUERY_CLOSE, closed + len(_USER_QUERY_CLOSE))
    return None


def _ends_at_host_boundary(prompt: str, closed: int) -> bool:
    index = closed + len(_USER_QUERY_CLOSE)
    while index < len(prompt) and prompt[index].isspace():
        index += 1
    if index == len(prompt):
        return True
    if prompt[index] != "<":
        return False
    # A closing tag here means the element is nested in another one. That is where a block
    # quoting this markup keeps a turn it quotes, not where the host closes the submitted one.
    if prompt.startswith(_CLOSING_TAG_PREFIX, index):
        return False
    return any(prompt.startswith(prefix, index) for prefix in _HOST_BLOCK_OPEN_PREFIXES)


def _query_within_bounds(query: str) -> tuple[str, bool]:
    """Trim a query to the request bounds, keeping the most recent text."""

    if len(query) <= _MAX_QUERY_CHARACTERS and len(query.encode("utf-8")) <= _MAX_QUERY_BYTES:
        return query, False
    retained = query[-_MAX_QUERY_CHARACTERS:]
    encoded = retained.encode("utf-8")
    if len(encoded) > _MAX_QUERY_BYTES:
        retained = encoded[-_MAX_QUERY_BYTES:].decode("utf-8", errors="ignore")
    return retained, True


def _emit_query_event(*, source: str, truncated: bool, characters: int, utf8_bytes: int) -> None:
    """Report a query the hook had to reduce, since the recall itself stays silent."""

    event: dict[str, object] = {
        "component": "powercontext.workbuddy.recall",
        "event": "query_reduction",
        "source": source,
        "truncated": truncated,
        "characters": characters,
        "utf8_bytes": utf8_bytes,
    }
    sys.stderr.write(json.dumps(event, separators=(",", ":")) + "\n")


def _prepare_context(
    query: str,
    scope_id: str,
    *,
    settings: WorkBuddyPluginSettings,
    deadline: float,
) -> Mapping[str, object]:
    return _post_json(
        "/v1/context/prepare",
        {
            "scope_id": scope_id,
            "query": query,
            "max_bytes": _MAX_CONTEXT_BYTES,
            **({"assembly": settings.context_assembly} if settings.context_assembly is not None else {}),
        },
        settings=settings,
        deadline=deadline,
        expected_status=200,
    )


def _capture_prompt(
    payload: Mapping[str, object],
    *,
    prompt: str,
    cwd: str,
    scope_id: str,
    settings: WorkBuddyPluginSettings,
    deadline: float,
) -> Mapping[str, object]:
    session_id = _payload_identifier(payload, "session_id")
    prompt_id = _payload_identifier(payload, "prompt_id", "request_id")
    identity = "\0".join((scope_id, session_id or "", prompt_id or "", prompt))
    source_id = f"workbuddy-user-prompt:{sha256(identity.encode()).hexdigest()}"
    metadata = {
        "origin": "workbuddy",
        "event": "user_prompt_submit",
        "cwd": cwd,
    }
    if session_id is not None:
        metadata["session_id"] = session_id
    if prompt_id is not None:
        metadata["prompt_id"] = prompt_id
    return _post_json(
        "/v1/sources/content",
        {
            "scope_id": scope_id,
            "source_id": source_id,
            "content": prompt,
            "metadata": metadata,
        },
        settings=settings,
        deadline=deadline,
    )


def _flush_through(
    scope_id: str,
    position: int,
    *,
    settings: WorkBuddyPluginSettings,
    deadline: float,
) -> None:
    for _ in range(settings.flush_max_calls):
        result = _post_json(
            "/v1/memory/flush",
            {"scope_id": scope_id},
            settings=settings,
            deadline=deadline,
        )
        cursor = result.get("current_cursor")
        if isinstance(cursor, int) and not isinstance(cursor, bool) and cursor >= position:
            return
    raise RuntimeError


def _source_position(response: Mapping[str, object]) -> int:
    position = response.get("position")
    if not isinstance(position, int) or isinstance(position, bool) or position < 1:
        raise TypeError
    return position


def _payload_identifier(payload: Mapping[str, object], *names: str) -> str | None:
    for name in names:
        value = payload.get(name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _is_user_prompt_submit(value: object) -> bool:
    return isinstance(value, str) and value.replace("_", "").lower() == "userpromptsubmit"


def _post_json(
    path: str,
    payload: Mapping[str, object],
    *,
    settings: WorkBuddyPluginSettings,
    deadline: float,
    expected_status: int | None = None,
) -> Mapping[str, object]:
    request = Request(  # noqa: S310 - settings validation enforces the transport policy.
        f"{settings.server_url}{path}",
        data=json.dumps(payload, separators=(",", ":")).encode(),
        headers=_request_headers(settings),
        method="POST",
    )
    request_timeout = min(settings.request_timeout_seconds, _remaining_time(deadline))
    request_deadline = min(deadline, monotonic() + request_timeout)
    try:
        with open_bounded(request, timeout=request_timeout) as response:
            if expected_status is not None and response.status != expected_status:
                raise _HttpStatusError(response.status)
            result = json.loads(_read_response(response, deadline=request_deadline))
    except HTTPError as error:
        raise _HttpStatusError(error.code) from error
    except OSError as error:
        raise _ServerUnavailableError from error
    except ValueError as error:
        raise _InvalidResponseError from error
    if not isinstance(result, dict):
        raise _InvalidResponseError
    return cast(dict[str, object], result)


def _request_headers(settings: WorkBuddyPluginSettings) -> dict[str, str]:
    headers = dict(_REQUEST_HEADERS)
    if settings.authorization is not None:
        headers["Authorization"] = settings.authorization
    return headers


def _read_response(response: _Response, *, deadline: float) -> bytes:
    """Read one response under a wall-clock deadline and a hard size bound."""

    bind_response_deadline(response, deadline)
    content = bytearray()
    while True:
        _set_response_timeout(response, _remaining_time(deadline))
        remaining_bytes = _MAX_RESPONSE_BYTES + 1 - len(content)
        chunk = response.read(min(_READ_CHUNK_BYTES, remaining_bytes))
        if not chunk:
            return bytes(content)
        content.extend(chunk)
        if len(content) > _MAX_RESPONSE_BYTES:
            raise ValueError("PowerContext response exceeds the hook limit")  # noqa: TRY003


def _remaining_time(deadline: float) -> float:
    remaining = deadline - monotonic()
    if remaining <= 0:
        raise TimeoutError
    return remaining


def _set_response_timeout(response: object, timeout: float) -> None:
    """Tighten urllib's socket timeout before each bounded read."""

    raw = getattr(getattr(response, "fp", None), "raw", None)
    sock = getattr(raw, "_sock", None)
    settimeout = getattr(sock, "settimeout", None)
    if settimeout is not None:
        settimeout(timeout)


def _recall_context(
    query: str,
    scope_id: str,
    *,
    settings: WorkBuddyPluginSettings,
    deadline: float,
) -> str | None:
    try:
        prepared = _validate_prepared_context(_prepare_context(query, scope_id, settings=settings, deadline=deadline))
    except _HttpStatusError as error:
        if error.status == 401:
            outcome = "authentication_failed"
        elif error.status == 404:
            outcome = "version_mismatch"
        elif error.status == 503:
            outcome = "server_unavailable"
        else:
            outcome = "invalid_response"
        _emit_context_event(outcome, http_status=error.status)
        return None
    except (_ServerUnavailableError, TimeoutError):
        _emit_context_event("server_unavailable")
        return None
    except _InvalidResponseError:
        _emit_context_event("invalid_response")
        return None

    status = cast(str, prepared["status"])
    content_bytes = cast(int, prepared["content_bytes"])
    if status == "empty":
        _emit_context_event("empty", http_status=200, context_status=status, content_bytes=content_bytes)
        return None
    return cast(str, prepared["content"])


def _emit_context_event(
    outcome: str,
    *,
    http_status: int | None = None,
    context_status: str | None = None,
    content_bytes: int | None = None,
) -> None:
    event: dict[str, object] = {
        "component": "powercontext.workbuddy.recall",
        "event": "context_prepare",
        "outcome": outcome,
    }
    if http_status is not None:
        event["http_status"] = http_status
    if context_status is not None:
        event["context_status"] = context_status
    if content_bytes is not None:
        event["content_bytes"] = content_bytes
    sys.stderr.write(json.dumps(event, separators=(",", ":")) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
