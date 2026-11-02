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

"""Run Claude Code against one evaluation Server and retain its native session transcript."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .environment import loopback_proxy_settings
from .transcript import read_claude_session as read_transcript

HOST = "claude-code"
__all__ = [
    "HOST",
    "PREFIX",
    "HostRun",
    "PluginSession",
    "child_environment",
    "plugin_identity",
    "plugin_problems",
    "read_transcript",
    "run",
    "version",
]
SERVER_NAME = "powercontext"
PREFIX = f"mcp__{SERVER_NAME}__"
# The parent may be another Claude Code session or carry a personal PowerContext configuration.
SCRUBBED_PREFIXES = ("CLAUDE_", "ANTHROPIC_", "POWERCONTEXT_", "CODEX_", "GATEGUARD_", "ECC_")
TURN_TIMEOUT_SECONDS = 900
PLUGIN_DIR = Path(__file__).resolve().parents[2] / "integrations" / "claude-code" / "plugins" / "powercontext"
PLUGIN_SKILL = "powercontext:powercontext-project-context"
# Skills bundled with Claude Code 2.1.280. The baseline disables every skill; the plugin variant exposes only the
# plugin's own, so each bundled skill is denied and an unlisted one invalidates the session.
BUNDLED_SKILLS = (
    "batch",
    "claude-api",
    "code-review",
    "dataviz",
    "debug",
    "design",
    "design-sync",
    "doctor",
    "fewer-permission-prompts",
    "loop",
    "run",
    "run-skill-generator",
    "schedule",
    "simplify",
    "update-config",
    "verify",
)


@dataclass(frozen=True, slots=True)
class HostRun:
    host_version: str
    model: str
    session_id: str
    transcript: Path
    init: dict[str, Any]
    exit_codes: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class PluginSession:
    """What a host's PowerContext plugin needs to act as an installed, Scope-bound integration."""

    server_url: str
    scope_id: str | None
    authorization: str | None
    client_config: Path


def tree_sha256(root: Path, *, exclude: frozenset[str] = frozenset()) -> str:
    """Hash a plugin's files by relative path, skipping caches and the named relative paths."""

    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if not path.is_file() or relative in exclude or {"__pycache__", ".venv"} & set(path.parts):
            continue
        digest.update(relative.encode() + b"\0" + path.read_bytes() + b"\0")
    return digest.hexdigest()


def plugin_identity() -> dict[str, str]:
    manifest = json.loads((PLUGIN_DIR / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    return {"name": manifest["name"], "version": manifest["version"], "tree_sha256": tree_sha256(PLUGIN_DIR)}


def plugin_problems(init: dict[str, Any], turns: list[Any], hook_failures: dict[str, int]) -> list[str]:
    """Reasons a plugin session did not run the repository plugin with only its own Skill.

    The Claude Code plugin has no PreToolUse hook, so a failed hook request never blocks a model call.
    """

    del hook_failures
    problems = []
    loaded = [
        plugin
        for plugin in init.get("plugins") or []
        if plugin.get("name") == "powercontext" and Path(str(plugin.get("path"))).resolve() == PLUGIN_DIR
    ]
    if not loaded:
        problems.append("The repository PowerContext plugin was not loaded")
    unexpected = sorted(set(init.get("skills") or []) - {PLUGIN_SKILL, *BUNDLED_SKILLS})
    if unexpected:
        problems.append(f"Skills outside the plugin were exposed: {unexpected}")
    for turn in turns:
        for call in turn.calls:
            if call.name == "Skill" and call.arguments.get("skill") != PLUGIN_SKILL:
                problems.append(f"Tool call {call.call_id} invoked a Skill outside the plugin")
    return problems


def _executable() -> str:
    executable = shutil.which("claude")
    if executable is None:
        raise RuntimeError("Claude Code CLI is not on PATH")  # noqa: TRY003
    return executable


def child_environment(environ: dict[str, str] | None = None) -> dict[str, str]:
    """Return an environment without inherited host sessions or personal PowerContext settings."""

    source = os.environ if environ is None else environ
    env = {key: value for key, value in source.items() if not key.upper().startswith(SCRUBBED_PREFIXES)}
    # The evaluation Server is loopback-only; a configured HTTPS proxy must not intercept MCP traffic.
    env.update(loopback_proxy_settings(env))
    # Load every MCP tool up front; deferred search would add a tool the evaluation does not expose.
    env["ENABLE_TOOL_SEARCH"] = "false"
    return env


def version() -> str:
    completed = subprocess.run(  # noqa: S603 - fixed executable resolved from PATH
        [_executable(), "--version"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
        env=child_environment(),
        timeout=60,
    )
    return completed.stdout.strip()


def _transcript_path(session_id: str) -> Path:
    matches = sorted((Path.home() / ".claude" / "projects").glob(f"*/{session_id}.jsonl"))
    if len(matches) != 1:
        raise RuntimeError(f"Expected one Claude Code transcript for {session_id}, found {len(matches)}")  # noqa: TRY003
    return matches[0]


def _plugin_environment(plugin: PluginSession) -> dict[str, str]:
    env = {
        "POWERCONTEXT_CLAUDE_SERVER_URL": plugin.server_url,
        # Missing, so the hooks never read the user's saved PowerContext client settings.
        "POWERCONTEXT_CLIENT_CONFIG_FILE": str(plugin.client_config),
    }
    if plugin.scope_id:
        env["POWERCONTEXT_CLAUDE_SCOPE_ID"] = plugin.scope_id
    if plugin.authorization:
        env["POWERCONTEXT_CLAUDE_AUTHORIZATION"] = plugin.authorization
    return env


def _tool_arguments(plugin: PluginSession | None) -> list[str]:
    if plugin is None:
        return ["--disable-slash-commands", "--tools", "", "--allowedTools", f"mcp__{SERVER_NAME}"]
    # The plugin's hooks and Skill load for this session only; its own .mcp.json is ignored by --strict-mcp-config.
    return [
        "--plugin-dir",
        str(PLUGIN_DIR),
        "--tools",
        "Skill",
        "--allowedTools",
        f"mcp__{SERVER_NAME}",
        "Skill",
        "--disallowedTools",
        *(f"Skill({name})" for name in BUNDLED_SKILLS),
    ]


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
    """Run each prompt as one logical turn of a single native session; built-in tools are disabled."""

    mcp_config = workdir / "mcp.json"
    server = {"type": "http", "url": mcp_url, **({"headers": headers} if headers else {})}
    mcp_config.write_text(json.dumps({"mcpServers": {SERVER_NAME: server}}), encoding="utf-8")
    session_id = str(uuid.uuid4())
    common = [
        "--model",
        model,
        "--output-format",
        "stream-json",
        "--verbose",
        "--strict-mcp-config",
        "--mcp-config",
        str(mcp_config),
        "--setting-sources",
        "project",
        *_tool_arguments(plugin),
        "--permission-mode",
        "bypassPermissions",
    ]
    env = {**child_environment(), **(_plugin_environment(plugin) if plugin else {})}
    init: dict[str, Any] = {}
    exit_codes = []
    for index, prompt in enumerate(prompts):
        session = ["--session-id", session_id] if index == 0 else ["--resume", session_id]
        completed = subprocess.run(  # noqa: S603 - fixed executable and arguments
            [_executable(), "-p", prompt, *session, *common],
            cwd=workdir,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=TURN_TIMEOUT_SECONDS,
        )
        exit_codes.append(completed.returncode)
        (private_dir / f"stream-turn-{index + 1}.jsonl").write_text(completed.stdout, encoding="utf-8")
        (private_dir / f"stderr-turn-{index + 1}.txt").write_text(completed.stderr, encoding="utf-8")
        for line in completed.stdout.splitlines():
            if line.startswith("{"):
                event = json.loads(line)
                if event.get("type") == "system" and event.get("subtype") == "init" and not init:
                    init = event
    transcript = private_dir / "transcript.jsonl"
    shutil.copyfile(_transcript_path(session_id), transcript)
    return HostRun(version(), model, session_id, transcript, init, tuple(exit_codes))
