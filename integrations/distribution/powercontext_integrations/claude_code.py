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

"""Claude Code installation, configuration, and native discovery."""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from shutil import which
from typing import Any, cast
from urllib.parse import urlsplit, urlunsplit

import typer

from powercontext.cli.system import (
    Diagnostic,
    DiagnosticStatus,
    SetupError,
)
from powercontext.transport import is_loopback_host

from .hosts import host_adapter
from .native import _required_string, _write_bytes_atomically

PLUGIN_NAME = "powercontext"
CLAUDE_MARKETPLACE_NAME = "powercontext"
_GITHUB_REPOSITORY = re.compile(r"^[^/\s]+/[^/\s]+$")


@dataclass(frozen=True, slots=True)
class ClaudeCodeSetupResult:
    marketplace: str
    plugin: str
    plugin_version: str
    settings_file: str
    cache_dir: str
    data_dir: str
    authorization_state: str = "not_attempted"


def install_claude_code_plugin(
    *,
    source: str,
    ref: str,
    server_url: str,
    capture_prompts: bool,
    allow_insecure_http: bool = False,
) -> ClaudeCodeSetupResult:
    """Install and verify the plugin from one local or Git marketplace source."""

    if which("claude") is None:
        raise SetupError.unavailable("Claude Code CLI")
    server_url = _normalize_claude_server_url(server_url, allow_insecure_http=allow_insecure_http)

    _write_claude_setup_plan(_claude_setup_plan())
    marketplace_source = _normalize_claude_marketplace_source(source, ref=ref)
    marketplaces = _run_claude_json("plugin", "marketplace", "list")
    marketplace = _claude_marketplace(marketplaces, CLAUDE_MARKETPLACE_NAME)
    if marketplace is not None and not _claude_marketplace_matches(marketplace, marketplace_source):
        raise SetupError(
            f"Claude Code marketplace `{CLAUDE_MARKETPLACE_NAME}` uses {_describe_claude_marketplace_source(marketplace)}, but setup requested {marketplace_source}. Remove it with `claude plugin marketplace remove {CLAUDE_MARKETPLACE_NAME}`, then rerun setup."
        )
    marketplace_existed = marketplace is not None

    plugins = _run_claude_json("plugin", "list")
    previous_plugin = _claude_plugin(plugins, scope="user")
    plugin_existed = previous_plugin is not None
    settings_snapshot = _snapshot_claude_settings()
    marketplace_added = False
    plugin_added = False
    try:
        if not marketplace_existed:
            _run_claude("plugin", "marketplace", "add", marketplace_source, "--scope", "user")
            marketplace_added = True
        else:
            # An existing marketplace and plugin keep their cached version until
            # both are refreshed, so setup would otherwise configure a status
            # line for a cache this release never wrote.
            _run_claude("plugin", "marketplace", "update", CLAUDE_MARKETPLACE_NAME)
        if plugin_existed:
            _run_claude(
                "plugin",
                "update",
                f"{PLUGIN_NAME}@{CLAUDE_MARKETPLACE_NAME}",
                "--scope",
                "user",
            )
        _run_claude(
            "plugin",
            "install",
            f"{PLUGIN_NAME}@{CLAUDE_MARKETPLACE_NAME}",
            "--scope",
            "user",
        )
        plugin_added = not plugin_existed
        installed = _run_claude_json("plugin", "list")
        plugin = _require_enabled_claude_plugin(installed, scope="user")
        _configure_claude_plugin(
            plugin=plugin,
            server_url=server_url,
            capture_prompts=capture_prompts,
            allow_insecure_http=allow_insecure_http,
        )
    except SetupError:
        if plugin_added:
            with suppress(SetupError):
                _run_claude(
                    "plugin",
                    "uninstall",
                    f"{PLUGIN_NAME}@{CLAUDE_MARKETPLACE_NAME}",
                    "--scope",
                    "user",
                )
        with suppress(OSError):
            _restore_claude_settings(settings_snapshot)
        if marketplace_added:
            with suppress(SetupError):
                _run_claude(
                    "plugin",
                    "marketplace",
                    "remove",
                    CLAUDE_MARKETPLACE_NAME,
                )
        raise

    plan = _claude_setup_plan()
    from .authorization import configure_stored_authorization, setup_authorization_value

    authorization_state = configure_stored_authorization(
        "claude-code", server_url=server_url, value=setup_authorization_value("claude-code")
    )
    return ClaudeCodeSetupResult(
        marketplace=CLAUDE_MARKETPLACE_NAME,
        plugin=PLUGIN_NAME,
        plugin_version=_required_string(plugin, "version"),
        settings_file=plan["settings_file"],
        cache_dir=plan["cache_dir"],
        data_dir=plan["data_dir"],
        authorization_state=authorization_state,
    )


def run_claude_code_diagnostics() -> dict[str, Diagnostic]:
    """Collect diagnostics for the optional Claude Code integration."""

    executable = which("claude")
    if executable is None:
        return {
            "claude_code": Diagnostic(
                status=DiagnosticStatus.FAILED,
                detail="Claude Code CLI is not installed or is not on PATH",
            ),
            "plugin": Diagnostic(
                status=DiagnosticStatus.SKIPPED,
                detail="not checked because Claude Code CLI is unavailable",
            ),
        }
    try:
        result = _run_claude_json("plugin", "list")
    except SetupError as error:
        return {
            "claude_code": Diagnostic(status=DiagnosticStatus.FAILED, detail=str(error)),
            "plugin": Diagnostic(status=DiagnosticStatus.SKIPPED, detail="plugin list is unavailable"),
        }
    plugin = _claude_plugin(result)
    plugin_enabled = plugin is not None and plugin.get("enabled") is True
    return {
        "claude_code": Diagnostic(status=DiagnosticStatus.OK, detail=executable),
        "plugin": Diagnostic(
            status=DiagnosticStatus.OK if plugin_enabled else DiagnosticStatus.FAILED,
            detail=(
                f"{plugin.get('id')} enabled={plugin.get('enabled')}"
                if plugin is not None
                else "PowerContext plugin is not installed"
            ),
        ),
    }


def _normalize_claude_marketplace_source(source: str, *, ref: str) -> str:
    candidate = Path(source).expanduser()
    is_local = source.startswith((".", "/", "~")) or candidate.is_absolute() or candidate.exists()
    if is_local:
        return str(candidate.resolve())
    if not ref:
        return source
    if _GITHUB_REPOSITORY.fullmatch(source):
        return f"{source}@{ref}"
    return f"{source}#{ref}"


def _normalize_claude_server_url(value: str, *, allow_insecure_http: bool = False) -> str:
    normalized = value.strip().rstrip("/")
    parsed = urlsplit(normalized)
    if parsed.username is not None or parsed.password is not None:
        raise SetupError("PowerContext Server URL must not contain credentials.")
    if parsed.hostname is None or parsed.scheme not in {"http", "https"}:
        raise SetupError("PowerContext Server URL must use HTTP or HTTPS.")
    if parsed.query or parsed.fragment:
        raise SetupError("PowerContext Server URL must not contain a query or fragment.")
    if parsed.scheme == "http" and not is_loopback_host(parsed.hostname) and not allow_insecure_http:
        raise SetupError("Unencrypted PowerContext Server URLs must be loopback addresses.")
    path = parsed.path.rstrip("/")
    if path.endswith("/mcp"):
        path = path.removesuffix("/mcp")
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", "")).rstrip("/")


def _claude_config_dir() -> Path:
    configured = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(configured).expanduser() if configured else Path.home() / ".claude"


def _claude_setup_plan() -> dict[str, str]:
    config_dir = _claude_config_dir()
    return {
        "settings_file": str(config_dir / "settings.json"),
        "cache_dir": str(config_dir / "plugins" / "cache" / CLAUDE_MARKETPLACE_NAME / PLUGIN_NAME / "<version>"),
        "data_dir": str(config_dir / "plugins" / "data" / f"{PLUGIN_NAME}-{CLAUDE_MARKETPLACE_NAME}"),
    }


def _write_claude_setup_plan(plan: dict[str, str]) -> None:
    typer.echo("Claude Code setup plan (no changes made yet):", err=True)
    typer.echo(f"  Settings entry: {plan['settings_file']}", err=True)
    typer.echo(f"  Plugin cache: {plan['cache_dir']}", err=True)
    typer.echo(f"  Plugin data: {plan['data_dir']}", err=True)
    typer.echo("  Permissions: read/write access to the Claude Code configuration directory", err=True)
    typer.echo(
        f"  Rollback: claude plugin uninstall {PLUGIN_NAME}@{CLAUDE_MARKETPLACE_NAME} --scope user",
        err=True,
    )
    typer.echo(
        f"  Rollback: claude plugin marketplace remove {CLAUDE_MARKETPLACE_NAME}",
        err=True,
    )


def _claude_marketplace(value: object, name: str) -> dict[str, Any] | None:
    if not isinstance(value, list):
        return None
    for item in value:
        if isinstance(item, dict) and item.get("name") == name:
            return cast(dict[str, Any], item)
    return None


def _claude_marketplace_matches(marketplace: dict[str, Any], requested: str) -> bool:
    source_kind = marketplace.get("source")
    if source_kind == "directory":
        existing_path = marketplace.get("path")
        if not isinstance(existing_path, str):
            return False
        return os.path.normcase(str(Path(existing_path).resolve())) == os.path.normcase(str(Path(requested).resolve()))
    if source_kind == "github":
        requested_repo, separator, requested_ref = requested.partition("@")
        existing_repo = marketplace.get("repo")
        existing_ref = marketplace.get("ref")
        return (
            isinstance(existing_repo, str)
            and existing_repo.casefold() == requested_repo.casefold()
            and _claude_marketplace_ref_matches(existing_ref, requested_ref if separator else "")
        )
    if source_kind == "git":
        requested_url, separator, requested_ref = requested.rpartition("#")
        existing_url = marketplace.get("url")
        existing_ref = marketplace.get("ref")
        return (
            isinstance(existing_url, str)
            and existing_url == (requested_url if separator else requested)
            and _claude_marketplace_ref_matches(existing_ref, requested_ref if separator else "")
        )
    return False


def _claude_marketplace_ref_matches(existing: object, requested: str) -> bool:
    """Accept omitted Claude JSON refs while still rejecting an explicit mismatch."""

    return existing is None or existing == "" or existing == requested


def _describe_claude_marketplace_source(marketplace: dict[str, Any]) -> str:
    fields = {name: marketplace[name] for name in ("source", "path", "repo", "url", "ref") if name in marketplace}
    return json.dumps(fields, sort_keys=True)


def _claude_plugin(value: object, *, scope: str | None = None) -> dict[str, Any] | None:
    if not isinstance(value, list):
        return None
    for item in value:
        if (
            isinstance(item, dict)
            and item.get("id") == f"{PLUGIN_NAME}@{CLAUDE_MARKETPLACE_NAME}"
            and (scope is None or item.get("scope") == scope)
        ):
            return cast(dict[str, Any], item)
    return None


def _require_enabled_claude_plugin(value: object, *, scope: str | None = None) -> dict[str, Any]:
    plugin = _claude_plugin(value, scope=scope)
    if plugin is None or plugin.get("enabled") is not True:
        raise SetupError("Claude Code did not report an enabled PowerContext plugin after installation.")
    return plugin


def _snapshot_claude_settings() -> bytes | None:
    settings_file = _claude_config_dir() / "settings.json"
    try:
        return settings_file.read_bytes()
    except FileNotFoundError:
        return None


def _configure_claude_plugin(
    *,
    plugin: dict[str, Any],
    server_url: str,
    capture_prompts: bool,
    allow_insecure_http: bool = False,
) -> None:
    """Merge non-sensitive plugin options unsupported by the Claude install CLI."""

    settings_file = _claude_config_dir() / "settings.json"
    try:
        settings = json.loads(settings_file.read_text(encoding="utf-8")) if settings_file.exists() else {}
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        if isinstance(error, OSError):
            raise SetupError(f"Cannot update Claude Code settings at {settings_file}: {error}") from error
        raise SetupError(
            f"Claude Code settings at {settings_file} must contain a JSON object with object-valued plugin options."
        ) from error
    if not isinstance(settings, dict):
        raise SetupError(
            f"Claude Code settings at {settings_file} must contain a JSON object with object-valued plugin options."
        )

    plugin_configs = settings.setdefault("pluginConfigs", {})
    if not isinstance(plugin_configs, dict):
        raise SetupError(
            f"Claude Code settings at {settings_file} must contain a JSON object with object-valued plugin options."
        )
    plugin_id = f"{PLUGIN_NAME}@{CLAUDE_MARKETPLACE_NAME}"
    plugin_config = plugin_configs.setdefault(plugin_id, {})
    if not isinstance(plugin_config, dict):
        raise SetupError(
            f"Claude Code settings at {settings_file} must contain a JSON object with object-valued plugin options."
        )
    options = plugin_config.setdefault("options", {})
    if not isinstance(options, dict):
        raise SetupError(
            f"Claude Code settings at {settings_file} must contain a JSON object with object-valued plugin options."
        )
    options.update({
        "server_url": server_url,
        "capture_prompts": capture_prompts,
        "allow_insecure_http": allow_insecure_http,
    })

    install_path = _claude_plugin_install_path(plugin)
    host_adapter("claude-code").prepare(install_path)
    statusline_command = shlex.join([
        "powercontext-hook",
        "--script",
        str(install_path / "scripts" / "statusline.py"),
        "--",
        "--server-url",
        server_url,
    ])
    statusline = settings.get("statusLine")
    if statusline is None or _is_powercontext_statusline(statusline):
        settings["statusLine"] = {
            "type": "command",
            "command": statusline_command,
            "refreshInterval": 30,
        }
    try:
        _write_bytes_atomically(settings_file, (json.dumps(settings, indent=2) + "\n").encode())
    except OSError as error:
        raise SetupError(f"Cannot update Claude Code settings at {settings_file}: {error}") from error


def _claude_plugin_install_path(plugin: dict[str, Any]) -> Path:
    install_path = plugin.get("installPath")
    if isinstance(install_path, str) and install_path:
        return Path(install_path)
    version_value = _required_string(plugin, "version")
    return _claude_config_dir() / "plugins" / "cache" / CLAUDE_MARKETPLACE_NAME / PLUGIN_NAME / version_value


def _is_powercontext_statusline(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    command = value.get("command")
    if not isinstance(command, str):
        return False
    try:
        tokens = shlex.split(command)
    except ValueError:
        return False
    return any(
        Path(token).name == "statusline.py" and any("powercontext" in part.casefold() for part in Path(token).parts)
        for token in tokens
    )


def _restore_claude_settings(snapshot: bytes | None) -> None:
    settings_file = _claude_config_dir() / "settings.json"
    if snapshot is None:
        settings_file.unlink(missing_ok=True)
        return
    _write_bytes_atomically(settings_file, snapshot)


def _run_claude(*arguments: str) -> subprocess.CompletedProcess[str]:
    executable = which("claude")
    if executable is None:
        raise SetupError.unavailable("Claude Code CLI")
    command = [executable, *arguments]
    try:
        completed = subprocess.run(  # noqa: S603 - arguments are passed directly to the fixed Claude executable.
            command,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise SetupError.command_unavailable(command, error) from error
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or f"exit code {completed.returncode}"
        raise SetupError.command_failed(command, detail)
    return completed


def _run_claude_json(*arguments: str) -> object:
    command = [*arguments, "--json"]
    completed = _run_claude(*command)
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise SetupError.invalid_command_output(["claude", *command], "invalid JSON") from error
