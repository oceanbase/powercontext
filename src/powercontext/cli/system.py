"""Installation and diagnostics commands for an installed PowerContext tool."""

from __future__ import annotations

import json
import subprocess
from dataclasses import asdict, dataclass
from enum import StrEnum
from importlib.metadata import version
from pathlib import Path
from shutil import which
from typing import Annotated, Any
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

import typer
from pydantic import ValidationError

from powercontext.http import HealthResponse, ReadinessResponse, ReadinessStatus
from powercontext.paths import powercontext_data_dir

HELP_OPTION_NAMES = ("-h", "--help")
DEFAULT_MARKETPLACE_SOURCE = "oceanbase/powercontext"
DEFAULT_MARKETPLACE_REF = "master"
PLUGIN_NAME = "powercontext"

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
        return cls(f"Codex did not return {name}")


@dataclass(frozen=True, slots=True)
class CodexSetupResult:
    marketplace: str
    plugin: str
    plugin_version: str
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


def _required_string(value: dict[str, Any], name: str) -> str:
    result = value.get(name)
    if not isinstance(result, str) or not result:
        raise SetupError.missing_result(name)
    return result


__all__ = [
    "CodexSetupResult",
    "Diagnostic",
    "DiagnosticStatus",
    "SetupError",
    "doctor_app",
    "install_codex_plugin",
    "run_codex_diagnostics",
    "run_diagnostics",
    "setup_app",
]
