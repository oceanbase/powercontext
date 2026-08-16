#!/usr/bin/env python3
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

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PLUGIN_ROOT))

from hooks import prepared_context as _prepared_context  # noqa: E402
from scripts.project_scope import derive_scope_id  # noqa: E402
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
    "User-Agent": "powercontext-codex-plugin/0.1.0",
}


class _Response(Protocol):
    fp: object
    status: int

    def __enter__(self) -> _Response: ...

    def __exit__(self, *args: object) -> object: ...

    def read(self, amount: int = -1) -> bytes: ...


class _RejectRedirects(HTTPRedirectHandler):
    """Leave every 3xx response to urllib's default HTTP error handler."""

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
    def __init__(self, status: int) -> None:
        self.status = status
        super().__init__(f"PowerContext returned HTTP {status}")


class _ServerUnavailableError(RuntimeError):
    pass


def main(settings: CodexPluginSettings | None = None) -> int:
    """Process one Codex hook payload and fail open."""

    try:
        settings = CodexPluginSettings() if settings is None else settings
        http_deadline = monotonic() + settings.http_budget_seconds
        payload = cast(dict[str, Any], json.load(sys.stdin))
        if not _is_user_prompt_submit(payload.get("hook_event_name")):
            return 0
        prompt = payload.get("prompt")
        cwd = payload.get("cwd")
        if not isinstance(prompt, str) or not prompt.strip() or not isinstance(cwd, str):
            _emit_context_event("skipped")
            return 0
        scope_id = derive_scope_id(cwd, configured_scope_id=settings.scope_id)
        context = _recall_context(prompt, scope_id, settings=settings, deadline=http_deadline)
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
        if context:
            with suppress(Exception):
                _record_evaluation_trace(
                    payload,
                    query=prompt,
                    injected_text=context,
                    scope_id=scope_id,
                )
            json.dump(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "UserPromptSubmit",
                        "additionalContext": context,
                    }
                },
                sys.stdout,
                separators=(",", ":"),
            )
            sys.stdout.write("\n")
    except Exception:
        return 0
    return 0


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
    request_timeout = min(settings.request_timeout_seconds, _remaining_time(deadline))
    request_deadline = min(deadline, monotonic() + request_timeout)
    try:
        with _URL_OPENER.open(request, timeout=request_timeout) as response:
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


def _request_headers(settings: CodexPluginSettings) -> dict[str, str]:
    headers = dict(_REQUEST_HEADERS)
    if settings.authorization is not None:
        headers["Authorization"] = settings.authorization.get_secret_value()
    return headers


def _read_response(response: _Response, *, deadline: float) -> bytes:
    """Read one response under a wall-clock deadline and a hard size bound."""

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


def _set_response_timeout(response: _Response, timeout: float) -> None:
    """Tighten urllib's socket timeout before each bounded read."""

    raw = getattr(response.fp, "raw", None)
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
    except _ServerUnavailableError:
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
    http_status: int | None = None,
    context_status: str | None = None,
    content_bytes: int | None = None,
) -> None:
    event: dict[str, object] = {
        "component": "powercontext.codex.recall",
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
