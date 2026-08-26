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

"""Invocation scope and stable scope resolution for the LangChain middleware."""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
from dataclasses import dataclass, field
from shutil import which
from urllib.parse import urlsplit

from powercontext.limits import MAX_SCOPE_ID_LENGTH

_SCP_REMOTE = re.compile(r"^(?:[^@/\s]+@)?(?P<host>[^:/\s]+):(?P<path>.+)$")


@dataclass(frozen=True, slots=True)
class PowerContextScope:
    """PowerContext configuration for one LangChain agent invocation.

    Pass this value as the ``context`` argument to ``agent.invoke`` or ``agent.ainvoke``. Each invocation of one
    agent can use a different scope. Every field is optional; unset fields fall back to the LangChain integration's
    environment settings.
    """

    scope_id: str | None = None
    base_url: str | None = None
    token: str | None = field(default=None, repr=False)
    timeout: float | None = None


class MissingScopeError(ValueError):
    """Raised when no explicit scope is configured and no Git remote is available."""

    def __init__(self) -> None:
        super().__init__(
            "PowerContext scope could not be resolved. Set POWERCONTEXT_LANGCHAIN_SCOPE_ID, pass "
            "PowerContextScope(scope_id=...), or run inside a Git repository with a network remote."
        )


def resolve_scope_id(scope_id: str | None = None, *, cwd: str | None = None) -> str:
    """Return an explicit or Git-derived scope, or raise when neither is available."""

    explicit = (scope_id or "").strip()
    if explicit:
        return _bounded_explicit(explicit)

    remote = _git_remote(cwd or os.getcwd())
    normalized_remote = normalize_git_remote(remote) if remote else None
    if normalized_remote:
        return _bounded("git", normalized_remote)

    raise MissingScopeError


def normalize_git_remote(remote: str) -> str | None:
    """Normalize common network remotes without retaining credentials."""

    value = remote.strip()
    if not value:
        return None
    scp_match = _SCP_REMOTE.fullmatch(value)
    if scp_match and "://" not in value:
        host = scp_match.group("host").lower()
        if len(host) == 1 and host.isalpha():
            return None
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


def _git_remote(cwd: str) -> str | None:
    executable = which("git")
    if executable is None:
        return None
    try:
        completed = subprocess.run(  # noqa: S603 - git executable and arguments are integration-owned.
            [executable, "config", "--get", "remote.origin.url"],
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return completed.stdout.strip() or None


__all__ = ["MissingScopeError", "PowerContextScope", "normalize_git_remote", "resolve_scope_id"]
