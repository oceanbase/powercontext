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

"""Inject one bounded PowerContext bootstrap package when a host session starts."""

from __future__ import annotations

import json
import sys
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass, field
from http.client import HTTPException
from time import monotonic
from typing import Protocol, cast
from urllib.error import HTTPError
from urllib.request import Request

from .bootstrap_context import InvalidBootstrapResponse, validate_bootstrap_context, validate_delivery_receipt
from .host_profile import HostProfile
from .scope_binding_errors import (
    ScopeBindingError,
    ScopeBindingRejectedError,
    ScopeBindingStatusError,
    ScopeBindingUnavailableError,
)
from .transport import bind_response_deadline, complete_body, open_bounded

_MAX_RESPONSE_BYTES = 1_048_576
_READ_CHUNK_BYTES = 65_536
_BOOTSTRAP_PROFILE = "powercontext.scope-bootstrap.v1"


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


class _ErrorCodeReader(Protocol):
    def __call__(self, error: HTTPError, *, deadline: float) -> str | None: ...


@dataclass(frozen=True)
class SessionStartEvent:
    """One SessionStart event, translated from the host's payload."""

    session_id: str
    cwd: str
    lifecycle: str
    event_id: str | None


@dataclass(frozen=True)
class BootstrapConnection:
    """The host settings that the bootstrap requests use, as plain values.

    ``resolve_scope_id`` resolves the event's Scope through the host's Scope client, within the deadline it is given.
    ``read_error_code`` reads the error code of a failed bootstrap request through the same client, which fails the
    request as unavailable when that body stalls or breaks off, and as invalid when it exceeds the size bound.
    """

    server_url: str
    authorization: str | None = field(repr=False)
    request_timeout_seconds: float
    http_budget_seconds: float
    enabled: bool
    max_bytes: int
    handoff: Mapping[str, object] | None
    resolve_scope_id: Callable[[float], str]
    read_error_code: _ErrorCodeReader


def hook_event_id(payload: Mapping[str, object]) -> str | None:
    """Return the identifier that a hook payload gives its event, if it gives a usable one."""

    for name in ("event_id", "hook_id", "invocation_id", "request_id"):
        value = payload.get(name)
        if isinstance(value, str) and value.strip() and len(value.strip()) <= 512:
            return value.strip()
    return None


def run_session_start(
    host: HostProfile,
    event: SessionStartEvent,
    *,
    connect: Callable[[], BootstrapConnection],
    emit: Callable[[str], None],
) -> None:
    """Hand the event's bootstrap package to ``emit`` once the Server has recorded its delivery.

    ``connect`` loads the host's settings after the session's previous receipt has been read; if it fails, that
    receipt is cleared and nothing is reported. ``emit`` writes the package in the host's native form and raises if it
    cannot.

    The flow reports the failures it recognizes as diagnostics on stderr and does not record them in the host's
    diagnostic throttle, which applies to the prompt hooks' ``systemMessage``. It recognizes Scope binding errors;
    transport errors and timeouts, including a response that stalls, breaks off, or ends before its Content-Length,
    which ``http.client`` may raise as ``HTTPException``; HTTP error statuses, with the error code the Server sent;
    responses that exceed the size bound or cannot be decoded; and, once the package has arrived, any failure to record
    its delivery or to emit it. Anything else propagates, so the host's entry point must still catch it.
    """

    previous_receipt = host.bootstrap_state.load_receipt_state(event.session_id)
    try:
        connection = connect()
    except Exception:
        _clear_observed_receipt(host, event.session_id, previous_receipt)
        return
    deadline = monotonic() + connection.http_budget_seconds
    prepared = _resolve_and_prepare(host, event, connection, deadline=deadline)
    if prepared is None:
        _clear_observed_receipt(host, event.session_id, previous_receipt)
        return
    scope_id, response = prepared
    if response["status"] != "ready":
        receipt = _synchronize_non_ready_receipt(host, event.session_id, scope_id, response, previous_receipt)
        if response["status"] == "empty":
            _emit_bootstrap_event(
                host,
                "empty",
                http_status=200,
                context_status="empty",
                content_bytes=0,
                truncated=cast(bool, response["truncated"]),
                receipt_state=receipt["state"],
            )
        return
    receipt = cast(dict[str, str], response["receipt"])
    receipt_id = receipt["receipt_id"]
    try:
        delivered = validate_delivery_receipt(
            _post_json(
                "/v1/context/bootstrap/receipts",
                {"scope_id": scope_id, "receipt_id": receipt_id, "outcome": "injected"},
                host=host,
                connection=connection,
                deadline=deadline,
            ),
            receipt_id=receipt_id,
        )
        _require_injected(delivered)
    except Exception:
        _clear_stale_receipt_after_failed_claim(host, event.session_id, scope_id, receipt_id, previous_receipt)
        _emit_bootstrap_event(host, "delivery_failed")
        with suppress(Exception):
            _post_json(
                "/v1/context/bootstrap/receipts",
                {"scope_id": scope_id, "receipt_id": receipt_id, "outcome": "failed"},
                host=host,
                connection=connection,
                deadline=deadline,
            )
        return
    try:
        host.bootstrap_state.save_receipt(event.session_id, scope_id, receipt_id)
        emit(cast(str, response["content"]))
    except Exception:
        host.bootstrap_state.clear_receipt_if_matches(event.session_id, scope_id, receipt_id)
        _emit_bootstrap_event(host, "delivery_failed")


def _resolve_and_prepare(
    host: HostProfile,
    event: SessionStartEvent,
    connection: BootstrapConnection,
    *,
    deadline: float,
) -> tuple[str, dict[str, object]] | None:
    try:
        scope_id = connection.resolve_scope_id(deadline)
        request: dict[str, object] = {
            "scope_id": scope_id,
            "enabled": connection.enabled,
            "profile": _BOOTSTRAP_PROFILE,
            "lifecycle": event.lifecycle,
            "integration": host.integration,
            "max_bytes": connection.max_bytes,
        }
        if connection.handoff is not None:
            request["handoff"] = connection.handoff
        if event.event_id is not None:
            request["event_id"] = event.event_id
        response = validate_bootstrap_context(
            _post_json("/v1/context/bootstrap", request, host=host, connection=connection, deadline=deadline),
            max_bytes=connection.max_bytes,
        )
    except ScopeBindingRejectedError:
        _emit_bootstrap_event(host, "authentication_failed")
    except (ScopeBindingUnavailableError, OSError, TimeoutError, HTTPException):
        # http.client reports a truncated or malformed response as HTTPException, not OSError.
        _emit_bootstrap_event(host, "server_unavailable", recovery="powercontext doctor")
    except (ScopeBindingStatusError, _HttpStatusError) as error:
        _emit_http_failure(host, error.status, error.code)
    except (ScopeBindingError, InvalidBootstrapResponse):
        _emit_bootstrap_event(host, "invalid_response")
    else:
        return scope_id, response
    return None


def _post_json(
    path: str,
    payload: Mapping[str, object],
    *,
    host: HostProfile,
    connection: BootstrapConnection,
    deadline: float,
) -> dict[str, object]:
    headers = {"Accept": "application/json", "Content-Type": "application/json", "User-Agent": host.user_agent}
    if connection.authorization is not None:
        headers["Authorization"] = connection.authorization
    request = Request(  # noqa: S310 - the host's settings validate the configured transport.
        f"{connection.server_url}{path}",
        data=json.dumps(payload, separators=(",", ":")).encode(),
        headers=headers,
        method="POST",
    )
    timeout = min(connection.request_timeout_seconds, _remaining_time(deadline))
    # One deadline covers the headers and the body, so a slow header phase leaves the body only what is left.
    request_deadline = min(deadline, monotonic() + timeout)
    try:
        with open_bounded(request, timeout=timeout) as response:
            if response.status != 200:
                raise _HttpStatusError(response.status, path)
            raw = _read_response(response, deadline=request_deadline)
    except HTTPError as error:
        code = connection.read_error_code(error, deadline=request_deadline)
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
    host: HostProfile,
    session_id: str,
    scope_id: str,
    receipt_id: str,
    previous_receipt: tuple[str, str] | None,
) -> None:
    if previous_receipt is not None and previous_receipt != (scope_id, receipt_id):
        host.bootstrap_state.clear_receipt_if_matches(session_id, *previous_receipt)


def _clear_observed_receipt(host: HostProfile, session_id: str, previous_receipt: tuple[str, str] | None) -> None:
    if previous_receipt is not None:
        host.bootstrap_state.clear_receipt_if_matches(session_id, *previous_receipt)


def _synchronize_non_ready_receipt(
    host: HostProfile,
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
    _clear_observed_receipt(host, session_id, previous_receipt)
    return receipt


def _emit_http_failure(host: HostProfile, status: int, code: str | None = None) -> None:
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
        host,
        outcome,
        http_status=status,
        error_code=code,
        recovery="powercontext doctor" if outcome == "server_unavailable" else None,
    )


def _emit_bootstrap_event(
    host: HostProfile,
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
        "component": f"{host.component}.bootstrap",
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
