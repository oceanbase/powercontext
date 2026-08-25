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

"""Resolve Claude Code identities through the PowerContext Scope service."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections.abc import Mapping, Sequence
from hashlib import sha256
from pathlib import Path
from shutil import which
from time import monotonic
from typing import Any, Protocol
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from typing_extensions import override

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PLUGIN_ROOT))

from claude_code_settings import ClaudeCodePluginSettings  # noqa: E402
from scope_binding_errors import (  # noqa: E402
    ScopeBindingError,
    ScopeBindingRejectedError,
    ScopeBindingStatusError,
    ScopeBindingUnavailableError,
)

_MAX_RESPONSE_BYTES = 1_048_576
_REQUEST_HEADERS = {
    "Accept": "application/json",
    "Content-Type": "application/json",
    "User-Agent": "powercontext-claude-code-plugin/0.1.1",
}


class ScopeBindingSettings(Protocol):
    """Settings required by the dependency-free binding client."""

    server_url: str
    authorization: str | None
    scope_id: str | None
    request_timeout_seconds: float


class _Response(Protocol):
    status: int

    def __enter__(self) -> _Response: ...

    def __exit__(self, *args: object) -> object: ...

    def read(self, amount: int = -1) -> bytes: ...


class _RejectRedirects(HTTPRedirectHandler):
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


def resolve_scope_id(
    cwd: str,
    *,
    session_id: str | None,
    settings: ScopeBindingSettings,
    deadline: float,
) -> str:
    """Resolve explicit, session, workspace, then server-default binding."""

    response = _request_json(
        "/v1/scope-bindings/resolve",
        {
            "explicit_scope_id": settings.scope_id,
            "binding_keys": binding_keys(cwd, session_id=session_id, deadline=deadline),
        },
        settings=settings,
        deadline=deadline,
    )
    scope_id = response.get("scope_id")
    if not isinstance(scope_id, str) or not scope_id.strip() or scope_id != scope_id.strip():
        raise ScopeBindingError
    return scope_id


def bind_scope(
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
    settings: ScopeBindingSettings,
    deadline: float,
) -> bool:
    """Remove the durable workspace binding from the Scope service."""

    response = _request_json(
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
    return {"integration": "claude-code", "kind": "session", "external_id": value}


def workspace_binding_key(cwd: str, *, deadline: float | None = None) -> dict[str, str]:
    root_value = _git_value(cwd, "rev-parse", "--show-toplevel", timeout=_git_timeout(deadline))
    root = Path(root_value or cwd).resolve(strict=False)
    return {
        "integration": "claude-code",
        "kind": "workspace",
        "external_id": sha256(os.fsencode(root)).hexdigest(),
    }


def _request_json(
    path: str,
    payload: Mapping[str, object],
    *,
    settings: ScopeBindingSettings,
    deadline: float,
    method: str = "POST",
) -> Mapping[str, object]:
    remaining = _remaining_time(deadline)
    headers = dict(_REQUEST_HEADERS)
    if settings.authorization is not None:
        headers["Authorization"] = settings.authorization
    request = Request(  # noqa: S310 - settings validates the configured transport.
        f"{settings.server_url}{path}",
        data=json.dumps(payload, separators=(",", ":")).encode(),
        headers=headers,
        method=method,
    )
    try:
        with _URL_OPENER.open(request, timeout=min(settings.request_timeout_seconds, remaining)) as response:
            if response.status < 200 or response.status >= 300:
                raise ScopeBindingStatusError(response.status, path)
            raw = _read_bounded(response, deadline=deadline)
    except HTTPError as error:
        if error.code == 401:
            raise ScopeBindingRejectedError from error
        if error.code == 503:
            raise ScopeBindingUnavailableError from error
        raise ScopeBindingStatusError(error.code, path) from error
    except (OSError, TimeoutError) as error:
        raise ScopeBindingUnavailableError from error
    try:
        value: Any = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ScopeBindingError from error
    if not isinstance(value, dict):
        raise ScopeBindingError
    return value


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
    chunks: list[bytes] = []
    size = 0
    while True:
        _set_response_timeout(response, _remaining_time(deadline))
        chunk = response.read(1)
        if not chunk:
            return b"".join(chunks)
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
        completed = subprocess.run(  # noqa: S603 - git executable and arguments are integration-owned.
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
    settings = ClaudeCodePluginSettings.from_environment()
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
