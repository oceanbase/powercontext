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

"""Bind PowerContext MCP data-plane calls to the current Antigravity Session."""

from __future__ import annotations

import contextlib
import json
import sys
from pathlib import Path
from time import monotonic

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
if str(_PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_ROOT))

_BINDING_FAILURE_REASON = "PowerContext could not resolve the current Scope binding."


def _emit(decision: dict[str, object]) -> None:
    with contextlib.suppress(BrokenPipeError):
        sys.stdout.write(json.dumps(decision, ensure_ascii=False, separators=(",", ":")) + "\n")


def _ask(overwrite: dict[str, object] | None = None) -> None:
    if overwrite is None:
        _emit({"decision": "ask"})
    else:
        _emit({"decision": "ask", "overwrite": overwrite})


def _deny(reason: str = _BINDING_FAILURE_REASON) -> None:
    _emit({"decision": "deny", "reason": reason})


try:
    from antigravity_host import ANTIGRAVITY, debug_log, resolve_cwd, resolve_session_id
    from powercontext_lifecycle.scope_binding import binding_keys, resolve_scope_id, session_binding_key
    from powercontext_lifecycle.scope_binding_errors import ScopeBindingRejectedError, ScopeBindingStatusError
    from settings import (
        PRE_TOOL_USE_HOST_TIMEOUT_SECONDS,
        AntigravityPluginSettings,
        host_bounded_http_budget,
    )
except Exception:
    if __name__ != "__main__":
        raise
    sys.stdin.read()
    _deny()
    raise SystemExit(0) from None

_AUTHENTICATION_FAILURE_REASON = (
    "PowerContext authentication failed (HTTP 401): the configured credential was rejected. "
    "Set POWERCONTEXT_ANTIGRAVITY_AUTHORIZATION or update credentials.json."
)
_AUTHORIZATION_DENIED_REASON = (
    "PowerContext authorization denied (HTTP 403) while resolving the current Scope binding: "
    "the configured credential lacks permission."
)
_CONTROL_OPERATIONS = frozenset({"set_scope_binding", "clear_scope_binding"})
_RESOLVE_OPERATIONS = frozenset({"resolve_scope_binding"})
_HOST_OPERATIONS = frozenset({"create_scope", "get_scope", "list_scopes", "publish_artifact"})
_CURRENT_OPERATIONS = frozenset({
    "acknowledge_handoff",
    "activate_handoff",
    "approve_candidate",
    "capture_content_source",
    "capture_content_sources",
    "change_atomic_memory_lifecycle",
    "check_context",
    "capture_working_note",
    "commit_handoff",
    "continue_handoff",
    "create_remote_skill_target",
    "create_work_contract",
    "download_skill_package",
    "finalize_handoff",
    "flush_memory",
    "forget_artifact",
    "flush_topic_memory",
    "generate_experience",
    "generate_skill",
    "get_artifact",
    "get_artifact_governance",
    "get_candidate",
    "get_candidate_history",
    "get_artifact_revision",
    "get_atomic_memory_state",
    "get_experience",
    "get_memory_entry",
    "get_recall_manifest",
    "get_skill",
    "get_skill_package_manifest",
    "get_topic_memory",
    "get_topic_memory_processing_block",
    "get_topic_memory_processing_status",
    "handoff_current_work",
    "import_external_skill",
    "list_candidates",
    "list_atomic_memories",
    "list_external_skills",
    "list_managed_skills",
    "list_memory_entries",
    "list_remote_skill_targets",
    "merge_artifacts",
    "merge_atomic_memories",
    "prepare_context",
    "prepare_bootstrap_context",
    "prepare_handoff",
    "prepare_handoff_hint",
    "preview_atomic_memory_restoration",
    "propose_experience",
    "propose_skill",
    "propose_skill_package",
    "publish_remote_skill",
    "query_code",
    "record_skill_usage",
    "record_bootstrap_delivery",
    "record_task_outcome",
    "reject_candidate",
    "remember_memory",
    "replace_artifact",
    "rename_remote_skill_target",
    "resolve_external_skill",
    "restore_artifact",
    "restore_atomic_memory",
    "retry_topic_memory_processing",
    "revise_candidate",
    "revoke_remote_skill_target",
    "scan_external_skills",
    "search_atomic_memory",
    "search_memory",
    "search_topic_memory",
    "unpublish_remote_skill",
    "update_skill_lifecycle",
})
_SELECTION_OPERATIONS = frozenset({"get_handoff_report", "get_stats"})


def _is_powercontext_server(server_name: object) -> bool:
    if not isinstance(server_name, str):
        return False
    return server_name in ("powercontext", "powercontext_powercontext") or server_name.endswith("_powercontext")


def _extract_powercontext_call(name: object, args: object) -> tuple[str, dict[str, object], bool] | None:
    """Return (operation, tool_input, is_lazy_wrapper) or None if not a PowerContext MCP call."""

    if name == "call_mcp_tool":
        if not isinstance(args, dict) or not _is_powercontext_server(args.get("ServerName")):
            return None
        op = args.get("ToolName")
        raw_args = {} if args.get("Arguments") is None else args.get("Arguments")
        if not isinstance(op, str) or not op.strip() or not isinstance(raw_args, dict):
            raise ValueError("invalid PowerContext call_mcp_tool payload")  # noqa: TRY003
        return op.strip(), {str(k): v for k, v in raw_args.items()}, True

    if isinstance(name, str):
        for prefix in ("mcp_powercontext_powercontext_", "mcp_powercontext_"):
            if name.startswith(prefix):
                op = name.removeprefix(prefix)
                if not isinstance(args, dict):
                    raise ValueError("invalid PowerContext eager MCP payload")  # noqa: TRY003
                return op, {str(k): v for k, v in args.items()}, False
    return None


def _denial_reason(error: Exception) -> str:
    if isinstance(error, ScopeBindingRejectedError):
        return _AUTHENTICATION_FAILURE_REASON
    if isinstance(error, ScopeBindingStatusError):
        if error.status == 401:
            return _AUTHENTICATION_FAILURE_REASON
        if error.status == 403:
            return _AUTHORIZATION_DENIED_REASON
    return _BINDING_FAILURE_REASON


def _rewrite_payload(updated_input: dict[str, object], *, is_lazy: bool) -> None:
    _ask({"Arguments": updated_input} if is_lazy else updated_input)


def _bind_operation(
    operation: str,
    tool_input: dict[str, object],
    *,
    is_lazy: bool,
    cwd: str,
    session_id: str,
    loaded: AntigravityPluginSettings,
) -> None:
    if operation in _CONTROL_OPERATIONS:
        updated = dict(tool_input)
        updated["key"] = session_binding_key(ANTIGRAVITY, session_id)
        debug_log("pre-tool-use", "bound", operation=operation, session_id=session_id, mode="control")
        _rewrite_payload(updated, is_lazy=is_lazy)
        return

    if operation in _RESOLVE_OPERATIONS:
        debug_log("pre-tool-use", "bound", operation=operation, session_id=session_id, mode="resolve")
        _rewrite_payload(
            {
                "explicit_scope_id": loaded.scope_id,
                "binding_keys": binding_keys(ANTIGRAVITY, cwd, session_id=session_id),
            },
            is_lazy=is_lazy,
        )
        return

    if operation in _HOST_OPERATIONS:
        _ask()
        return

    selection = operation in _SELECTION_OPERATIONS or (
        operation not in _CURRENT_OPERATIONS and tool_input.get("selection") is not None
    )
    if not selection and operation not in _CURRENT_OPERATIONS and tool_input.get("scope_id") is None:
        _ask()
        return

    budget = host_bounded_http_budget(
        loaded.http_budget_seconds,
        host_timeout=PRE_TOOL_USE_HOST_TIMEOUT_SECONDS,
    )
    scope_id = resolve_scope_id(
        ANTIGRAVITY,
        cwd,
        session_id=session_id,
        settings=loaded.scope_binding_settings(),
        deadline=monotonic() + budget,
        persist_session=True,
    )
    updated = dict(tool_input)
    if selection:
        updated["selection"] = {"mode": "exact", "scope_ids": [scope_id]}
    else:
        updated["scope_id"] = scope_id
    debug_log(
        "pre-tool-use", "bound", operation=operation, session_id=session_id, scope_id=scope_id, selection=selection
    )
    _rewrite_payload(updated, is_lazy=is_lazy)


def main(settings: AntigravityPluginSettings | None = None) -> int:
    try:
        payload = json.load(sys.stdin)
        tool = payload.get("toolCall") if isinstance(payload, dict) else None
        extracted = _extract_powercontext_call(tool.get("name"), tool.get("args")) if isinstance(tool, dict) else None
        if extracted is None:
            _ask()
            return 0
    except Exception as error:
        _deny(_denial_reason(error))
        return 0

    operation, tool_input, is_lazy = extracted
    try:
        session_id = resolve_session_id(payload)
        cwd = resolve_cwd(payload)
        if session_id is None or cwd is None:
            _deny()
            return 0
        loaded = AntigravityPluginSettings.from_environment() if settings is None else settings
        _bind_operation(operation, tool_input, is_lazy=is_lazy, cwd=cwd, session_id=session_id, loaded=loaded)
    except Exception as error:
        debug_log("pre-tool-use", "deny", operation=operation, error=str(error))
        _deny(_denial_reason(error))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
