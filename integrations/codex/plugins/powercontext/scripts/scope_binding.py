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

import os
import subprocess
import sys
from hashlib import sha256
from pathlib import Path
from shutil import which
from time import monotonic

from powercontext.client.integration.native import (
    ScopeBindingError,
    ScopeBindingUnavailableError,
)
from powercontext.client.integration.native import (
    ScopeBindingRejectedError as ScopeBindingRejectedError,
)
from powercontext.client.integration.native import (
    ScopeBindingStatusError as ScopeBindingStatusError,
)
from powercontext.client.integration.native import (
    scope_request as _post_json,
)

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PLUGIN_ROOT))

from settings import CodexPluginSettings  # noqa: E402


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


def _remaining_time(deadline: float) -> float:
    remaining = deadline - monotonic()
    if remaining <= 0:
        raise ScopeBindingUnavailableError
    return remaining


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
