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

"""Bind PowerContext MCP data-plane calls to the current Codex Session."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from time import monotonic
from typing import Any, cast

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PLUGIN_ROOT))

from scripts.scope_binding import (  # noqa: E402
    ScopeBindingError,
    binding_keys,
    resolve_scope_id,
    session_binding_key,
)
from settings import CodexPluginSettings  # noqa: E402

_PREFIX = "mcp__powercontext__"
_CONTROL_OPERATIONS = frozenset({"set_scope_binding", "clear_scope_binding"})
_CURRENT_SCOPE_OPERATIONS = frozenset({"resolve_scope_binding"})
_HOST_OPERATIONS = frozenset({"create_scope", "get_scope", "list_scopes", "publish_artifact"})
_SCOPE_BOUND_OPERATIONS = frozenset({
    "acknowledge_handoff",
    "activate_handoff",
    "approve_artifact_candidate",
    "capture_content_source",
    "commit_handoff",
    "continue_handoff",
    "create_work_contract",
    "finalize_handoff",
    "get_artifact_candidate",
    "get_handoff_report",
    "get_memory_entry",
    "handoff_current_work",
    "list_artifact_candidates",
    "list_memory_entries",
    "record_task_outcome",
    "reject_artifact_candidate",
    "remember_memory",
    "retire_memory_entry",
    "revise_artifact_candidate",
    "revise_memory_entry",
    "search_memory",
})


def main(settings: CodexPluginSettings | None = None) -> int:
    try:
        payload = cast(dict[str, Any], json.load(sys.stdin))
        tool_name = payload.get("tool_name")
        tool_input = payload.get("tool_input")
        session_id = payload.get("session_id")
        cwd = payload.get("cwd")
        if (
            not isinstance(tool_name, str)
            or not tool_name.startswith(_PREFIX)
            or not isinstance(tool_input, dict)
            or not isinstance(session_id, str)
            or not isinstance(cwd, str)
        ):
            return 0
        operation = tool_name.removeprefix(_PREFIX)
        if operation in _CONTROL_OPERATIONS:
            updated = dict(tool_input)
            updated["key"] = session_binding_key(session_id)
            _allow(updated)
            return 0
        if operation in _CURRENT_SCOPE_OPERATIONS:
            settings = CodexPluginSettings() if settings is None else settings
            _allow({
                "explicit_scope_id": settings.scope_id,
                "binding_keys": binding_keys(cwd, session_id=session_id),
            })
            return 0
        if operation in _HOST_OPERATIONS:
            _allow(dict(tool_input))
            return 0
        if operation not in _SCOPE_BOUND_OPERATIONS:
            return 0
        settings = CodexPluginSettings() if settings is None else settings
        scope_id = resolve_scope_id(
            cwd,
            session_id=session_id,
            settings=settings,
            deadline=monotonic() + settings.http_budget_seconds,
        )
        updated = dict(tool_input)
        if operation == "get_handoff_report":
            updated["selection"] = {"mode": "exact", "scope_ids": [scope_id]}
        else:
            updated["scope_id"] = scope_id
        _allow(updated)
    except (ScopeBindingError, ValueError, OSError, json.JSONDecodeError):
        _deny()
    return 0


def _allow(updated_input: dict[str, object]) -> None:
    json.dump(
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "allow",
                "updatedInput": updated_input,
            }
        },
        sys.stdout,
        separators=(",", ":"),
    )
    sys.stdout.write("\n")


def _deny() -> None:
    json.dump(
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": "PowerContext could not resolve the current Scope binding.",
            }
        },
        sys.stdout,
        separators=(",", ":"),
    )
    sys.stdout.write("\n")


if __name__ == "__main__":
    raise SystemExit(main())
