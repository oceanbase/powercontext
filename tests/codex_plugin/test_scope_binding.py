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

import asyncio
import io
import json
import os
import shutil
import subprocess
import sys
import time
from http.client import BadStatusLine, HTTPMessage
from pathlib import Path
from types import ModuleType
from typing import Any
from urllib.error import HTTPError

import pytest
from fastmcp import Client

from powercontext.http._generated import operations as generated_operations
from powercontext.server.app import create_app
from powercontext.server.mcp import create_mcp_server


def _operations_with_scope_mode(scope_mode: str) -> frozenset[str]:
    return frozenset(
        value.operation_id
        for value in vars(generated_operations).values()
        if isinstance(value, generated_operations.Operation) and value.scope_mode == scope_mode
    )


PLUGIN_ROOT = Path(__file__).resolve().parents[2] / "integrations" / "codex" / "plugins" / "powercontext"
CURRENT_OPERATIONS = _operations_with_scope_mode("current")
SELECTION_OPERATIONS = _operations_with_scope_mode("selection")


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


@pytest.mark.parametrize("operation", sorted(CURRENT_OPERATIONS))
def test_pre_tool_hook_binds_every_current_scope_operation(
    operation: str,
    bind_tools_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(bind_tools_module, "resolve_scope_id", lambda *_args, **_kwargs: "bound-scope")
    monkeypatch.setattr(
        sys,
        "stdin",
        io.StringIO(
            json.dumps({
                "session_id": "session-a",
                "cwd": "/workspace",
                "tool_name": f"mcp__powercontext__{operation}",
                "tool_input": {"scope_id": "agent-selected"},
            })
        ),
    )
    output = io.StringIO()
    monkeypatch.setattr(sys, "stdout", output)

    assert bind_tools_module.main() == 0
    updated = json.loads(output.getvalue())["hookSpecificOutput"]["updatedInput"]
    assert updated["scope_id"] == "bound-scope"


@pytest.mark.parametrize("operation", sorted(SELECTION_OPERATIONS))
def test_pre_tool_hook_binds_every_selection_scope_operation(
    operation: str,
    bind_tools_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(bind_tools_module, "resolve_scope_id", lambda *_args, **_kwargs: "bound-scope")
    monkeypatch.setattr(
        sys,
        "stdin",
        io.StringIO(
            json.dumps({
                "session_id": "session-a",
                "cwd": "/workspace",
                "tool_name": f"mcp__powercontext__{operation}",
                "tool_input": {"selection": {"mode": "all"}},
            })
        ),
    )
    output = io.StringIO()
    monkeypatch.setattr(sys, "stdout", output)

    assert bind_tools_module.main() == 0
    updated = json.loads(output.getvalue())["hookSpecificOutput"]["updatedInput"]
    assert updated["selection"] == {"mode": "exact", "scope_ids": ["bound-scope"]}


def test_pre_tool_hook_binds_every_mcp_tool_that_takes_a_scope(
    bind_tools_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A tool whose Scope the hook leaves to the Agent could read or write a Scope the Session is not bound to."""

    async def list_tools() -> list[Any]:
        async with Client(create_mcp_server(create_app())) as client:
            return await client.list_tools()

    explicit = (
        bind_tools_module._HOST_OPERATIONS
        | bind_tools_module._CONTROL_OPERATIONS
        | bind_tools_module._RESOLVE_OPERATIONS
    )
    scoped = {
        tool.name: set(tool.inputSchema.get("properties", {})) & {"scope_id", "selection"}
        for tool in asyncio.run(list_tools())
        if tool.name not in explicit
    }
    monkeypatch.setattr(bind_tools_module, "resolve_scope_id", lambda *_args, **_kwargs: "bound-scope")
    unbound = []
    for name, fields in sorted(scoped.items()):
        if not fields:
            continue
        tool_input = {"scope_id": "agent-selected"} if "scope_id" in fields else {"selection": {"mode": "all"}}
        payload = {"session_id": "session-a", "cwd": "/workspace", "tool_name": f"mcp__powercontext__{name}"}
        monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({**payload, "tool_input": tool_input})))
        output = io.StringIO()
        monkeypatch.setattr(sys, "stdout", output)
        assert bind_tools_module.main() == 0
        updated = json.loads(output.getvalue() or "{}").get("hookSpecificOutput", {}).get("updatedInput", {})
        if updated.get("scope_id") != "bound-scope" and updated.get("selection") != {
            "mode": "exact",
            "scope_ids": ["bound-scope"],
        }:
            unbound.append(name)

    assert unbound == []


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


def _hook_payload() -> str:
    return json.dumps({
        "hook_event_name": "PreToolUse",
        "session_id": "session-a",
        "cwd": "/workspace",
        "tool_name": "mcp__powercontext__search_memory",
        "tool_input": {"query": "current state"},
    })


def _run_hook(
    bind_tools_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, object]:
    monkeypatch.setattr(sys, "stdin", io.StringIO(_hook_payload()))
    output = io.StringIO()
    monkeypatch.setattr(sys, "stdout", output)

    assert bind_tools_module.main() == 0
    return json.loads(output.getvalue())["hookSpecificOutput"]


def _run_denied_binding_hook(
    bind_tools_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
) -> dict[str, object]:
    def fail(*_args, **_kwargs):
        raise error

    monkeypatch.setattr(bind_tools_module, "resolve_scope_id", fail)
    return _run_hook(bind_tools_module, monkeypatch)


def test_pre_tool_hook_denies_data_plane_when_binding_is_unavailable(
    bind_tools_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _run_denied_binding_hook(
        bind_tools_module,
        monkeypatch,
        sys.modules["scope_binding"].ScopeBindingError(),
    )

    assert result["permissionDecision"] == "deny"
    assert result["permissionDecisionReason"] == bind_tools_module._BINDING_FAILURE_REASON


def test_pre_tool_hook_names_authentication_failure(
    bind_tools_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _run_denied_binding_hook(
        bind_tools_module,
        monkeypatch,
        bind_tools_module.ScopeBindingRejectedError(),
    )

    assert result["permissionDecision"] == "deny"
    assert result["permissionDecisionReason"] == bind_tools_module._AUTHENTICATION_FAILURE_REASON
    assert "401" in str(result["permissionDecisionReason"])


def test_pre_tool_hook_names_missing_permission_for_a_denied_credential(
    bind_tools_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _run_denied_binding_hook(
        bind_tools_module,
        monkeypatch,
        bind_tools_module.ScopeBindingStatusError(403, "/v1/scope-bindings/resolve"),
    )

    assert result["permissionDecision"] == "deny"
    assert result["permissionDecisionReason"] == bind_tools_module._AUTHORIZATION_DENIED_REASON
    assert "lacks permission" in str(result["permissionDecisionReason"])
    assert "403" in str(result["permissionDecisionReason"])


def test_pre_tool_hook_keeps_the_binding_reason_for_a_transport_failure(
    bind_tools_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scope_binding = sys.modules["scope_binding"]
    result = _run_denied_binding_hook(
        bind_tools_module,
        monkeypatch,
        scope_binding.ScopeBindingUnavailableError(),
    )

    assert result["permissionDecision"] == "deny"
    assert result["permissionDecisionReason"] == bind_tools_module._BINDING_FAILURE_REASON


def test_pre_tool_hook_keeps_the_binding_reason_for_a_protocol_failure(
    bind_tools_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _run_denied_binding_hook(
        bind_tools_module,
        monkeypatch,
        BadStatusLine("SSH-2.0-OpenSSH_9.6"),
    )

    assert result["permissionDecision"] == "deny"
    assert result["permissionDecisionReason"] == bind_tools_module._BINDING_FAILURE_REASON


def test_pre_tool_hook_keeps_the_binding_reason_for_an_unclassified_status(
    bind_tools_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _run_denied_binding_hook(
        bind_tools_module,
        monkeypatch,
        bind_tools_module.ScopeBindingStatusError(500, "/v1/scope-bindings/resolve"),
    )

    assert result["permissionDecision"] == "deny"
    assert result["permissionDecisionReason"] == bind_tools_module._BINDING_FAILURE_REASON


@pytest.mark.parametrize(
    ("status", "reason_attribute"),
    [
        (401, "_AUTHENTICATION_FAILURE_REASON"),
        (403, "_AUTHORIZATION_DENIED_REASON"),
    ],
)
def test_pre_tool_hook_classifies_the_scope_service_status(
    status: int,
    reason_attribute: str,
    bind_tools_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scope_binding = sys.modules["scope_binding"]

    def fail(*_args, **_kwargs):
        raise HTTPError(
            "http://server/v1/scope-bindings/resolve",
            status,
            "error",
            HTTPMessage(),
            io.BytesIO(b"{}"),
        )

    monkeypatch.setattr(scope_binding, "open_bounded", fail)
    result = _run_hook(bind_tools_module, monkeypatch)

    assert result["permissionDecision"] == "deny"
    assert result["permissionDecisionReason"] == getattr(bind_tools_module, reason_attribute)


# Codex 0.167.2 runs the call with the Agent's own arguments when the hook exits without a decision, so a hook that
# cannot bind a PowerContext call must deny it rather than stay silent.
def test_pre_tool_hook_denies_a_payload_that_is_not_an_object(
    bind_tools_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "stdin", io.StringIO("[]"))
    output = io.StringIO()
    monkeypatch.setattr(sys, "stdout", output)

    assert bind_tools_module.main() == 0
    assert json.loads(output.getvalue())["hookSpecificOutput"]["permissionDecision"] == "deny"


@pytest.mark.parametrize("missing", ["session_id", "cwd", "tool_input"])
def test_pre_tool_hook_denies_a_powercontext_call_it_cannot_bind(
    missing: str,
    bind_tools_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = json.loads(_hook_payload())
    del payload[missing]
    monkeypatch.setattr(bind_tools_module, "resolve_scope_id", lambda *_args, **_kwargs: "bound-scope")
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    output = io.StringIO()
    monkeypatch.setattr(sys, "stdout", output)

    assert bind_tools_module.main() == 0
    assert json.loads(output.getvalue())["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_pre_tool_hook_leaves_other_tools_alone(
    bind_tools_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {**json.loads(_hook_payload()), "tool_name": "mcp__other__search_memory"}
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    output = io.StringIO()
    monkeypatch.setattr(sys, "stdout", output)

    assert bind_tools_module.main() == 0
    assert output.getvalue() == ""


@pytest.mark.parametrize("error", [RuntimeError("unexpected"), KeyError("scope_id"), TypeError("unexpected")])
def test_pre_tool_hook_denies_when_binding_fails_unexpectedly(
    error: Exception,
    bind_tools_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _run_denied_binding_hook(bind_tools_module, monkeypatch, error)

    assert result["permissionDecision"] == "deny"
    assert result["permissionDecisionReason"] == bind_tools_module._BINDING_FAILURE_REASON


# A non-ASCII call read under an ASCII locale must not turn the load failure into an undecided exit.
@pytest.mark.parametrize("query", ["current state", "état courant"])
def test_pre_tool_hook_denies_when_the_plugin_cannot_load(query: str, tmp_path: Path) -> None:
    plugin = tmp_path / "powercontext"
    shutil.copytree(PLUGIN_ROOT, plugin, ignore=shutil.ignore_patterns(".venv", "__pycache__"))
    (plugin / "scripts" / "scope_binding.py").write_text('raise ImportError("synthetic load failure")\n')
    payload = {**json.loads(_hook_payload()), "tool_input": {"query": query}}

    completed = subprocess.run(
        [sys.executable, str(plugin / "hooks" / "bind_tools.py")],
        input=json.dumps(payload, ensure_ascii=False).encode(),
        capture_output=True,
        env={**os.environ, "PYTHONIOENCODING": "ascii"},
        timeout=60,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr.decode(errors="replace")
    assert json.loads(completed.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_pre_tool_hook_resolves_within_the_host_timeout(
    bind_tools_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A hook still resolving at the host deadline is killed without a decision, and Codex runs the call unbound."""
    configuration = json.loads((PLUGIN_ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))
    host_timeout = configuration["hooks"]["PreToolUse"][0]["hooks"][0]["timeout"]
    monkeypatch.setenv("POWERCONTEXT_CODEX_HTTP_BUDGET_SECONDS", str(host_timeout * 3))
    remaining: list[float] = []

    def resolve(*_args: object, deadline: float, **_kwargs: object) -> str:
        remaining.append(deadline - time.monotonic())
        return "bound-scope"

    monkeypatch.setattr(bind_tools_module, "resolve_scope_id", resolve)
    result = _run_hook(bind_tools_module, monkeypatch)

    assert result["permissionDecision"] == "allow"
    # The hook's process start-up and its output also have to fit inside the host timeout.
    assert 0 < remaining[0] <= host_timeout - 2


class _CliSettings:
    http_budget_seconds = 6.0


def test_bind_scope_puts_the_codex_workspace_key(
    scope_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    requests: list[tuple[str, dict[str, object], str]] = []

    def request(path, payload, *, settings, deadline, method="POST"):
        requests.append((path, payload, method))
        return {"scope_id": "scp_project_a"}

    monkeypatch.setattr(scope_module, "_post_json", request)
    settings = _CliSettings()

    bound = scope_module.bind_scope(str(tmp_path), "scp_project_a", settings=settings, deadline=float("inf"))
    key = scope_module.workspace_binding_key(str(tmp_path))

    assert bound == "scp_project_a"
    assert requests == [("/v1/scope-bindings", {"key": key, "scope_id": "scp_project_a"}, "PUT")]
    assert key["integration"] == "codex"
    assert key["kind"] == "workspace"
    assert key["external_id"] != str(tmp_path)


def test_bind_scope_rejects_a_different_server_scope(
    scope_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    def request(path, payload, *, settings, deadline, method="POST"):
        return {"scope_id": "scp_other"}

    monkeypatch.setattr(scope_module, "_post_json", request)

    with pytest.raises(scope_module.ScopeBindingError):
        scope_module.bind_scope(str(tmp_path), "scp_project_a", settings=_CliSettings(), deadline=float("inf"))


def test_clear_scope_binding_removes_only_the_workspace_key(
    scope_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    requests: list[tuple[str, dict[str, object]]] = []

    def request(path, payload, *, settings, deadline, method="POST"):
        requests.append((path, payload))
        return {"cleared": True}

    monkeypatch.setattr(scope_module, "_post_json", request)

    assert scope_module.clear_scope_binding(str(tmp_path), settings=_CliSettings(), deadline=float("inf")) is True
    assert requests == [("/v1/scope-bindings/clear", {"key": scope_module.workspace_binding_key(str(tmp_path))})]


def test_cli_bind_scope_prints_the_bound_scope_id(
    scope_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    seen: dict[str, object] = {}

    def bind(cwd, scope_id, *, settings, deadline):
        seen["cwd"] = cwd
        seen["scope_id"] = scope_id
        seen["settings"] = settings
        return scope_id

    monkeypatch.setattr(scope_module, "CodexPluginSettings", lambda: _CliSettings())
    monkeypatch.setattr(scope_module, "bind_scope", bind)

    assert scope_module.main(["--cwd", str(tmp_path), "--bind-scope", "scp_project_a"]) == 0

    assert seen["cwd"] == str(tmp_path)
    assert seen["scope_id"] == "scp_project_a"
    assert isinstance(seen["settings"], _CliSettings)
    assert capsys.readouterr().out.strip() == "scp_project_a"


def test_cli_clear_scope_prints_the_directory_resolution(
    scope_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[tuple[object, ...]] = []

    def clear(cwd, *, settings, deadline):
        calls.append(("clear", cwd))
        return True

    def resolve(cwd, *, session_id, settings, deadline, persist_session=False):
        calls.append(("resolve", cwd, session_id, persist_session))
        return "scp_default"

    monkeypatch.setattr(scope_module, "CodexPluginSettings", lambda: _CliSettings())
    monkeypatch.setattr(scope_module, "clear_scope_binding", clear)
    monkeypatch.setattr(scope_module, "resolve_scope_id", resolve)

    assert scope_module.main(["--cwd", str(tmp_path), "--clear-scope"]) == 0

    assert calls == [
        ("clear", str(tmp_path)),
        ("resolve", str(tmp_path), None, False),
    ]
    assert capsys.readouterr().out.strip() == "scp_default"
