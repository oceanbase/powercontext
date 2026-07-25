"""Installation and diagnostics commands for an installed PowerContext tool."""

from __future__ import annotations

import json
import subprocess
from dataclasses import asdict, dataclass
from importlib.metadata import version
from pathlib import Path
from shutil import which
from typing import Annotated, Any
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

import typer

from powercontext.paths import powercontext_data_dir

HELP_OPTION_NAMES = ("-h", "--help")
DEFAULT_MARKETPLACE_SOURCE = "oceanbase/powercontext"
DEFAULT_MARKETPLACE_REF = "main"
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


@dataclass(frozen=True, slots=True)
class Diagnostic:
    ok: bool
    detail: str


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

    if json_output:
        typer.echo(json.dumps(asdict(result), indent=2))
        return
    typer.echo("PowerContext Codex setup complete.")
    typer.echo(f"Plugin: {result.plugin}@{result.marketplace} ({result.plugin_version})")
    typer.echo(f"Data directory: {result.data_dir}")
    typer.echo("Next: run `powercontext server run`, start a new Codex session, then review `/hooks`.")


@doctor_app.callback()
def doctor(
    server_url: Annotated[
        str,
        typer.Option(help="PowerContext Server base URL."),
    ] = "http://127.0.0.1:8000",
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Write the result as JSON."),
    ] = False,
) -> None:
    """Check the package, Codex plugin, Server, and configured database."""

    diagnostics = run_diagnostics(server_url=server_url)
    is_healthy = all(diagnostic.ok for diagnostic in diagnostics.values())
    if json_output:
        typer.echo(
            json.dumps(
                {
                    "ok": is_healthy,
                    "checks": {name: asdict(diagnostic) for name, diagnostic in diagnostics.items()},
                },
                indent=2,
            )
        )
    else:
        for name, diagnostic in diagnostics.items():
            status = "ok" if diagnostic.ok else "failed"
            typer.echo(f"{name}: {status} - {diagnostic.detail}")
    if not is_healthy:
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

    package = Diagnostic(ok=True, detail=f"powercontext {version('powercontext')}")
    codex, plugin = _codex_diagnostics()
    server = _server_diagnostic(server_url)
    database = _database_diagnostic()
    return {
        "package": package,
        "codex": codex,
        "plugin": plugin,
        "server": server,
        "database": database,
    }


def _codex_diagnostics() -> tuple[Diagnostic, Diagnostic]:
    executable = which("codex")
    if executable is None:
        missing = Diagnostic(ok=False, detail="Codex CLI is not installed or is not on PATH")
        return missing, Diagnostic(ok=False, detail="plugin cannot be inspected without Codex CLI")
    try:
        result = _run_codex_json("plugin", "list")
    except SetupError as error:
        return (
            Diagnostic(ok=False, detail=str(error)),
            Diagnostic(ok=False, detail="plugin list is unavailable"),
        )
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
    return (
        Diagnostic(ok=True, detail=executable),
        Diagnostic(
            ok=plugin is not None,
            detail=(
                f"{plugin.get('pluginId')} enabled={plugin.get('enabled')}"
                if plugin is not None
                else "PowerContext plugin is not installed"
            ),
        ),
    )


def _server_diagnostic(server_url: str) -> Diagnostic:
    parsed = urlsplit(server_url)
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        return Diagnostic(ok=False, detail="Server URL must be an HTTP base URL without credentials or query data")
    request = Request(  # noqa: S310 - a user-selected diagnostics endpoint is expected.
        f"{server_url.rstrip('/')}/health/ready",
        headers={"Accept": "application/json", "User-Agent": "powercontext-doctor"},
    )
    try:
        with urlopen(request, timeout=3) as response:  # noqa: S310
            payload = json.load(response)
    except HTTPError as error:
        return Diagnostic(ok=False, detail=f"readiness returned HTTP {error.code}")
    except (OSError, ValueError) as error:
        return Diagnostic(ok=False, detail=f"cannot reach {server_url}: {error}")
    status = payload.get("status") if isinstance(payload, dict) else None
    return Diagnostic(ok=status == "ready", detail=f"{server_url} status={status}")


def _database_diagnostic() -> Diagnostic:
    try:
        from sqlalchemy.engine import make_url

        from powercontext.server.settings import ServerSettings

        database = ServerSettings().database
    except (ImportError, ValueError) as error:
        return Diagnostic(ok=False, detail=f"cannot load Server settings: {error}")
    if database.kind != "sqlite":
        return Diagnostic(ok=True, detail=f"configured backend={database.kind}")
    path = Path(make_url(database.url).database or "")
    return Diagnostic(ok=path.is_file(), detail=str(path))


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
    "SetupError",
    "doctor_app",
    "install_codex_plugin",
    "run_diagnostics",
    "setup_app",
]
