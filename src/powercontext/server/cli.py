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

import asyncio
import signal
from contextlib import AsyncExitStack, nullcontext
from pathlib import Path
from typing import Annotated, Any, Literal

import typer
from pydantic import ValidationError

from powercontext.builtin.persistence.oceanbase import OceanBaseConfig, OceanBaseProfile
from powercontext.builtin.persistence.processing_migration import (
    apply_processing_migration,
    plan_processing_migration,
    verify_processing_migration,
)
from powercontext.builtin.persistence.seekdb import SeekDBConfig, SeekDBProfile
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.runtime.composition import open_builtin_runtime
from powercontext.builtin.runtime.config import BuiltinConfig
from powercontext.builtin.runtime.processing_registry import canonical_processing_manifest
from powercontext.cli.env_file import environment_context
from powercontext.cli.inference_notice import write_inference_capability_notice
from powercontext.server.authz import PrincipalRef
from powercontext.server.authz.composition import open_builtin_access_control
from powercontext.server.configuration import (
    ServerConfigurationError,
    resolve_server_environment_file,
    server_settings_context,
)
from powercontext.server.factory import create_server_app
from powercontext.server.logging import configure_server_logging
from powercontext.server.processing_security import build_worker_security
from powercontext.server.settings import (
    MissingAuthenticationProviderError,
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
    "set POWERCONTEXT_SERVER_AUTH_TOKEN=... or disable Access Control with "
    "POWERCONTEXT_SERVER_ACCESS_MODE=disabled"
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


@app.command("processing-migrate")
def processing_migrate(
    action: Annotated[Literal["plan", "apply", "verify"], typer.Option(help="Offline migration action.")] = "plan",
    env_file: Annotated[Path | None, typer.Option(help="Load deployment settings from this environment file.")] = None,
    maintenance_confirmed: Annotated[
        bool,
        typer.Option(help="Confirm all old background candidates, Workers, writes and explicit triggers are stopped."),
    ] = False,
    migration_id: Annotated[
        str, typer.Option(help="Stable resume ID; use a new ID for an offline mode switch.")
    ] = "rfc1515",
    batch_size: Annotated[int, typer.Option(min=1, max=10000, help="Maximum rows committed per migration step.")] = 100,
) -> None:
    """Plan, apply or verify the resumable artifact-processing schema migration."""

    if action == "apply" and not maintenance_confirmed:
        raise typer.BadParameter("apply requires --maintenance-confirmed after stopping old workers and writes")  # noqa: TRY003
    with server_settings_context(env_file=env_file) as settings:
        ready = asyncio.run(_processing_maintenance(settings, action, migration_id=migration_id, batch_size=batch_size))
    if not ready:
        raise typer.Exit(code=1)


async def _processing_maintenance(
    settings: ServerSettings,
    action: Literal["plan", "apply", "verify"],
    *,
    migration_id: str,
    batch_size: int,
) -> bool:
    config = BuiltinConfig(
        runtime=settings.runtime,
        database=settings.database,
        inference=settings.inference,
        handoff_report=settings.handoff_report,
        external_skills=settings.external_skills,
    )
    manifest = canonical_processing_manifest(config)
    database = settings.database
    if isinstance(database, SQLiteConfig):
        if database.is_in_memory:
            raise typer.BadParameter("offline migration requires a persistent database")  # noqa: TRY003
        opened = SQLiteProfile.open(database, tables=())
    elif isinstance(database, OceanBaseConfig):
        opened = OceanBaseProfile.open(database, tables=())
    elif isinstance(database, SeekDBConfig):
        opened = SeekDBProfile.open(database, tables=())
    else:
        raise typer.BadParameter("unsupported migration database")  # noqa: TRY003
    async with opened as profile:
        if action == "plan":
            async with profile.database.transaction() as connection:
                plan = await plan_processing_migration(connection, config_manifest=manifest)
            typer.echo(plan.model_dump_json())
            return True
        if action == "apply":
            while True:
                async with profile.database.transaction() as connection:
                    progress = await apply_processing_migration(
                        connection,
                        config_manifest=manifest,
                        migration_id=migration_id,
                        batch_size=batch_size,
                    )
                if progress.complete:
                    break
        async with profile.database.transaction() as connection:
            verification = await verify_processing_migration(connection, config_manifest=manifest)
        typer.echo(verification.model_dump_json())
        return verification.ready


@app.command()
def run(
    host: Annotated[str | None, typer.Option(help="Address to bind.")] = None,
    port: Annotated[int | None, typer.Option(min=1, max=65535, help="Port to bind.")] = None,
    env_file: Annotated[
        Path | None,
        typer.Option(help="Load Server and provider settings from this environment file."),
    ] = None,
    no_env_file: Annotated[
        bool,
        typer.Option("--no-env-file", help="Do not discover or load an environment file."),
    ] = False,
    role: Annotated[
        Literal["all", "api", "background"] | None,
        typer.Option(help="Run all components, only APIs, or only background processing."),
    ] = None,
) -> None:
    """Run the configured API and/or background service in the foreground."""

    if env_file is not None and no_env_file:
        raise typer.BadParameter("cannot be combined with --env-file", param_hint="--no-env-file")  # noqa: TRY003
    selected_env_file = resolve_server_environment_file(env_file, discover=not no_env_file)
    role_context = (
        nullcontext()
        if role is None
        else environment_context(
            {"POWERCONTEXT_SERVER_RUNTIME_ARTIFACT_PROCESSING_ROLE": role},
            override=True,
        )
    )
    try:
        with (
            role_context,
            server_settings_context(
                host=host,
                port=port,
                env_file=selected_env_file,
                process_environment_overrides=True,
            ) as settings,
        ):
            if selected_env_file is not None:
                typer.echo(f"Loaded environment file: {selected_env_file}")
            _run_configured_server(settings)
    except ServerConfigurationError as error:
        if isinstance(error.cause, ValidationError):
            raise _friendly_bad_parameter(error.cause) from error
        if env_file is not None:
            hint = "Invalid value for --env-file"
        elif selected_env_file is not None:
            hint = "Invalid default environment file"
        else:
            hint = "Invalid Server configuration"
        typer.echo(f"{hint}: {error}", err=True)
        raise typer.Exit(code=2) from error
    except MissingAuthenticationProviderError as error:
        raise typer.BadParameter(_MISSING_BEARER_CLI_MESSAGE) from error


def _run_configured_server(settings: ServerSettings) -> None:
    """Run one already-validated configuration in the current process."""

    configure_server_logging(settings.logging)
    tracing = configure_server_tracing(settings.tracing)
    try:
        write_inference_capability_notice(
            generation_model=settings.inference.generation_model,
            embedding_model=settings.inference.embedding_model,
        )
        if settings.runtime.artifact_processing_role == "background":
            _run_background(settings, tracing)
            return
        application = create_server_app(settings=settings, tracing=tracing)
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


def _run_background(settings: ServerSettings, tracing: Any) -> None:
    """Run a Supervisor-only process until SIGINT or SIGTERM."""

    asyncio.run(_run_background_async(settings, tracing))


async def _run_background_async(settings: ServerSettings, tracing: Any) -> None:
    config = BuiltinConfig(
        runtime=settings.runtime,
        database=settings.database,
        handoff_report=settings.handoff_report,
        inference=settings.inference,
        external_skills=settings.external_skills,
    )
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    installed: list[signal.Signals] = []
    for signum in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signum, stopped.set)
        except (NotImplementedError, RuntimeError):
            continue
        installed.append(signum)
    try:
        async with AsyncExitStack() as resources:
            principal = (
                PrincipalRef(type="service", id="server-token", description="PowerContext static bearer")
                if settings.auth.token is not None
                else None
            )
            access = None
            if settings.access.mode == "enforced":
                access = await resources.enter_async_context(
                    open_builtin_access_control(
                        settings.database,
                        bootstrap_administrators=() if principal is None else (principal,),
                        deployment_id=settings.access.deployment_id,
                    )
                )
            worker_security = build_worker_security(settings, access, legacy_static_principal=principal)
            await resources.enter_async_context(
                open_builtin_runtime(
                    config,
                    instrumentation=tracing.instrumentation,
                    tracing=tracing,
                    worker_security=worker_security,
                )
            )
            await stopped.wait()
    finally:
        for signum in installed:
            loop.remove_signal_handler(signum)
