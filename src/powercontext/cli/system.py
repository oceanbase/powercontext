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

"""Installation and diagnostics commands for an installed PowerContext tool."""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
from contextlib import suppress
from dataclasses import dataclass
from enum import StrEnum
from importlib.metadata import version
from pathlib import Path
from queue import Empty, Queue
from shutil import which
from threading import Thread
from time import monotonic
from typing import Annotated, Any, cast
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

import typer
from pydantic import ValidationError

from powercontext.cli.dsh_runtime import DshProfile, resolve_dsh_target
from powercontext.cli.hosts import stdin_is_tty
from powercontext.cli.installation import SetupError, setup
from powercontext.cli.transport import is_remote_http
from powercontext.client.settings import normalize_server_url
from powercontext.client.transport_policy import resolve_client_transport
from powercontext.http import HealthResponse, ReadinessResponse, ReadinessStatus
from powercontext.paths import powercontext_data_dir
from powercontext.transport import canonical_loopback_endpoint, is_loopback_host

HELP_OPTION_NAMES = ("-h", "--help")
DEFAULT_MARKETPLACE_SOURCE = "oceanbase/powercontext"
DEFAULT_MARKETPLACE_REF = "master"
DEFAULT_CLAUDE_CODE_SERVER_URL = "http://127.0.0.1:8000"
DEFAULT_OPENCLAW_SERVER_URL = "http://127.0.0.1:8000"
PLUGIN_NAME = "powercontext"
CLAUDE_MARKETPLACE_NAME = "powercontext"
_GITHUB_REPOSITORY = re.compile(r"^[^/\s]+/[^/\s]+$")
_CODEX_REQUIRED_MCP_TOOLS = frozenset({"remember_memory", "search_memory"})
# Keep the packaged checklist aligned with capability-bearing server-mcp tools in
# integrations/capabilities.toml; CLI contract tests exercise each declared tool.
_CODEX_FULL_MCP_TOOLS = frozenset({
    "acknowledge_handoff",
    "activate_handoff",
    "capture_content_source",
    "change_atomic_memory_lifecycle",
    "capture_working_note",
    "clear_scope_binding",
    "commit_handoff",
    "continue_handoff",
    "prepare_handoff_hint",
    "create_scope",
    "create_work_contract",
    "finalize_handoff",
    "generate_experience",
    "generate_skill",
    "get_artifact",
    "get_candidate",
    "get_candidate_history",
    "get_artifact_revision",
    "get_atomic_memory_state",
    "get_experience",
    "get_handoff_report",
    "get_memory_entry",
    "get_recall_manifest",
    "get_scope",
    "get_skill",
    "get_topic_memory",
    "handoff_current_work",
    "import_external_skill",
    "list_candidates",
    "list_atomic_memories",
    "list_external_skills",
    "list_managed_skills",
    "list_memory_entries",
    "list_scopes",
    "merge_atomic_memories",
    "pc_source_add",
    "pc_source_capabilities",
    "pc_source_instance_set_enabled",
    "pc_source_instances",
    "pc_source_status",
    "pc_source_target_set_enabled",
    "preview_atomic_memory_restoration",
    "propose_experience",
    "propose_skill",
    "publish_artifact",
    "record_task_outcome",
    "remember_memory",
    "replace_artifact",
    "resolve_external_skill",
    "resolve_scope_binding",
    "restore_atomic_memory",
    "scan_external_skills",
    "search_atomic_memory",
    "search_memory",
    "search_topic_memory",
    "set_scope_binding",
})
_CODEX_APP_SERVER_TIMEOUT_SECONDS = 15.0

setup_app = typer.Typer(
    name="setup",
    context_settings={"help_option_names": HELP_OPTION_NAMES},
    help="Install and configure PowerContext integrations.",
    invoke_without_command=True,
)
doctor_app = typer.Typer(
    name="doctor",
    context_settings={"help_option_names": HELP_OPTION_NAMES},
    help="Check an installed PowerContext environment.",
    invoke_without_command=True,
)


class DiagnosticStatus(StrEnum):
    """Outcome of one installation diagnostic."""

    OK = "ok"
    DEGRADED = "degraded"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass(frozen=True, slots=True)
class Diagnostic:
    status: DiagnosticStatus
    detail: str
    checks: dict[str, str] | None = None

    @property
    def ok(self) -> bool:
        """Return whether this check passed."""

        return self.status is DiagnosticStatus.OK

    def as_json(self) -> dict[str, object]:
        """Return the stable external diagnostic representation."""

        result: dict[str, object] = {
            "ok": self.ok,
            "status": self.status.value,
            "detail": self.detail,
        }
        if self.checks is not None:
            result["checks"] = self.checks
        return result


SourceOption = Annotated[str, typer.Option(help="PowerContext Git source or local checkout path.")]
RefOption = Annotated[str, typer.Option(help="Git ref used for a remote source.")]
ServerOption = Annotated[str | None, typer.Option(help="Server URL; resolves environment and saved settings.")]
HttpOption = Annotated[
    bool | None,
    typer.Option("--allow-insecure-http/--no-allow-insecure-http", help="Explicitly allow unencrypted remote HTTP."),
]
JsonOption = Annotated[bool, typer.Option("--json", help="Write the result as JSON.")]
CaptureOption = Annotated[
    bool | None,
    typer.Option(
        "--capture-prompts/--no-capture-prompts",
        help="Claude Code, Kimi Code and ZCode only; omission preserves the capture preference.",
    ),
]
AgentsArgument = Annotated[list[str] | None, typer.Argument(help="Agent names; select interactively when omitted.")]


@setup_app.callback()
def setup_agents(
    context: typer.Context,
    agents: AgentsArgument = None,
    source: SourceOption = DEFAULT_MARKETPLACE_SOURCE,
    ref: RefOption = DEFAULT_MARKETPLACE_REF,
    server_url: ServerOption = None,
    allow_insecure_http: HttpOption = None,
    json_output: JsonOption = False,
    capture_prompts: CaptureOption = None,
    scope_id: Annotated[str | None, typer.Option(help="Existing Scope ID for Kimi Code.")] = None,
    workspace_scope: Annotated[
        bool,
        typer.Option("--workspace-scope", help="Kimi Code: resolve workspace bindings, then the Server default Scope."),
    ] = False,
    boundary_flush: Annotated[
        bool | None, typer.Option("--boundary-flush/--no-boundary-flush", help="ZCode boundary capture preference.")
    ] = None,
    profile: Annotated[
        str | None,
        typer.Option(help="DSH profile or Kimi MCP profile: minimal, recommended, full; omission preserves it."),
    ] = None,
    dsh_command: Annotated[Path | None, typer.Option(help="DSH launcher path.")] = None,
    minimax_data_dir: Annotated[
        Path | None, typer.Option(help="MiniMax data root; plugins/powercontext is added.")
    ] = None,
    minimax_command: Annotated[Path | None, typer.Option(help="MiniMax mcode launcher path.")] = None,
    env_file: Annotated[Path | None, typer.Option(help="Setup environment file; defaults to .env.")] = None,
) -> None:
    """Install, configure, activate and diagnose the selected agent integrations."""
    from powercontext.cli.hosts import (
        diagnostic_result,
        doctor_checks,
        native_options,
        report_agents,
        select_hosts,
        validate_native_options,
    )
    from powercontext.cli.transport import setup_environment_file

    token = setup_environment_file.set(env_file)
    context.call_on_close(lambda: setup_environment_file.reset(token))
    selected = select_hosts(agents, json_output=json_output)
    options = {
        "capture_prompts": capture_prompts,
        "scope_id": scope_id,
        "boundary_flush": boundary_flush,
        "workspace_scope": workspace_scope or None,
        "profile": profile,
        "minimax_data_dir": minimax_data_dir,
        "minimax_command": minimax_command,
    }
    validate_native_options(selected, **options, dsh_command=dsh_command)

    def complete(host):
        native = native_options(host, **options)
        if host == "dsh":
            native["dsh_target"] = resolve_dsh_target(DshProfile(profile) if profile is not None else None, dsh_command)
            if native["dsh_target"].profile == DshProfile.DESKTOP:
                typer.echo("Fully quit DSH Desktop before replacing its plugin.", err=True)
        if host == "kimi-code" and not scope_id and not workspace_scope and not json_output and stdin_is_tty():
            from powercontext.cli.kimi_code import kimi_home

            if not (kimi_home() / "plugins/managed/powercontext/powercontext.json").is_file():
                native["scope_id"] = typer.prompt("Existing Scope ID for Kimi Code")
        result = setup(
            host,
            source,
            ref,
            server_url=server_url,
            allow_insecure_http=allow_insecure_http,
            json_output=json_output,
            **native,
        )
        checks = doctor_checks(host, **native)
        return {"setup": "complete", "result": result, **diagnostic_result(checks)}

    report_agents(selected, complete, json_output=json_output)


@doctor_app.callback()
def doctor(
    agents: AgentsArgument = None,
    server_url: ServerOption = None,
    allow_insecure_http: HttpOption = None,
    json_output: JsonOption = False,
    profile: Annotated[
        DshProfile | None, typer.Option(help="DSH profile; defaults to the saved profile or web.")
    ] = None,
    dsh_command: Annotated[Path | None, typer.Option(help="DSH launcher path.")] = None,
    minimax_data_dir: Annotated[
        Path | None, typer.Option(help="MiniMax data root; defaults to the selected installation.")
    ] = None,
    minimax_command: Annotated[Path | None, typer.Option(help="MiniMax mcode launcher path.")] = None,
    runtime: Annotated[bool, typer.Option(help="Inspect Kimi Hook history.")] = False,
    mcp: Annotated[bool, typer.Option(help="Discover Kimi Server MCP tools without executing operations.")] = False,
    cwd: Annotated[Path | None, typer.Option(help="Kimi workspace for Scope resolution and runtime history.")] = None,
    session_id: Annotated[str | None, typer.Option(help="Exact Kimi session.")] = None,
    runtime_data_dir: Annotated[Path | None, typer.Option(help="Actual ZCode plugin data directory.")] = None,
    prepare: Annotated[bool, typer.Option(help="Probe ZCode readonly context preparation.")] = False,
) -> None:
    """Check selected integrations, or saved integrations and the environment by default."""
    from powercontext.cli.hosts import (
        diagnostic_result,
        doctor_checks,
        report_agents,
        select_hosts,
        validate_native_options,
    )
    from powercontext.cli.installation import installers
    from powercontext.cli.transport import client_config_file

    if session_id is not None and not runtime:
        raise typer.BadParameter("--session-id requires --runtime")  # noqa: TRY003 - CLI input validation.
    explicit_server = server_url is not None or allow_insecure_http is not None
    if agents is None and not explicit_server:
        path = client_config_file()
        try:
            saved = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"version": 1, "hosts": {}}
            configured = saved.get("hosts") if isinstance(saved, dict) else None
            if not isinstance(saved, dict) or saved.get("version") != 1 or not isinstance(configured, dict):
                raise ValueError  # noqa: TRY301 - invalid saved connections.
            agents = [name for name in configured if name in installers()]
        except (OSError, ValueError) as error:
            raise typer.BadParameter("Cannot read saved agent connections") from error  # noqa: TRY003 - CLI input validation.
    options = {
        "runtime": True if runtime else None,
        "mcp": True if mcp else None,
        "cwd": cwd,
        "session_id": session_id,
        "runtime_data_dir": runtime_data_dir,
        "prepare": True if prepare else None,
        "dsh_command": dsh_command,
        "minimax_data_dir": minimax_data_dir,
        "minimax_command": minimax_command,
    }
    selected = select_hosts(agents, json_output=json_output) if agents else ()
    validate_native_options(selected, **options)
    if profile is not None and "dsh" not in selected:
        raise typer.BadParameter("Doctor --profile requires selecting dsh")  # noqa: TRY003
    if not selected:
        checks = run_diagnostics(server_url=server_url, allow_insecure_http=allow_insecure_http)
        _write_diagnostics(checks, json_output=json_output)
        if not _diagnostics_ok(checks):
            raise typer.Exit(code=1)
        return
    if explicit_server:
        raise typer.BadParameter("Agent diagnostics use their saved connections; change them with config agent")  # noqa: TRY003 - CLI input validation.
    report_agents(
        selected,
        lambda host: diagnostic_result(doctor_checks(host, **options, profile=profile if host == "dsh" else None)),
        json_output=json_output,
    )


def _codex_endpoint_updates(marketplace: str, plugin_version: str, server_url: str) -> dict[Path, bytes]:
    """Keep the installed native MCP and Hook endpoint and saved credential source identical."""

    from powercontext.cli.authorization import credential_path

    if any(part in {"", ".", ".."} or "/" in part or "\\" in part for part in (marketplace, plugin_version)):
        raise SetupError("Invalid Codex plugin cache location")  # noqa: TRY003
    codex_home = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))).expanduser()
    path = codex_home / "plugins" / "cache" / marketplace / PLUGIN_NAME / plugin_version / ".mcp.json"
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
        entry = config["mcpServers"][PLUGIN_NAME]
        if not isinstance(entry, dict) or entry.get("type") != "http":
            raise ValueError("Expected an HTTP MCP server")  # noqa: TRY003, TRY301
        entry["url"] = server_url.rstrip("/") + "/mcp/"
        # Codex filters the helper environment, including CODEX_HOME. Pass only the
        # absolute credential path; the helper shares the Hook's URL-bound reader.
        helper = [
            "uv",
            "run",
            "--frozen",
            "--quiet",
            "--project",
            str(path.parent.resolve()),
            "python",
            str((path.parent / "mcp_headers.py").resolve()),
            "--credential-file",
            str(credential_path("codex").absolute()),
        ]
        entry["http_headers_helper"] = (
            subprocess.list2cmdline(helper) if sys.platform == "win32" else shlex.join(helper)
        )
        return {path: (json.dumps(config, indent=2) + "\n").encode()}
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise SetupError(  # noqa: TRY003
            f"Cannot configure the installed Codex MCP endpoint at {path}; rerun setup after checking the plugin cache"
        ) from error


def run_diagnostics(*, server_url: str | None = None, allow_insecure_http: bool | None = None) -> dict[str, Diagnostic]:
    """Collect installed-environment diagnostics without changing state."""

    return {
        "package": Diagnostic(status=DiagnosticStatus.OK, detail=f"powercontext {version('powercontext')}"),
        **server_diagnostics(server_url=server_url, allow_insecure_http=allow_insecure_http),
    }


def server_diagnostics(
    *, server_url: str | None, allow_insecure_http: bool | None, readiness: Diagnostic | None = None
) -> dict[str, Diagnostic]:
    """Check the selected Server and its optional local service registration."""
    service: dict[str, Diagnostic] = {}
    try:
        server_url, allowed = resolve_client_transport(
            "client", server_url=server_url, allow_insecure_http=allow_insecure_http
        )
        server_url = normalize_server_url(server_url, allow_insecure_http=allowed)
    except ValueError as error:
        liveness = Diagnostic(status=DiagnosticStatus.FAILED, detail=str(error))
    else:
        service = _local_service_diagnostics(server_url)
        if is_remote_http(server_url):
            service["transport"] = Diagnostic(
                status=DiagnosticStatus.DEGRADED,
                detail="Insecure HTTP explicitly enabled; credentials and content are unencrypted in transit",
            )
        if readiness is not None:
            return {**service, "server": readiness}
        liveness = _server_liveness_diagnostic(server_url)
    readiness = (
        _server_readiness_diagnostic(server_url)
        if liveness.ok and server_url is not None
        else Diagnostic(
            status=DiagnosticStatus.SKIPPED,
            detail="not checked because Server liveness failed",
        )
    )
    return {
        **service,
        "server_liveness": liveness,
        "server_readiness": readiness,
    }


def _local_service_diagnostics(server_url: str) -> dict[str, Diagnostic]:
    """Correlate a loopback diagnostic target with the optional personal service registration."""

    parsed = urlsplit(server_url)
    if not is_loopback_host(parsed.hostname):
        return {}

    try:
        from powercontext.service.controller import ServiceController
    except ModuleNotFoundError:
        # Personal service inspection is optional for CLI-only installations.
        return {}
    from powercontext.service.model import (
        DefinitionState,
        ManagerState,
        RegistrationState,
        SupportState,
    )

    controller = ServiceController()
    try:
        status = controller.registration_status()
    except Exception as error:  # Native diagnostics must not hide the Server checks that follow.
        return {
            "service_support": Diagnostic(
                status=DiagnosticStatus.DEGRADED,
                detail=f"personal service status is unavailable: {error}",
            )
        }

    diagnostics: dict[str, Diagnostic] = {}
    if status.support is SupportState.UNSUPPORTED:
        diagnostics["service_support"] = Diagnostic(
            status=DiagnosticStatus.OK,
            detail=f"unsupported (optional): {status.detail or 'no verified native adapter'}",
        )
        return diagnostics
    diagnostics["service_support"] = Diagnostic(
        status=DiagnosticStatus.OK,
        detail="native personal service adapter is supported",
    )

    if status.registration is RegistrationState.NOT_INSTALLED:
        diagnostics["service_registration"] = Diagnostic(
            status=DiagnosticStatus.OK,
            detail="not_installed (optional)",
        )
        return diagnostics
    if status.registration is not RegistrationState.INSTALLED:
        diagnostics["service_registration"] = Diagnostic(
            status=DiagnosticStatus.FAILED,
            detail=status.detail or status.registration.value,
        )
        return diagnostics
    if status.endpoint is None or canonical_loopback_endpoint(status.endpoint) != canonical_loopback_endpoint(
        server_url
    ):
        return {}

    try:
        status = controller.status()
    except Exception as error:  # Manager diagnostics must not hide the Server checks that follow.
        diagnostics["service_registration"] = Diagnostic(
            status=DiagnosticStatus.OK,
            detail="installed",
        )
        diagnostics["service_manager"] = Diagnostic(
            status=DiagnosticStatus.DEGRADED,
            detail=f"personal service manager status is unavailable: {error}",
        )
        return diagnostics

    diagnostics["service_registration"] = Diagnostic(
        status=DiagnosticStatus.OK,
        detail="installed",
    )
    diagnostics["service_definition"] = Diagnostic(
        status=(DiagnosticStatus.OK if status.definition is DefinitionState.CURRENT else DiagnosticStatus.FAILED),
        detail=status.definition.value,
    )
    diagnostics["service_manager"] = Diagnostic(
        status=(DiagnosticStatus.OK if status.manager is ManagerState.ACTIVE else DiagnosticStatus.FAILED),
        detail=(
            f"{status.manager.value}; ownership: {status.manager_ownership.value}"
            if status.log_location is None
            else (f"{status.manager.value}; ownership: {status.manager_ownership.value}; logs: {status.log_location}")
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

    from powercontext.cli.authorization import (
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

    if authorization.status == "url_mismatch":
        return (
            Diagnostic(
                status=DiagnosticStatus.OK,
                detail=(
                    "stored credential state is url_mismatch; the credential for the previous endpoint is preserved "
                    "and will not be forwarded; the native probe will verify an unauthenticated connection. "
                    "If the selected Server requires authentication, supply POWERCONTEXT_CODEX_AUTHORIZATION or "
                    "POWERCONTEXT_CLIENT_API_TOKEN while rerunning `powercontext setup codex`"
                ),
                checks=checks,
            ),
            None,
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


def _codex_search_path() -> str:
    """Return the PATH value to search, mirroring which()'s unset-PATH fallback roots."""
    path = os.environ.get("PATH")
    if path is not None:
        return path
    confstr = getattr(os, "confstr", None)
    if confstr is None:
        return os.defpath
    try:
        return confstr("CS_PATH")
    except ValueError:
        # CS_PATH is not a known confstr variable on this platform.
        return os.defpath


def _codex_path_executable() -> str | None:
    """Resolve Codex from PATH directories without the implicit current-directory preference.

    ``shutil.which`` inserts the current directory first on Windows (unconditionally
    before Python 3.12, and unless ``NoDefaultCurrentDirectoryInExePath`` is set from
    3.12 on), which lets a ``codex.cmd`` in the working directory shadow the PATH
    executable. Discovery per #2184 is about PATH, so enumerate PATH entries directly
    while keeping which()'s matching semantics across Python 3.11-3.14: PATHEXT
    candidates on Windows, the bare name with an executable-bit check on POSIX.
    Explicit PATH entries - including ``.`` and empty entries - keep their usual meaning.
    An unset PATH falls back to which()'s search roots (``os.confstr("CS_PATH")``, then
    ``os.defpath``), and an empty PATH value means no search at all, like which().
    """
    name = "codex"
    if sys.platform == "win32":
        extensions = [
            extension.rstrip(".")
            for extension in (os.getenv("PATHEXT") or ".COM;.EXE;.BAT;.CMD").split(os.pathsep)
            if extension
        ]
        candidates = [name + extension for extension in extensions]
        if name.upper().endswith(tuple(extension.upper() for extension in extensions)):
            candidates.insert(0, name)
    else:
        candidates = [name]
    path = _codex_search_path()
    # Parity with which() (bpo-35755): an empty PATH value means no search at all,
    # while empty entries inside a non-empty PATH keep their usual cwd meaning.
    if not path:
        return None
    seen: set[str] = set()
    for directory in path.split(os.pathsep):
        key = os.path.normcase(directory)
        if key in seen:
            continue
        seen.add(key)
        for candidate in candidates:
            resolved = os.path.join(directory, candidate)
            if os.path.isfile(resolved) and (sys.platform == "win32" or os.access(resolved, os.X_OK)):
                return resolved
    return None


def _codex_command(*arguments: str) -> list[str]:
    """Resolve Codex and reject inputs that Windows batch parsing can reinterpret."""
    executable = _codex_path_executable()
    if executable is None:
        raise SetupError("Codex CLI is not installed or is not on PATH.")  # noqa: TRY003
    command = [executable, *arguments]
    # shell=False does not prevent cmd.exe from interpreting a batch launcher's inputs.
    # Windows also launches PATHEXT aliases ending in spaces or dots as batch files.
    if sys.platform == "win32" and Path(executable.rstrip(" .")).suffix.casefold() in {".cmd", ".bat"}:
        if any(character in '&|<>()^%!"\x7f' or ord(character) < 32 for value in command for character in value):
            raise SetupError(  # noqa: TRY003
                "Codex CLI Windows batch launchers cannot safely pass shell metacharacters or ASCII control characters "
                "in their path or arguments. Use a native Codex executable, or a batch-launcher path and arguments "
                "without these characters."
            )
    return command


def _run_codex_mcp_list() -> list[dict[str, Any]]:
    """Read Codex's resolved native MCP configuration without exposing header values."""

    command = _codex_command("mcp", "list", "--json")
    try:
        completed = subprocess.run(  # noqa: S603 - arguments are fixed and do not contain credentials.
            command,
            check=False,
            capture_output=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise SetupError(f"Cannot run {' '.join(command[:-1])}: {error}") from error  # noqa: TRY003
    if completed.returncode != 0:
        detail = (
            completed.stderr.decode("utf-8", errors="replace").strip()
            or completed.stdout.decode("utf-8", errors="replace").strip()
            or f"exit code {completed.returncode}"
        )
        raise SetupError(f"`{' '.join(command[:-1])}` failed: {detail}")  # noqa: TRY003
    try:
        payload = json.loads(completed.stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SetupError(f"`{' '.join(command[:-1])}` returned invalid JSON") from error  # noqa: TRY003
    if not isinstance(payload, list) or any(not isinstance(item, dict) for item in payload):
        raise SetupError(f"`{' '.join(command[:-1])}` returned an unexpected result")  # noqa: TRY003
    return cast(list[dict[str, Any]], payload)


def _probe_codex_mcp_status(*, authorization: str | None = None) -> dict[str, Any]:  # noqa: C901
    """Initialize Codex app-server and return its PowerContext MCP status."""

    command = _codex_command("app-server", "--listen", "stdio://")
    environment = os.environ.copy()
    if authorization is None:
        environment.pop("POWERCONTEXT_CODEX_AUTHORIZATION", None)
    else:
        environment["POWERCONTEXT_CODEX_AUTHORIZATION"] = authorization
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
        raise SetupError(f"Cannot run {' '.join(command)}: {error}") from error  # noqa: TRY003
    stdin = process.stdin
    stdout = process.stdout
    if stdin is None or stdout is None:
        process.kill()
        raise SetupError("Codex app-server did not provide stdio for the native MCP probe")  # noqa: TRY003

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
            raise SetupError("Codex app-server closed during the native MCP probe") from error  # noqa: TRY003

    def receive(request_id: int) -> dict[str, Any]:
        deadline = monotonic() + _CODEX_APP_SERVER_TIMEOUT_SECONDS
        while True:
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise SetupError("Codex native MCP probe timed out")  # noqa: TRY003
            try:
                message = messages.get(timeout=remaining)
            except Empty as error:
                raise SetupError("Codex native MCP probe timed out") from error  # noqa: TRY003
            if message is None:
                raise SetupError("Codex app-server exited before completing the native MCP probe")  # noqa: TRY003
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
            raise SetupError("Codex app-server rejected native MCP probe initialization")  # noqa: TRY003
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
            raise SetupError("Codex app-server returned an invalid native MCP status response")  # noqa: TRY003
        server = next(
            (item for item in data if isinstance(item, dict) and item.get("name") == PLUGIN_NAME),
            None,
        )
        if server is None:
            raise SetupError("Codex native MCP status does not include PowerContext")  # noqa: TRY003
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


def _server_liveness_diagnostic(server_url: str) -> Diagnostic:
    try:
        status_code, payload = _request_json(server_url, "/health/live")
    except OSError:
        return Diagnostic(status=DiagnosticStatus.FAILED, detail=f"cannot reach {server_url}")
    if status_code != 200:
        return Diagnostic(status=DiagnosticStatus.FAILED, detail=f"liveness returned HTTP {status_code}")
    try:
        health = HealthResponse.model_validate(payload)
    except ValidationError:
        return Diagnostic(status=DiagnosticStatus.FAILED, detail="liveness returned an invalid response")
    return Diagnostic(
        status=DiagnosticStatus.OK if health.status == "ok" else DiagnosticStatus.FAILED,
        detail=f"{server_url} status={health.status}",
    )


def _server_readiness_diagnostic(server_url: str) -> Diagnostic:
    try:
        status_code, payload = _request_json(server_url, "/health/ready")
    except OSError:
        return Diagnostic(status=DiagnosticStatus.FAILED, detail=f"cannot reach {server_url}")
    if status_code not in {200, 503}:
        return Diagnostic(status=DiagnosticStatus.FAILED, detail=f"readiness returned HTTP {status_code}")
    try:
        readiness = ReadinessResponse.model_validate(payload)
    except ValidationError:
        return Diagnostic(status=DiagnosticStatus.FAILED, detail="readiness returned an invalid response")
    if status_code == 200 and readiness.status is ReadinessStatus.READY:
        diagnostic_status = DiagnosticStatus.OK
    elif status_code == 200 and readiness.status is ReadinessStatus.DEGRADED:
        diagnostic_status = DiagnosticStatus.DEGRADED
    else:
        diagnostic_status = DiagnosticStatus.FAILED
    return Diagnostic(
        status=diagnostic_status,
        detail=f"{server_url} status={readiness.status.value}",
        checks=readiness.checks,
    )


def _request_json(server_url: str, path: str) -> tuple[int, object]:
    request = Request(  # noqa: S310 - a user-selected diagnostics endpoint is expected.
        f"{server_url.rstrip('/')}{path}",
        headers={"Accept": "application/json", "User-Agent": "powercontext-doctor"},
    )
    try:
        with urlopen(request, timeout=3) as response:  # noqa: S310
            return response.getcode(), _load_json(response)
    except HTTPError as error:
        try:
            return error.code, _load_json(error)
        finally:
            error.close()


def _load_json(response: Any) -> object | None:
    try:
        return json.load(response)
    except (UnicodeError, ValueError):
        return None


def _diagnostics_ok(diagnostics: dict[str, Diagnostic]) -> bool:
    return _diagnostics_status(diagnostics) is DiagnosticStatus.OK


def _diagnostics_status(diagnostics: dict[str, Diagnostic]) -> DiagnosticStatus:
    statuses = {diagnostic.status for diagnostic in diagnostics.values()}
    for status in (DiagnosticStatus.FAILED, DiagnosticStatus.DEGRADED, DiagnosticStatus.SKIPPED):
        if status in statuses:
            return status
    return DiagnosticStatus.OK


def _write_diagnostics(
    diagnostics: dict[str, Diagnostic], *, json_output: bool, summary_status: DiagnosticStatus | None = None
) -> None:
    if json_output:
        status = summary_status if summary_status is not None else _diagnostics_status(diagnostics)
        typer.echo(
            json.dumps(
                {
                    "ok": status is DiagnosticStatus.OK,
                    "status": status.value,
                    "checks": {name: diagnostic.as_json() for name, diagnostic in diagnostics.items()},
                },
                indent=2,
            )
        )
        return
    for name, diagnostic in diagnostics.items():
        typer.echo(f"{name.replace('_', ' ')}: {diagnostic.status.value} - {diagnostic.detail}")
        if diagnostic.checks is not None:
            for check, status in diagnostic.checks.items():
                typer.echo(f"  {check}: {status}")


def _normalize_marketplace_source(source: str) -> tuple[str, bool]:
    candidate = Path(source).expanduser()
    is_local = source.startswith((".", "/", "~")) or candidate.exists()
    return (str(candidate.resolve()), True) if is_local else (source, False)


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


def _claude_config_dir() -> Path:
    configured = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(configured).expanduser() if configured else Path.home() / ".claude"


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


def _require_claude_plugin(value: object, *, scope: str | None = None) -> dict[str, Any]:
    plugin = _claude_plugin(value, scope=scope)
    if plugin is None:
        raise SetupError("Claude Code did not report an installed PowerContext plugin after installation.")  # noqa: TRY003
    return plugin


def _claude_configuration_updates(
    *,
    plugin: dict[str, Any],
    server_url: str,
    capture_prompts: bool | None,
    allow_insecure_http: bool = False,
) -> dict[Path, bytes]:
    """Merge non-sensitive plugin options unsupported by the Claude install CLI."""

    settings_file = _claude_config_dir() / "settings.json"
    try:
        settings = json.loads(settings_file.read_text(encoding="utf-8")) if settings_file.exists() else {}
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        if isinstance(error, OSError):
            raise SetupError(f"Cannot update Claude Code settings at {settings_file}: {error}") from error  # noqa: TRY003
        raise SetupError(  # noqa: TRY003
            f"Claude Code settings at {settings_file} must contain a JSON object with object-valued plugin options."
        ) from error
    if not isinstance(settings, dict):
        raise SetupError(  # noqa: TRY003
            f"Claude Code settings at {settings_file} must contain a JSON object with object-valued plugin options."
        )

    plugin_configs = settings.setdefault("pluginConfigs", {})
    if not isinstance(plugin_configs, dict):
        raise SetupError(  # noqa: TRY003
            f"Claude Code settings at {settings_file} must contain a JSON object with object-valued plugin options."
        )
    plugin_id = f"{PLUGIN_NAME}@{CLAUDE_MARKETPLACE_NAME}"
    plugin_config = plugin_configs.setdefault(plugin_id, {})
    if not isinstance(plugin_config, dict):
        raise SetupError(  # noqa: TRY003
            f"Claude Code settings at {settings_file} must contain a JSON object with object-valued plugin options."
        )
    options = plugin_config.setdefault("options", {})
    if not isinstance(options, dict):
        raise SetupError(  # noqa: TRY003
            f"Claude Code settings at {settings_file} must contain a JSON object with object-valued plugin options."
        )
    options.update({
        "server_url": server_url,
        "allow_insecure_http": allow_insecure_http,
    })

    if capture_prompts is not None:
        options["capture_prompts"] = capture_prompts

    install_path = _claude_plugin_install_path(plugin)
    statusline_command = shlex.join([
        "python3",
        str(install_path / "scripts" / "statusline.py"),
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
    return {settings_file: (json.dumps(settings, indent=2) + "\n").encode()}


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


def _run_codex_json(*arguments: str) -> dict[str, Any]:
    command = _codex_command(*arguments, "--json")
    try:
        completed = subprocess.run(  # noqa: S603 - arguments are passed directly to the fixed Codex executable.
            command,
            check=False,
            capture_output=True,
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise SetupError(f"Cannot run {' '.join(command[:-1])}: {error}") from error  # noqa: TRY003
    if completed.returncode != 0:
        detail = (
            completed.stderr.decode("utf-8", errors="replace").strip()
            or completed.stdout.decode("utf-8", errors="replace").strip()
            or f"exit code {completed.returncode}"
        )
        raise SetupError(f"`{' '.join(command[:-1])}` failed: {detail}")  # noqa: TRY003
    try:
        result = json.loads(completed.stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SetupError(f"`{' '.join(command[:-1])}` returned invalid JSON") from error  # noqa: TRY003
    if not isinstance(result, dict):
        raise SetupError(f"`{' '.join(command[:-1])}` returned an unexpected result")  # noqa: TRY003
    return result


def _installed_codex_plugin() -> dict[str, Any] | None:
    plugins = [
        item
        for item in _run_codex_json("plugin", "list")["installed"]
        if item["name"] == PLUGIN_NAME and item["installed"]
    ]
    if len(plugins) > 1:
        raise SetupError("Expected one installed PowerContext plugin in Codex; inspect codex plugin list")  # noqa: TRY003
    return plugins[0] if plugins else None


def _run_claude(*arguments: str) -> subprocess.CompletedProcess[str]:
    executable = which("claude")
    if executable is None:
        raise SetupError("Claude Code CLI is not installed or is not on PATH.")  # noqa: TRY003
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
        raise SetupError(f"Cannot run {' '.join(command)}: {error}") from error  # noqa: TRY003
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or f"exit code {completed.returncode}"
        raise SetupError(f"`{' '.join(command)}` failed: {detail}")  # noqa: TRY003
    return completed


def _run_claude_json(*arguments: str) -> object:
    command = [*arguments, "--json"]
    completed = _run_claude(*command)
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise SetupError(f"`{' '.join(['claude', *command])}` returned invalid JSON") from error  # noqa: TRY003


def _required_string(value: dict[str, Any], name: str) -> str:
    result = value.get(name)
    if not isinstance(result, str) or not result:
        raise SetupError(f"Integration CLI did not return {name}")  # noqa: TRY003
    return result


__all__ = ["Diagnostic", "DiagnosticStatus", "SetupError", "doctor_app", "run_diagnostics", "setup_app"]


class CodexInstaller:
    def install(self, source):
        if _codex_path_executable() is None:
            raise SetupError("Codex CLI is not installed or is not on PATH.")  # noqa: TRY003
        marketplace_source, local = _normalize_marketplace_source(source.location)
        arguments = ["plugin", "marketplace", "add", marketplace_source]
        if not local:
            arguments.extend(("--ref", source.ref))
        marketplace = _required_string(_run_codex_json(*arguments), "marketplaceName")
        plugin = next(
            (
                item
                for item in _run_codex_json("plugin", "list")["installed"]
                if item["name"] == PLUGIN_NAME and item["marketplaceName"] == marketplace and item["installed"]
            ),
            None,
        )
        if plugin is None or plugin.get("enabled") is not False:
            plugin = _run_codex_json("plugin", "add", f"{PLUGIN_NAME}@{marketplace}")
        return {
            "marketplace": marketplace,
            "plugin": _required_string(plugin, "name"),
            "plugin_version": _required_string(plugin, "version"),
            "data_dir": str(powercontext_data_dir()),
        }

    def configure(self, connection):
        from powercontext.cli.authorization import (
            configure_codex_desktop_authorization,
            credential_path,
            read_stored_authorization,
        )
        from powercontext.cli.transport import configure_connection

        record = _installed_codex_plugin()
        if record is None:
            raise SetupError("PowerContext is not installed in Codex; run powercontext setup codex")  # noqa: TRY003
        marketplace = _required_string(record, "marketplaceName")
        state = configure_connection(
            connection,
            writes=_codex_endpoint_updates(marketplace, _required_string(record, "version"), connection.server_url),
        )
        value = read_stored_authorization(credential_path("codex"), server_url=connection.server_url).authorization
        if value is not None:
            configure_codex_desktop_authorization(value)
        return {"authorization_state": state}

    def verify(self) -> dict[str, Diagnostic]:
        """Collect plugin and native MCP diagnostics for the optional Codex integration."""

        executable = _codex_path_executable()
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
            plugin = _installed_codex_plugin()
        except SetupError as error:
            return {
                "codex": Diagnostic(status=DiagnosticStatus.FAILED, detail=str(error)),
                "plugin": Diagnostic(status=DiagnosticStatus.SKIPPED, detail="plugin list is unavailable"),
            }
        plugin_enabled = plugin is not None and plugin.get("enabled") is True
        detail = (
            f"{plugin.get('pluginId')} enabled={plugin.get('enabled')}"
            if plugin is not None
            else "PowerContext plugin is not installed"
        )
        if plugin is not None and not plugin_enabled:
            detail += (
                f"; setup preserves disabled plugin version {plugin.get('version')}; "
                "use codex plugin add to refresh its source and enable it"
            )
        diagnostics = {
            "codex": Diagnostic(status=DiagnosticStatus.OK, detail=executable),
            "mcp_full_profile": Diagnostic(
                status=DiagnosticStatus.SKIPPED, detail="not checked because native MCP discovery has not succeeded"
            ),
            "plugin": Diagnostic(
                status=DiagnosticStatus.OK if plugin_enabled else DiagnosticStatus.FAILED,
                detail=detail,
            ),
        }
        if not plugin_enabled:
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
        if native_authorization is None and (
            not credential_helper or (authorization_diagnostic.checks or {}).get("setup_managed") == "url_mismatch"
        ):
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
        missing_full = sorted(_CODEX_FULL_MCP_TOOLS - tool_names)
        diagnostics["mcp_full_profile"] = Diagnostic(
            status=DiagnosticStatus.OK if not missing_full else DiagnosticStatus.DEGRADED,
            detail=(
                "Codex native MCP exposes all full-profile tools; model readiness and Hook execution are separate checks"
                if not missing_full
                else "Full-profile tools unavailable: "
                + ", ".join(missing_full)
                + "; upgrade the Server and refresh the plugin, then open a new Codex session"
            ),
        )
        return diagnostics


@dataclass
class ClaudeInstaller:
    capture_prompts: bool | None = None

    def install(self, source):
        if which("claude") is None:
            raise SetupError("Claude Code CLI is not installed or is not on PATH.")  # noqa: TRY003
        location = _normalize_claude_marketplace_source(source.location, ref=source.ref)
        market = _claude_marketplace(_run_claude_json("plugin", "marketplace", "list"), CLAUDE_MARKETPLACE_NAME)
        if market is not None and not _claude_marketplace_matches(market, location):
            raise SetupError(  # noqa: TRY003
                f"Claude Code marketplace `powercontext` uses {_describe_claude_marketplace_source(market)}, but setup requested {location}. Remove it with `claude plugin marketplace remove powercontext`, then rerun setup."
            )
        installed = _claude_plugin(_run_claude_json("plugin", "list"), scope="user")
        if market is None:
            _run_claude("plugin", "marketplace", "add", location, "--scope", "user")
        else:
            _run_claude("plugin", "marketplace", "update", CLAUDE_MARKETPLACE_NAME)
        operation = "install" if installed is None else "update"
        _run_claude("plugin", operation, f"{PLUGIN_NAME}@{CLAUDE_MARKETPLACE_NAME}", "--scope", "user")
        plugin = _require_claude_plugin(_run_claude_json("plugin", "list"), scope="user")
        config_dir = _claude_config_dir()
        return {
            "marketplace": CLAUDE_MARKETPLACE_NAME,
            "plugin": PLUGIN_NAME,
            "plugin_version": _required_string(plugin, "version"),
            "settings_file": str(config_dir / "settings.json"),
            "cache_dir": str(_claude_plugin_install_path(plugin)),
            "data_dir": str(config_dir / "plugins/data" / f"{PLUGIN_NAME}-{CLAUDE_MARKETPLACE_NAME}"),
        }

    def configure(self, connection):
        from powercontext.cli.transport import configure_connection

        plugin = _require_claude_plugin(_run_claude_json("plugin", "list"), scope="user")
        updates = _claude_configuration_updates(
            plugin=plugin,
            server_url=connection.server_url,
            capture_prompts=self.capture_prompts,
            allow_insecure_http=connection.allow_insecure_http,
        )
        return {"authorization_state": configure_connection(connection, writes=updates)}

    def verify(self) -> dict[str, Diagnostic]:
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
        errors = plugin.get("errors") if plugin is not None else None
        plugin_enabled = plugin is not None and plugin.get("enabled") is True and not errors
        return {
            "claude_code": Diagnostic(status=DiagnosticStatus.OK, detail=executable),
            "plugin": Diagnostic(
                status=DiagnosticStatus.OK if plugin_enabled else DiagnosticStatus.FAILED,
                detail=(
                    f"{plugin.get('id')} enabled={plugin.get('enabled')}" + (f"; errors: {errors}" if errors else "")
                    if plugin is not None
                    else "PowerContext plugin is not installed"
                ),
            ),
        }
