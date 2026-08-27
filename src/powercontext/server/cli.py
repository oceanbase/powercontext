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

"""CLI commands owned by the ready-to-run service entry point."""

from __future__ import annotations

import os
from collections.abc import Mapping
from contextlib import nullcontext
from pathlib import Path
from typing import Annotated, Any

import typer
from pydantic import ValidationError

from powercontext.cli.env_file import EnvironmentFileError, environment_context, read_environment_file
from powercontext.server.factory import create_server_app
from powercontext.server.logging import configure_server_logging
from powercontext.server.settings import (
    MissingBearerTokenError,
    ServerSettings,
    UnauthenticatedNonLoopbackBindError,
)
from powercontext.server.tracing import configure_server_tracing

HELP_OPTION_NAMES = ("-h", "--help")

# Shown when the merged bind fails the unauthenticated-non-loopback policy. It repeats the
# operator's concrete levers -- authenticate, stay on loopback, or opt in via the full env var --
# instead of surfacing pydantic's internal validation dump (see ``_friendly_bad_parameter``).
_UNSAFE_BIND_CLI_MESSAGE = (
    "refusing to bind an unauthenticated Server to a non-loopback address; "
    "enable authentication, keep the bind on loopback, or set "
    "POWERCONTEXT_SERVER_ALLOW_UNAUTHENTICATED_NON_LOOPBACK=true to opt in"
)

# Shown when authentication is enabled without a token; names the concrete env-var levers the
# operator can set instead of surfacing pydantic's internal validation dump.
_MISSING_BEARER_CLI_MESSAGE = (
    "authentication is enabled but no bearer token is configured; "
    "set POWERCONTEXT_SERVER_AUTH_TOKEN=... or disable it with "
    "POWERCONTEXT_SERVER_AUTH_ENABLED=false"
)

app = typer.Typer(
    name="server",
    context_settings={"help_option_names": HELP_OPTION_NAMES},
    help="Run a configured PowerContext service.",
    no_args_is_help=True,
)


@app.callback()
def main() -> None:
    """Manage the PowerContext service process."""


@app.command()
def run(
    host: Annotated[str | None, typer.Option(help="Address to bind.")] = None,
    port: Annotated[int | None, typer.Option(min=1, max=65535, help="Port to bind.")] = None,
    env_file: Annotated[
        Path | None,
        typer.Option(help="Load Server and provider settings from this environment file."),
    ] = None,
) -> None:
    """Run the ASGI service in the foreground."""

    loaded: Mapping[str, str] = {}
    if env_file is not None:
        try:
            loaded = read_environment_file(env_file)
        except (EnvironmentFileError, OSError) as error:
            typer.echo(f"Invalid value for --env-file: {error}", err=True)
            raise typer.Exit(code=2) from error
    server_environment = {name for name in os.environ if name.startswith("POWERCONTEXT_SERVER_")}
    loaded_context = (
        environment_context(loaded, override=True, clear=server_environment) if env_file is not None else nullcontext()
    )
    with loaded_context:
        # Layer CLI overrides in before validation so the bind policy checks the address
        # the process will actually use, including values loaded from --env-file.
        http_overrides: dict[str, Any] = {}
        if host is not None:
            http_overrides["host"] = host
        if port is not None:
            http_overrides["port"] = port
        settings_kwargs: dict[str, Any] = {"http": http_overrides} if http_overrides else {}
        try:
            settings = ServerSettings(**settings_kwargs)
        except ValidationError as error:
            raise _friendly_bad_parameter(error) from error
        configure_server_logging(settings.logging)
        tracing = configure_server_tracing(settings.tracing)
        try:
            application = create_server_app(settings=settings, tracing=tracing)
            if settings.dashboard.enabled:
                if application.state.dashboard_started:
                    typer.echo(f"PowerContext Dashboard: http://{settings.http.host}:{settings.http.port}/")
                else:
                    typer.echo(
                        f"PowerContext Dashboard failed to start: {application.state.dashboard_startup_error}",
                        err=True,
                    )
            _run_server(
                application,
                host=settings.http.host,
                port=settings.http.port,
            )
        finally:
            tracing.shutdown()


def _friendly_bad_parameter(error: ValidationError) -> typer.BadParameter:
    """Translate a settings ``ValidationError`` into an actionable CLI parameter error.

    ``ServerSettings`` enforces its policies at construction time, so a rejected ``--host`` /
    environment combination arrives here wrapped in pydantic's generic validation report. The
    policy failures an operator can act on directly are recognised by identity via pydantic's
    ``ctx['error']`` -- not by matching the raw text -- and translated into a concrete lever.
    Anything else falls back to pydantic's message unchanged.
    """

    for detail in error.errors(include_context=True):
        cause = (detail.get("ctx") or {}).get("error")
        if isinstance(cause, UnauthenticatedNonLoopbackBindError):
            return typer.BadParameter(_UNSAFE_BIND_CLI_MESSAGE, param_hint="--host")
        if isinstance(cause, MissingBearerTokenError):
            return typer.BadParameter(_MISSING_BEARER_CLI_MESSAGE)
    return typer.BadParameter(str(error))


def _run_server(application: Any, *, host: str, port: int) -> None:
    import uvicorn

    uvicorn.run(application, host=host, port=port, access_log=False, log_config=None)
