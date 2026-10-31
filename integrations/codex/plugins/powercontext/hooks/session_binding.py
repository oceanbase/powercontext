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

"""Bind a Codex Session and optionally inject one bounded bootstrap package."""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Mapping
from contextlib import suppress
from http.client import HTTPException
from pathlib import Path
from time import monotonic
from typing import Any, Protocol, cast
from urllib.error import HTTPError
from urllib.request import Request

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS_ROOT = _PLUGIN_ROOT / "scripts"
sys.path.insert(0, str(_PLUGIN_ROOT))
sys.path.insert(0, str(_SCRIPTS_ROOT))

from bootstrap_state import (  # noqa: E402
    clear_receipt_if_matches,
    load_receipt_state,
    save_receipt,
)
from hooks.bootstrap_context import (  # noqa: E402
    InvalidBootstrapResponse,
    validate_bootstrap_context,
    validate_delivery_receipt,
)
from plugin_version import PLUGIN_VERSION  # noqa: E402
from scope_binding import (  # noqa: E402
    ScopeBindingError,
    ScopeBindingRejectedError,
    ScopeBindingStatusError,
    ScopeBindingUnavailableError,
    bind_response_deadline,
    complete_body,
    open_bounded,
    read_error_code,
    resolve_scope_id,
)
from settings import CodexPluginSettings  # noqa: E402

_LIFECYCLES = frozenset({"startup", "resume", "clear", "compact", "restore", "fork"})
_MAX_RESPONSE_BYTES = 1_048_576
_READ_CHUNK_BYTES = 65_536
_BOOTSTRAP_PROFILE = "powercontext.scope-bootstrap.v1"
_REQUEST_HEADERS = {
    "Accept": "application/json",
    "Content-Type": "application/json",
    "User-Agent": f"powercontext-codex-plugin/{PLUGIN_VERSION}",
}


class _ReadableResponse(Protocol):
    status: int

    def read(self, amount: int = -1) -> bytes: ...

    def __enter__(self) -> _ReadableResponse: ...

    def __exit__(self, *args: object) -> object: ...


class _HttpStatusError(RuntimeError):
    def __init__(self, status: int, path: str, code: str | None = None) -> None:
        self.status = status
        self.path = path
        self.code = code
        super().__init__(f"PowerContext returned HTTP {status}")


def main(settings: CodexPluginSettings | None = None) -> int:
    try:
        payload = cast(dict[str, Any], json.load(sys.stdin))
        session_id = payload.get("session_id")
        cwd = payload.get("cwd")
        if not isinstance(session_id, str) or not isinstance(cwd, str):
            return 0
        source = payload.get("source")
        if not isinstance(source, str) or source not in _LIFECYCLES:
            settings = CodexPluginSettings() if settings is None else settings
            resolve_scope_id(
                cwd,
                session_id=session_id,
                settings=settings,
                deadline=monotonic() + settings.http_budget_seconds,
                persist_session=True,
            )
            return 0
        previous_receipt = load_receipt_state(session_id)
        try:
            settings = CodexPluginSettings() if settings is None else settings
        except Exception:
            _clear_observed_receipt(session_id, previous_receipt)
            return 0
        deadline = monotonic() + settings.http_budget_seconds
        prepared = _resolve_and_prepare(cwd, session_id, source, payload, settings=settings, deadline=deadline)
        if prepared is None:
            _clear_observed_receipt(session_id, previous_receipt)
            return 0
        scope_id, response = prepared
        if response["status"] != "ready":
            receipt = _synchronize_non_ready_receipt(session_id, scope_id, response, previous_receipt)
            if response["status"] == "empty":
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
            _clear_stale_receipt_after_failed_claim(session_id, scope_id, receipt_id, previous_receipt)
            _emit_bootstrap_event("delivery_failed")
            with suppress(Exception):
                _post_json(
                    "/v1/context/bootstrap/receipts",
                    {"scope_id": scope_id, "receipt_id": receipt_id, "outcome": "failed"},
                    settings=settings,
                    deadline=deadline,
                )
            return 0
        try:
            save_receipt(session_id, scope_id, receipt_id)
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
            sys.stdout.flush()
        except Exception as error:
            _handle_output_failure(session_id, scope_id, receipt_id, error)
            return 0
    except Exception:
        return 0
    return 0


def _resolve_and_prepare(
    cwd: str,
    session_id: str,
    source: str,
    payload: Mapping[str, object],
    *,
    settings: CodexPluginSettings,
    deadline: float,
) -> tuple[str, dict[str, object]] | None:
    try:
        scope_id = resolve_scope_id(
            cwd,
            session_id=session_id,
            settings=settings,
            deadline=deadline,
            persist_session=True,
        )
        request: dict[str, object] = {
            "scope_id": scope_id,
            "enabled": settings.bootstrap_context,
            "profile": _BOOTSTRAP_PROFILE,
            "lifecycle": source,
            "integration": "codex",
            "max_bytes": settings.bootstrap_max_bytes,
        }
        if settings.bootstrap_handoff is not None:
            request["handoff"] = settings.bootstrap_handoff.model_dump()
        event_id = _identifier(payload, "event_id", "hook_id", "invocation_id", "request_id")
        if event_id is not None:
            request["event_id"] = event_id
        response = validate_bootstrap_context(
            _post_json("/v1/context/bootstrap", request, settings=settings, deadline=deadline),
            max_bytes=settings.bootstrap_max_bytes,
        )
    except ScopeBindingRejectedError:
        _emit_bootstrap_event("authentication_failed")
    except (ScopeBindingUnavailableError, OSError, TimeoutError, HTTPException):
        # http.client reports a truncated or malformed response as HTTPException, not OSError.
        _emit_bootstrap_event("server_unavailable", recovery="powercontext doctor")
    except (ScopeBindingStatusError, _HttpStatusError) as error:
        _emit_http_failure(error.status, error.code)
    except (ScopeBindingError, InvalidBootstrapResponse):
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
    settings: CodexPluginSettings,
    deadline: float,
) -> dict[str, object]:
    headers = dict(_REQUEST_HEADERS)
    if settings.authorization is not None:
        headers["Authorization"] = settings.authorization.get_secret_value()
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
            raw = _read_response(response, deadline=min(deadline, monotonic() + timeout))
    except HTTPError as error:
        code = read_error_code(error, deadline=min(deadline, monotonic() + timeout))
        raise _HttpStatusError(error.code, path, code) from error
    try:
        value = json.loads(raw)
    except (ValueError, RecursionError) as error:
        # Besides JSONDecodeError, json raises ValueError for an over-long integer and RecursionError for deep nesting.
        raise InvalidBootstrapResponse from error
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
            return complete_body(response, bytes(content))
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


def _clear_stale_receipt_after_failed_claim(
    session_id: str,
    scope_id: str,
    receipt_id: str,
    previous_receipt: tuple[str, str] | None,
) -> None:
    if previous_receipt is not None and previous_receipt != (scope_id, receipt_id):
        clear_receipt_if_matches(session_id, *previous_receipt)


def _clear_observed_receipt(session_id: str, previous_receipt: tuple[str, str] | None) -> None:
    if previous_receipt is not None:
        clear_receipt_if_matches(session_id, *previous_receipt)


def _handle_output_failure(session_id: str, scope_id: str, receipt_id: str, error: Exception) -> None:
    clear_receipt_if_matches(session_id, scope_id, receipt_id)
    if isinstance(error, BrokenPipeError):
        try:
            stdout = sys.stdout.fileno()
            descriptor = os.open(os.devnull, os.O_WRONLY)
            try:
                os.dup2(descriptor, stdout)
            finally:
                os.close(descriptor)
        except (AttributeError, OSError, ValueError):
            pass
    _emit_bootstrap_event("delivery_failed")


def _synchronize_non_ready_receipt(
    session_id: str,
    scope_id: str,
    response: Mapping[str, object],
    previous_receipt: tuple[str, str] | None,
) -> dict[str, str]:
    """Retain a receipt only when this exact lifecycle event was already injected."""

    receipt = cast(dict[str, str], response["receipt"])
    if (
        response.get("status") == "skipped"
        and response.get("reason") == "already_delivered"
        and receipt["state"] == "injected"
        and previous_receipt == (scope_id, receipt["receipt_id"])
    ):
        return receipt
    _clear_observed_receipt(session_id, previous_receipt)
    return receipt


def _emit_http_failure(status: int, code: str | None = None) -> None:
    if status == 401:
        outcome = "authentication_failed"
    elif status == 403:
        outcome = "authorization_denied"
    elif status == 404 and code is None:
        outcome = "version_mismatch"
    elif status == 503:
        outcome = "server_unavailable"
    else:
        outcome = "invalid_response"
    _emit_bootstrap_event(
        outcome,
        http_status=status,
        error_code=code,
        recovery="powercontext doctor" if outcome == "server_unavailable" else None,
    )


def _emit_bootstrap_event(
    outcome: str,
    *,
    http_status: int | None = None,
    error_code: str | None = None,
    context_status: str | None = None,
    content_bytes: int | None = None,
    truncated: bool | None = None,
    receipt_state: str | None = None,
    recovery: str | None = None,
) -> None:
    event: dict[str, object] = {
        "component": "powercontext.codex.bootstrap",
        "event": "context_bootstrap",
        "outcome": outcome,
        "profile": _BOOTSTRAP_PROFILE,
    }
    if http_status is not None:
        event["http_status"] = http_status
    if error_code is not None:
        event["error_code"] = error_code
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
