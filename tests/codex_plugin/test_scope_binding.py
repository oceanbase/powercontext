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

from __future__ import annotations

import io
import json
import sys
from types import ModuleType

import pytest


def test_pre_tool_hook_overwrites_agent_scope_with_session_binding(
    bind_tools_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        bind_tools_module,
        "resolve_scope_id",
        lambda _cwd, *, session_id, **_kwargs: f"scope-for-{session_id}",
    )
    monkeypatch.setattr(
        sys,
        "stdin",
        io.StringIO(
            json.dumps({
                "hook_event_name": "PreToolUse",
                "session_id": "session-a",
                "cwd": "/workspace",
                "tool_name": "mcp__powercontext__remember_memory",
                "tool_input": {"scope_id": "agent-selected", "kind": "decision", "text": "Use Scope binding."},
            })
        ),
    )
    output = io.StringIO()
    monkeypatch.setattr(sys, "stdout", output)

    assert bind_tools_module.main() == 0
    result = json.loads(output.getvalue())["hookSpecificOutput"]
    assert result["permissionDecision"] == "allow"
    assert result["updatedInput"]["scope_id"] == "scope-for-session-a"
    assert result["updatedInput"]["text"] == "Use Scope binding."


def test_pre_tool_hook_fixes_control_binding_to_current_session(
    bind_tools_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        sys,
        "stdin",
        io.StringIO(
            json.dumps({
                "hook_event_name": "PreToolUse",
                "session_id": "session-a",
                "cwd": "/workspace",
                "tool_name": "mcp__powercontext__set_scope_binding",
                "tool_input": {
                    "key": {"integration": "other", "kind": "session", "external_id": "session-b"},
                    "scope_id": "target-scope",
                },
            })
        ),
    )
    output = io.StringIO()
    monkeypatch.setattr(sys, "stdout", output)

    assert bind_tools_module.main() == 0
    updated = json.loads(output.getvalue())["hookSpecificOutput"]["updatedInput"]
    assert updated == {
        "key": {"integration": "codex", "kind": "session", "external_id": "session-a"},
        "scope_id": "target-scope",
    }


def test_pre_tool_hook_resolves_only_the_current_session_scope(
    bind_tools_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        bind_tools_module,
        "binding_keys",
        lambda cwd, *, session_id: [
            {"integration": "codex", "kind": "session", "external_id": session_id},
            {"integration": "codex", "kind": "workspace", "external_id": cwd},
        ],
    )
    monkeypatch.setattr(
        sys,
        "stdin",
        io.StringIO(
            json.dumps({
                "hook_event_name": "PreToolUse",
                "session_id": "session-a",
                "cwd": "/workspace",
                "tool_name": "mcp__powercontext__resolve_scope_binding",
                "tool_input": {
                    "explicit_scope_id": "agent-selected",
                    "binding_keys": [{"integration": "other", "kind": "session", "external_id": "session-b"}],
                },
            })
        ),
    )
    output = io.StringIO()
    monkeypatch.setattr(sys, "stdout", output)

    assert bind_tools_module.main() == 0
    updated = json.loads(output.getvalue())["hookSpecificOutput"]["updatedInput"]
    assert updated == {
        "explicit_scope_id": None,
        "binding_keys": [
            {"integration": "codex", "kind": "session", "external_id": "session-a"},
            {"integration": "codex", "kind": "workspace", "external_id": "/workspace"},
        ],
    }


def test_pre_tool_hook_preserves_explicit_publication_boundaries(
    bind_tools_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tool_input = {
        "source": {
            "scope_id": "source-scope",
            "artifact": {"family": "handoff", "artifact_id": "handoff", "revision": 3},
        },
        "target_scope_id": "target-scope",
        "idempotency_key": "handoff-3",
    }
    monkeypatch.setattr(
        sys,
        "stdin",
        io.StringIO(
            json.dumps({
                "hook_event_name": "PreToolUse",
                "session_id": "session-a",
                "cwd": "/workspace",
                "tool_name": "mcp__powercontext__publish_artifact",
                "tool_input": tool_input,
            })
        ),
    )
    output = io.StringIO()
    monkeypatch.setattr(sys, "stdout", output)

    assert bind_tools_module.main() == 0
    result = json.loads(output.getvalue())["hookSpecificOutput"]
    assert result["permissionDecision"] == "allow"
    assert result["updatedInput"] == tool_input


def test_pre_tool_hook_limits_handoff_report_to_the_bound_scope(
    bind_tools_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        bind_tools_module,
        "resolve_scope_id",
        lambda _cwd, *, session_id, **_kwargs: f"scope-for-{session_id}",
    )
    monkeypatch.setattr(
        sys,
        "stdin",
        io.StringIO(
            json.dumps({
                "hook_event_name": "PreToolUse",
                "session_id": "session-a",
                "cwd": "/workspace",
                "tool_name": "mcp__powercontext__get_handoff_report",
                "tool_input": {"selection": {"mode": "all"}, "format": "json"},
            })
        ),
    )
    output = io.StringIO()
    monkeypatch.setattr(sys, "stdout", output)

    assert bind_tools_module.main() == 0
    updated = json.loads(output.getvalue())["hookSpecificOutput"]["updatedInput"]
    assert updated == {
        "selection": {"mode": "exact", "scope_ids": ["scope-for-session-a"]},
        "format": "json",
    }


def test_pre_tool_hook_denies_data_plane_when_binding_is_unavailable(
    bind_tools_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(*_args, **_kwargs):
        raise bind_tools_module.ScopeBindingError

    monkeypatch.setattr(bind_tools_module, "resolve_scope_id", fail)
    monkeypatch.setattr(
        sys,
        "stdin",
        io.StringIO(
            json.dumps({
                "hook_event_name": "PreToolUse",
                "session_id": "session-a",
                "cwd": "/workspace",
                "tool_name": "mcp__powercontext__search_memory",
                "tool_input": {"query": "current state"},
            })
        ),
    )
    output = io.StringIO()
    monkeypatch.setattr(sys, "stdout", output)

    assert bind_tools_module.main() == 0
    result = json.loads(output.getvalue())["hookSpecificOutput"]
    assert result["permissionDecision"] == "deny"
