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

"""Recall context for a submitted prompt and capture the prompt, without blocking the host."""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass, field
from hashlib import sha256
from http.client import HTTPException
from time import monotonic
from typing import Protocol, cast
from urllib.error import HTTPError
from urllib.request import Request

from .host_profile import HostProfile
from .prepared_context import (
    MAX_CONTEXT_BYTES,
    InvalidPreparedContextResponse,
    bounded_recall_query,
    validate_prepared_context,
)
from .prompt_privacy import scrub_secrets
from .scope_binding import ScopeBindingSettings, resolve_scope_id
from .scope_binding_errors import (
    ScopeBindingRejectedError,
    ScopeBindingStatusError,
    ScopeBindingUnavailableError,
)
from .transport import bind_response_deadline, complete_body, open_bounded

_MAX_RESPONSE_BYTES = 1_048_576
_MAX_SOURCE_LENGTH = 200_000
_READ_CHUNK_BYTES = 65_536
_FAILURE_OUTCOMES = frozenset({"authentication_failed", "version_mismatch", "server_unavailable", "invalid_response"})


class _ReadableResponse(Protocol):
    def read(self, n: int = -1) -> bytes: ...


class _HttpStatusError(RuntimeError):
    def __init__(self, status: int, path: str = "/v1/context/prepare", code: str | None = None) -> None:
        self.status = status
        self.path = path
        self.code = code
        super().__init__(f"PowerContext returned HTTP {status}")


class _ServerUnavailableError(RuntimeError):
    pass


_COMPATIBILITY_OR_AVAILABILITY_PATHS = frozenset({
    "/health/live",
    "/health/ready",
    "/v1/capabilities",
    "/v1/context/prepare",
    "/v1/scope-bindings/resolve",
})
_TASK_OUTCOME_GUIDANCE = (
    "PowerContext task outcome guidance: Only when the task is actually complete or explicitly interrupted, "
    "call the PowerContext record_task_outcome tool if available. Skip it on intermediate turns. "
    "A prompt boundary, Stop, or session end alone is insufficient. Report the actual status and omit secrets."
)
_AUTOMATIC_OPERATION_PATHS = {
    "scope_resolve": "/v1/scope-bindings/resolve",
    "context_prepare": "/v1/context/prepare",
    "capture_source": "/v1/sources/content",
    "flush_memory": "/v1/memory/flush",
}


@dataclass(frozen=True)
class UserPromptEvent:
    """One UserPromptSubmit event, translated from the host's payload.

    ``prompt`` and ``cwd`` are ``None`` when the payload carries no string for them. ``prompt_id`` is the host's
    identifier for the prompt, which a capture records in the metadata field that the host's profile names. The prompt
    and the working directory stay out of ``repr``.
    """

    prompt: str | None = field(repr=False)
    cwd: str | None = field(repr=False)
    session_id: str | None
    prompt_id: str | None


@dataclass(frozen=True)
class PromptSettings:
    """The host settings that the prompt flow uses, as plain values.

    ``scope_binding`` resolves the prompt's Scope, and the flow's own requests use its Server, credential, and request
    timeout. ``prompt_recall`` and ``capture_prompts`` turn recall and capture on separately; with both off the flow
    sends nothing.
    """

    scope_binding: ScopeBindingSettings
    include_code: bool
    context_assembly: Mapping[str, object] | None
    prompt_recall: bool
    capture_prompts: bool
    flush_on_capture: bool
    flush_max_calls: int


@dataclass(frozen=True)
class SubmittedPrompt:
    """What one submitted prompt leaves for the host to report.

    ``context`` is the prepared context to inject, if any. ``diagnostics`` are the failure diagnostics for the host to
    show its user, already throttled; the flow writes every other diagnostic to stderr itself. ``scrubbed_prompt`` is
    the prompt with recognized credentials replaced, which the recall query was cut from. ``scope_id`` is ``None`` when
    the Scope could not be resolved.
    """

    scope_id: str | None
    scrubbed_prompt: str
    context: str | None
    diagnostics: list[dict[str, object]]

    @property
    def agent_context(self) -> str | None:
        """Place trusted guidance before the unchanged historical context once a Scope is resolved."""
        if self.scope_id is None:
            return None
        if self.context:
            return f"{_TASK_OUTCOME_GUIDANCE}\n\n{self.context}"
        return _TASK_OUTCOME_GUIDANCE


def payload_identifier(payload: Mapping[str, object], *names: str) -> str | None:
    """Return the first of the named payload fields that holds a non-blank string, without surrounding whitespace."""

    for name in names:
        value = payload.get(name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def run_user_prompt_submit(
    host: HostProfile,
    event: UserPromptEvent,
    settings: PromptSettings,
    *,
    deadline: float,
) -> SubmittedPrompt | None:
    """Recall context for the event's prompt and capture the prompt, under one HTTP deadline.

    With recall and capture both off, the flow returns ``None`` without reporting anything. An event without a prompt
    or a working directory, or with a blank prompt, is reported as skipped and returns ``None``. A failure to resolve
    the Scope becomes a ``scope_resolve`` diagnostic, and the flow then neither recalls nor captures. Otherwise the
    session's bootstrap receipt is cleared, and goes with the recall when recall is on. Recall, capture, and flush fail
    independently: the failures that the flow recognizes become diagnostics, and any other recall failure only loses
    the context. A prompt in which credentials were recognized is not captured.
    """

    if not (settings.prompt_recall or settings.capture_prompts):
        return None
    prompt = event.prompt
    cwd = event.cwd
    if prompt is None or not prompt.strip() or cwd is None:
        _emit_context_event(host, "skipped")
        return None
    emitted_diagnostics: set[str] = set()
    diagnostic_events: list[dict[str, object]] = []
    scrubbed_prompt = scrub_secrets(prompt, settings.scope_binding.authorization)
    try:
        scope_id = resolve_scope_id(
            host,
            cwd,
            session_id=event.session_id,
            settings=settings.scope_binding,
            deadline=deadline,
        )
    except Exception as error:
        _emit_failure_event(
            host,
            "scope_resolve",
            _scope_resolution_failure(error),
            emitted_diagnostics=emitted_diagnostics,
            diagnostic_events=diagnostic_events,
        )
        return SubmittedPrompt(
            scope_id=None,
            scrubbed_prompt=scrubbed_prompt,
            context=None,
            diagnostics=diagnostic_events,
        )
    bootstrap_receipt_id = host.bootstrap_state.load_receipt(event.session_id, scope_id)
    host.bootstrap_state.clear_receipt(event.session_id)
    query = bounded_recall_query(scrubbed_prompt)
    context: str | None = None
    # Behind the gateway, Core rejects any prepared-context block that the gateway did not inject itself.
    if settings.prompt_recall:
        with suppress(Exception):
            context = _recall_context(
                host,
                query,
                scope_id,
                settings=settings,
                deadline=deadline,
                bootstrap_receipt_id=bootstrap_receipt_id,
                emitted_diagnostics=emitted_diagnostics,
                diagnostic_events=diagnostic_events,
            )
    if scrubbed_prompt != prompt:
        with suppress(Exception):
            _emit_context_event(host, "skipped", event_name="capture_source", reason="sensitive_content")
    else:
        _capture_and_flush(
            host,
            event,
            prompt=prompt,
            cwd=cwd,
            scope_id=scope_id,
            settings=settings,
            deadline=deadline,
            emitted_diagnostics=emitted_diagnostics,
            diagnostic_events=diagnostic_events,
        )
    return SubmittedPrompt(
        scope_id=scope_id,
        scrubbed_prompt=scrubbed_prompt,
        context=context,
        diagnostics=diagnostic_events,
    )


def _http_failure_outcome(error: _HttpStatusError, *, operation: str) -> str | None:
    if error.status == 401:
        return "authentication_failed"
    if error.status == 404 and error.path in _COMPATIBILITY_OR_AVAILABILITY_PATHS and error.code is None:
        return "version_mismatch"
    if error.status == 503:
        return "server_unavailable"
    if error.status in {404, 409, 422}:
        return "invalid_response" if _AUTOMATIC_OPERATION_PATHS.get(operation) == error.path else None
    return "invalid_response"


def _decode_error_code(raw: bytes) -> str | None:
    try:
        decoded = json.loads(raw.decode("utf-8"))
    except (ValueError, RecursionError):
        # Besides JSONDecodeError, json raises ValueError for an over-long integer and RecursionError for deep nesting.
        return None
    if not isinstance(decoded, dict):
        return None
    error = decoded.get("error")
    if not isinstance(error, dict):
        return None
    code = error.get("code")
    return code if isinstance(code, str) else None


def _capture_and_flush(
    host: HostProfile,
    event: UserPromptEvent,
    *,
    prompt: str,
    cwd: str,
    scope_id: str,
    settings: PromptSettings,
    deadline: float,
    emitted_diagnostics: set[str],
    diagnostic_events: list[dict[str, object]],
) -> None:
    if not settings.capture_prompts or len(prompt) > _MAX_SOURCE_LENGTH:
        return
    try:
        captured = _capture_prompt(
            host,
            event,
            prompt=prompt,
            cwd=cwd,
            scope_id=scope_id,
            settings=settings,
            deadline=deadline,
        )
        position = _source_position(captured)
    except Exception as error:
        _emit_failure_event(
            host,
            "capture_source",
            error,
            emitted_diagnostics=emitted_diagnostics,
            diagnostic_events=diagnostic_events,
        )
        return
    if not settings.flush_on_capture:
        return
    try:
        _flush_through(host, scope_id, position, settings=settings, deadline=deadline)
    except Exception as error:
        _emit_failure_event(
            host,
            "flush_memory",
            error,
            emitted_diagnostics=emitted_diagnostics,
            diagnostic_events=diagnostic_events,
        )


def _prepare_context(
    host: HostProfile,
    query: str,
    scope_id: str,
    *,
    settings: PromptSettings,
    deadline: float,
    bootstrap_receipt_id: str | None = None,
) -> Mapping[str, object]:
    return _post_json(
        "/v1/context/prepare",
        {
            "scope_id": scope_id,
            "query": query,
            "max_bytes": MAX_CONTEXT_BYTES,
            **({"include_code": True} if settings.include_code else {}),
            **({"assembly": settings.context_assembly} if settings.context_assembly is not None else {}),
            **({"bootstrap_receipt_id": bootstrap_receipt_id} if bootstrap_receipt_id is not None else {}),
        },
        host=host,
        settings=settings,
        deadline=deadline,
        expected_status=200,
    )


def _capture_prompt(
    host: HostProfile,
    event: UserPromptEvent,
    *,
    prompt: str,
    cwd: str,
    scope_id: str,
    settings: PromptSettings,
    deadline: float,
) -> Mapping[str, object]:
    identity = "\0".join((scope_id, event.session_id or "", event.prompt_id or "", prompt))
    source_id = f"{host.integration}-user-prompt:{sha256(identity.encode()).hexdigest()}"
    metadata = {
        "origin": host.integration,
        "event": "user_prompt_submit",
        "cwd": cwd,
    }
    if event.session_id is not None:
        metadata["session_id"] = event.session_id
    if event.prompt_id is not None:
        metadata[host.prompt_id_field] = event.prompt_id
    return _post_json(
        "/v1/sources/content",
        {
            "scope_id": scope_id,
            "source_id": source_id,
            "content": prompt,
            "metadata": metadata,
        },
        host=host,
        settings=settings,
        deadline=deadline,
    )


def _flush_through(
    host: HostProfile,
    scope_id: str,
    position: int,
    *,
    settings: PromptSettings,
    deadline: float,
) -> None:
    for _ in range(settings.flush_max_calls):
        result = _post_json(
            "/v1/memory/flush",
            {"scope_id": scope_id},
            host=host,
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


def _post_json(
    path: str,
    payload: Mapping[str, object],
    *,
    host: HostProfile,
    settings: PromptSettings,
    deadline: float,
    expected_status: int | None = None,
) -> Mapping[str, object]:
    server = settings.scope_binding
    headers = {
        "X-PowerContext-Dream-Contract": "2",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": host.user_agent,
    }
    if server.authorization is not None:
        headers["Authorization"] = server.authorization
    request = Request(  # noqa: S310 - the host's settings validate the configured transport.
        f"{server.server_url}{path}",
        data=json.dumps(payload, separators=(",", ":")).encode(),
        headers=headers,
        method="POST",
    )
    request_deadline = deadline
    try:
        request_timeout = min(server.request_timeout_seconds, _remaining_time(deadline))
        request_deadline = min(deadline, monotonic() + request_timeout)
        with open_bounded(request, timeout=request_timeout) as response:
            if expected_status is not None and response.status != expected_status:
                code = _decode_error_code(_read_response(response, deadline=request_deadline))
                raise _HttpStatusError(response.status, path, code)
            result = json.loads(_read_response(response, deadline=request_deadline))
    except HTTPError as error:
        try:
            error_body = _read_response(error, deadline=request_deadline, chunk_bytes=1)
        except (OSError, HTTPException) as failure:
            # A stalled, reset, or truncated body is a transport failure, as it is for a successful response.
            raise _ServerUnavailableError from failure
        except ValueError as failure:
            raise InvalidPreparedContextResponse from failure
        raise _HttpStatusError(error.code, path, _decode_error_code(error_body)) from error
    except (OSError, HTTPException) as error:
        # http.client reports a truncated or malformed response as HTTPException, not OSError.
        raise _ServerUnavailableError from error
    except (ValueError, RecursionError) as error:
        raise InvalidPreparedContextResponse from error
    if not isinstance(result, dict):
        raise InvalidPreparedContextResponse
    return cast(dict[str, object], result)


def _read_response(
    response: _ReadableResponse,
    *,
    deadline: float,
    chunk_bytes: int = _READ_CHUNK_BYTES,
) -> bytes:
    """Read one response under a wall-clock deadline and a hard size bound."""

    bind_response_deadline(response, deadline)
    content = bytearray()
    while True:
        _set_response_timeout(response, _remaining_time(deadline))
        remaining_bytes = _MAX_RESPONSE_BYTES + 1 - len(content)
        chunk = response.read(min(chunk_bytes, remaining_bytes))
        if not chunk:
            return complete_body(response, bytes(content))
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
    host: HostProfile,
    query: str,
    scope_id: str,
    *,
    settings: PromptSettings,
    deadline: float,
    bootstrap_receipt_id: str | None = None,
    emitted_diagnostics: set[str] | None = None,
    diagnostic_events: list[dict[str, object]] | None = None,
) -> str | None:
    try:
        if bootstrap_receipt_id is None:
            response = _prepare_context(host, query, scope_id, settings=settings, deadline=deadline)
        else:
            response = _prepare_context(
                host,
                query,
                scope_id,
                settings=settings,
                deadline=deadline,
                bootstrap_receipt_id=bootstrap_receipt_id,
            )
        prepared = validate_prepared_context(response)
    except _HttpStatusError as error:
        outcome = _http_failure_outcome(error, operation="context_prepare")
        if outcome is not None:
            _emit_context_event(
                host,
                outcome,
                http_status=error.status,
                error_code=error.code,
                recovery="powercontext doctor" if outcome == "server_unavailable" else None,
                emitted_diagnostics=emitted_diagnostics,
                diagnostic_events=diagnostic_events,
            )
        return None
    except (_ServerUnavailableError, TimeoutError):
        _emit_context_event(
            host,
            "server_unavailable",
            recovery="powercontext doctor",
            emitted_diagnostics=emitted_diagnostics,
            diagnostic_events=diagnostic_events,
        )
        return None
    except InvalidPreparedContextResponse:
        _emit_context_event(
            host,
            "invalid_response",
            emitted_diagnostics=emitted_diagnostics,
            diagnostic_events=diagnostic_events,
        )
        return None

    status = cast(str, prepared["status"])
    content_bytes = cast(int, prepared["content_bytes"])
    if status == "empty":
        _emit_context_event(
            host,
            "empty",
            http_status=200,
            context_status=status,
            content_bytes=content_bytes,
            diagnostic_events=diagnostic_events,
        )
        return None
    return cast(str, prepared["content"])


def _emit_context_event(
    host: HostProfile,
    outcome: str,
    *,
    event_name: str = "context_prepare",
    http_status: int | None = None,
    error_code: str | None = None,
    context_status: str | None = None,
    content_bytes: int | None = None,
    recovery: str | None = None,
    reason: str | None = None,
    emitted_diagnostics: set[str] | None = None,
    diagnostic_events: list[dict[str, object]] | None = None,
) -> None:
    if emitted_diagnostics is not None and outcome in _FAILURE_OUTCOMES:
        key = outcome
        if key in emitted_diagnostics:
            return
        emitted_diagnostics.add(key)
        if not host.diagnostics.should_emit(outcome):
            return
    event: dict[str, object] = {
        "component": f"{host.component}.recall",
        "event": event_name,
        "outcome": outcome,
    }
    if http_status is not None:
        event["http_status"] = http_status
    if error_code is not None:
        event["error_code"] = error_code
    if context_status is not None:
        event["context_status"] = context_status
    if content_bytes is not None:
        event["content_bytes"] = content_bytes
    event.update({key: value for key, value in (("recovery", recovery), ("reason", reason)) if value is not None})
    if diagnostic_events is None or outcome not in _FAILURE_OUTCOMES:
        sys.stderr.write(json.dumps(event, separators=(",", ":")) + "\n")
    else:
        diagnostic_events.append(event)


def _emit_failure_event(
    host: HostProfile,
    event_name: str,
    error: BaseException,
    *,
    emitted_diagnostics: set[str],
    diagnostic_events: list[dict[str, object]] | None = None,
) -> None:
    if isinstance(error, _HttpStatusError):
        outcome = _http_failure_outcome(error, operation=event_name)
        if outcome is not None:
            _emit_context_event(
                host,
                outcome,
                event_name=event_name,
                http_status=error.status,
                error_code=error.code,
                recovery="powercontext doctor" if outcome == "server_unavailable" else None,
                emitted_diagnostics=emitted_diagnostics,
                diagnostic_events=diagnostic_events,
            )
    elif isinstance(error, (_ServerUnavailableError, TimeoutError)):
        _emit_context_event(
            host,
            "server_unavailable",
            event_name=event_name,
            recovery="powercontext doctor",
            emitted_diagnostics=emitted_diagnostics,
            diagnostic_events=diagnostic_events,
        )
    else:
        _emit_context_event(
            host,
            "invalid_response",
            event_name=event_name,
            emitted_diagnostics=emitted_diagnostics,
            diagnostic_events=diagnostic_events,
        )


def _scope_resolution_failure(error: Exception) -> Exception:
    """Restate a Scope client failure as the error that this flow's own requests raise for the same response."""

    path = _AUTOMATIC_OPERATION_PATHS["scope_resolve"]
    if isinstance(error, ScopeBindingRejectedError):
        return _HttpStatusError(401, path)
    if isinstance(error, ScopeBindingUnavailableError):
        return _ServerUnavailableError() if error.status is None else _HttpStatusError(error.status, path)
    if isinstance(error, ScopeBindingStatusError):
        return _HttpStatusError(error.status, error.path, error.code)
    if isinstance(error, OSError):
        return _ServerUnavailableError()
    return error
