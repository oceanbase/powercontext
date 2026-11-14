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

"""Resolve Codex external identities through the PowerContext Scope service."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections.abc import Mapping, Sequence
from hashlib import sha256
from http.client import HTTPException
from pathlib import Path
from shutil import which
from time import monotonic
from typing import Any, Protocol
from urllib.error import HTTPError
from urllib.request import Request

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PLUGIN_ROOT))

from plugin_version import PLUGIN_VERSION  # noqa: E402
from powercontext_lifecycle.scope_binding_errors import (  # noqa: E402
    ScopeBindingError,
    ScopeBindingRejectedError,
    ScopeBindingStatusError,
    ScopeBindingUnavailableError,
)
from powercontext_lifecycle.transport import bind_response_deadline, complete_body, open_bounded  # noqa: E402
from settings import CodexPluginSettings  # noqa: E402

_MAX_RESPONSE_BYTES = 1_048_576
_REQUEST_HEADERS = {
    "Accept": "application/json",
    "Content-Type": "application/json",
    "User-Agent": f"powercontext-codex-plugin/{PLUGIN_VERSION}",
}


class _Response(Protocol):
    def read(self, amount: int = -1, /) -> bytes: ...


def resolve_scope_id(
    cwd: str,
    *,
    session_id: str | None,
    settings: CodexPluginSettings,
    deadline: float,
    persist_session: bool = False,
) -> str:
    """Resolve explicit, session, workspace, then default binding in that order."""

    keys = binding_keys(cwd, session_id=session_id, deadline=deadline)
    response = _post_json(
        "/v1/scope-bindings/resolve",
        {
            "explicit_scope_id": settings.scope_id,
            "binding_keys": keys,
        },
        settings=settings,
        deadline=deadline,
    )
    scope_id = response.get("scope_id")
    if not isinstance(scope_id, str) or not scope_id.strip() or scope_id != scope_id.strip():
        raise ScopeBindingError
    if persist_session and session_id is not None and settings.scope_id is None:
        _post_json(
            "/v1/scope-bindings",
            {
                "key": session_binding_key(session_id),
                "scope_id": scope_id,
            },
            settings=settings,
            deadline=deadline,
            method="PUT",
        )
    return scope_id


def bind_scope(
    cwd: str,
    scope_id: str,
    /,
    *,
    settings: CodexPluginSettings,
    deadline: float,
) -> str:
    """Persist the Codex workspace identity to one server-owned Scope."""

    response = _post_json(
        "/v1/scope-bindings",
        {"key": workspace_binding_key(cwd, deadline=deadline), "scope_id": scope_id},
        settings=settings,
        deadline=deadline,
        method="PUT",
    )
    resolved = response.get("scope_id")
    if not isinstance(resolved, str) or resolved != scope_id:
        raise ScopeBindingError
    return resolved


def clear_scope_binding(
    cwd: str,
    /,
    *,
    settings: CodexPluginSettings,
    deadline: float,
) -> bool:
    """Remove the durable Codex workspace binding from the Scope service."""

    response = _post_json(
        "/v1/scope-bindings/clear",
        {"key": workspace_binding_key(cwd, deadline=deadline)},
        settings=settings,
        deadline=deadline,
    )
    cleared = response.get("cleared")
    if not isinstance(cleared, bool):
        raise ScopeBindingError
    return cleared


def binding_keys(cwd: str, *, session_id: str | None, deadline: float | None = None) -> list[dict[str, str]]:
    keys: list[dict[str, str]] = []
    if session_id is not None:
        keys.append(session_binding_key(session_id))
    keys.append(workspace_binding_key(cwd, deadline=deadline))
    return keys


def session_binding_key(session_id: str) -> dict[str, str]:
    value = session_id.strip()
    if not value or len(value) > 256:
        raise ScopeBindingError
    return {"integration": "codex", "kind": "session", "external_id": value}


def workspace_binding_key(cwd: str, *, deadline: float | None = None) -> dict[str, str]:
    root_value = _git_value(cwd, "rev-parse", "--show-toplevel", timeout=_git_timeout(deadline))
    root = Path(root_value or cwd).resolve(strict=False)
    external_id = sha256(os.fsencode(root)).hexdigest()
    return {"integration": "codex", "kind": "workspace", "external_id": external_id}


def _post_json(
    path: str,
    payload: Mapping[str, object],
    *,
    settings: CodexPluginSettings,
    deadline: float,
    method: str = "POST",
) -> Mapping[str, object]:
    remaining = _remaining_time(deadline)
    request_deadline = min(deadline, monotonic() + settings.request_timeout_seconds)
    headers = dict(_REQUEST_HEADERS)
    if settings.authorization is not None:
        headers["Authorization"] = settings.authorization.get_secret_value()
    request = Request(  # noqa: S310 - settings validates the configured transport.
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
        chunk = response.read(1)
        if not chunk:
            return complete_body(response, b"".join(chunks))
        size += len(chunk)
        if size > _MAX_RESPONSE_BYTES:
            raise ScopeBindingError
        chunks.append(chunk)


def _git_timeout(deadline: float | None) -> float:
    return 2.0 if deadline is None else min(2.0, _remaining_time(deadline))


def _git_value(cwd: str, *arguments: str, timeout: float = 2.0) -> str | None:
    executable = which("git")
    if executable is None:
        return None
    try:
        completed = subprocess.run(  # noqa: S603 - executable and arguments are integration-owned.
            [executable, *arguments],
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return completed.stdout.strip() or None


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cwd", default=os.getcwd())
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--bind-scope", metavar="SCOPE_ID")
    action.add_argument("--clear-scope", action="store_true")
    arguments = parser.parse_args(argv)
    settings = CodexPluginSettings()
    deadline = monotonic() + settings.http_budget_seconds
    if arguments.bind_scope is not None:
        print(bind_scope(arguments.cwd, arguments.bind_scope, settings=settings, deadline=deadline))
        return 0
    if arguments.clear_scope:
        clear_scope_binding(arguments.cwd, settings=settings, deadline=deadline)
    print(resolve_scope_id(arguments.cwd, session_id=None, settings=settings, deadline=deadline))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
