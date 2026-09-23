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

from pathlib import Path
from typing import Annotated

import typer

from powercontext.cli.system import (
    Diagnostic,
    DiagnosticStatus,
    SetupError,
    _diagnostics_ok,
    _write_diagnostics,
    doctor,
)

from .hosts import HOST_ADAPTERS, run_host_setup

HELP_OPTION_NAMES = ("-h", "--help")
DEFAULT_MARKETPLACE_SOURCE = "oceanbase/powercontext"
DEFAULT_MARKETPLACE_REF = "master"

setup_app = typer.Typer(
    name="setup",
    context_settings={"help_option_names": HELP_OPTION_NAMES},
    help="Install and configure PowerContext integrations.",
    no_args_is_help=True,
)
doctor_app = typer.Typer(
    callback=doctor,
    name="doctor",
    context_settings={"help_option_names": HELP_OPTION_NAMES},
    help="Check an installed PowerContext environment.",
    invoke_without_command=True,
)
SetupSource = Annotated[str, typer.Option(help="PowerContext Git source or local checkout path.")]
SetupRef = Annotated[str, typer.Option(help="Git ref used for a remote source.")]
SetupServer = Annotated[
    str | None, typer.Option(help="PowerContext Server URL; resolves host/common environment and saved settings.")
]
SetupConsent = Annotated[
    bool | None,
    typer.Option("--allow-insecure-http/--no-allow-insecure-http", help="Explicitly allow unencrypted remote HTTP."),
]
SetupJSON = Annotated[bool, typer.Option("--json", help="Write the result as JSON.")]
SetupPython = Annotated[
    str | None, typer.Option(help="Application Python executable; defaults to the project virtual environment.")
]
SetupDestination = Annotated[Path | None, typer.Option(help="Plugin directory for a directory-based integration.")]


@setup_app.callback()
def setup_configuration(
    context: typer.Context,
    env_file: Annotated[
        Path | None, typer.Option(help="Setup environment file; defaults to .env in this directory.")
    ] = None,
) -> None:
    """Select a safely parsed configuration file for all setup targets."""
    from .transport import setup_environment_file

    token = setup_environment_file.set(env_file)
    context.call_on_close(lambda: setup_environment_file.reset(token))


def setup_host(
    context: typer.Context,
    source: SetupSource = DEFAULT_MARKETPLACE_SOURCE,
    ref: SetupRef = DEFAULT_MARKETPLACE_REF,
    server_url: SetupServer = None,
    allow_insecure_http: SetupConsent = None,
    json_output: SetupJSON = False,
    python: SetupPython = None,
    destination: SetupDestination = None,
) -> None:
    run_host_setup(
        context.info_name or "",
        source=source,
        ref=ref,
        server_url=server_url,
        allow_insecure_http=allow_insecure_http,
        json_output=json_output,
        python=python,
        destination=destination,
    )


def setup_claude_code(
    context: typer.Context,
    source: SetupSource = DEFAULT_MARKETPLACE_SOURCE,
    ref: SetupRef = DEFAULT_MARKETPLACE_REF,
    server_url: SetupServer = None,
    capture_prompts: Annotated[
        bool, typer.Option(help="Capture Claude Code user prompts as ordinary Source evidence.")
    ] = True,
    allow_insecure_http: SetupConsent = None,
    json_output: SetupJSON = False,
    python: SetupPython = None,
    destination: SetupDestination = None,
) -> None:
    run_host_setup(
        context.info_name or "",
        source=source,
        ref=ref,
        server_url=server_url,
        capture_prompts=capture_prompts,
        allow_insecure_http=allow_insecure_http,
        json_output=json_output,
        python=python,
        destination=destination,
    )


for _adapter in HOST_ADAPTERS:
    setup_app.command(_adapter.name, help=f"Install and configure the PowerContext {_adapter.label} integration.")(
        setup_claude_code if "capture_prompts" in _adapter.setup_options else setup_host
    )


@setup_app.command("select")
def setup_select(
    host: Annotated[
        list[str] | None,
        typer.Option(help="Integration target to install. Repeatable. Required with --json or a non-TTY."),
    ] = None,
    source: SetupSource = DEFAULT_MARKETPLACE_SOURCE,
    ref: SetupRef = DEFAULT_MARKETPLACE_REF,
    server_url: SetupServer = None,
    capture_prompts: Annotated[
        bool,
        typer.Option(help="Capture Claude Code user prompts as ordinary Source evidence."),
    ] = True,
    allow_insecure_http: SetupConsent = None,
    json_output: SetupJSON = False,
    python: SetupPython = None,
    destination: SetupDestination = None,
) -> None:
    """Install selected integration targets without scanning PATH."""

    from .hosts import run_setup_select

    run_setup_select(
        hosts=host,
        source=source,
        ref=ref,
        server_url=server_url,
        capture_prompts=capture_prompts,
        json_output=json_output,
        allow_insecure_http=allow_insecure_http,
        python=python,
        destination=destination,
    )


def _register_host_doctors() -> None:
    from .host import HOST_ADAPTERS, HostAdapter

    def register(adapter: HostAdapter) -> None:
        def doctor_host(
            json_output: SetupJSON = False,
            python: SetupPython = None,
            destination: SetupDestination = None,
            server: Annotated[bool, typer.Option(help="Also check the configured Server.")] = False,
        ) -> None:
            options = {
                key: value for key, value in {"python": python, "destination": destination}.items() if value is not None
            }
            if any(key not in adapter.setup_options for key in options):
                raise typer.BadParameter("This integration does not accept the supplied installation location.")
            diagnostics = adapter.diagnose(server=server, **options)
            _write_diagnostics(diagnostics, json_output=json_output)
            if not _diagnostics_ok(diagnostics):
                raise typer.Exit(code=1)

        doctor_app.command(adapter.name, help=f"Check {adapter.label} and its PowerContext integration.")(doctor_host)

    for adapter in HOST_ADAPTERS:
        register(adapter)


_register_host_doctors()


@doctor_app.command("integrations")
def doctor_integrations(
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Write the result as JSON."),
    ] = False,
) -> None:
    """Report first-class host CLI and integration status without failing on missing CLIs."""

    from .hosts import run_doctor_integrations

    run_doctor_integrations(json_output=json_output)


__all__ = ["Diagnostic", "DiagnosticStatus", "SetupError", "doctor_app", "setup_app"]
