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

"""Install and diagnose the PowerContext Antigravity (AGY) plugin."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from shutil import which
from typing import Any
from urllib.request import Request, urlopen

from powercontext.cli.authorization import (
    credential_path,
    read_stored_authorization,
    setup_authorization_value,
)
from powercontext.cli.guidance import HOST_GUIDANCE, SKILL_FILE, preserve_installed_guidance
from powercontext.cli.installation import SetupError, publish
from powercontext.cli.memory_protocol import installed_memory_protocol, read_plugin_memory_protocol
from powercontext.cli.system import Diagnostic, DiagnosticStatus, _request_json
from powercontext.cli.transport import configure_connection
from powercontext.client.transport_policy import normalize_client_url

OWNER_MARKER = ".powercontext-owned"
DEFAULT_SERVER_URL = "http://127.0.0.1:8000"
_REQUIRED_FILES = (
    Path("plugin.json"),
    Path("hooks.json"),
    Path("mcp_config.json"),
    Path("hooks") / "hook.py",
    Path("hooks") / "bind_tools.py",
    SKILL_FILE,
)
_HOOK_EVENTS = ("SessionStart", "PreInvocation", "PreToolUse")


def antigravity_home() -> Path:
    """Return Antigravity's global customization directory (~/.gemini/config)."""

    configured = os.environ.get("ANTIGRAVITY_CONFIG_DIR", "").strip()
    return Path(configured).expanduser() if configured else Path.home() / ".gemini" / "config"


def antigravity_plugin_dir() -> Path:
    """Return the installed PowerContext plugin directory under Antigravity."""

    return antigravity_home() / "plugins" / "powercontext"


def antigravity_executable() -> str | None:
    """Locate the Antigravity CLI (`agy`) or macOS desktop app."""

    if os.name == "nt":
        cmd = which("agy.cmd")
        if cmd:
            return cmd
    cli = which("agy")
    if cli:
        return cli
    if sys.platform == "darwin":
        app = Path("/Applications/Antigravity.app")
        if app.is_dir():
            return str(app)
    return None


def _require_plugin_package(path: Path) -> None:
    missing = [rel.as_posix() for rel in _REQUIRED_FILES if not (path / rel).is_file()]
    if missing:
        raise SetupError(f"PowerContext Antigravity package at {path} is missing: {', '.join(missing)}")  # noqa: TRY003


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
        return loaded if isinstance(loaded, dict) else {}
    except (OSError, UnicodeError, ValueError):
        return {}


def _is_owned_or_recognized(destination: Path) -> bool:
    if (destination / OWNER_MARKER).is_file():
        return True
    return destination.is_symlink() and (destination / "antigravity_host.py").is_file()


def _resolve_hook_python() -> tuple[str, str]:
    """Return (executable_path, quoted_shell_prefix) for Antigravity hook scripts."""

    executable = sys.executable if os.name == "nt" else (which("python3") or sys.executable)
    quoted = subprocess.list2cmdline([executable]) if os.name == "nt" else shlex.quote(executable)
    return executable, quoted


def _iter_hook_entries(group: dict[str, Any]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for event in ("SessionStart", "PreInvocation"):
        items = group.get(event)
        if isinstance(items, list):
            entries.extend(item for item in items if isinstance(item, dict))
    pre_tool = group.get("PreToolUse")
    if isinstance(pre_tool, list):
        for matcher_group in pre_tool:
            nested = matcher_group.get("hooks") if isinstance(matcher_group, dict) else None
            if isinstance(nested, list):
                entries.extend(item for item in nested if isinstance(item, dict))
    return entries


def _rewrite_installed_hooks_commands(staged: Path) -> None:
    hooks_path = staged / "hooks.json"
    data = _read_json(hooks_path)
    group = data.get("powercontext")
    if not isinstance(group, dict):
        return
    _, python_cmd = _resolve_hook_python()
    for entry in _iter_hook_entries(group):
        cmd = entry.get("command")
        if isinstance(cmd, str) and cmd.startswith("python3 "):
            entry["command"] = f"{python_cmd} {cmd[len('python3 ') :]}"
    hooks_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _refresh_live_mcp() -> None:
    address = os.environ.get("ANTIGRAVITY_LS_ADDRESS", "").strip()
    token = os.environ.get("ANTIGRAVITY_CSRF_TOKEN", "").strip()
    if not address or not token:
        return
    req = Request(
        f"http://{address}/exa.language_server_pb.LanguageServerService/RefreshMcpServers",
        data=b"{}",
        headers={"Content-Type": "application/json", "x-codeium-csrf-token": token},
        method="POST",
    )
    with suppress(Exception), urlopen(req, timeout=3):  # noqa: S310 - local language server address from runtime env.
        pass


def _extract_command_strings(hooks_data: dict[str, Any]) -> dict[str, list[str]]:
    commands: dict[str, list[str]] = {}
    for event in _HOOK_EVENTS:
        entries = hooks_data.get(event)
        found: list[str] = []
        if isinstance(entries, list):
            for item in entries:
                if not isinstance(item, dict):
                    continue
                if event == "PreToolUse" and isinstance(item.get("hooks"), list):
                    for nested in item["hooks"]:
                        if isinstance(nested, dict) and isinstance(nested.get("command"), str):
                            found.append(nested["command"])
                elif isinstance(item.get("command"), str):
                    found.append(item["command"])
        commands[event] = found
    return commands


def _split_shell_words(command: str) -> list[str]:
    return [part.strip('"') for part in shlex.split(command, posix=(os.name != "nt"))]


def _validate_smoke_output(event: str, stdout: str) -> str | None:
    try:
        parsed = json.loads(stdout.strip())
    except ValueError:
        return f"{event} hook emitted invalid JSON during smoke probe"
    if event == "PreToolUse":
        if not isinstance(parsed, dict) or parsed.get("decision") != "ask":
            return f"PreToolUse hook failed bind_tools smoke probe: {parsed!r}"
        return None
    if not isinstance(parsed, dict):
        return f"{event} hook emitted non-object JSON during smoke probe"
    return None


def _smoke_check_hook_command(destination: Path, command: str, event: str) -> str | None:
    words = _split_shell_words(command)
    if len(words) < 2:
        return f"{event} hook command is malformed: {command}"
    python_token, script_token = words[0], words[1]
    python_exe = python_token if Path(python_token).is_file() else which(python_token)
    if not python_exe:
        return f"{event} hook interpreter not found: {python_token}"
    script_path = Path(script_token) if Path(script_token).is_absolute() else destination / script_token
    if not script_path.is_file():
        return f"{event} hook script missing: {script_path}"
    args = [python_exe, "-S", str(script_path), *words[2:]]
    try:
        completed = subprocess.run(  # noqa: S603 - validated command words from installed hooks.json.
            args,
            input="{}",
            text=True,
            capture_output=True,
            cwd=destination,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        return f"{event} hook smoke probe failed: {error}"
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or f"exit {completed.returncode}"
        return f"{event} hook failed under python -S: {detail}"
    return _validate_smoke_output(event, completed.stdout)


def _verify_host(home: Path) -> Diagnostic:
    executable = antigravity_executable()
    if executable:
        return Diagnostic(DiagnosticStatus.OK, executable)
    if home.is_dir():
        return Diagnostic(
            DiagnosticStatus.DEGRADED, f"Antigravity config dir found at {home}, CLI/App binary not on PATH"
        )
    return Diagnostic(DiagnosticStatus.FAILED, "Antigravity CLI/App and config directory not found")


def _verify_plugin(home: Path, destination: Path) -> Diagnostic:
    installed = (destination / OWNER_MARKER).is_file() and HOST_GUIDANCE["antigravity"].recognizes(destination)
    if not installed:
        return Diagnostic(DiagnosticStatus.FAILED, "PowerContext Antigravity plugin is not installed")
    native_cfg = _read_json(destination / "powercontext.json")
    prompt_recall = native_cfg.get("prompt_recall", True)
    capture_prompts = native_cfg.get("capture_prompts", True)
    bootstrap_context = native_cfg.get("bootstrap_context", False)
    checks = {
        "prompt_recall": str(bool(prompt_recall)).lower(),
        "capture_prompts": str(bool(capture_prompts)).lower(),
        "bootstrap_context": str(bool(bootstrap_context)).lower(),
    }
    cfg = _read_json(home / "config.json")
    plugin_cfg = (cfg.get("plugins") or {}).get("powercontext") if isinstance(cfg.get("plugins"), dict) else None
    if isinstance(plugin_cfg, dict) and plugin_cfg.get("enabled") is False:
        return Diagnostic(
            DiagnosticStatus.DEGRADED,
            f"installed at {destination}, disabled in config.json",
            checks=checks,
        )
    return Diagnostic(
        DiagnosticStatus.OK,
        f"{destination} (prompt_recall={bool(prompt_recall)}, capture_prompts={bool(capture_prompts)})",
        checks=checks,
    )


def _verify_runtime(destination: Path) -> Diagnostic:
    hooks_data = _read_json(destination / "hooks.json").get("powercontext", {})
    configured_python: str | None = None
    if isinstance(hooks_data, dict):
        for cmds in _extract_command_strings(hooks_data).values():
            if cmds:
                words = _split_shell_words(cmds[0])
                if words:
                    configured_python = which(words[0]) or (words[0] if Path(words[0]).is_file() else None)
                    break
    python_runtime = configured_python or which("python3") or sys.executable
    if python_runtime:
        return Diagnostic(DiagnosticStatus.OK, f"stdlib ({python_runtime})")
    return Diagnostic(DiagnosticStatus.FAILED, "python3 not found on PATH for stdlib hooks")


def _verify_hooks(destination: Path) -> Diagnostic:
    hooks_data = _read_json(destination / "hooks.json").get("powercontext", {})
    if not isinstance(hooks_data, dict):
        return Diagnostic(DiagnosticStatus.FAILED, "missing or invalid hooks.json")
    commands_by_event = _extract_command_strings(hooks_data)
    missing_events = [ev for ev in _HOOK_EVENTS if not commands_by_event.get(ev)]
    if missing_events:
        return Diagnostic(DiagnosticStatus.FAILED, f"missing hooks for: {', '.join(missing_events)}")
    for event in _HOOK_EVENTS:
        for cmd in commands_by_event[event]:
            error = _smoke_check_hook_command(destination, cmd, event)
            if error is not None:
                return Diagnostic(DiagnosticStatus.FAILED, error)
    return Diagnostic(DiagnosticStatus.OK, "valid (python -S smoke probe passed)")


def _authorization_header_from_entry(entry: object) -> str | None:
    if not isinstance(entry, dict):
        return None
    headers = entry.get("headers")
    if not isinstance(headers, dict):
        return None
    for key, value in headers.items():
        if str(key).lower() == "authorization" and isinstance(value, str):
            return value
    return None


def _verify_mcp_and_credentials(home: Path, destination: Path) -> Diagnostic:
    mcp_data = _read_json(destination / "mcp_config.json")
    native_cfg = _read_json(destination / "powercontext.json")
    endpoint = native_cfg.get("server_url")
    entry = (mcp_data.get("mcpServers") or {}).get("powercontext") or {}
    mcp_url = entry.get("url") if isinstance(entry, dict) else None
    mcp_ok = (
        isinstance(endpoint, str)
        and bool(endpoint)
        and isinstance(mcp_url, str)
        and mcp_url.rstrip("/") == endpoint.rstrip("/") + "/mcp"
    )
    if not mcp_ok or not isinstance(endpoint, str) or not isinstance(mcp_url, str):
        return Diagnostic(DiagnosticStatus.FAILED, "missing or mismatched")

    cred_file = credential_path("antigravity")
    cred_status = "not_configured"
    if cred_file.is_symlink() or cred_file.exists():
        if cred_file.is_symlink() or (
            os.name != "nt" and stat.S_IMODE(cred_file.stat().st_mode) & (stat.S_IRWXG | stat.S_IRWXO)
        ):
            return Diagnostic(
                DiagnosticStatus.FAILED,
                f"{cred_file} must have 0600 permissions; hooks ignore group/world-accessible credentials",
            )
        stored = read_stored_authorization(cred_file, server_url=endpoint)
        cred_status = stored.status
        if stored.authorization is not None and _authorization_header_from_entry(entry) != stored.authorization:
            return Diagnostic(
                DiagnosticStatus.FAILED,
                "mcp_config.json Authorization header is out of sync with credentials.json; re-run powercontext setup antigravity",
            )

    global_mcp = _read_json(home / "mcp_config.json")
    global_entry = (global_mcp.get("mcpServers") or {}).get("powercontext")
    if isinstance(global_entry, dict):
        return Diagnostic(
            DiagnosticStatus.DEGRADED,
            f"duplicate 'powercontext' MCP server in {home / 'mcp_config.json'} and plugin mcp_config.json; remove the global entry so tools are not listed twice",
            checks={"global_duplicate": "present", "credential_file": cred_status},
        )

    return Diagnostic(
        DiagnosticStatus.OK,
        mcp_url,
        checks={"global_duplicate": "none", "credential_file": cred_status},
    )


def _active_extraction_failure(extraction: object) -> tuple[str, str] | None:
    observation = extraction.get("observation") if isinstance(extraction, dict) else None
    last_failure = observation.get("last_failure") if isinstance(observation, dict) else None
    last_success = observation.get("last_success_at") if isinstance(observation, dict) else None
    if not isinstance(last_failure, dict):
        return None
    code = last_failure.get("code")
    if not isinstance(code, str) or not code:
        return None
    if last_success is not None and str(last_failure.get("occurred_at", "")) < str(last_success):
        return None
    return code, str(last_failure.get("stage", "unknown"))


def _verify_hook_diagnostics() -> Diagnostic:
    plugin_data_root = antigravity_home().parent / "antigravity" / "plugin_data"
    plugin_data_dir = plugin_data_root / "powercontext"
    diag_override = os.environ.get("POWERCONTEXT_DIAGNOSTIC_STATE_FILE", "").strip()
    diag_candidates = (
        (Path(diag_override).expanduser(),)
        if diag_override
        else (
            Path.home() / ".config" / "powercontext" / "antigravity-diagnostics.json",
            plugin_data_dir / "antigravity-diagnostics.json",
        )
    )
    diag_data = next((_read_json(path) for path in diag_candidates if path.is_file()), {})
    plugin_data = os.environ.get("PLUGIN_DATA", "").strip()
    state_candidates: list[Path] = [
        plugin_data_dir / "state",
        Path.home() / ".config" / "powercontext" / "state" / "antigravity",
    ]
    if plugin_data:
        env_plugin_dir = Path(plugin_data).expanduser()
        is_sibling_plugin = (
            env_plugin_dir.parent.resolve() == plugin_data_root.resolve() and env_plugin_dir.name != "powercontext"
        )
        if not is_sibling_plugin:
            state_candidates = [env_plugin_dir / "state"]
    seen_dirs: set[Path] = set()
    state_count = 0
    for state_dir in state_candidates:
        resolved = state_dir.resolve()
        if resolved not in seen_dirs and state_dir.is_dir():
            seen_dirs.add(resolved)
            state_count += len(list(state_dir.glob("*.json")))
    recorded_keys = sorted(str(k) for k in diag_data)[:10]
    checks = {
        "session_state_files": str(state_count),
        "diagnostic_entries": str(len(diag_data)),
    }
    if recorded_keys:
        checks["recorded_codes"] = ",".join(recorded_keys)
    return Diagnostic(
        DiagnosticStatus.OK,
        f"{state_count} session state file(s), {len(diag_data)} diagnostic entry/entries",
        checks=checks,
    )


def extraction_schedule_diagnostic(server_url: str, *, authorization: str | None = None) -> Diagnostic:
    """Report whether the connected Server has background automatic extraction enabled."""

    try:
        status, body = _request_json(server_url, "/v1/capabilities", authorization=authorization)
    except OSError:
        return Diagnostic(
            DiagnosticStatus.OK,
            "automatic extraction status unavailable",
            checks={"automatic_processing": "unknown"},
        )
    if status != 200 or not isinstance(body, dict):
        return Diagnostic(
            DiagnosticStatus.OK,
            f"automatic extraction status unavailable (HTTP {status})",
            checks={"automatic_processing": "unknown"},
        )
    extraction = body.get("extraction")
    background = extraction.get("background") if isinstance(extraction, dict) else None
    auto_enabled = background.get("automatic_processing_enabled") if isinstance(background, dict) else None
    failure = _active_extraction_failure(extraction)
    if auto_enabled is True:
        detail = "automatic background extraction enabled"
        checks = {"automatic_processing": "enabled"}
        if failure is not None:
            detail += f" (last failure: {failure[0]} at {failure[1]})"
            checks["last_failure"] = f"{failure[0]}@{failure[1]}"
        return Diagnostic(DiagnosticStatus.OK, detail, checks=checks)
    if auto_enabled is False:
        return Diagnostic(
            DiagnosticStatus.OK,
            "on-demand only (set POWERCONTEXT_SERVER_RUNTIME_MEMORY_SCHEDULE_SECONDS to enable automatic background extraction)",
            checks={"automatic_processing": "disabled"},
        )
    return Diagnostic(
        DiagnosticStatus.OK,
        "automatic extraction status unavailable",
        checks={"automatic_processing": "unknown"},
    )


@dataclass
class AntigravityInstaller:
    prompt_recall: bool | None = None
    capture_prompts: bool | None = None

    def install(self, source: Any) -> dict[str, Any]:
        with source.package("antigravity") as plugin:
            _require_plugin_package(plugin)
            destination = antigravity_plugin_dir()
            was_symlink = destination.is_symlink()
            if (destination.exists() or was_symlink) and not _is_owned_or_recognized(destination):
                message = f"Antigravity plugin path {destination} already exists and is not owned by PowerContext"
                raise SetupError(message)
            destination.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix=".powercontext-", dir=destination.parent) as temporary:
                staged = Path(temporary) / "powercontext"
                shutil.copytree(
                    plugin,
                    staged,
                    ignore=shutil.ignore_patterns(".venv", "__pycache__", ".pytest_cache", "tests"),
                )
                _rewrite_installed_hooks_commands(staged)
                if destination.is_dir() and not was_symlink:
                    preserve_installed_guidance(destination, staged)
                    for name in ("powercontext.json", "mcp_config.json"):
                        if (destination / name).is_file():
                            shutil.copy2(destination / name, staged / name)
                (staged / OWNER_MARKER).write_text("powercontext-antigravity-v1\n", encoding="utf-8")
                if was_symlink:
                    destination.unlink()
                publish([(staged, destination)])
            return {"plugin": "powercontext", "plugin_path": str(destination)}

    def configure(self, connection: Any) -> dict[str, Any]:
        destination = antigravity_plugin_dir()
        if not (destination / OWNER_MARKER).is_file():
            raise SetupError("PowerContext is not installed in Antigravity; run powercontext setup antigravity")  # noqa: TRY003
        endpoint = normalize_client_url(connection.server_url).removesuffix("/mcp").rstrip("/")
        previous = _read_json(destination / "powercontext.json")
        prompt_recall = self.prompt_recall if self.prompt_recall is not None else previous.get("prompt_recall", True)
        capture = self.capture_prompts if self.capture_prompts is not None else previous.get("capture_prompts", True)

        mcp_path = destination / "mcp_config.json"
        mcp = _read_json(mcp_path)
        servers = mcp.setdefault("mcpServers", {})
        if not isinstance(servers, dict):
            servers = {}
            mcp["mcpServers"] = servers
        entry = servers.setdefault("powercontext", {})
        if not isinstance(entry, dict):
            entry = {}
            servers["powercontext"] = entry
        entry["url"] = endpoint + "/mcp/"

        auth_header = (
            setup_authorization_value("antigravity")
            or read_stored_authorization(credential_path("antigravity"), server_url=endpoint).authorization
        )
        headers = entry.get("headers")
        if auth_header:
            clean_headers = dict(headers) if isinstance(headers, dict) else {}
            clean_headers["Authorization"] = auth_header
            entry["headers"] = clean_headers
        elif isinstance(headers, dict):
            clean_headers = {k: v for k, v in headers.items() if str(k).lower() != "authorization"}
            if clean_headers:
                entry["headers"] = clean_headers
            else:
                entry.pop("headers", None)

        settings = {
            "server_url": endpoint,
            "allow_insecure_http": connection.allow_insecure_http,
            "prompt_recall": bool(prompt_recall),
            "capture_prompts": bool(capture),
        }
        writes = {
            destination / "powercontext.json": (json.dumps(settings, ensure_ascii=False, indent=2) + "\n").encode(),
            mcp_path: (json.dumps(mcp, ensure_ascii=False, indent=2) + "\n").encode(),
        }
        auth_state = configure_connection(connection, writes=writes)
        _refresh_live_mcp()
        return {"plugin_path": str(destination), "authorization_state": auth_state, "server_url": endpoint}

    def verify(self) -> dict[str, Diagnostic]:
        home = antigravity_home()
        destination = antigravity_plugin_dir()
        protocol = read_plugin_memory_protocol(destination, ".powercontext.json")
        return {
            "antigravity": _verify_host(home),
            "plugin": _verify_plugin(home, destination),
            "plugin_runtime": _verify_runtime(destination),
            "plugin_memory_protocol": installed_memory_protocol(protocol, "antigravity"),
            "hooks": _verify_hooks(destination),
            "mcp_configuration": _verify_mcp_and_credentials(home, destination),
            "hook_diagnostics": _verify_hook_diagnostics(),
        }


__all__ = [
    "AntigravityInstaller",
    "antigravity_executable",
    "antigravity_home",
    "antigravity_plugin_dir",
    "extraction_schedule_diagnostic",
]
