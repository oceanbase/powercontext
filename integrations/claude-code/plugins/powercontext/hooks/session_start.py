#!/usr/bin/env python3
# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Inject one bounded PowerContext package at Claude Code SessionStart."""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping
from contextlib import suppress
from pathlib import Path
from time import monotonic
from typing import Any, Protocol, cast
from urllib.error import HTTPError
from urllib.request import Request

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS_ROOT = _PLUGIN_ROOT / "scripts"
sys.path.insert(0, str(_PLUGIN_ROOT))
sys.path.insert(0, str(_SCRIPTS_ROOT))

from bootstrap_state import clear_receipt, save_receipt  # noqa: E402
from claude_code_settings import ClaudeCodePluginSettings  # noqa: E402
from hooks.bootstrap_context import (  # noqa: E402
    InvalidBootstrapResponse,
    validate_bootstrap_context,
    validate_delivery_receipt,
)
from hooks.diagnostics import should_emit as _should_emit_diagnostic  # noqa: E402
from scope_binding_errors import (  # noqa: E402
    ScopeBindingError,
    ScopeBindingRejectedError,
    ScopeBindingStatusError,
    ScopeBindingUnavailableError,
)
from workspace_scope import (  # noqa: E402
    bind_response_deadline,
    open_bounded,
    resolve_scope_id,
)

_LIFECYCLES = frozenset({"startup", "resume", "clear", "compact", "fork"})
_MAX_RESPONSE_BYTES = 1_048_576
_READ_CHUNK_BYTES = 65_536
_BOOTSTRAP_PROFILE = "powercontext.scope-bootstrap.v1"
_FAILURE_OUTCOMES = frozenset({
    "authentication_failed",
    "authorization_denied",
    "delivery_failed",
    "invalid_response",
    "server_unavailable",
    "version_mismatch",
})
_REQUEST_HEADERS = {
    "Accept": "application/json",
    "Content-Type": "application/json",
    "User-Agent": "powercontext-claude-code-plugin/0.1.3",
}


class _ReadableResponse(Protocol):
    status: int

    def read(self, amount: int = -1) -> bytes: ...

    def __enter__(self) -> _ReadableResponse: ...

    def __exit__(self, *args: object) -> object: ...


class _HttpStatusError(RuntimeError):
    def __init__(self, status: int, path: str) -> None:
        self.status = status
        self.path = path
        super().__init__(f"PowerContext returned HTTP {status}")


def main(settings: ClaudeCodePluginSettings | None = None) -> int:
    """Process one SessionStart payload without ever blocking Claude Code."""

    try:
        payload = cast(dict[str, Any], json.load(sys.stdin))
        session_id = payload.get("session_id")
        cwd = payload.get("cwd")
        source = payload.get("source")
        if (
            not isinstance(session_id, str)
            or not isinstance(cwd, str)
            or not isinstance(source, str)
            or source not in _LIFECYCLES
        ):
            return 0
        settings = ClaudeCodePluginSettings.from_environment() if settings is None else settings
        deadline = monotonic() + settings.http_budget_seconds
        clear_receipt(session_id)
        prepared = _resolve_and_prepare(cwd, session_id, source, payload, settings=settings, deadline=deadline)
        if prepared is None:
            return 0
        scope_id, response = prepared
        if response["status"] != "ready":
            if response["status"] == "empty":
                receipt = cast(dict[str, str], response["receipt"])
                _emit_bootstrap_event(
                    "empty",
                    http_status=200,
                    context_status="empty",
                    content_bytes=0,
                    truncated=cast(bool, response["truncated"]),
                    receipt_state=receipt["state"],
                )
            return 0
        receipt = cast(dict[str, str], response["receipt"])
        receipt_id = receipt["receipt_id"]
        try:
            save_receipt(session_id, scope_id, receipt_id)
            delivered = validate_delivery_receipt(
                _post_json(
                    "/v1/context/bootstrap/receipts",
                    {"scope_id": scope_id, "receipt_id": receipt_id, "outcome": "injected"},
                    settings=settings,
                    deadline=deadline,
                ),
                receipt_id=receipt_id,
            )
            _require_injected(delivered)
        except Exception:
            clear_receipt(session_id)
            _emit_bootstrap_event("delivery_failed")
            with suppress(Exception):
                _post_json(
                    "/v1/context/bootstrap/receipts",
                    {"scope_id": scope_id, "receipt_id": receipt_id, "outcome": "failed"},
                    settings=settings,
                    deadline=deadline,
                )
            return 0
        json.dump(
            {
                "hookSpecificOutput": {
                    "hookEventName": "SessionStart",
                    "additionalContext": response["content"],
                }
            },
            sys.stdout,
            separators=(",", ":"),
        )
        sys.stdout.write("\n")
    except Exception:
        return 0
    return 0


def _resolve_and_prepare(
    cwd: str,
    session_id: str,
    source: str,
    payload: Mapping[str, object],
    *,
    settings: ClaudeCodePluginSettings,
    deadline: float,
) -> tuple[str, dict[str, object]] | None:
    try:
        scope_id = resolve_scope_id(
            cwd,
            session_id=session_id,
            settings=settings,
            deadline=deadline,
        )
        request: dict[str, object] = {
            "scope_id": scope_id,
            "enabled": settings.bootstrap_context,
            "profile": _BOOTSTRAP_PROFILE,
            "lifecycle": source,
            "integration": "claude-code",
            "max_bytes": settings.bootstrap_max_bytes,
        }
        if settings.bootstrap_handoff is not None:
            request["handoff"] = settings.bootstrap_handoff.as_request()
        event_id = _identifier(payload, "event_id", "hook_id", "invocation_id", "request_id")
        if event_id is not None:
            request["event_id"] = event_id
        response = validate_bootstrap_context(
            _post_json("/v1/context/bootstrap", request, settings=settings, deadline=deadline),
            max_bytes=settings.bootstrap_max_bytes,
        )
    except ScopeBindingRejectedError:
        _emit_bootstrap_event("authentication_failed")
    except (ScopeBindingUnavailableError, OSError, TimeoutError):
        _emit_bootstrap_event("server_unavailable", recovery="powercontext doctor")
    except (ScopeBindingStatusError, _HttpStatusError) as error:
        _emit_http_failure(error.status)
    except (ScopeBindingError, InvalidBootstrapResponse, UnicodeDecodeError, json.JSONDecodeError):
        _emit_bootstrap_event("invalid_response")
    else:
        return scope_id, response
    return None


def _identifier(payload: Mapping[str, object], *names: str) -> str | None:
    for name in names:
        value = payload.get(name)
        if isinstance(value, str) and value.strip() and len(value.strip()) <= 512:
            return value.strip()
    return None


def _post_json(
    path: str,
    payload: Mapping[str, object],
    *,
    settings: ClaudeCodePluginSettings,
    deadline: float,
) -> dict[str, object]:
    headers = dict(_REQUEST_HEADERS)
    if settings.authorization is not None:
        headers["Authorization"] = settings.authorization
    request = Request(  # noqa: S310 - settings validates the configured transport.
        f"{settings.server_url}{path}",
        data=json.dumps(payload, separators=(",", ":")).encode(),
        headers=headers,
        method="POST",
    )
    timeout = min(settings.request_timeout_seconds, _remaining_time(deadline))
    try:
        with open_bounded(request, timeout=timeout) as response:
            if response.status != 200:
                raise _HttpStatusError(response.status, path)
            value = json.loads(_read_response(response, deadline=min(deadline, monotonic() + timeout)))
    except HTTPError as error:
        raise _HttpStatusError(error.code, path) from error
    if not isinstance(value, dict):
        raise InvalidBootstrapResponse
    return cast(dict[str, object], value)


def _read_response(response: _ReadableResponse, *, deadline: float) -> bytes:
    bind_response_deadline(response, deadline)
    content = bytearray()
    while True:
        remaining = _MAX_RESPONSE_BYTES + 1 - len(content)
        chunk = response.read(min(_READ_CHUNK_BYTES, remaining))
        if not chunk:
            return bytes(content)
        content.extend(chunk)
        if len(content) > _MAX_RESPONSE_BYTES:
            raise InvalidBootstrapResponse


def _remaining_time(deadline: float) -> float:
    remaining = deadline - monotonic()
    if remaining <= 0:
        raise TimeoutError
    return remaining


def _require_injected(receipt: Mapping[str, str]) -> None:
    if receipt.get("state") != "injected":
        raise InvalidBootstrapResponse


def _emit_http_failure(status: int) -> None:
    if status == 401:
        outcome = "authentication_failed"
    elif status == 403:
        outcome = "authorization_denied"
    elif status == 404:
        outcome = "version_mismatch"
    elif status == 503:
        outcome = "server_unavailable"
    else:
        outcome = "invalid_response"
    _emit_bootstrap_event(
        outcome,
        http_status=status,
        recovery="powercontext doctor" if outcome == "server_unavailable" else None,
    )


def _emit_bootstrap_event(
    outcome: str,
    *,
    http_status: int | None = None,
    context_status: str | None = None,
    content_bytes: int | None = None,
    truncated: bool | None = None,
    receipt_state: str | None = None,
    recovery: str | None = None,
) -> None:
    if outcome in _FAILURE_OUTCOMES and not _should_emit_diagnostic(outcome):
        return
    event: dict[str, object] = {
        "component": "powercontext.claude_code.bootstrap",
        "event": "context_bootstrap",
        "outcome": outcome,
        "profile": _BOOTSTRAP_PROFILE,
    }
    if http_status is not None:
        event["http_status"] = http_status
    if context_status is not None:
        event["context_status"] = context_status
    if content_bytes is not None:
        event["content_bytes"] = content_bytes
    if truncated is not None:
        event["truncated"] = truncated
    if receipt_state is not None:
        event["receipt_state"] = receipt_state
    if recovery is not None:
        event["recovery"] = recovery
    sys.stderr.write(json.dumps(event, separators=(",", ":")) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
