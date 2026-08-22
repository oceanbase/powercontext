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

# ruff: noqa: TRY003

"""Configure the PowerContext memory provider in OpenClaw."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Sequence
from urllib.parse import urlsplit, urlunsplit

POWERCONTEXT_TOOLS = (
    "powercontext_memory_search",
    "powercontext_memory_get",
    "powercontext_memory_store",
    "powercontext_memory_revise",
    "powercontext_memory_retire",
)
MIN_OPENCLAW_VERSION = (2026, 8, 1, 2)
OPENCLAW_VERSION_PATTERN = re.compile(r"(?:OpenClaw\s+)?(\d+)\.(\d+)\.(\d+)(?:-beta\.(\d+))?")


class ConfigurationError(RuntimeError):
    """Report a configuration failure suitable for a CLI error."""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action",
        choices=("enable", "disable", "off", "status"),
        help="configuration action",
    )
    parser.add_argument(
        "endpoint",
        nargs="?",
        default="http://127.0.0.1:8765",
        help="PowerContext Server URL for enable",
    )
    parser.add_argument(
        "scope_mode",
        nargs="?",
        default="agent",
        choices=("agent", "project"),
        help="memory scope for enable",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        openclaw = find_openclaw()
        if args.action == "enable":
            require_supported_openclaw(openclaw)
            enable(openclaw, args.endpoint, args.scope_mode)
        elif args.action == "disable":
            disable(openclaw)
        elif args.action == "off":
            turn_off(openclaw)
        else:
            status(openclaw)
    except ConfigurationError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    return 0


def find_openclaw() -> str:
    configured = os.environ.get("OPENCLAW_BIN")
    if configured:
        return configured
    candidates = ("openclaw.cmd", "openclaw") if os.name == "nt" else ("openclaw",)
    for candidate in candidates:
        executable = shutil.which(candidate)
        if executable is not None:
            return executable
    raise ConfigurationError("OpenClaw CLI is not installed or is not on PATH")


def require_supported_openclaw(executable: str) -> str:
    completed = run_command([executable, "--version"], timeout=30)
    version_text = (completed.stdout or "").strip()
    match = OPENCLAW_VERSION_PATTERN.search(version_text)
    if match is None:
        raise ConfigurationError("could not determine OpenClaw version; requires >= 2026.8.1-beta.2")
    major = int(match.group(1))
    minor = int(match.group(2))
    patch = int(match.group(3))
    beta = int(match.group(4)) if match.group(4) is not None else 10**9
    if (major, minor, patch, beta) < MIN_OPENCLAW_VERSION:
        raise ConfigurationError(f"OpenClaw {version_text} is unsupported; requires >= 2026.8.1-beta.2")
    return version_text


def enable(executable: str, endpoint: str, scope_mode: str) -> None:
    normalized_endpoint = normalize_endpoint(endpoint)
    settings = [
        {"path": "plugins.entries.memory-powercontext.enabled", "value": True},
        {"path": "plugins.entries.memory-powercontext.config.endpoint", "value": normalized_endpoint},
        {"path": "plugins.entries.memory-powercontext.config.autoRecall", "value": True},
        {"path": "plugins.entries.memory-powercontext.config.autoCapture", "value": True},
        {"path": "plugins.entries.memory-powercontext.config.scopeMode", "value": scope_mode},
        {"path": "plugins.entries.memory-powercontext.hooks.allowConversationAccess", "value": True},
        {"path": "plugins.slots.memory", "value": "memory-powercontext"},
    ]
    if read_config_value(executable, "gateway.mode") is None:
        settings.insert(0, {"path": "gateway.mode", "value": "local"})
    run_command(
        [executable, "config", "set", "--batch-json", json.dumps(settings, separators=(",", ":"))],
        timeout=60,
    )
    set_tools_allowlist(executable, add=True)
    restart_gateway(executable)


def disable(executable: str) -> None:
    run_command(
        [
            executable,
            "config",
            "set",
            "--batch-json",
            json.dumps(
                [
                    {"path": "plugins.entries.memory-powercontext.enabled", "value": False},
                    {"path": "plugins.entries.memory-core.enabled", "value": True},
                    {"path": "plugins.slots.memory", "value": "memory-core"},
                ],
                separators=(",", ":"),
            ),
        ],
        timeout=60,
    )
    set_tools_allowlist(executable, add=False)
    restart_gateway(executable)


def turn_off(executable: str) -> None:
    run_command([executable, "config", "set", "plugins.slots.memory", "none"], timeout=60)
    set_tools_allowlist(executable, add=False)
    restart_gateway(executable)


def status(executable: str) -> None:
    for path in (
        "plugins.entries.memory-powercontext.enabled",
        "plugins.entries.memory-powercontext.config.endpoint",
        "plugins.slots.memory",
    ):
        print(run_command([executable, "config", "get", path], timeout=60).stdout.strip())
    allowlist = read_tools_allowlist(executable)
    allowed = set(allowlist)
    print(f"tools.alsoAllow: {json.dumps(allowlist, ensure_ascii=False)}")
    for tool in POWERCONTEXT_TOOLS:
        print(f"  {tool}: {'allowed' if tool in allowed else 'not allowed'}")
    count = sum(tool in allowed for tool in POWERCONTEXT_TOOLS)
    print(f"PowerContext tools allowed: {count}/{len(POWERCONTEXT_TOOLS)}")


def normalize_endpoint(endpoint: str) -> str:
    value = endpoint.strip().rstrip("/")
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or parsed.hostname is None:
        raise ConfigurationError("endpoint must be an http(s) URL without whitespace, quotes, or backslashes")
    if parsed.username is not None or parsed.password is not None:
        raise ConfigurationError("endpoint must not contain credentials")
    if parsed.query or parsed.fragment:
        raise ConfigurationError("endpoint must not contain a query or fragment")
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))


def read_tools_allowlist(executable: str) -> list[object]:
    completed = run_command([executable, "config", "get", "tools.alsoAllow", "--json"], timeout=60, check=False)
    if completed.returncode != 0:
        return []
    try:
        value = json.loads(completed.stdout or "[]")
    except json.JSONDecodeError as error:
        raise ConfigurationError("OpenClaw returned invalid JSON for tools.alsoAllow") from error
    if not isinstance(value, list):
        raise ConfigurationError("OpenClaw tools.alsoAllow is not an array")
    return value


def read_config_value(executable: str, path: str) -> object | None:
    """Read a config value, returning ``None`` when the path is absent."""

    command = [executable, "config", "get", path, "--json"]
    completed = run_command(command, timeout=60, check=False)
    if completed.returncode != 0:
        return None
    try:
        return json.loads(completed.stdout or "null")
    except json.JSONDecodeError as error:
        raise ConfigurationError(f"OpenClaw returned invalid JSON for {path}") from error


def set_tools_allowlist(executable: str, *, add: bool) -> None:
    current = read_tools_allowlist(executable)
    if add:
        updated = list(current)
        for tool in POWERCONTEXT_TOOLS:
            if tool not in updated:
                updated.append(tool)
    else:
        updated = [value for value in current if not isinstance(value, str) or value not in POWERCONTEXT_TOOLS]
    run_command(
        [
            executable,
            "config",
            "set",
            "tools.alsoAllow",
            json.dumps(updated, ensure_ascii=False, separators=(",", ":")),
            "--strict-json",
        ],
        timeout=60,
    )


def restart_gateway(executable: str) -> None:
    if os.environ.get("OPENCLAW_RESTART", "1") != "0":
        run_command([executable, "gateway", "restart"], timeout=180)


def run_command(command: list[str], *, timeout: int, check: bool = True) -> subprocess.CompletedProcess[str]:
    try:
        completed = subprocess.run(  # noqa: S603 - arguments are passed directly to fixed executables.
            command,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise ConfigurationError(f"cannot run {' '.join(command)}: {error}") from error
    if check and completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or f"exit code {completed.returncode}"
        raise ConfigurationError(f"`{' '.join(command)}` failed: {detail}")
    return completed


if __name__ == "__main__":
    raise SystemExit(main())
