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

"""Recall memory and capture the current Codex prompt without blocking Codex."""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Mapping
from contextlib import suppress
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from time import monotonic
from typing import Any, Protocol, cast
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from typing_extensions import override

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PLUGIN_ROOT))

from hooks import prepared_context as _prepared_context  # noqa: E402
from hooks.diagnostics import should_emit as _should_emit_diagnostic  # noqa: E402
from scripts.scope_binding import resolve_scope_id  # noqa: E402
from settings import CodexPluginSettings  # noqa: E402

_MAX_CONTEXT_BYTES = _prepared_context.MAX_CONTEXT_BYTES
_InvalidResponseError = _prepared_context.InvalidPreparedContextResponse
_validate_prepared_context = _prepared_context.validate_prepared_context
_MAX_RESPONSE_BYTES = 1_048_576
_MAX_SOURCE_LENGTH = 200_000
_READ_CHUNK_BYTES = 65_536
_REQUEST_HEADERS = {
    "Accept": "application/json",
    "Content-Type": "application/json",
    "User-Agent": "powercontext-codex-plugin/0.2.0",
}
_FAILURE_OUTCOMES = frozenset({"authentication_failed", "version_mismatch", "server_unavailable", "invalid_response"})


class _ReadableResponse(Protocol):
    def read(self, n: int = -1) -> bytes: ...


class _Response(_ReadableResponse, Protocol):
    status: int

    def __enter__(self) -> _Response: ...

    def __exit__(self, *args: object) -> object: ...


class _RejectRedirects(HTTPRedirectHandler):
    """Leave every 3xx response to urllib's default HTTP error handler."""

    @override
    def redirect_request(
        self,
        req: Request,
        fp: object,
        code: int,
        msg: str,
        headers: object,
        newurl: str,
    ) -> Request | None:
        return None


_URL_OPENER = build_opener(_RejectRedirects)


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
})
_AUTOMATIC_OPERATION_PATHS = {
    "context_prepare": "/v1/context/prepare",
    "capture_source": "/v1/sources/content",
    "flush_memory": "/v1/memory/flush",
}


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
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(decoded, dict):
        return None
    error = decoded.get("error")
    if not isinstance(error, dict):
        return None
    code = error.get("code")
    return code if isinstance(code, str) else None


def main(settings: CodexPluginSettings | None = None) -> int:
    """Process one Codex hook payload and fail open."""

    try:
        settings = CodexPluginSettings() if settings is None else settings
        http_deadline = monotonic() + settings.http_budget_seconds
        stdin = sys.stdin
        if hasattr(stdin, "buffer"):
            payload = cast(dict[str, Any], json.loads(stdin.buffer.read().decode("utf-8")))
        else:
            payload = cast(dict[str, Any], json.load(stdin))
        if not _is_user_prompt_submit(payload.get("hook_event_name")):
            return 0
        emitted_diagnostics: set[str] = set()
        diagnostic_events: list[dict[str, object]] = []
        prompt = payload.get("prompt")
        cwd = payload.get("cwd")
        if not isinstance(prompt, str) or not prompt.strip() or not isinstance(cwd, str):
            _emit_context_event("skipped", diagnostic_events=diagnostic_events)
            _write_hook_output(diagnostic_events=diagnostic_events)
            return 0
        session_id = _payload_identifier(payload, "session_id", "conversation_id", "thread_id")
        scope_id = resolve_scope_id(
            cwd,
            session_id=session_id,
            settings=settings,
            deadline=http_deadline,
        )
        context = _recall_context(
            prompt,
            scope_id,
            settings=settings,
            deadline=http_deadline,
            emitted_diagnostics=emitted_diagnostics,
            diagnostic_events=diagnostic_events,
        )
        _capture_and_flush(
            payload,
            prompt=prompt,
            cwd=cwd,
            scope_id=scope_id,
            settings=settings,
            deadline=http_deadline,
            emitted_diagnostics=emitted_diagnostics,
            diagnostic_events=diagnostic_events,
        )
        if context:
            with suppress(Exception):
                _record_evaluation_trace(
                    payload,
                    query=prompt,
                    injected_text=context,
                    scope_id=scope_id,
                )
        _write_hook_output(context=context, diagnostic_events=diagnostic_events)
    except Exception:
        return 0
    return 0


def _capture_and_flush(
    payload: Mapping[str, object],
    *,
    prompt: str,
    cwd: str,
    scope_id: str,
    settings: CodexPluginSettings,
    deadline: float,
    emitted_diagnostics: set[str],
    diagnostic_events: list[dict[str, object]],
) -> None:
    if not settings.capture_prompts or len(prompt) > _MAX_SOURCE_LENGTH:
        return
    try:
        captured = _capture_prompt(
            payload,
            prompt=prompt,
            cwd=cwd,
            scope_id=scope_id,
            settings=settings,
            deadline=deadline,
        )
        position = _source_position(captured)
    except Exception as error:
        _emit_failure_event(
            "capture_source",
            error,
            emitted_diagnostics=emitted_diagnostics,
            diagnostic_events=diagnostic_events,
        )
        return
    if not settings.flush_on_capture:
        return
    try:
        _flush_through(scope_id, position, settings=settings, deadline=deadline)
    except Exception as error:
        _emit_failure_event(
            "flush_memory",
            error,
            emitted_diagnostics=emitted_diagnostics,
            diagnostic_events=diagnostic_events,
        )


def _prepare_context(
    query: str,
    scope_id: str,
    *,
    settings: CodexPluginSettings,
    deadline: float,
) -> Mapping[str, object]:
    return _post_json(
        "/v1/context/prepare",
        {
            "scope_id": scope_id,
            "query": query,
            "max_bytes": _MAX_CONTEXT_BYTES,
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
    settings: CodexPluginSettings,
    deadline: float,
) -> Mapping[str, object]:
    session_id = _payload_identifier(payload, "session_id", "conversation_id", "thread_id")
    turn_id = _payload_identifier(payload, "turn_id", "request_id")
    identity = "\0".join((scope_id, session_id or "", turn_id or "", prompt))
    source_id = f"codex-user-prompt:{sha256(identity.encode()).hexdigest()}"
    metadata = {
        "origin": "codex",
        "event": "user_prompt_submit",
        "cwd": cwd,
    }
    if session_id is not None:
        metadata["session_id"] = session_id
    if turn_id is not None:
        metadata["turn_id"] = turn_id
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
    settings: CodexPluginSettings,
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
    settings: CodexPluginSettings,
    deadline: float,
    expected_status: int | None = None,
) -> Mapping[str, object]:
    request = Request(  # noqa: S310 - settings validation enforces the transport policy.
        f"{settings.server_url}{path}",
        data=json.dumps(payload, separators=(",", ":")).encode(),
        headers=_request_headers(settings),
        method="POST",
    )
    request_deadline = deadline
    try:
        request_timeout = min(settings.request_timeout_seconds, _remaining_time(deadline))
        request_deadline = min(deadline, monotonic() + request_timeout)
        with _URL_OPENER.open(request, timeout=request_timeout) as response:
            if expected_status is not None and response.status != expected_status:
                code = _decode_error_code(_read_response(response, deadline=request_deadline))
                raise _HttpStatusError(response.status, path, code)
            result = json.loads(_read_response(response, deadline=request_deadline))
    except HTTPError as error:
        try:
            error_body = _read_response(error, deadline=request_deadline, chunk_bytes=1)
        except TimeoutError as timeout:
            raise _ServerUnavailableError from timeout
        except OSError:
            error_body = b""
        raise _HttpStatusError(error.code, path, _decode_error_code(error_body)) from error
    except TimeoutError as error:
        raise _ServerUnavailableError from error
    except OSError as error:
        raise _ServerUnavailableError from error
    except ValueError as error:
        raise _InvalidResponseError from error
    if not isinstance(result, dict):
        raise _InvalidResponseError
    return cast(dict[str, object], result)


def _request_headers(settings: CodexPluginSettings) -> dict[str, str]:
    headers = dict(_REQUEST_HEADERS)
    if settings.authorization is not None:
        headers["Authorization"] = settings.authorization.get_secret_value()
    return headers


def _read_response(
    response: _ReadableResponse,
    *,
    deadline: float,
    chunk_bytes: int = _READ_CHUNK_BYTES,
) -> bytes:
    """Read one response under a wall-clock deadline and a hard size bound."""

    content = bytearray()
    while True:
        _set_response_timeout(response, _remaining_time(deadline))
        remaining_bytes = _MAX_RESPONSE_BYTES + 1 - len(content)
        chunk = response.read(min(chunk_bytes, remaining_bytes))
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
    settings: CodexPluginSettings,
    deadline: float,
    emitted_diagnostics: set[str] | None = None,
    diagnostic_events: list[dict[str, object]] | None = None,
) -> str | None:
    try:
        prepared = _validate_prepared_context(_prepare_context(query, scope_id, settings=settings, deadline=deadline))
    except _HttpStatusError as error:
        outcome = _http_failure_outcome(error, operation="context_prepare")
        if outcome is not None:
            _emit_context_event(
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
            "server_unavailable",
            recovery="powercontext doctor",
            emitted_diagnostics=emitted_diagnostics,
            diagnostic_events=diagnostic_events,
        )
        return None
    except _InvalidResponseError:
        _emit_context_event(
            "invalid_response",
            emitted_diagnostics=emitted_diagnostics,
            diagnostic_events=diagnostic_events,
        )
        return None

    status = cast(str, prepared["status"])
    content_bytes = cast(int, prepared["content_bytes"])
    if status == "empty":
        _emit_context_event(
            "empty",
            http_status=200,
            context_status=status,
            content_bytes=content_bytes,
            diagnostic_events=diagnostic_events,
        )
        return None
    return cast(str, prepared["content"])


def _record_evaluation_trace(
    payload: Mapping[str, object],
    *,
    query: str,
    injected_text: str,
    scope_id: str,
) -> None:
    """Append the exact injected context when the isolated evaluator requests an audit trace."""

    raw_path = os.environ.get("POWERCONTEXT_EVAL_TRACE_PATH")
    if raw_path is None or not raw_path.strip():
        eval_home = os.environ.get("POWERCONTEXT_HOME")
        if not scope_id.startswith("eval:") or eval_home is None or not eval_home.strip():
            return
        home = Path(eval_home)
        if not home.is_absolute():
            return
        raw_path = os.fspath(home / "evaluation-injections.jsonl")
    event: dict[str, object] = {
        "event_type": "powercontext_injection",
        "observed_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "query": query,
        "injected_text": injected_text,
        # The prepared-context v1 response deliberately does not expose raw search hits.
        "hits": [],
        "scope_id": scope_id,
    }
    session_id = _payload_identifier(payload, "session_id", "conversation_id", "thread_id")
    turn_id = _payload_identifier(payload, "turn_id", "request_id")
    if session_id is not None:
        event["session_id"] = session_id
    if turn_id is not None:
        event["turn_id"] = turn_id
    encoded = (json.dumps(event, separators=(",", ":")) + "\n").encode()
    flags = os.O_APPEND | os.O_CREAT | os.O_WRONLY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(raw_path, flags, 0o600)
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "ab", closefd=False) as trace:
            trace.write(encoded)
            trace.flush()
    finally:
        os.close(descriptor)


def _emit_context_event(
    outcome: str,
    *,
    event_name: str = "context_prepare",
    http_status: int | None = None,
    error_code: str | None = None,
    context_status: str | None = None,
    content_bytes: int | None = None,
    recovery: str | None = None,
    emitted_diagnostics: set[str] | None = None,
    diagnostic_events: list[dict[str, object]] | None = None,
) -> None:
    if emitted_diagnostics is not None and outcome in _FAILURE_OUTCOMES:
        key = outcome
        if key in emitted_diagnostics:
            return
        emitted_diagnostics.add(key)
        if not _should_emit_diagnostic(outcome):
            return
    event: dict[str, object] = {
        "component": "powercontext.codex.recall",
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
    if recovery is not None:
        event["recovery"] = recovery
    if diagnostic_events is None or outcome not in _FAILURE_OUTCOMES:
        sys.stderr.write(json.dumps(event, separators=(",", ":")) + "\n")
    else:
        diagnostic_events.append(event)


def _emit_failure_event(
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
            "server_unavailable",
            event_name=event_name,
            recovery="powercontext doctor",
            emitted_diagnostics=emitted_diagnostics,
            diagnostic_events=diagnostic_events,
        )
    else:
        _emit_context_event(
            "invalid_response",
            event_name=event_name,
            emitted_diagnostics=emitted_diagnostics,
            diagnostic_events=diagnostic_events,
        )


def _write_hook_output(
    *,
    context: str | None = None,
    diagnostic_events: list[dict[str, object]],
) -> None:
    output: dict[str, object] = {}
    if diagnostic_events:
        output["systemMessage"] = "\n".join(json.dumps(event, separators=(",", ":")) for event in diagnostic_events)
    if context:
        output["hookSpecificOutput"] = {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": context,
        }
    if output:
        json.dump(output, sys.stdout, separators=(",", ":"))
        sys.stdout.write("\n")


if __name__ == "__main__":
    raise SystemExit(main())
