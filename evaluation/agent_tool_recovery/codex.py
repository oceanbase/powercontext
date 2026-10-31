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

"""Run Codex against one evaluation Server and bind its native ``exec --json`` events.

Codex events carry no timestamps and report usage only per turn. The harness records when it received each event
line in a sidecar, and token use after the first failure is reported as unavailable instead of being apportioned.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .claude_code import HostRun, PluginSession, child_environment, tree_sha256
from .transcript import ModelMessage, ToolCall, ToolResult, TranscriptError, Turn, Usage

HOST = "codex"
SERVER_NAME = "powercontext"
# Codex names a call by server and tool; normalize to the Claude Code form so reports share one vocabulary.
PREFIX = f"mcp__{SERVER_NAME}__"
TURN_TIMEOUT_SECONDS = 900
EXECUTABLE_VARIABLE = "AGENT_TOOL_RECOVERY_CODEX"
RECEIPTS = "receipt-times.json"
# Calls Codex or the plugin's PreToolUse hook refuse before sending measure the harness, not the model or Server.
HOST_REFUSALS = ("MCP tool call requires approval", "PowerContext could not resolve the current Scope binding")
SHELL = "shell"
REPOSITORY_PLUGIN = Path(__file__).resolve().parents[2] / "integrations" / "codex" / "plugins" / "powercontext"
# Codex has no per-session plugin directory. The repository plugin is installed once, disabled, from a local
# marketplace whose copy pins the hook and MCP endpoint to this gateway port; each run enables it by override.
PLUGIN_MARKETPLACE = "powercontext-eval"
PLUGIN_ID = f"powercontext@{PLUGIN_MARKETPLACE}"
PLUGIN_PORT = 18765
MARKETPLACE_DIR = Path.home() / ".powercontext-eval" / "codex-marketplace"


def _executable() -> str:
    executable = os.environ.get(EXECUTABLE_VARIABLE) or shutil.which("codex")
    if executable is None:
        raise RuntimeError("Codex CLI is not on PATH")  # noqa: TRY003
    return executable


def _environment() -> dict[str, str]:
    env = child_environment()
    # CODEX_HOME selects the sign-in and plugin directory that installed_plugin_root() checks; keep the CLI on it.
    if "CODEX_HOME" in os.environ:
        env["CODEX_HOME"] = os.environ["CODEX_HOME"]
    # An inherited API key would switch Codex from the user's ChatGPT sign-in to API-key authentication.
    env.pop("OPENAI_API_KEY", None)
    env.pop("ENABLE_TOOL_SEARCH", None)
    return env


def version() -> str:
    completed = subprocess.run(  # noqa: S603 - fixed executable resolved from PATH or the declared override
        [_executable(), "--version"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
        env=_environment(),
        timeout=60,
    )
    return completed.stdout.strip()


def _toml_string(value: str) -> str:
    return json.dumps(value)


def _plugin_version() -> str:
    return json.loads((REPOSITORY_PLUGIN / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))["version"]


def installed_plugin_root() -> Path:
    codex_home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    return codex_home / "plugins" / "cache" / PLUGIN_MARKETPLACE / "powercontext" / _plugin_version()


def _installed_url(root: Path) -> str:
    return json.loads((root / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"][SERVER_NAME]["url"]


def plugin_identity() -> dict[str, str]:
    root = installed_plugin_root()
    return {
        "id": PLUGIN_ID,
        "version": _plugin_version(),
        "tree_sha256": tree_sha256(root, exclude=frozenset({".mcp.json"})),
        "installed_mcp_url": _installed_url(root),
    }


def prepare_marketplace(destination: Path = MARKETPLACE_DIR) -> Path:
    """Write the local marketplace whose plugin copy pins its hook and MCP endpoint to the gateway port.

    Installing and disabling the plugin stays with the user, because it changes their Codex configuration.
    """

    if destination.exists():
        raise FileExistsError(f"{destination} already exists; remove it to rebuild the marketplace")  # noqa: TRY003
    plugin = destination / "plugins" / "powercontext"
    shutil.copytree(REPOSITORY_PLUGIN, plugin, ignore=shutil.ignore_patterns("__pycache__", ".venv"))
    manifest = {
        "name": PLUGIN_MARKETPLACE,
        "interface": {"displayName": "PowerContext evaluation"},
        "plugins": [
            {
                "name": "powercontext",
                "source": {"source": "local", "path": "./plugins/powercontext"},
                "policy": {"installation": "AVAILABLE"},
                "category": "Productivity",
            }
        ],
    }
    (destination / ".agents" / "plugins").mkdir(parents=True)
    (destination / ".agents" / "plugins" / "marketplace.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    endpoint = plugin / ".mcp.json"
    configuration = json.loads(endpoint.read_text(encoding="utf-8"))
    configuration["mcpServers"][SERVER_NAME]["url"] = f"http://127.0.0.1:{PLUGIN_PORT}/mcp/"
    endpoint.write_text(json.dumps(configuration, indent=2) + "\n", encoding="utf-8")
    return destination


def check_installed_plugin(mcp_url: str) -> None:
    """Fail before a run whose installed plugin is not the repository plugin pinned to this gateway."""

    root = installed_plugin_root()
    if not (root / ".mcp.json").exists():
        raise RuntimeError(f"{PLUGIN_ID} is not installed at {root}")  # noqa: TRY003
    if _installed_url(root) != mcp_url:
        raise RuntimeError(f"{PLUGIN_ID} targets {_installed_url(root)}, not {mcp_url}")  # noqa: TRY003
    exclude = frozenset({".mcp.json"})
    if tree_sha256(root, exclude=exclude) != tree_sha256(REPOSITORY_PLUGIN, exclude=exclude):
        raise RuntimeError(f"{PLUGIN_ID} differs from {REPOSITORY_PLUGIN}; reinstall it")  # noqa: TRY003


def _plugin_arguments() -> list[str]:
    source = _toml_string(str(MARKETPLACE_DIR))
    return [
        "--config",
        f'marketplaces={{{PLUGIN_MARKETPLACE}={{source_type="local",source={source}}}}}',
        # Replaces the plugin table, so only the evaluation plugin is enabled.
        "--config",
        f"plugins={{{_toml_string(PLUGIN_ID)}={{enabled=true}}}}",
        # Codex reads a Skill through its read-only shell; the elevated sandbox cannot start from a background process.
        "--config",
        'windows.sandbox="unelevated"',
        # The hooks are the repository plugin's own code, verified against the checkout before the run.
        "--dangerously-bypass-hook-trust",
    ]


def plugin_problems(init: dict[str, Any], turns: list[Turn], hook_failures: dict[str, int]) -> list[str]:
    """Reasons a plugin session is not evidence of model behavior after a Server failure.

    The PreToolUse hook resolves the Scope binding before every PowerContext call and denies the call when that
    fails; ``exec --json`` records no event for a denied call, so such a session cannot be bound call by call.
    """

    del init  # Codex reports no session init; hook requests reaching the Server prove the plugin loaded.
    root = str(installed_plugin_root()).replace("\\", "/").lower()
    problems = [
        f"Shell command {call.call_id} reached outside the plugin"
        for turn in turns
        for call in turn.calls
        if call.name == SHELL and root not in str(call.arguments.get("command")).replace("\\", "/").lower()
    ]
    if hook_failures.get("resolve_scope_binding"):
        problems.append("The PreToolUse hook could not resolve the Scope binding; Codex records no denied call")
    return problems


def command(*, mcp_url: str, headers: dict[str, str], model: str, workdir: Path, plugin: bool = False) -> list[str]:
    arguments = [
        _executable(),
        "exec",
        "--json",
        "--color",
        "never",
        # The user's config.toml carries personal plugins, hooks, and MCP servers; authentication still uses
        # CODEX_HOME, so no credential is copied.
        "--ignore-user-config",
        "--ignore-rules",
        "--ephemeral",
        "--skip-git-repo-check",
        "--sandbox",
        "read-only",
        "--model",
        model,
        "--config",
        'approval_policy="never"',
        "--config",
        f"mcp_servers.{SERVER_NAME}.url={_toml_string(mcp_url)}",
        # With approvals disabled, Codex rejects write tools that are not pre-approved before they reach the Server;
        # this matches Claude Code's --allowedTools grant for the PowerContext server only.
        "--config",
        f'mcp_servers.{SERVER_NAME}.default_tools_approval_mode="approve"',
    ]
    if headers:
        table = ", ".join(f"{_toml_string(name)} = {_toml_string(value)}" for name, value in headers.items())
        arguments += ["--config", f"mcp_servers.{SERVER_NAME}.http_headers={{{table}}}"]
    if plugin:
        arguments += _plugin_arguments()
    return [*arguments, "--cd", str(workdir), "-"]


def _stream(process: subprocess.Popen[str], transcript: Path) -> list[str]:
    """Copy native stdout unchanged and record the harness receipt time of every line."""

    receipts: list[str] = []
    with transcript.open("w", encoding="utf-8", newline="") as output:
        assert process.stdout is not None  # noqa: S101 - Popen was opened with stdout=PIPE
        for line in process.stdout:
            receipts.append(datetime.now(UTC).isoformat())
            output.write(line)
    return receipts


def _plugin_environment(plugin: PluginSession) -> dict[str, str]:
    # The hook endpoint comes from the installed .mcp.json; the Scope and credential come from the environment.
    env = {"POWERCONTEXT_CLIENT_CONFIG_FILE": str(plugin.client_config)}
    if plugin.scope_id:
        env["POWERCONTEXT_CODEX_SCOPE_ID"] = plugin.scope_id
    if plugin.authorization:
        env["POWERCONTEXT_CODEX_AUTHORIZATION"] = plugin.authorization
    return env


def run(  # noqa: PLR0913
    prompts: list[str],
    *,
    mcp_url: str,
    headers: dict[str, str],
    model: str,
    workdir: Path,
    private_dir: Path,
    plugin: PluginSession | None = None,
) -> HostRun:
    """Run one ephemeral Codex turn; an ephemeral session cannot be resumed, so cases have a single turn."""

    if len(prompts) != 1:
        raise ValueError("The Codex adapter runs single-turn cases only")  # noqa: TRY003
    if plugin is not None:
        check_installed_plugin(mcp_url)
    transcript = private_dir / "transcript.jsonl"
    process = subprocess.Popen(  # noqa: S603 - fixed executable and arguments
        command(mcp_url=mcp_url, headers=headers, model=model, workdir=workdir, plugin=plugin is not None),
        cwd=workdir,
        env={**_environment(), **(_plugin_environment(plugin) if plugin else {})},
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    stderr: list[str] = []
    drain = threading.Thread(target=lambda: stderr.extend(process.stderr or []), daemon=True)
    drain.start()
    assert process.stdin is not None  # noqa: S101 - Popen was opened with stdin=PIPE
    process.stdin.write(prompts[0])
    process.stdin.close()
    deadline = time.monotonic() + TURN_TIMEOUT_SECONDS
    timer = threading.Timer(TURN_TIMEOUT_SECONDS, process.kill)
    timer.start()
    try:
        receipts = _stream(process, transcript)
        exit_code = process.wait(timeout=max(deadline - time.monotonic(), 1))
    finally:
        timer.cancel()
    drain.join(timeout=10)
    (private_dir / RECEIPTS).write_text(json.dumps(receipts), encoding="utf-8")
    (private_dir / "stderr-turn-1.txt").write_text("".join(stderr), encoding="utf-8")
    thread = next(
        (event for event in _events(transcript) if event.get("type") == "thread.started"),
        {},
    )
    return HostRun(version(), model, str(thread.get("thread_id", "")), transcript, {}, (exit_code,))


def _events(path: Path) -> list[dict[str, Any]]:
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("{"):
            value = json.loads(line)
            if isinstance(value, dict):
                events.append(value)
    return events


def _result_text(item: dict[str, Any]) -> str:
    error = item.get("error")
    if isinstance(error, dict) and isinstance(error.get("message"), str):
        return error["message"]
    result = item.get("result")
    if isinstance(result, dict):
        blocks = result.get("content")
        if isinstance(blocks, list):
            return "\n".join(str(block.get("text") or "") for block in blocks if isinstance(block, dict))
        return json.dumps(result.get("structured_content"), ensure_ascii=False)
    return ""


def _is_error(item: dict[str, Any]) -> bool:
    result = item.get("result")
    return (
        item.get("status") == "failed"
        or bool(item.get("error"))
        or (isinstance(result, dict) and bool(result.get("is_error") or result.get("isError")))
    )


def _bind_call(item: dict[str, Any], issued_at: datetime, at: datetime) -> ToolCall | str:
    """Bind one completed MCP or shell item, or return why it cannot count as a model-issued call."""

    item_id = item["id"]
    if item["type"] == "command_execution":
        failed = item.get("status") == "failed" or item.get("exit_code") not in {0, None}
        text = str(item.get("aggregated_output") or "")
        command = {"command": str(item.get("command") or "")}
        return ToolCall(item_id, SHELL, command, issued_at, ToolResult(failed, text, at))
    arguments = item.get("arguments")
    if not isinstance(arguments, dict) or not isinstance(item.get("tool"), str):
        return f"Tool call {item_id} without tool name or object arguments"
    text = _result_text(item)
    if text.startswith(HOST_REFUSALS):
        return f"Codex refused tool call {item_id} before sending it"
    name = f"mcp__{item.get('server')}__{item['tool']}"
    return ToolCall(item_id, name, arguments, issued_at, ToolResult(_is_error(item), text, at))


def read_transcript(path: Path, prompts: list[str]) -> list[Turn]:  # noqa: C901, PLR0912
    """Bind Codex ``exec --json`` events of one ephemeral run into exactly one complete turn."""

    problems: list[str] = []
    lines = path.read_text(encoding="utf-8").splitlines()
    receipts_path = path.with_name(RECEIPTS)
    receipts = json.loads(receipts_path.read_text(encoding="utf-8")) if receipts_path.exists() else []
    if len(receipts) != len(lines):
        raise TranscriptError([f"Receipt times do not match {len(lines)} transcript lines"])
    turn: Turn | None = None
    # Receipt time of each started item; overlapping calls are ordered by when they were issued, not completed.
    started: dict[str, datetime] = {}
    calls: dict[str, ToolCall] = {}
    completed = False
    for index, line in enumerate(lines):
        if not line.startswith("{"):
            continue
        event = json.loads(line)
        at = datetime.fromisoformat(receipts[index])
        kind = event.get("type")
        if kind == "turn.started":
            if turn is not None:
                problems.append(f"Additional turn at line {index + 1}")
                continue
            turn = Turn(index=1, started_at=at)
            continue
        if kind in {"turn.failed", "error"}:
            problems.append(f"Host turn failure at line {index + 1}")
            continue
        if turn is None:
            continue
        item = event.get("item")
        if kind == "turn.completed":
            usage = event.get("usage") if isinstance(event.get("usage"), dict) else {}
            turn.messages.append(
                ModelMessage(
                    f"turn-{turn.index}",
                    at,
                    Usage(
                        input_tokens=int(usage.get("input_tokens") or 0) - int(usage.get("cached_input_tokens") or 0),
                        output_tokens=int(usage.get("output_tokens") or 0),
                        cache_creation_input_tokens=int(usage.get("cache_write_input_tokens") or 0),
                        cache_read_input_tokens=int(usage.get("cached_input_tokens") or 0),
                    ),
                )
            )
            turn.usage_granularity = "turn"
            completed = True
            continue
        if not isinstance(item, dict):
            continue
        item_id = item.get("id")
        if item.get("type") in {"mcp_tool_call", "command_execution"} and isinstance(item_id, str):
            if kind == "item.started":
                started[item_id] = at
                continue
            if kind != "item.completed":
                continue
            if item_id in calls:
                problems.append(f"Duplicate tool call ID {item_id}")
                continue
            call = _bind_call(item, started.pop(item_id, at), at)
            if isinstance(call, str):
                problems.append(call)
                continue
            calls[item_id] = call
            turn.calls.append(call)
            turn.terminal_text = turn.terminal_at = None
        elif item.get("type") == "agent_message" and kind == "item.completed" and not started:
            text = str(item.get("text") or "").strip()
            if text:
                turn.terminal_text, turn.terminal_at = text, at
    if turn is None or len(prompts) != 1:
        problems.append(f"Expected one turn for {len(prompts)} prompt(s)")
    if started:
        problems.append(f"Tool calls without results: {sorted(started)}")
    if not completed:
        problems.append("Turn did not complete")
    if turn is not None and turn.terminal_at is None:
        problems.append("Turn 1 has no terminal assistant response")
    if problems or turn is None:
        raise TranscriptError(problems)
    # Items complete in result order; rows follow the order the model issued its calls.
    turn.calls.sort(key=lambda call: call.at)
    return [turn]
