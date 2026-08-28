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

"""Derive a stable PowerContext scope for one project directory."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from collections.abc import Sequence
from contextlib import suppress
from pathlib import Path
from shutil import which
from urllib.parse import urlsplit

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PLUGIN_ROOT))

from claude_code_settings import ClaudeCodePluginSettings  # noqa: E402

_MAX_SCOPE_LENGTH = 256
_SCP_REMOTE = re.compile(r"^(?:[^@/\s]+@)?(?P<host>[^:/\s]+):(?P<path>.+)$")
_WORKSPACE_STATE_SCHEMA = "powercontext.codex-workspace.v1"
_WORKSPACE_STATE_DIRECTORY = "powercontext"
_WORKSPACE_STATE_FILE = "codex-workspace.json"


def resolve_scope_id(cwd: str, *, configured_scope_id: str | None = None) -> str:
    """Return the configured, workspace-bound, or derived scope for one checkout."""

    if configured_scope_id:
        return _bounded_explicit(configured_scope_id)
    bound_scope_id = read_bound_scope_id(cwd)
    if bound_scope_id is not None:
        return bound_scope_id
    return derive_scope_id(cwd)


def derive_scope_id(cwd: str, *, configured_scope_id: str | None = None) -> str:
    """Return an explicit, remote-derived, or path-derived project scope."""

    if configured_scope_id:
        return _bounded_explicit(configured_scope_id)
    root_value = _git_value(cwd, "rev-parse", "--show-toplevel")
    project_root = Path(root_value or cwd).resolve(strict=False)
    remote = _git_value(str(project_root), "config", "--get", "remote.origin.url")
    normalized_remote = normalize_git_remote(remote) if remote else None
    if normalized_remote:
        return _bounded("git", normalized_remote)
    return f"local:{hashlib.sha256(os.fsencode(project_root)).hexdigest()}"


def bind_workstream_scope(cwd: str, scope_id: str, /) -> str:
    """Persist one shared Workstream scope in Git-private state."""

    normalized_scope_id = _bounded_explicit(scope_id.strip())
    if not normalized_scope_id:
        raise ValueError("Workstream scope must be non-empty")  # noqa: TRY003
    state_path = _workspace_state_path(cwd)
    if state_path is None:
        raise ValueError("Workstream scope binding requires a Git workspace")  # noqa: TRY003
    _write_workspace_state(state_path, normalized_scope_id)
    return normalized_scope_id


def read_bound_scope_id(cwd: str, /) -> str | None:
    """Read the shared Codex/Claude Workstream binding from Git-private state."""

    state_path = _workspace_state_path(cwd)
    if state_path is None:
        return None
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("schema") != _WORKSPACE_STATE_SCHEMA:
        return None
    scope_id = payload.get("scope_id")
    if (
        not isinstance(scope_id, str)
        or not scope_id
        or scope_id != scope_id.strip()
        or len(scope_id) > _MAX_SCOPE_LENGTH
    ):
        return None
    return scope_id


def clear_workstream_scope(cwd: str, /) -> bool:
    """Remove only the Git-private shared Workstream binding file."""

    state_path = _workspace_state_path(cwd)
    if state_path is None:
        return False
    try:
        state_path.unlink()
    except FileNotFoundError:
        return False
    return True


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
    if len(candidate) <= _MAX_SCOPE_LENGTH:
        return candidate
    return f"{prefix}:sha256:{hashlib.sha256(value.encode()).hexdigest()}"


def _bounded_explicit(value: str) -> str:
    if len(value) <= _MAX_SCOPE_LENGTH:
        return value
    return f"sha256:{hashlib.sha256(value.encode()).hexdigest()}"


def _workspace_state_path(cwd: str, /) -> Path | None:
    git_directory = _git_value(cwd, "rev-parse", "--absolute-git-dir")
    if git_directory is None:
        return None
    return Path(git_directory) / _WORKSPACE_STATE_DIRECTORY / _WORKSPACE_STATE_FILE


def _write_workspace_state(state_path: Path, scope_id: str, /) -> None:
    state_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary_path = state_path.with_name(f".{state_path.name}.{os.getpid()}.tmp")
    encoded = (
        json.dumps({"schema": _WORKSPACE_STATE_SCHEMA, "scope_id": scope_id}, separators=(",", ":")) + "\n"
    ).encode()
    descriptor: int | None = None
    try:
        descriptor = os.open(temporary_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.write(descriptor, encoded)
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        os.replace(temporary_path, state_path)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        with suppress(FileNotFoundError):
            temporary_path.unlink()


def _git_value(cwd: str, *arguments: str) -> str | None:
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
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return completed.stdout.strip() or None


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cwd", default=os.getcwd())
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--bind-workstream", metavar="SCOPE_ID")
    action.add_argument("--clear-workstream", action="store_true")
    arguments = parser.parse_args(argv)
    if arguments.bind_workstream is not None:
        print(bind_workstream_scope(arguments.cwd, arguments.bind_workstream))
        return 0
    if arguments.clear_workstream:
        clear_workstream_scope(arguments.cwd)
    settings = ClaudeCodePluginSettings.from_environment()
    print(resolve_scope_id(arguments.cwd, configured_scope_id=settings.scope_id))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
