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

"""What the shared lifecycle modules need to know about Antigravity."""

from __future__ import annotations

import contextlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from plugin_version import PLUGIN_VERSION
from powercontext_lifecycle.bootstrap_state import BootstrapState
from powercontext_lifecycle.diagnostics import DiagnosticThrottle
from powercontext_lifecycle.host_profile import HostProfile

ANTIGRAVITY = HostProfile(
    integration="antigravity",
    user_agent=f"powercontext-antigravity-plugin/{PLUGIN_VERSION}",
    component="powercontext.antigravity",
    prompt_id_field="turn_id",
    diagnostics=DiagnosticThrottle(state_file="antigravity-diagnostics.json"),
    bootstrap_state=BootstrapState(
        directory_variable="POWERCONTEXT_BOOTSTRAP_STATE_DIR",
        plugin_data_variable="PLUGIN_DATA",
    ),
)

_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_MAX_DEBUG_LOG_BYTES = 2 * 1024 * 1024


def debug_log_path() -> Path:
    """Return the opt-in debug log location for Antigravity hooks."""

    override = os.environ.get("POWERCONTEXT_ANTIGRAVITY_DEBUG_LOG", "").strip()
    if override:
        return Path(override).expanduser()
    plugin_data = os.environ.get("PLUGIN_DATA", "").strip()
    if plugin_data:
        return Path(plugin_data).expanduser() / "antigravity-hooks.log"
    return Path.home() / ".config" / "powercontext" / "antigravity-hooks.log"


def debug_log(hook_name: str, stage: str, **data: Any) -> None:
    """Write structured diagnostics only when explicitly opted in via environment."""

    if os.environ.get("POWERCONTEXT_ANTIGRAVITY_DEBUG", "").strip().lower() not in _TRUE_VALUES:
        return
    with contextlib.suppress(Exception):
        path = debug_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        if os.name != "nt":
            with contextlib.suppress(OSError):
                os.chmod(path.parent, 0o700)
        if path.is_file() and path.stat().st_size >= _MAX_DEBUG_LOG_BYTES:
            os.replace(path, path.with_suffix(".log.1"))
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "hook": f"antigravity:{hook_name}",
            "stage": stage,
            "data": data,
        }
        line = (json.dumps(entry, ensure_ascii=False) + "\n").encode("utf-8")
        if os.name == "nt":
            with path.open("ab") as fh:
                fh.write(line)
        else:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            try:
                os.write(fd, line)
            finally:
                os.close(fd)


def normalize_workspace_path(raw: str) -> str:
    """Convert file:// workspace URIs to local filesystem paths."""

    stripped = raw.strip()
    if stripped.startswith("file://"):
        return unquote(urlsplit(stripped).path)
    return stripped


def resolve_cwd(payload: dict[str, Any]) -> str | None:
    """Extract the workspace root path from an Antigravity hook payload."""

    ws_paths = payload.get("workspacePaths")
    if isinstance(ws_paths, list):
        for item in ws_paths:
            if isinstance(item, str) and item.strip():
                normalized = normalize_workspace_path(item)
                if normalized:
                    return normalized
    for key in ("cwd", "workspace", "workspacePath", "rootPath"):
        val = payload.get(key)
        if isinstance(val, str) and val.strip():
            normalized = normalize_workspace_path(val)
            if normalized:
                return normalized
    return None


def resolve_session_id(payload: dict[str, Any]) -> str | None:
    """Extract and namespace an Antigravity conversation/session identifier."""

    for key in ("conversationId", "conversation_id", "sessionId", "session_id"):
        val = payload.get(key)
        if isinstance(val, str) and val.strip():
            raw = val.strip()
            return raw if raw.startswith("ag-") else f"ag-{raw}"
    return None
