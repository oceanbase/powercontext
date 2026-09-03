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

"""User-facing lifecycle commands for the personal PowerContext Server service."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer

from powercontext.server import cli as _server_role_dependency
from powercontext.service.controller import ServiceController
from powercontext.service.model import ServiceError, ServiceStatus

del _server_role_dependency

HELP_OPTION_NAMES = ("-h", "--help")

app = typer.Typer(
    name="service",
    context_settings={"help_option_names": HELP_OPTION_NAMES},
    help="Install and inspect the current user's persistent PowerContext Server.",
    no_args_is_help=True,
)


@app.callback()
def main() -> None:
    """Manage one native current-user PowerContext Server registration."""


@app.command()
def install(
    env_file: Annotated[
        Path | None,
        typer.Option(help="Load persistent Server and provider settings from this protected environment file."),
    ] = None,
) -> None:
    """Install, enable, and start the personal Server service."""

    try:
        status = _controller().install(env_file=env_file)
    except (OSError, ServiceError) as error:
        typer.echo(f"PowerContext personal service installation failed: {error}", err=True)
        if isinstance(error, ServiceError) and error.status is not None:
            _write_status(error.status, json_output=False)
        raise typer.Exit(code=error.exit_code if isinstance(error, ServiceError) else 1) from error
    typer.echo("PowerContext personal service installed.")
    _write_status(status, json_output=False)


@app.command()
def status(
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Write the service state as JSON."),
    ] = False,
) -> None:
    """Inspect registration, manager state, liveness, and logs."""

    try:
        service_status = _controller().status()
    except (OSError, ServiceError) as error:
        typer.echo(f"PowerContext personal service status failed: {error}", err=True)
        raise typer.Exit(code=error.exit_code if isinstance(error, ServiceError) else 1) from error
    _write_status(service_status, json_output=json_output)
    if not service_status.ok:
        raise typer.Exit(code=1)


@app.command()
def uninstall() -> None:
    """Stop and remove the PowerContext-owned personal service registration."""

    try:
        status = _controller().uninstall()
    except (OSError, ServiceError) as error:
        typer.echo(f"PowerContext personal service uninstall failed: {error}", err=True)
        if isinstance(error, ServiceError) and error.status is not None:
            _write_status(error.status, json_output=False)
        raise typer.Exit(code=error.exit_code if isinstance(error, ServiceError) else 1) from error
    typer.echo("PowerContext personal service uninstalled.")
    _write_status(status, json_output=False)


def _controller() -> ServiceController:
    return ServiceController()


def _write_status(status: ServiceStatus, *, json_output: bool) -> None:
    if json_output:
        typer.echo(json.dumps(status.as_json(), indent=2))
        return
    typer.echo(f"support: {status.support.value}")
    typer.echo(f"registration: {status.registration.value}")
    typer.echo(f"definition: {status.definition.value}")
    typer.echo(f"manager ownership: {status.manager_ownership.value}")
    typer.echo(f"manager: {status.manager.value}")
    liveness = status.server_liveness.value
    if status.endpoint is not None:
        liveness = f"{liveness} ({status.endpoint})"
    typer.echo(f"server liveness: {liveness}")
    typer.echo(f"logs: {status.log_location or 'unavailable'}")
    if status.detail:
        typer.echo(f"detail: {status.detail}")
    if status.recovery_action:
        typer.echo(f"action: {status.recovery_action}")


__all__ = ["app"]
