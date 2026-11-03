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

"""Operator-facing portable bundle commands using a controlled built-in Runtime."""

from __future__ import annotations

import asyncio
import json
import stat
from collections.abc import Awaitable, Callable
from dataclasses import asdict
from pathlib import Path
from typing import Annotated, TypeVar, assert_never
from urllib.parse import urlsplit
from urllib.request import url2pathname

import typer
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from powercontext.builtin.persistence.artifacts import ArtifactRepository
from powercontext.builtin.persistence.candidates import CandidateRepository
from powercontext.builtin.persistence.database import AsyncDatabase
from powercontext.builtin.persistence.oceanbase import OceanBaseConfig, OceanBaseProfile
from powercontext.builtin.persistence.seekdb import SeekDBConfig, SeekDBProfile
from powercontext.builtin.persistence.sources import SourceRepository
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.portability import (
    BundleCompatibilityError,
    BundleConflictError,
    BundleFormatError,
    BundleInspection,
    BundleProgress,
    BundleReceipt,
    BundleRecoveryRequiredError,
    BundleValidation,
    PortableBundleService,
)
from powercontext.builtin.runtime import BuiltinConfig, open_builtin_runtime
from powercontext.builtin.runtime.relational import BUILTIN_ARTIFACT_TYPES, BUILTIN_CANDIDATE_PROPOSAL_TYPES
from powercontext.builtin.sources import BUILTIN_SOURCE_REGISTRY
from powercontext.paths import default_scheduler_path
from powercontext.server.configuration import server_settings_context

HELP_OPTION_NAMES = ("-h", "--help")
_SQLITE_TARGET_INSPECTION_ERROR = "cannot inspect configured SQLite target"
_ResultT = TypeVar("_ResultT", BundleInspection, BundleValidation, BundleReceipt)

archive_app = typer.Typer(
    name="archive",
    context_settings={"help_option_names": HELP_OPTION_NAMES},
    help="Create, inspect, validate, and restore portable logical bundles.",
    no_args_is_help=True,
)


@archive_app.command("export")
def export_bundle(
    scope_id: Annotated[
        list[str], typer.Option("--scope-id", min=1, help="Complete scope to export; repeat as needed.")
    ],
    output: Annotated[Path, typer.Option("--output", help="New portable bundle file to create.")],
    compress: Annotated[bool, typer.Option("--compress/--no-compress", help="Compress the portable bundle.")] = True,
    env_file: Annotated[
        Path | None, typer.Option("--env-file", help="Deployment environment for SQLite, SeekDB, or OceanBase.")
    ] = None,
) -> None:
    """Create a verified logical bundle from complete scopes."""

    _emit_archive_result(
        lambda archive: archive.export(
            scope_id, output, authorize=_authorize_local_export, progress=_emit_progress, compress=compress
        ),
        env_file=env_file,
    )


@archive_app.command("inspect")
def inspect_bundle(
    source: Annotated[Path, typer.Argument(help="Portable bundle file to inspect.")],
) -> None:
    """Verify archive structure and checksums without writing domain data."""

    try:
        _emit(asyncio.run(PortableBundleService.inspect(source, progress=_emit_progress)))
    except BundleFormatError as error:
        typer.echo(f"error: archive validation failed: {error}", err=True)
        raise typer.Exit(code=2) from error


@archive_app.command("restore")
def restore_bundle(
    source: Annotated[Path, typer.Argument(help="Portable bundle file to validate or restore.")],
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Validate only; never write domain data.")] = False,
    yes: Annotated[bool, typer.Option("--yes", help="Confirm a write restore.")] = False,
    env_file: Annotated[
        Path | None, typer.Option("--env-file", help="Target deployment environment for validation or restore.")
    ] = None,
) -> None:
    """Validate or restore a portable bundle into the configured deployment."""

    if dry_run:
        _emit_validation_result(source, env_file=env_file)
        return
    if not yes:
        raise typer.BadParameter(  # noqa: TRY003
            "restore writes immutable records; re-run with --yes to confirm",
            param_hint="--yes",
        )
    _emit_archive_result(
        lambda archive: archive.restore(source, progress=_emit_progress), env_file=env_file, recovery=True
    )


async def _run_archive(
    operation: Callable[[PortableBundleService], Awaitable[_ResultT]],
    /,
    *,
    env_file: Path | None,
    recovery: bool = False,
) -> _ResultT:
    with server_settings_context(env_file=env_file) as settings:
        config = BuiltinConfig(
            runtime=settings.runtime,
            database=settings.database,
            inference=settings.inference,
            handoff_report=settings.handoff_report,
            external_skills=settings.external_skills,
        )
        async with open_builtin_runtime(
            config, scheduler_path=default_scheduler_path(), _archive_recovery=recovery
        ) as runtime:
            if runtime.archive is None:
                raise RuntimeError("portable archive service is unavailable")  # noqa: TRY003
            return await operation(runtime.archive)


async def _authorize_local_export(_scopes: tuple[str, ...], /) -> None:
    """A local operator's authority is the OS permission to read the database."""


async def _validate_only(source: Path, /, *, env_file: Path | None) -> BundleValidation:
    """Open only the configured database profile; do not initialize Runtime state."""

    with server_settings_context(env_file=env_file) as settings:
        database = settings.database
        if isinstance(database, SQLiteConfig):
            existing = database.is_persistent and await asyncio.to_thread(_database_file_exists, database.url)
            if existing:
                async with SQLiteProfile.open_readonly(database) as profile:
                    return await _validate_with_database(profile.database, source)
            engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=database.echo, hide_parameters=True)
            validation_database = AsyncDatabase.own(engine, shared_connection=True)
            try:
                return await _validate_with_database(validation_database, source)
            finally:
                await validation_database.close()
        if isinstance(database, OceanBaseConfig):
            async with OceanBaseProfile.open(database, tables=()) as profile:
                return await _validate_with_database(profile.database, source)
        if isinstance(database, SeekDBConfig):
            async with SeekDBProfile.open(database, tables=()) as profile:
                return await _validate_with_database(profile.database, source)
        assert_never(database)


async def _validate_with_database(database: AsyncDatabase, source: Path, /) -> BundleValidation:
    async def projection_capability(_scopes: tuple[str, ...], /) -> None:
        return None

    artifacts = ArtifactRepository(BUILTIN_ARTIFACT_TYPES)
    archive = PortableBundleService(
        database,
        projection_rebuilder=projection_capability,
        supported_source_types=tuple(definition.name for definition in BUILTIN_SOURCE_REGISTRY.definitions),
        supported_artifact_families=artifacts.families,
        artifact_content_decoder=artifacts.decode_content,
        candidate_decoder=CandidateRepository(BUILTIN_CANDIDATE_PROPOSAL_TYPES).decode_candidate,
        source_decoder=SourceRepository(BUILTIN_SOURCE_REGISTRY).decode_source,
    )
    return await archive.validate(source, progress=_emit_progress)


def _database_file_exists(value: str, /) -> bool:
    authority = ""
    try:
        url = make_url(value)
        arguments, options = url.get_dialect()().create_connect_args(url)
        filename = str(arguments[0])
        if options.get("uri") and filename.startswith("file:"):
            uri = urlsplit(filename.replace("\t", "%09").replace("\n", "%0A").replace("\r", "%0D"))
            authority = uri.netloc
            filename = url2pathname(uri.path).partition("\0")[0]
    except (OSError, ValueError) as error:
        raise BundleFormatError(_SQLITE_TARGET_INSPECTION_ERROR) from error
    if authority not in {"", "localhost"} or not filename:
        raise BundleFormatError(_SQLITE_TARGET_INSPECTION_ERROR)
    target = Path(filename)
    for candidate in (target, *target.parents):
        try:
            mode = candidate.stat().st_mode
        except FileNotFoundError:
            continue
        except (OSError, ValueError) as error:
            raise BundleFormatError(_SQLITE_TARGET_INSPECTION_ERROR) from error
        if candidate == target and stat.S_ISREG(mode):
            return True
        if candidate != target and stat.S_ISDIR(mode):
            return False
        raise BundleFormatError(_SQLITE_TARGET_INSPECTION_ERROR)
    raise BundleFormatError(_SQLITE_TARGET_INSPECTION_ERROR)


def _emit_progress(value: BundleProgress, /) -> None:
    typer.echo(json.dumps({"progress": asdict(value)}, sort_keys=True), err=True)


def _emit_archive_result(
    operation: Callable[[PortableBundleService], Awaitable[_ResultT]],
    /,
    *,
    env_file: Path | None,
    recovery: bool = False,
) -> None:
    """Render expected archive failures without leaking stack traces or records."""

    try:
        _emit(asyncio.run(_run_archive(operation, env_file=env_file, recovery=recovery)))
    except BundleCompatibilityError as error:
        typer.echo(f"error: archive target is incompatible: {error}", err=True)
        raise typer.Exit(code=3) from error
    except BundleFormatError as error:
        typer.echo(f"error: archive validation failed: {error}", err=True)
        raise typer.Exit(code=2) from error
    except BundleConflictError as error:
        typer.echo(f"error: archive restore conflict: {error}", err=True)
        raise typer.Exit(code=3) from error
    except BundleRecoveryRequiredError as error:
        typer.echo(f"error: archive recovery required: {error}", err=True)
        raise typer.Exit(code=3) from error


def _emit_validation_result(source: Path, /, *, env_file: Path | None) -> None:
    try:
        validation = asyncio.run(_validate_only(source, env_file=env_file))
        _emit(validation)
        if not validation.compatible:
            raise typer.Exit(code=3)
    except BundleCompatibilityError as error:
        typer.echo(f"error: archive target is incompatible: {error}", err=True)
        raise typer.Exit(code=3) from error
    except BundleFormatError as error:
        typer.echo(f"error: archive validation failed: {error}", err=True)
        raise typer.Exit(code=2) from error


def _emit(value: BundleInspection | BundleValidation | BundleReceipt, /) -> None:
    typer.echo(json.dumps(asdict(value), default=list, sort_keys=True))
