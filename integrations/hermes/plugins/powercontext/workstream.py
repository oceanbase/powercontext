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

"""Git-private Workstream scope binding for the Hermes integration."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from shutil import which

from .helpers import safe_scope

WORKSTREAM_STATE_SCHEMA = "powercontext.codex-workspace.v1"
WORKSTREAM_STATE_DIRECTORY = "powercontext"
WORKSTREAM_STATE_FILE = "codex-workspace.json"


def git_value(cwd: str, *arguments: str) -> str | None:
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


def state_path(cwd: str) -> Path | None:
    git_directory = git_value(cwd, "rev-parse", "--absolute-git-dir")
    if git_directory is None:
        return None
    return Path(git_directory) / WORKSTREAM_STATE_DIRECTORY / WORKSTREAM_STATE_FILE


def read_scope(cwd: str) -> str | None:
    path = state_path(cwd)
    if path is None:
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(value, dict) or value.get("schema") != WORKSTREAM_STATE_SCHEMA:
        return None
    scope_id = value.get("scope_id")
    if not isinstance(scope_id, str) or not scope_id.strip() or len(scope_id) > 256:
        return None
    return safe_scope(scope_id)


def write_scope(cwd: str, scope_id: str) -> Path:
    path = state_path(cwd)
    if path is None:
        raise ValueError("Workstream binding requires a Git workspace")  # noqa: TRY003
    normalized_scope_id = safe_scope(scope_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary_path.write_text(
            json.dumps({"schema": WORKSTREAM_STATE_SCHEMA, "scope_id": normalized_scope_id}, separators=(",", ":"))
            + "\n",
            encoding="utf-8",
        )
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)
    return path


def clear_scope(cwd: str) -> bool:
    path = state_path(cwd)
    if path is None:
        return False
    try:
        path.unlink()
    except FileNotFoundError:
        return False
    except OSError:
        return False
    return True
