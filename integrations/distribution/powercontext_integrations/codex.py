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

"""Codex installation, configuration, and native discovery."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from contextlib import suppress
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path
from queue import Empty, Queue
from shutil import which
from threading import Thread
from time import monotonic
from typing import Any, cast

from powercontext.cli.system import (
    Diagnostic,
    DiagnosticStatus,
    SetupError,
)
from powercontext.paths import powercontext_data_dir

from .hosts import host_adapter
from .native import _required_string, _write_bytes_atomically

DEFAULT_SERVER_URL = "http://127.0.0.1:8000"
PLUGIN_NAME = "powercontext"
_CODEX_REQUIRED_MCP_TOOLS = frozenset({"remember_memory", "search_memory"})
_CODEX_APP_SERVER_TIMEOUT_SECONDS = 15.0


@dataclass(frozen=True, slots=True)
class CodexSetupResult:
    marketplace: str
    plugin: str
    plugin_version: str
    data_dir: str
    authorization_state: str = "not_attempted"


def install_codex_plugin(*, source: str, ref: str, server_url: str | None = None) -> CodexSetupResult:
    """Install the plugin from one local or Git marketplace source."""

    if which("codex") is None:
        raise SetupError.unavailable("Codex CLI")

    data_dir = powercontext_data_dir()
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise SetupError(f"Cannot create PowerContext data directory {data_dir}: {error}") from error

    marketplace_source, is_local = _normalize_marketplace_source(source)
    marketplace_arguments = ["plugin", "marketplace", "add", marketplace_source]
    if not is_local:
        marketplace_arguments.extend(("--ref", ref))
    marketplace = _run_codex_json(*marketplace_arguments)
    marketplace_name = _required_string(marketplace, "marketplaceName")

    plugin = _run_codex_json("plugin", "add", f"{PLUGIN_NAME}@{marketplace_name}")
    from .authorization import (
        configure_codex_desktop_authorization,
        configure_stored_authorization,
        credential_path,
        read_stored_authorization,
        setup_authorization_value,
    )

    authorization_server_url = server_url or DEFAULT_SERVER_URL
    authorization_state = configure_stored_authorization(
        "codex",
        server_url=authorization_server_url,
        value=setup_authorization_value("codex"),
    )
    authorization = read_stored_authorization(credential_path("codex"), server_url=authorization_server_url)
    _configure_codex_endpoint(marketplace_name, _required_string(plugin, "version"), authorization_server_url)
    if authorization.authorization is not None:
        try:
            configure_codex_desktop_authorization(authorization.authorization)
        except OSError as error:
            raise SetupError(f"Cannot configure Codex Desktop authorization: {error}") from error
    return CodexSetupResult(
        marketplace=marketplace_name,
        plugin=_required_string(plugin, "name"),
        plugin_version=_required_string(plugin, "version"),
        data_dir=str(data_dir),
        authorization_state=authorization_state,
    )


def _configure_codex_endpoint(marketplace: str, plugin_version: str, server_url: str | None) -> None:
    """Bind native MCP to the Hook endpoint and saved credential source."""

    from .authorization import credential_path

    if any(part in {"", ".", ".."} or "/" in part or "\\" in part for part in (marketplace, plugin_version)):
        raise SetupError("Invalid Codex plugin cache location")
    codex_home = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))).expanduser()
    path = codex_home / "plugins" / "cache" / marketplace / PLUGIN_NAME / plugin_version / ".mcp.json"
    try:
        host_adapter("codex").prepare(path.parent, server_url=server_url)
        config = json.loads(path.read_text(encoding="utf-8"))
        entry = config["mcpServers"]["powercontext"]
        if not isinstance(entry, dict) or entry.get("type") != "http":
            raise ValueError("Expected an HTTP MCP server")  # noqa: TRY301
        # Codex filters CODEX_HOME. Use the installed client's Python and pass the
        # credential location explicitly so no separate plugin environment is needed.
        helper = [
            sys.executable,
            str((path.parent / "mcp_headers.py").resolve()),
            "--credential-file",
            str(credential_path("codex").absolute()),
        ]
        entry["http_headers_helper"] = (
            subprocess.list2cmdline(helper) if sys.platform == "win32" else shlex.join(helper)
        )
        _write_bytes_atomically(path, (json.dumps(config, indent=2) + "\n").encode())
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise SetupError(f"Cannot generate Codex plugin resources at {path.parent}") from error


def run_codex_diagnostics() -> dict[str, Diagnostic]:
    """Collect plugin and native MCP diagnostics for the optional Codex integration."""

    executable = which("codex")
    if executable is None:
        return {
            "codex": Diagnostic(
                status=DiagnosticStatus.FAILED,
                detail="Codex CLI is not installed or is not on PATH",
            ),
            "plugin": Diagnostic(
                status=DiagnosticStatus.SKIPPED,
                detail="not checked because Codex CLI is unavailable",
            ),
        }
    try:
        result = _run_codex_json("plugin", "list")
    except SetupError as error:
        return {
            "codex": Diagnostic(status=DiagnosticStatus.FAILED, detail=str(error)),
            "plugin": Diagnostic(status=DiagnosticStatus.SKIPPED, detail="plugin list is unavailable"),
        }
    installed = result.get("installed")
    plugin = None
    if isinstance(installed, list):
        plugin = next(
            (
                item
                for item in installed
                if isinstance(item, dict)
                and item.get("name") == PLUGIN_NAME
                and item.get("installed") is True
                and item.get("enabled") is True
            ),
            None,
        )
    diagnostics = {
        "codex": Diagnostic(status=DiagnosticStatus.OK, detail=executable),
        "plugin": Diagnostic(
            status=DiagnosticStatus.OK if plugin is not None else DiagnosticStatus.FAILED,
            detail=(
                f"{plugin.get('pluginId')} enabled={plugin.get('enabled')}"
                if plugin is not None
                else "PowerContext plugin is not installed"
            ),
        ),
    }
    if plugin is None:
        diagnostics["mcp_configuration"] = Diagnostic(
            status=DiagnosticStatus.SKIPPED,
            detail="not checked because the PowerContext plugin is unavailable",
        )
        diagnostics["authorization"] = Diagnostic(
            status=DiagnosticStatus.SKIPPED,
            detail="not checked because the PowerContext MCP entry is unavailable",
        )
        diagnostics["mcp_tools"] = Diagnostic(
            status=DiagnosticStatus.SKIPPED,
            detail="not checked because the PowerContext plugin is unavailable",
        )
        return diagnostics

    try:
        servers = _run_codex_mcp_list()
    except SetupError as error:
        diagnostics["mcp_configuration"] = Diagnostic(status=DiagnosticStatus.FAILED, detail=str(error))
        diagnostics["authorization"] = Diagnostic(
            status=DiagnosticStatus.SKIPPED,
            detail="not checked because native MCP configuration is unavailable",
        )
        diagnostics["mcp_tools"] = Diagnostic(
            status=DiagnosticStatus.SKIPPED,
            detail="not checked because native MCP configuration is unavailable",
        )
        return diagnostics

    server = next((item for item in servers if item.get("name") == PLUGIN_NAME), None)
    transport = server.get("transport") if server is not None else None
    environment_headers = transport.get("env_http_headers") if isinstance(transport, dict) else None
    mcp_url = transport.get("url") if isinstance(transport, dict) else None
    configuration_ok = (
        server is not None
        and server.get("enabled") is True
        and isinstance(mcp_url, str)
        and bool(mcp_url)
        and environment_headers == {"Authorization": "POWERCONTEXT_CODEX_AUTHORIZATION"}
    )
    diagnostics["mcp_configuration"] = Diagnostic(
        status=DiagnosticStatus.OK if configuration_ok else DiagnosticStatus.FAILED,
        detail=(
            f"native HTTP MCP enabled with environment overrides; auth_status={server.get('auth_status', 'unknown')}"
            if configuration_ok and server is not None
            else "PowerContext native MCP entry is missing, disabled, or lacks environment-backed authorization; "
            "reinstall the current plugin"
        ),
    )
    if not configuration_ok or not isinstance(mcp_url, str):
        diagnostics["authorization"] = Diagnostic(
            status=DiagnosticStatus.SKIPPED,
            detail="not checked because native MCP configuration is invalid",
        )
        diagnostics["mcp_tools"] = Diagnostic(
            status=DiagnosticStatus.SKIPPED,
            detail="not checked because native MCP configuration is invalid",
        )
        return diagnostics

    credential_helper = isinstance(transport, dict) and bool(transport.get("http_headers_helper"))
    authorization_diagnostic, native_authorization = _resolve_codex_native_authorization(
        mcp_url, credential_helper=credential_helper
    )
    diagnostics["authorization"] = authorization_diagnostic
    if not authorization_diagnostic.ok:
        diagnostics["mcp_tools"] = Diagnostic(
            status=DiagnosticStatus.SKIPPED,
            detail="not checked because Codex host authorization is invalid",
        )
        return diagnostics

    try:
        native_server = _probe_codex_mcp_status(authorization=native_authorization)
    except SetupError as error:
        diagnostics["mcp_tools"] = Diagnostic(status=DiagnosticStatus.FAILED, detail=str(error))
        return diagnostics
    tools = native_server.get("tools")
    tool_names = set(tools) if isinstance(tools, dict) else set()
    missing = sorted(_CODEX_REQUIRED_MCP_TOOLS - tool_names)
    if native_authorization is None and not credential_helper:
        failure_hint = (
            "; check Server availability and, for an authenticated Server, set "
            "POWERCONTEXT_CODEX_AUTHORIZATION while rerunning `powercontext setup codex`"
        )
    else:
        failure_hint = "; check Server availability and whether the effective host credential is still valid"
    diagnostics["mcp_tools"] = Diagnostic(
        status=DiagnosticStatus.OK if not missing else DiagnosticStatus.FAILED,
        detail=(
            f"Codex native MCP initialized and discovered {len(tool_names)} tools"
            if not missing
            else "Codex native MCP did not discover required tools: " + ", ".join(missing) + failure_hint
        ),
    )
    return diagnostics


def _codex_authorization_checks(
    *,
    stored_state: str,
    stored_authorization: str | None,
    process_state: str,
    process_authorization: str | None,
    desktop_authorization: str | None,
    credential_helper: bool = False,
) -> dict[str, str]:
    if stored_authorization is None:
        setup_managed_state = stored_state
    elif process_authorization is not None:
        setup_managed_state = "matches_current_process" if stored_authorization == process_authorization else "stale"
    elif credential_helper:
        setup_managed_state = "available_to_host"
    elif desktop_authorization is not None:
        setup_managed_state = "matches_desktop_restart" if stored_authorization == desktop_authorization else "stale"
    else:
        setup_managed_state = "configured_but_unavailable_to_host"

    if desktop_authorization is None:
        desktop_restart_state = "not_configured" if sys.platform == "win32" else "not_applicable"
    elif process_authorization is None:
        desktop_restart_state = "configured"
    else:
        desktop_restart_state = (
            "matches_current_process"
            if desktop_authorization == process_authorization
            else "differs_from_current_process"
        )
    return {
        "current_process": process_state,
        "setup_managed": setup_managed_state,
        "desktop_restart": desktop_restart_state,
    }


def _codex_stored_authorization_issue(stored_state: str, setup_managed_state: str) -> str | None:
    if setup_managed_state == "stale":
        return "setup-managed credential is stale"
    if stored_state not in {"configured", "not_configured"}:
        return f"stored credential state is {stored_state}"
    return None


def _codex_process_authorization_detail(stored_issue: str | None, desktop_restart_state: str) -> str:
    detail_parts = ["current process authorization is configured and will be used by the native MCP probe"]
    if stored_issue is not None:
        detail_parts.append(stored_issue)
    if desktop_restart_state == "differs_from_current_process":
        detail_parts.append(
            "Windows user authorization differs from the current process after restarting Codex Desktop"
        )
    elif desktop_restart_state == "not_configured":
        detail_parts.append("Windows user authorization is not configured for a restarted Codex Desktop")
    return "; ".join(detail_parts)


def _codex_desktop_authorization_detail(*, matches_stored: bool, stored_issue: str | None) -> str:
    detail_parts = [
        "current process authorization is not configured; Windows user authorization will be used by the native MCP "
        "probe for the environment expected after restarting Codex Desktop"
    ]
    if matches_stored:
        detail_parts.append("Windows user authorization matches the setup-managed credential")
    elif stored_issue is not None:
        detail_parts.append(stored_issue)
    return "; ".join(detail_parts)


def _resolve_codex_native_authorization(
    mcp_url: str, *, credential_helper: bool = False
) -> tuple[Diagnostic, str | None]:
    """Resolve the redacted Codex host authorization state for one MCP URL."""

    from .authorization import (
        credential_path,
        normalize_authorization,
        read_codex_desktop_authorization,
        read_stored_authorization,
    )

    authorization = read_stored_authorization(
        credential_path("codex"), server_url=mcp_url.rstrip("/").removesuffix("/mcp")
    )
    process_value = os.environ.get("POWERCONTEXT_CODEX_AUTHORIZATION")
    process_authorization: str | None = None
    comparable_process_authorization: str | None = None
    process_state = "not_configured"
    if process_value is not None:
        try:
            normalized_process_authorization = normalize_authorization(process_value)
        except ValueError:
            process_state = "invalid"
        else:
            scheme, separator, _credential = process_value.partition(" ")
            if process_value != process_value.strip() or not separator or scheme.casefold() != "bearer":
                process_state = "invalid"
            else:
                process_authorization = process_value
                comparable_process_authorization = normalized_process_authorization
                process_state = "configured"
    desktop_authorization = read_codex_desktop_authorization()
    expected_authorization = authorization.authorization
    checks = _codex_authorization_checks(
        stored_state=authorization.status,
        stored_authorization=expected_authorization,
        process_state=process_state,
        process_authorization=comparable_process_authorization,
        desktop_authorization=desktop_authorization,
        credential_helper=credential_helper,
    )
    stored_issue = _codex_stored_authorization_issue(authorization.status, checks["setup_managed"])

    if process_authorization is not None:
        return (
            Diagnostic(
                status=DiagnosticStatus.OK,
                detail=_codex_process_authorization_detail(stored_issue, checks["desktop_restart"]),
                checks=checks,
            ),
            process_authorization,
        )

    if process_state == "invalid":
        return (
            Diagnostic(
                status=DiagnosticStatus.FAILED,
                detail=(
                    "current process authorization is invalid; set a complete Bearer credential in "
                    "POWERCONTEXT_CODEX_AUTHORIZATION"
                ),
                checks=checks,
            ),
            None,
        )

    if credential_helper and expected_authorization is not None:
        return (
            Diagnostic(
                status=DiagnosticStatus.OK,
                detail="native MCP credential helper will read the setup-managed credential; no authorization export is required",
                checks=checks,
            ),
            None,
        )

    if desktop_authorization is not None:
        return (
            Diagnostic(
                status=DiagnosticStatus.OK,
                detail=_codex_desktop_authorization_detail(
                    matches_stored=expected_authorization == desktop_authorization,
                    stored_issue=stored_issue,
                ),
                checks=checks,
            ),
            desktop_authorization,
        )

    authorization_ok = authorization.status == "not_configured"
    authorization_detail = (
        "no host authorization is configured; the native probe will verify an unauthenticated connection"
        if authorization_ok
        else (
            "setup-managed credential is not available to the Codex host; upgrade Codex to a version supporting "
            "http_headers_helper and rerun `powercontext setup codex` with the matching PowerContext plugin, "
            "then restart Codex; "
            "for an older installation, set POWERCONTEXT_CODEX_AUTHORIZATION before launching Codex"
            if authorization.status == "configured"
            else f"stored credential state is {authorization.status}; rerun `powercontext setup codex`"
        )
    )
    return (
        Diagnostic(
            status=DiagnosticStatus.OK if authorization_ok else DiagnosticStatus.FAILED,
            detail=authorization_detail,
            checks=checks,
        ),
        None,
    )


def _run_codex_mcp_list() -> list[dict[str, Any]]:
    """Read Codex's resolved native MCP configuration without exposing header values."""

    command = ["codex", "mcp", "list", "--json"]
    try:
        completed = subprocess.run(  # noqa: S603 - arguments are fixed and do not contain credentials.
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise SetupError.command_unavailable(command[:-1], error) from error
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or f"exit code {completed.returncode}"
        raise SetupError.command_failed(command[:-1], detail)
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise SetupError.invalid_command_output(command[:-1], "invalid JSON") from error
    if not isinstance(payload, list) or any(not isinstance(item, dict) for item in payload):
        raise SetupError.invalid_command_output(command[:-1], "an unexpected result")
    return cast(list[dict[str, Any]], payload)


def _probe_codex_mcp_status(*, authorization: str | None = None) -> dict[str, Any]:  # noqa: C901
    """Initialize Codex app-server and return its PowerContext MCP status."""

    executable = which("codex")
    if executable is None:
        raise SetupError.unavailable("Codex CLI")
    environment = os.environ.copy()
    if authorization is None:
        environment.pop("POWERCONTEXT_CODEX_AUTHORIZATION", None)
    else:
        environment["POWERCONTEXT_CODEX_AUTHORIZATION"] = authorization
    command = [executable, "app-server", "--listen", "stdio://"]
    try:
        process = subprocess.Popen(  # noqa: S603 - arguments are fixed and contain no credentials.
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=environment,
        )
    except OSError as error:
        raise SetupError.command_unavailable(command, error) from error
    stdin = process.stdin
    stdout = process.stdout
    if stdin is None or stdout is None:
        process.kill()
        raise SetupError("Codex app-server did not provide stdio for the native MCP probe")

    messages: Queue[dict[str, Any] | None] = Queue()

    def read_messages() -> None:
        try:
            for line in stdout:
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(message, dict):
                    messages.put(message)
        finally:
            messages.put(None)

    reader = Thread(target=read_messages, name="powercontext-codex-app-server", daemon=True)
    reader.start()

    def send(message: dict[str, Any]) -> None:
        try:
            stdin.write(json.dumps(message, ensure_ascii=True, separators=(",", ":")) + "\n")
            stdin.flush()
        except OSError as error:
            raise SetupError("Codex app-server closed during the native MCP probe") from error

    def receive(request_id: int) -> dict[str, Any]:
        deadline = monotonic() + _CODEX_APP_SERVER_TIMEOUT_SECONDS
        while True:
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise SetupError("Codex native MCP probe timed out")
            try:
                message = messages.get(timeout=remaining)
            except Empty as error:
                raise SetupError("Codex native MCP probe timed out") from error
            if message is None:
                raise SetupError("Codex app-server exited before completing the native MCP probe")
            if message.get("id") == request_id:
                return message

    try:
        send({
            "method": "initialize",
            "id": 1,
            "params": {
                "clientInfo": {
                    "name": "powercontext_doctor",
                    "title": "PowerContext Doctor",
                    "version": version("powercontext"),
                }
            },
        })
        initialized = receive(1)
        if "error" in initialized:
            raise SetupError("Codex app-server rejected native MCP probe initialization")
        send({"method": "initialized", "params": {}})
        send({
            "method": "mcpServerStatus/list",
            "id": 2,
            "params": {"limit": 100, "detail": "toolsAndAuthOnly"},
        })
        response = receive(2)
        result = response.get("result")
        data = result.get("data") if isinstance(result, dict) else None
        if not isinstance(data, list):
            raise SetupError("Codex app-server returned an invalid native MCP status response")
        server = next(
            (item for item in data if isinstance(item, dict) and item.get("name") == PLUGIN_NAME),
            None,
        )
        if server is None:
            raise SetupError("Codex native MCP status does not include PowerContext")
        return server
    finally:
        with suppress(OSError):
            stdin.close()
        with suppress(OSError):
            process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        reader.join(timeout=1)


def _normalize_marketplace_source(source: str) -> tuple[str, bool]:
    candidate = Path(source).expanduser()
    is_local = source.startswith((".", "/", "~")) or candidate.exists()
    return (str(candidate.resolve()), True) if is_local else (source, False)


def _run_codex_json(*arguments: str) -> dict[str, Any]:
    command = ["codex", *arguments, "--json"]
    try:
        completed = subprocess.run(  # noqa: S603 - arguments are passed directly to the fixed Codex executable.
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise SetupError.command_unavailable(command[:-1], error) from error
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or f"exit code {completed.returncode}"
        raise SetupError.command_failed(command[:-1], detail)
    try:
        result = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise SetupError.invalid_command_output(command[:-1], "invalid JSON") from error
    if not isinstance(result, dict):
        raise SetupError.invalid_command_output(command[:-1], "an unexpected result")
    return result
