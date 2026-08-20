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

"""Stable project scope derivation for Pydantic AI runs."""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
from collections.abc import Callable
from pathlib import Path
from shutil import which
from typing import Any, TypeAlias, cast
from urllib.parse import urlsplit

from pydantic_ai import RunContext

from powercontext.limits import MAX_SCOPE_ID_LENGTH

ScopeId: TypeAlias = str | Callable[[RunContext[Any]], str] | None

_SCP_REMOTE = re.compile(r"^(?:[^@/\s]+@)?(?P<host>[^:/\s]+):(?P<path>.+)$")


def resolve_scope_id(
    ctx: RunContext[Any],
    constructor_scope_id: ScopeId,
    settings_scope_id: str | None,
    *,
    cwd: str | os.PathLike[str] | None = None,
) -> str:
    """Resolve one scope using constructor, environment, Git, then local precedence."""

    if isinstance(constructor_scope_id, str):
        return _bounded_explicit(_require_scope(constructor_scope_id))
    if constructor_scope_id is not None:
        resolver = cast(Callable[[RunContext[Any]], str], constructor_scope_id)
        return _bounded_explicit(_require_scope(resolver(ctx)))
    if settings_scope_id is not None:
        return _bounded_explicit(_require_scope(settings_scope_id))
    return derive_scope_id(cwd)


def derive_scope_id(cwd: str | os.PathLike[str] | None = None) -> str:
    """Derive a stable scope from the normalized origin or resolved project path."""

    working_directory = os.fspath(cwd) if cwd is not None else os.getcwd()
    root_value = _git_value(working_directory, "rev-parse", "--show-toplevel")
    project_root = Path(root_value or working_directory).resolve(strict=False)
    remote = _git_value(os.fspath(project_root), "config", "--get", "remote.origin.url")
    normalized_remote = normalize_git_remote(remote) if remote else None
    if normalized_remote:
        return _bounded("git", normalized_remote)
    return f"local:{hashlib.sha256(os.fsencode(project_root)).hexdigest()}"


def normalize_git_remote(remote: str) -> str | None:
    """Normalize common network remotes without retaining credentials."""

    value = remote.strip()
    if not value:
        return None
    scp_match = _SCP_REMOTE.fullmatch(value)
    if scp_match and "://" not in value:
        host = scp_match.group("host").lower()
        path = _normalize_path(scp_match.group("path"))
        return f"{host}/{path}" if path else None
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https", "ssh", "git"} or parsed.hostname is None:
        return None
    host = parsed.hostname.lower()
    if parsed.port is not None:
        host = f"{host}:{parsed.port}"
    path = _normalize_path(parsed.path)
    return f"{host}/{path}" if path else None


def _normalize_path(path: str) -> str:
    normalized = "/".join(part for part in path.replace("\\", "/").split("/") if part)
    if normalized.endswith(".git"):
        normalized = normalized[:-4]
    return normalized.rstrip("/")


def _bounded(prefix: str, value: str) -> str:
    candidate = f"{prefix}:{value}"
    if len(candidate) <= MAX_SCOPE_ID_LENGTH:
        return candidate
    return f"{prefix}:sha256:{hashlib.sha256(value.encode()).hexdigest()}"


def _bounded_explicit(value: str) -> str:
    if len(value) <= MAX_SCOPE_ID_LENGTH:
        return value
    return f"sha256:{hashlib.sha256(value.encode()).hexdigest()}"


def _require_scope(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError("PowerContext scope callback must return a string")  # noqa: TRY003
    normalized = value.strip()
    if not normalized:
        raise ValueError("PowerContext scope_id must contain non-whitespace characters")  # noqa: TRY003
    return normalized


def _git_value(cwd: str, *arguments: str) -> str | None:
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
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return completed.stdout.strip() or None
