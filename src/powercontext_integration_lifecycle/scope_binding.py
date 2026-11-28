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

"""Resolve a host's sessions and workspaces to Scopes, and bind them, through the PowerContext Scope service."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from hashlib import sha256
from http.client import HTTPException
from pathlib import Path
from time import monotonic
from typing import Any, Protocol
from urllib.error import HTTPError
from urllib.request import Request

from .git_worktree import workspace_paths
from .host_profile import HostProfile
from .scope_binding_errors import (
    ScopeBindingError,
    ScopeBindingRejectedError,
    ScopeBindingStatusError,
    ScopeBindingUnavailableError,
)
from .transport import bind_response_deadline, complete_body, open_bounded

_MAX_RESPONSE_BYTES = 1_048_576


@dataclass(frozen=True)
class ScopeBindingSettings:
    """The host settings that the Scope requests use, as plain values.

    ``scope_id`` is the Scope that the settings name explicitly, if any.
    """

    server_url: str
    authorization: str | None = field(repr=False)
    scope_id: str | None
    request_timeout_seconds: float


class _Response(Protocol):
    def read(self, amount: int = -1, /) -> bytes: ...


def resolve_scope_id(
    host: HostProfile,
    cwd: str,
    *,
    session_id: str | None,
    settings: ScopeBindingSettings,
    deadline: float,
    persist_session: bool = False,
) -> str:
    """Resolve explicit, session, workspace, then server-default binding.

    With ``persist_session``, the resolved Scope is then bound to the session, unless the settings name a Scope.
    """

    response = _request_json(
        "/v1/scope-bindings/resolve",
        {
            "explicit_scope_id": settings.scope_id,
            "binding_keys": binding_keys(host, cwd, session_id=session_id, deadline=deadline),
        },
        host=host,
        settings=settings,
        deadline=deadline,
    )
    scope_id = response.get("scope_id")
    if not isinstance(scope_id, str) or not scope_id.strip() or scope_id != scope_id.strip():
        raise ScopeBindingError
    if persist_session and session_id is not None and settings.scope_id is None:
        _request_json(
            "/v1/scope-bindings",
            {"key": session_binding_key(host, session_id), "scope_id": scope_id},
            host=host,
            settings=settings,
            deadline=deadline,
            method="PUT",
        )
    return scope_id


def bind_scope(
    host: HostProfile,
    cwd: str,
    scope_id: str,
    /,
    *,
    settings: ScopeBindingSettings,
    deadline: float,
) -> str:
    """Persist the workspace identity to one server-owned Scope."""

    response = _request_json(
        "/v1/scope-bindings",
        {"key": workspace_binding_key(host, cwd, deadline=deadline), "scope_id": scope_id},
        host=host,
        settings=settings,
        deadline=deadline,
        method="PUT",
    )
    resolved = response.get("scope_id")
    if not isinstance(resolved, str) or resolved != scope_id:
        raise ScopeBindingError
    return resolved


def clear_scope_binding(
    host: HostProfile,
    cwd: str,
    /,
    *,
    settings: ScopeBindingSettings,
    deadline: float,
) -> bool:
    """Remove the durable workspace binding from the Scope service."""

    response = _request_json(
        "/v1/scope-bindings/clear",
        {"key": workspace_binding_key(host, cwd, deadline=deadline)},
        host=host,
        settings=settings,
        deadline=deadline,
    )
    cleared = response.get("cleared")
    if not isinstance(cleared, bool):
        raise ScopeBindingError
    return cleared


def binding_keys(
    host: HostProfile,
    cwd: str,
    *,
    session_id: str | None,
    deadline: float | None = None,
) -> list[dict[str, str]]:
    """Return the keys that resolution tries in order.

    They are the session's, the workspace's, and, in a linked worktree, its main checkout's.
    """

    keys: list[dict[str, str]] = []
    if session_id is not None:
        keys.append(session_binding_key(host, session_id))
    keys.extend(
        _workspace_key(host, path) for path in workspace_paths(cwd, git_root=True, timeout=_git_timeout(deadline))
    )
    return keys


def session_binding_key(host: HostProfile, session_id: str) -> dict[str, str]:
    value = session_id.strip()
    if not value or len(value) > 256:
        raise ScopeBindingError
    return {"integration": host.integration, "kind": "session", "external_id": value}


def workspace_binding_key(host: HostProfile, cwd: str, *, deadline: float | None = None) -> dict[str, str]:
    """Return the key of the workspace's own Git top level, the only key that binding and clearing write."""

    return _workspace_key(host, workspace_paths(cwd, git_root=True, timeout=_git_timeout(deadline))[0])


def _workspace_key(host: HostProfile, path: str) -> dict[str, str]:
    external_id = sha256(os.fsencode(Path(path).resolve(strict=False))).hexdigest()
    return {"integration": host.integration, "kind": "workspace", "external_id": external_id}


def _request_json(
    path: str,
    payload: Mapping[str, object],
    *,
    host: HostProfile,
    settings: ScopeBindingSettings,
    deadline: float,
    method: str = "POST",
) -> Mapping[str, object]:
    remaining = _remaining_time(deadline)
    request_deadline = min(deadline, monotonic() + settings.request_timeout_seconds)
    headers = {"Accept": "application/json", "Content-Type": "application/json", "User-Agent": host.user_agent}
    if settings.authorization is not None:
        headers["Authorization"] = settings.authorization
    request = Request(  # noqa: S310 - the host's settings validate the configured transport.
        f"{settings.server_url}{path}",
        data=json.dumps(payload, separators=(",", ":")).encode(),
        headers=headers,
        method=method,
    )
    try:
        with open_bounded(request, timeout=min(settings.request_timeout_seconds, remaining)) as response:
            if response.status < 200 or response.status >= 300:
                raise ScopeBindingStatusError(response.status, path)
            raw = _read_bounded(response, deadline=request_deadline)
    except HTTPError as error:
        if error.code == 401:
            raise ScopeBindingRejectedError from error
        if error.code == 503:
            raise ScopeBindingUnavailableError(503) from error
        raise ScopeBindingStatusError(error.code, path, read_error_code(error, deadline=request_deadline)) from error
    except (OSError, TimeoutError, HTTPException) as error:
        # http.client reports a truncated or malformed response as HTTPException, not OSError.
        raise ScopeBindingUnavailableError from error
    try:
        value: Any = json.loads(raw)
    except (ValueError, RecursionError) as error:
        # Besides JSONDecodeError, json raises ValueError for an over-long integer and RecursionError for deep nesting.
        raise ScopeBindingError from error
    if not isinstance(value, dict):
        raise ScopeBindingError
    return value


def read_error_code(error: HTTPError, *, deadline: float) -> str | None:
    """Return the code of one error body, failing the request when the body exceeds the size bound or breaks off."""

    try:
        raw = _read_bounded(error, deadline=deadline)
    except (OSError, HTTPException) as failure:
        # A stalled, reset, or truncated body is a transport failure, as it is for a successful response.
        raise ScopeBindingUnavailableError from failure
    try:
        body: Any = json.loads(raw)
    except (ValueError, RecursionError):
        return None
    detail = body.get("error") if isinstance(body, dict) else None
    code = detail.get("code") if isinstance(detail, dict) else None
    return code if isinstance(code, str) else None


def _remaining_time(deadline: float) -> float:
    remaining = deadline - monotonic()
    if remaining <= 0:
        raise ScopeBindingUnavailableError
    return remaining


def _set_response_timeout(response: object, timeout: float) -> None:
    """Tighten urllib's socket timeout before each bounded read."""

    raw = getattr(getattr(response, "fp", None), "raw", None)
    sock = getattr(raw, "_sock", None)
    settimeout = getattr(sock, "settimeout", None)
    if settimeout is not None:
        settimeout(timeout)


def _read_bounded(response: _Response, *, deadline: float) -> bytes:
    bind_response_deadline(response, deadline)
    chunks: list[bytes] = []
    size = 0
    while True:
        _set_response_timeout(response, _remaining_time(deadline))
        chunk = response.read(min(65_536, _MAX_RESPONSE_BYTES + 1 - size))
        if not chunk:
            return complete_body(response, b"".join(chunks))
        size += len(chunk)
        if size > _MAX_RESPONSE_BYTES:
            raise ScopeBindingError
        chunks.append(chunk)


def _git_timeout(deadline: float | None) -> float:
    return 2.0 if deadline is None else min(2.0, _remaining_time(deadline))
