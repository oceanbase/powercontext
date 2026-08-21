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
import subprocess
from contextlib import suppress
from dataclasses import asdict, dataclass
from enum import StrEnum
from importlib.metadata import version
from pathlib import Path
from shutil import which
from typing import Annotated, Any, cast
from urllib.error import HTTPError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen

import typer
from pydantic import ValidationError

from powercontext.http import HealthResponse, ReadinessResponse, ReadinessStatus
from powercontext.paths import powercontext_data_dir

HELP_OPTION_NAMES = ("-h", "--help")
DEFAULT_MARKETPLACE_SOURCE = "oceanbase/powercontext"
DEFAULT_MARKETPLACE_REF = "master"
PLUGIN_NAME = "powercontext"
CLAUDE_MARKETPLACE_NAME = "powercontext"
_GITHUB_REPOSITORY = re.compile(r"^[^/\s]+/[^/\s]+$")
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})

setup_app = typer.Typer(
    name="setup",
    context_settings={"help_option_names": HELP_OPTION_NAMES},
    help="Install and configure PowerContext integrations.",
    no_args_is_help=True,
)
doctor_app = typer.Typer(
    name="doctor",
    context_settings={"help_option_names": HELP_OPTION_NAMES},
    help="Check an installed PowerContext environment.",
    invoke_without_command=True,
)


class SetupError(RuntimeError):
    """Report a failed external setup command."""

    @classmethod
    def codex_unavailable(cls) -> SetupError:
        return cls("Codex CLI is not installed or is not on PATH.")

    @classmethod
    def claude_unavailable(cls) -> SetupError:
        return cls("Claude Code CLI is not installed or is not on PATH.")

    @classmethod
    def dsh_unavailable(cls) -> SetupError:
        return cls("DeepSeek Harness CLI is not installed or is not on PATH.")

    @classmethod
    def pi_unavailable(cls) -> SetupError:
        return cls("Pi CLI is not installed or is not on PATH.")

    @classmethod
    def hermes_unavailable(cls) -> SetupError:
        return cls("Hermes CLI is not installed or is not on PATH.")

    @classmethod
    def missing_dsh_plugin(cls, path: Path) -> SetupError:
        return cls(f"PowerContext DSH plugin was not found under {path}.")

    @classmethod
    def unbuilt_dsh_plugin(cls, path: Path) -> SetupError:
        return cls(f"PowerContext DSH plugin at {path} is missing lib/index.js. Build the plugin before setup.")

    @classmethod
    def missing_pi_package(cls, path: Path) -> SetupError:
        return cls(f"PowerContext Pi package was not found under {path}.")

    @classmethod
    def incomplete_pi_package(cls, path: Path) -> SetupError:
        return cls(f"PowerContext Pi package at {path} is missing its extension or project-context skill.")

    @classmethod
    def invalid_dsh_ref(cls, ref: str) -> SetupError:
        return cls(f"invalid DeepSeek Harness ref: {ref}")

    @classmethod
    def invalid_dsh_source(cls) -> SetupError:
        return cls("invalid DeepSeek Harness source; use a local path or an HTTPS/SSH GitHub repository")

    @classmethod
    def invalid_pi_ref(cls, ref: str) -> SetupError:
        return cls(f"invalid Pi ref: {ref}")

    @classmethod
    def invalid_pi_source(cls) -> SetupError:
        return cls("invalid Pi source; use a local path or an HTTPS/SSH GitHub repository")

    @classmethod
    def git_clone_failed(cls) -> SetupError:
        return cls("failed to clone the GitHub source")

    @classmethod
    def invalid_hermes_ref(cls, ref: str) -> SetupError:
        return cls(f"invalid Hermes ref: {ref}")

    @classmethod
    def invalid_hermes_source(cls, source: str) -> SetupError:
        return cls(f"invalid Hermes source: {source}")

    @classmethod
    def missing_hermes_plugin(cls, path: Path) -> SetupError:
        return cls(f"PowerContext Hermes plugin was not found under {path}.")

    @classmethod
    def hermes_plugin_write(cls, path: Path, error: OSError) -> SetupError:
        return cls(f"Cannot install PowerContext Hermes plugin at {path}: {error}")

    @classmethod
    def unsupported_hermes_version(cls, actual: str, minimum: str) -> SetupError:
        return cls(f"Hermes Agent v{actual} is unsupported; PowerContext requires Hermes Agent v{minimum} or newer.")

    @classmethod
    def data_directory(cls, path: Path, error: OSError) -> SetupError:
        return cls(f"Cannot create PowerContext data directory {path}: {error}")

    @classmethod
    def command_unavailable(cls, command: list[str], error: BaseException) -> SetupError:
        return cls(f"Cannot run {' '.join(command)}: {error}")

    @classmethod
    def command_failed(cls, command: list[str], detail: str) -> SetupError:
        return cls(f"`{' '.join(command)}` failed: {detail}")

    @classmethod
    def invalid_command_output(cls, command: list[str], detail: str) -> SetupError:
        return cls(f"`{' '.join(command)}` returned {detail}")

    @classmethod
    def missing_result(cls, name: str) -> SetupError:
        return cls(f"Integration CLI did not return {name}")

    @classmethod
    def claude_plugin_not_enabled(cls) -> SetupError:
        return cls("Claude Code did not report an enabled PowerContext plugin after installation.")

    @classmethod
    def claude_marketplace_source_mismatch(cls, requested: str, existing: str) -> SetupError:
        return cls(
            f"Claude Code marketplace `{CLAUDE_MARKETPLACE_NAME}` uses {existing}, "
            f"but setup requested {requested}. Remove it with "
            f"`claude plugin marketplace remove {CLAUDE_MARKETPLACE_NAME}`, then rerun setup."
        )

    @classmethod
    def invalid_claude_settings(cls, path: Path) -> SetupError:
        return cls(f"Claude Code settings at {path} must contain a JSON object with object-valued plugin options.")

    @classmethod
    def claude_settings_write(cls, path: Path, error: OSError) -> SetupError:
        return cls(f"Cannot update Claude Code settings at {path}: {error}")

    @classmethod
    def claude_server_url_credentials(cls) -> SetupError:
        return cls("PowerContext Server URL must not contain credentials.")

    @classmethod
    def claude_server_url_scheme(cls) -> SetupError:
        return cls("PowerContext Server URL must use HTTP or HTTPS.")

    @classmethod
    def claude_server_url_suffix(cls) -> SetupError:
        return cls("PowerContext Server URL must not contain a query or fragment.")

    @classmethod
    def claude_server_url_transport(cls) -> SetupError:
        return cls("Unencrypted PowerContext Server URLs must be loopback addresses.")


@dataclass(frozen=True, slots=True)
class CodexSetupResult:
    marketplace: str
    plugin: str
    plugin_version: str
    data_dir: str


@dataclass(frozen=True, slots=True)
class ClaudeCodeSetupResult:
    marketplace: str
    plugin: str
    plugin_version: str
    settings_file: str
    cache_dir: str
    data_dir: str


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


@setup_app.command("codex")
def setup_codex(
    source: Annotated[
        str,
        typer.Option(help="Codex marketplace Git source or local path."),
    ] = DEFAULT_MARKETPLACE_SOURCE,
    ref: Annotated[
        str,
        typer.Option(help="Git ref used for a remote marketplace source."),
    ] = DEFAULT_MARKETPLACE_REF,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Write the result as JSON."),
    ] = False,
) -> None:
    """Install the PowerContext Codex plugin and prepare local storage."""

    try:
        result = install_codex_plugin(source=source, ref=ref)
    except SetupError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=1) from error

    diagnostics = run_codex_diagnostics()
    if not _diagnostics_ok(diagnostics):
        _write_diagnostics(diagnostics, json_output=json_output)
        raise typer.Exit(code=1)

    if json_output:
        typer.echo(json.dumps(asdict(result), indent=2))
        return
    typer.echo("PowerContext Codex setup complete.")
    typer.echo(f"Plugin: {result.plugin}@{result.marketplace} ({result.plugin_version})")
    typer.echo(f"Data directory: {result.data_dir}")
    typer.echo("Next: run `powercontext server run`, start a new Codex session, then review `/hooks`.")


@setup_app.command("claude-code")
def setup_claude_code(
    source: Annotated[
        str,
        typer.Option(help="Claude Code marketplace Git source or local path."),
    ] = DEFAULT_MARKETPLACE_SOURCE,
    ref: Annotated[
        str,
        typer.Option(help="Git ref used for a remote marketplace source."),
    ] = DEFAULT_MARKETPLACE_REF,
    server_url: Annotated[
        str,
        typer.Option(help="PowerContext Server base URL configured for the plugin."),
    ] = "http://127.0.0.1:8000",
    capture_prompts: Annotated[
        bool,
        typer.Option(help="Capture Claude Code user prompts as ordinary Source evidence."),
    ] = True,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Write the result as JSON."),
    ] = False,
) -> None:
    """Install the PowerContext Claude Code plugin."""

    plan = _claude_setup_plan()
    _write_claude_setup_plan(plan)
    try:
        result = install_claude_code_plugin(
            source=source,
            ref=ref,
            server_url=server_url,
            capture_prompts=capture_prompts,
        )
    except SetupError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=1) from error

    if json_output:
        typer.echo(json.dumps(asdict(result), indent=2))
        return
    typer.echo("PowerContext Claude Code setup complete.")
    typer.echo(f"Plugin: {result.plugin}@{result.marketplace} ({result.plugin_version})")
    typer.echo(f"Settings: {result.settings_file}")
    typer.echo("Next: run `powercontext server run`, start a new Claude Code session, then review `/hooks` and `/mcp`.")


@setup_app.command("dsh")
def setup_dsh(
    source: Annotated[
        str,
        typer.Option(help="PowerContext Git source or local checkout path."),
    ] = DEFAULT_MARKETPLACE_SOURCE,
    ref: Annotated[
        str,
        typer.Option(help="Git ref used for a remote source."),
    ] = DEFAULT_MARKETPLACE_REF,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Write the result as JSON."),
    ] = False,
) -> None:
    """Install the PowerContext DeepSeek Harness plugin and prepare local storage."""

    from powercontext.cli.dsh import install_dsh_plugin, run_dsh_diagnostics

    try:
        result = install_dsh_plugin(source=source, ref=ref)
    except SetupError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=1) from error

    diagnostics = run_dsh_diagnostics()
    if not _diagnostics_ok(diagnostics):
        _write_diagnostics(diagnostics, json_output=json_output)
        raise typer.Exit(code=1)

    if json_output:
        typer.echo(json.dumps(asdict(result), indent=2))
        return
    typer.echo("PowerContext DeepSeek Harness setup complete.")
    typer.echo(f"Plugin: {result.plugin} ({result.plugin_path})")
    typer.echo(f"Data directory: {result.data_dir}")
    typer.echo("Next: run `powercontext server run`, then start `dsh web`.")


@setup_app.command("pi")
def setup_pi(
    source: Annotated[
        str,
        typer.Option(help="PowerContext Git source or local checkout path."),
    ] = DEFAULT_MARKETPLACE_SOURCE,
    ref: Annotated[
        str,
        typer.Option(help="Git ref used for a remote source."),
    ] = DEFAULT_MARKETPLACE_REF,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Write the result as JSON."),
    ] = False,
) -> None:
    """Install the PowerContext Pi package and prepare local storage."""

    from powercontext.cli.pi import install_pi_plugin, run_pi_diagnostics

    try:
        result = install_pi_plugin(source=source, ref=ref)
    except SetupError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=1) from error

    diagnostics = run_pi_diagnostics()
    if not _diagnostics_ok(diagnostics):
        _write_diagnostics(diagnostics, json_output=json_output)
        raise typer.Exit(code=1)

    if json_output:
        typer.echo(json.dumps(asdict(result), indent=2))
        return
    typer.echo("PowerContext Pi setup complete.")
    typer.echo(f"Package: {result.package} ({result.package_path})")
    typer.echo(f"Data directory: {result.data_dir}")
    typer.echo("Next: run `powercontext server run`, then start a new Pi session.")


@setup_app.command("hermes")
def setup_hermes(
    source: Annotated[
        str,
        typer.Option(help="PowerContext Git source or local checkout path."),
    ] = DEFAULT_MARKETPLACE_SOURCE,
    ref: Annotated[
        str,
        typer.Option(help="Git ref used for a remote source."),
    ] = DEFAULT_MARKETPLACE_REF,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Write the result as JSON."),
    ] = False,
) -> None:
    """Install the PowerContext Hermes memory provider."""

    from powercontext.cli.hermes import install_hermes_plugin, run_hermes_diagnostics

    try:
        result = install_hermes_plugin(source=source, ref=ref)
    except SetupError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=1) from error

    diagnostics = run_hermes_diagnostics()
    if not _diagnostics_ok(diagnostics):
        _write_diagnostics(diagnostics, json_output=json_output)
        raise typer.Exit(code=1)

    if json_output:
        typer.echo(json.dumps(asdict(result), indent=2))
        return
    typer.echo("PowerContext Hermes setup complete.")
    typer.echo(f"Plugin: {result.plugin} ({result.plugin_path})")
    typer.echo(f"Hermes home: {result.hermes_home}")
    typer.echo(f"Data directory: {result.data_dir}")
    typer.echo("Next: run `hermes memory setup`, select PowerContext, then start Hermes.")


@doctor_app.callback()
def doctor(
    context: typer.Context,
    server_url: Annotated[
        str,
        typer.Option(help="PowerContext Server base URL."),
    ] = "http://127.0.0.1:8000",
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Write the result as JSON."),
    ] = False,
) -> None:
    """Check the installed package and configured Server."""

    if context.invoked_subcommand is not None:
        return
    diagnostics = run_diagnostics(server_url=server_url)
    _write_diagnostics(diagnostics, json_output=json_output)
    if not _diagnostics_ok(diagnostics):
        raise typer.Exit(code=1)


@doctor_app.command("codex")
def doctor_codex(
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Write the result as JSON."),
    ] = False,
) -> None:
    """Check the optional Codex CLI and PowerContext plugin."""

    diagnostics = run_codex_diagnostics()
    _write_diagnostics(diagnostics, json_output=json_output)
    if not _diagnostics_ok(diagnostics):
        raise typer.Exit(code=1)


@doctor_app.command("claude-code")
def doctor_claude_code(
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Write the result as JSON."),
    ] = False,
) -> None:
    """Check the optional Claude Code CLI and PowerContext plugin."""

    diagnostics = run_claude_code_diagnostics()
    _write_diagnostics(diagnostics, json_output=json_output)
    if not _diagnostics_ok(diagnostics):
        raise typer.Exit(code=1)


@doctor_app.command("dsh")
def doctor_dsh(
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Write the result as JSON."),
    ] = False,
) -> None:
    """Check the optional DeepSeek Harness CLI and PowerContext plugin."""

    from powercontext.cli.dsh import run_dsh_diagnostics

    diagnostics = run_dsh_diagnostics()
    _write_diagnostics(diagnostics, json_output=json_output)
    if not _diagnostics_ok(diagnostics):
        raise typer.Exit(code=1)


@doctor_app.command("pi")
def doctor_pi(
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Write the result as JSON."),
    ] = False,
) -> None:
    """Check the optional Pi CLI and PowerContext package."""

    from powercontext.cli.pi import run_pi_diagnostics

    diagnostics = run_pi_diagnostics()

    _write_diagnostics(diagnostics, json_output=json_output)
    if not _diagnostics_ok(diagnostics):
        raise typer.Exit(code=1)


@doctor_app.command("hermes")
def doctor_hermes(
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Write the result as JSON."),
    ] = False,
) -> None:
    """Check the optional Hermes CLI and PowerContext memory provider."""

    from powercontext.cli.hermes import run_hermes_diagnostics

    diagnostics = run_hermes_diagnostics()
    _write_diagnostics(diagnostics, json_output=json_output)
    if not _diagnostics_ok(diagnostics):
        raise typer.Exit(code=1)


def install_codex_plugin(*, source: str, ref: str) -> CodexSetupResult:
    """Install the plugin from one local or Git marketplace source."""

    if which("codex") is None:
        raise SetupError.codex_unavailable()

    data_dir = powercontext_data_dir()
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise SetupError.data_directory(data_dir, error) from error

    marketplace_source, is_local = _normalize_marketplace_source(source)
    marketplace_arguments = ["plugin", "marketplace", "add", marketplace_source]
    if not is_local:
        marketplace_arguments.extend(("--ref", ref))
    marketplace = _run_codex_json(*marketplace_arguments)
    marketplace_name = _required_string(marketplace, "marketplaceName")

    plugin = _run_codex_json("plugin", "add", f"{PLUGIN_NAME}@{marketplace_name}")
    return CodexSetupResult(
        marketplace=marketplace_name,
        plugin=_required_string(plugin, "name"),
        plugin_version=_required_string(plugin, "version"),
        data_dir=str(data_dir),
    )


def install_claude_code_plugin(
    *,
    source: str,
    ref: str,
    server_url: str,
    capture_prompts: bool,
) -> ClaudeCodeSetupResult:
    """Install and verify the plugin from one local or Git marketplace source."""

    if which("claude") is None:
        raise SetupError.claude_unavailable()
    server_url = _normalize_claude_server_url(server_url)

    marketplace_source = _normalize_claude_marketplace_source(source, ref=ref)
    marketplaces = _run_claude_json("plugin", "marketplace", "list")
    marketplace = _claude_marketplace(marketplaces, CLAUDE_MARKETPLACE_NAME)
    if marketplace is not None and not _claude_marketplace_matches(marketplace, marketplace_source):
        raise SetupError.claude_marketplace_source_mismatch(
            marketplace_source,
            _describe_claude_marketplace_source(marketplace),
        )
    marketplace_existed = marketplace is not None

    plugins = _run_claude_json("plugin", "list")
    previous_plugin = _claude_plugin(plugins)
    plugin_existed = previous_plugin is not None
    settings_snapshot = _snapshot_claude_settings()
    marketplace_added = False
    plugin_added = False
    try:
        if not marketplace_existed:
            _run_claude("plugin", "marketplace", "add", marketplace_source, "--scope", "user")
            marketplace_added = True
        _run_claude(
            "plugin",
            "install",
            f"{PLUGIN_NAME}@{CLAUDE_MARKETPLACE_NAME}",
            "--scope",
            "user",
        )
        plugin_added = not plugin_existed
        installed = _run_claude_json("plugin", "list")
        plugin = _require_enabled_claude_plugin(installed)
        _configure_claude_plugin(server_url=server_url, capture_prompts=capture_prompts)
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
    return ClaudeCodeSetupResult(
        marketplace=CLAUDE_MARKETPLACE_NAME,
        plugin=PLUGIN_NAME,
        plugin_version=_required_string(plugin, "version"),
        settings_file=plan["settings_file"],
        cache_dir=plan["cache_dir"],
        data_dir=plan["data_dir"],
    )


def run_diagnostics(*, server_url: str) -> dict[str, Diagnostic]:
    """Collect installed-environment diagnostics without changing state."""

    package = Diagnostic(status=DiagnosticStatus.OK, detail=f"powercontext {version('powercontext')}")
    liveness = _server_liveness_diagnostic(server_url)
    readiness = (
        _server_readiness_diagnostic(server_url)
        if liveness.ok
        else Diagnostic(
            status=DiagnosticStatus.SKIPPED,
            detail="not checked because Server liveness failed",
        )
    )
    return {
        "package": package,
        "server_liveness": liveness,
        "server_readiness": readiness,
    }


def run_codex_diagnostics() -> dict[str, Diagnostic]:
    """Collect diagnostics for the optional Codex integration."""

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
    return {
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


def _server_liveness_diagnostic(server_url: str) -> Diagnostic:
    error = _server_url_error(server_url)
    if error is not None:
        return Diagnostic(status=DiagnosticStatus.FAILED, detail=error)
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


def _server_url_error(server_url: str) -> str | None:
    parsed = urlsplit(server_url)
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        return "Server URL must be an HTTP base URL without credentials or query data"
    return None


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


def _write_diagnostics(diagnostics: dict[str, Diagnostic], *, json_output: bool) -> None:
    if json_output:
        status = _diagnostics_status(diagnostics)
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


def _normalize_claude_server_url(value: str) -> str:
    normalized = value.strip().rstrip("/")
    parsed = urlsplit(normalized)
    if parsed.username is not None or parsed.password is not None:
        raise SetupError.claude_server_url_credentials()
    if parsed.hostname is None or parsed.scheme not in {"http", "https"}:
        raise SetupError.claude_server_url_scheme()
    if parsed.query or parsed.fragment:
        raise SetupError.claude_server_url_suffix()
    if parsed.scheme == "http" and parsed.hostname.lower() not in _LOOPBACK_HOSTS:
        raise SetupError.claude_server_url_transport()
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


def _claude_plugin(value: object) -> dict[str, Any] | None:
    if not isinstance(value, list):
        return None
    for item in value:
        if isinstance(item, dict) and item.get("id") == f"{PLUGIN_NAME}@{CLAUDE_MARKETPLACE_NAME}":
            return cast(dict[str, Any], item)
    return None


def _require_enabled_claude_plugin(value: object) -> dict[str, Any]:
    plugin = _claude_plugin(value)
    if plugin is None or plugin.get("enabled") is not True:
        raise SetupError.claude_plugin_not_enabled()
    return plugin


def _snapshot_claude_settings() -> bytes | None:
    settings_file = _claude_config_dir() / "settings.json"
    try:
        return settings_file.read_bytes()
    except FileNotFoundError:
        return None


def _configure_claude_plugin(*, server_url: str, capture_prompts: bool) -> None:
    """Merge non-sensitive plugin options unsupported by the Claude install CLI."""

    settings_file = _claude_config_dir() / "settings.json"
    try:
        settings = json.loads(settings_file.read_text(encoding="utf-8")) if settings_file.exists() else {}
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        if isinstance(error, OSError):
            raise SetupError.claude_settings_write(settings_file, error) from error
        raise SetupError.invalid_claude_settings(settings_file) from error
    if not isinstance(settings, dict):
        raise SetupError.invalid_claude_settings(settings_file)

    plugin_configs = settings.setdefault("pluginConfigs", {})
    if not isinstance(plugin_configs, dict):
        raise SetupError.invalid_claude_settings(settings_file)
    plugin_id = f"{PLUGIN_NAME}@{CLAUDE_MARKETPLACE_NAME}"
    plugin_config = plugin_configs.setdefault(plugin_id, {})
    if not isinstance(plugin_config, dict):
        raise SetupError.invalid_claude_settings(settings_file)
    options = plugin_config.setdefault("options", {})
    if not isinstance(options, dict):
        raise SetupError.invalid_claude_settings(settings_file)
    options.update({
        "server_url": server_url,
        "capture_prompts": capture_prompts,
    })
    try:
        _write_bytes_atomically(settings_file, (json.dumps(settings, indent=2) + "\n").encode())
    except OSError as error:
        raise SetupError.claude_settings_write(settings_file, error) from error


def _restore_claude_settings(snapshot: bytes | None) -> None:
    settings_file = _claude_config_dir() / "settings.json"
    if snapshot is None:
        settings_file.unlink(missing_ok=True)
        return
    _write_bytes_atomically(settings_file, snapshot)


def _write_bytes_atomically(path: Path, content: bytes) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    descriptor: int | None = None
    try:
        descriptor = os.open(temporary_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.write(descriptor, content)
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        os.replace(temporary_path, path)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        with suppress(FileNotFoundError):
            temporary_path.unlink()


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


def _run_claude(*arguments: str) -> subprocess.CompletedProcess[str]:
    executable = which("claude")
    if executable is None:
        raise SetupError.claude_unavailable()
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


def _required_string(value: dict[str, Any], name: str) -> str:
    result = value.get(name)
    if not isinstance(result, str) or not result:
        raise SetupError.missing_result(name)
    return result


__all__ = [
    "ClaudeCodeSetupResult",
    "CodexSetupResult",
    "Diagnostic",
    "DiagnosticStatus",
    "SetupError",
    "doctor_app",
    "install_claude_code_plugin",
    "install_codex_plugin",
    "run_claude_code_diagnostics",
    "run_codex_diagnostics",
    "run_diagnostics",
    "setup_app",
]
