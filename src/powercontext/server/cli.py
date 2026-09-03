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
from pathlib import Path
from typing import Annotated, Any, cast

import typer
from pydantic import ValidationError

from powercontext.builtin.artifacts.memory import EmbeddingProfile
from powercontext.builtin.handoff_report.sqlite import HANDOFF_REPORT_TABLES
from powercontext.builtin.persistence.memory_index import CompositeMemoryIndex
from powercontext.builtin.persistence.migration import SchemaMigrationError, migrate_database
from powercontext.builtin.persistence.oceanbase import OceanBaseConfig, OceanBaseProfile
from powercontext.builtin.persistence.oceanbase.experience_index import OceanBaseExperienceFTSIndex
from powercontext.builtin.persistence.oceanbase.memory_index import (
    OceanBaseMemoryFTSIndex,
    OceanBaseMemoryVectorIndex,
)
from powercontext.builtin.persistence.schema import create_tables
from powercontext.builtin.persistence.seekdb import SeekDBConfig, SeekDBProfile
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.persistence.sqlite.experience_index import SQLiteExperienceFTSIndex
from powercontext.builtin.persistence.sqlite.memory_index import SQLiteMemoryFTSIndex, SQLiteMemoryVectorIndex
from powercontext.builtin.persistence.tables import BUILTIN_TABLES
from powercontext.builtin.runtime.config import InferenceConfig
from powercontext.server.configuration import ServerConfigurationError, server_settings_context
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

    try:
        with server_settings_context(host=host, port=port, env_file=env_file) as settings:
            _run_configured_server(settings)
    except ServerConfigurationError as error:
        if isinstance(error.cause, ValidationError):
            raise _friendly_bad_parameter(error.cause) from error
        hint = "Invalid value for --env-file" if env_file is not None else "Invalid Server configuration"
        typer.echo(f"{hint}: {error}", err=True)
        raise typer.Exit(code=2) from error


@app.command()
def migrate(
    env_file: Annotated[
        Path | None,
        typer.Option(help="Load Server and database settings from this environment file."),
    ] = None,
) -> None:
    """Upgrade the configured database through the forward-only schema chain."""

    try:
        with server_settings_context(env_file=env_file) as settings:
            revision = asyncio.run(_migrate_configured_database(settings))
    except ServerConfigurationError as error:
        if isinstance(error.cause, ValidationError):
            raise _friendly_bad_parameter(error.cause) from error
        typer.echo(f"Invalid Server configuration: {error}", err=True)
        raise typer.Exit(code=2) from error
    except SchemaMigrationError as error:
        typer.echo(f"Schema migration failed: {error}", err=True)
        raise typer.Exit(code=1) from error
    typer.echo(f"PowerContext database schema is at {revision}.")


async def _migrate_configured_database(settings: ServerSettings) -> str:
    database = settings.database
    embedding = _configured_embedding_profile(settings.inference)
    report_tables = HANDOFF_REPORT_TABLES if settings.handoff_report.enabled else ()
    if isinstance(database, SQLiteConfig):
        index = CompositeMemoryIndex(
            SQLiteMemoryFTSIndex(),
            *((SQLiteMemoryVectorIndex(embedding),) if embedding is not None else ()),
        )
        experience_index = SQLiteExperienceFTSIndex()
        async with SQLiteProfile.open(
            database,
            tables=BUILTIN_TABLES + report_tables + index.tables,
            load_vector_extension=embedding is not None,
            create_schema=False,
        ) as profile:
            return await migrate_database(
                profile.database,
                provision=_schema_provisioner(
                    index,
                    experience_index,
                    report_tables,
                ),
            )
    index = CompositeMemoryIndex(
        OceanBaseMemoryFTSIndex(),
        *((OceanBaseMemoryVectorIndex(embedding),) if embedding is not None else ()),
    )
    experience_index = OceanBaseExperienceFTSIndex()
    tables = BUILTIN_TABLES + report_tables + index.tables
    if isinstance(database, OceanBaseConfig):
        async with OceanBaseProfile.open(database, tables=tables, create_schema=False) as profile:
            return await migrate_database(
                profile.database,
                provision=_schema_provisioner(
                    index,
                    experience_index,
                    report_tables,
                ),
            )
    if isinstance(database, SeekDBConfig):
        async with SeekDBProfile.open(database, tables=tables, create_schema=False) as profile:
            return await migrate_database(
                profile.database,
                provision=_schema_provisioner(
                    index,
                    experience_index,
                    report_tables,
                ),
            )
    raise RuntimeError("unsupported migration database profile")  # noqa: TRY003


def _configured_embedding_profile(settings: InferenceConfig) -> EmbeddingProfile | None:
    if settings.embedding_model is None:
        return None
    return EmbeddingProfile(
        profile_id=cast(str, settings.embedding_profile_id),
        model=settings.embedding_model,
        dimension=cast(int, settings.embedding_dimension),
        distance="l2",
        normalization=settings.embedding_normalization,
    )


def _schema_provisioner(index: CompositeMemoryIndex, experience_index, optional_tables):
    async def provision(connection) -> None:
        if optional_tables:
            await create_tables(connection, optional_tables)
        if index.tables:
            await create_tables(connection, index.tables)
        await index.initialize(connection)
        await experience_index.initialize(connection)

    return provision


def _run_configured_server(settings: ServerSettings) -> None:
    """Run one already-validated configuration in the current process."""

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
