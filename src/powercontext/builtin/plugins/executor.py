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

"""Run one Artifact plugin invocation inside a Supervisor Worker.

The executor owns the window, the cursor and the acknowledgment. The plugin only
sees the window's Sources and the writer; whatever it writes commits together with
the cursor advance, and a failure leaves both unchanged.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from powercontext._logging import log_safely
from powercontext.builtin.persistence.cursors import SourceCursorRepository
from powercontext.builtin.persistence.generation_sources import GenerationSourceAccess
from powercontext.builtin.persistence.sources import SourceRepository
from powercontext.builtin.plugins.families import register_plugin_family, revision_metadata
from powercontext.builtin.plugins.manifest import PluginManifest, load_plugin
from powercontext.builtin.plugins.writer import PluginArtifactWriter, PluginWriteError
from powercontext.builtin.runtime.config import DatabaseConfig
from powercontext.builtin.runtime.processing_contracts import (
    ArtifactProcessingWorkAssignment,
    ArtifactProcessingWorkerCompletion,
)
from powercontext.builtin.runtime.processing_execution import InvocationAlreadyHandled, ScopeInvocation
from powercontext.builtin.sources import BUILTIN_SOURCE_REGISTRY, SourceCursor
from powercontext.plugins import ArtifactPlugin, GenerateRequest, GenerationSource, PluginContext

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PluginWorkerSpec:
    """Picklable Worker input; the plugin reads its own settings inside the Worker."""

    database: DatabaseConfig
    directory: str
    manifest: dict[str, Any]
    security: dict[str, Any] | None = None
    inference: Any = None
    storage_root: str | None = None


def run_plugin_worker(
    spec: PluginWorkerSpec, assignment: ArtifactProcessingWorkAssignment, /
) -> ArtifactProcessingWorkerCompletion:
    return asyncio.run(execute_plugin(spec, assignment))


@asynccontextmanager
async def open_plugin_database(config: DatabaseConfig):
    from powercontext.builtin.persistence.oceanbase.profile import OceanBaseConfig, OceanBaseProfile
    from powercontext.builtin.persistence.seekdb.profile import SeekDBProfile
    from powercontext.builtin.persistence.sqlite.profile import SQLiteConfig, SQLiteProfile

    # The Server has prepared the schema; a Worker never creates tables.
    if isinstance(config, SQLiteConfig):
        opened = SQLiteProfile.open(config, tables=())
    elif isinstance(config, OceanBaseConfig):
        opened = OceanBaseProfile.open(config, tables=())
    else:
        opened = SeekDBProfile.open(config, tables=())
    async with opened as profile:
        yield profile.database


async def execute_plugin(  # noqa: C901 - one window from selection through acknowledgment
    spec: PluginWorkerSpec, assignment: ArtifactProcessingWorkAssignment, /, *, database: Any = None
) -> ArtifactProcessingWorkerCompletion:
    manifest = PluginManifest.model_validate(spec.manifest)
    generate = manifest.artifact
    if generate is None:
        raise ValueError("only Artifact plugins generate")  # noqa: TRY003
    if assignment.binding_name != manifest.binding_name or assignment.artifact_family != generate.family:
        raise ValueError("assignment does not match the plugin declaration")  # noqa: TRY003
    register_plugin_family(generate.family)
    scope_id = assignment.scope_id
    binding = manifest.binding_name
    sources = SourceRepository(BUILTIN_SOURCE_REGISTRY)
    cursors = SourceCursorRepository()
    async with AsyncExitStack() as resources:
        if database is None:
            database = await resources.enter_async_context(open_plugin_database(spec.database))
        from powercontext.builtin.authz.worker import open_worker_security

        security = await resources.enter_async_context(open_worker_security(spec.security, database))

        async def authorize(connection) -> None:
            if generate.scopes is not None and scope_id not in generate.scopes:
                raise PluginWriteError("the Scope is outside the plugin's scopes")  # noqa: TRY003
            if security is not None:
                await security.authorize_transaction(connection, scope_id)

        invocation = ScopeInvocation(assignment, authorize_transaction=authorize)
        inputs = generate.inputs
        try:
            async with database.transaction() as connection:
                await invocation.start(connection)
                cursor = await cursors.load(connection, scope_id, binding)
                after = 0 if cursor is None else cursor.cursor.sequence
                through = after
                rows = ()
                if inputs is not None:
                    high = await sources.journal_position(connection, scope_id)
                    through = min(high, after + inputs.max_sources)
                    rows = await GenerationSourceAccess(sources).list_window_for_generation(
                        connection, scope_id, after=after, through=through
                    )
            window = ()
            if inputs is not None:
                accepted = frozenset(inputs.source_types)
                # A matching Source without the declared Projection fails the window here.
                window = tuple(
                    GenerationSource(
                        ref=row.ref,
                        position=row.journal_position,
                        projection=BUILTIN_SOURCE_REGISTRY.project(
                            row.value, inputs.projection_key(row.ref.source_type)
                        ),
                    )
                    for row in rows
                    if row.ref.source_type in accepted
                )

            async def begin(connection) -> None:
                await invocation.guard(connection)

            async def complete(connection) -> None:
                await invocation.guard(connection)
                if through > after:
                    await cursors.save(
                        connection,
                        scope_id,
                        binding,
                        SourceCursor(sequence=through),
                        expected_generation=None if cursor is None else cursor.generation,
                    )
                remaining = inputs is not None and await sources.journal_position(connection, scope_id) > through
                await invocation.complete(connection, remaining_work=remaining)

            writer = PluginArtifactWriter(
                database=database,
                scope_id=scope_id,
                family=generate.family,
                metadata=revision_metadata(manifest.name, manifest.fingerprint),
                window_sources=frozenset((item.ref.source_type, item.ref.source_id) for item in window),
                sources=sources,
                begin=begin,
                complete=complete,
                transaction_timeout_seconds=generate.transaction_timeout_seconds,
                security=security,
            )
            call = bool(window) or inputs is None
            if call:
                directory = Path(spec.directory)
                loaded, declaration = load_plugin(directory, manifest.name)
                if loaded != manifest or not isinstance(declaration, ArtifactPlugin):
                    raise ValueError("the plugin changed after the Server checked it; restart the Server")  # noqa: TRY003
                async with AsyncExitStack() as plugin_resources:
                    from powercontext.builtin.plugins.storage import open_plugin_resources

                    storage, models = await open_plugin_resources(
                        spec.database, spec.storage_root, manifest.name, spec.inference, plugin_resources
                    )
                    context = PluginContext(
                        name=manifest.name,
                        scope_id=scope_id,
                        plugin_dir=directory,
                        logger=logging.getLogger(f"powercontext.plugin.{manifest.name}"),
                        resources=plugin_resources,
                        storage=storage,
                        models=models,
                    )
                    request = GenerateRequest(
                        scope_id=scope_id, reason="sources" if window else "schedule", sources=window
                    )
                    try:
                        plugin = await declaration.setup(context)
                        await plugin.generate(request, writer)
                    except Exception as error:
                        if not writer.committed:
                            raise
                        # Results are durable; do not consume or retry the window again.
                        log_safely(
                            logger,
                            logging.WARNING,
                            f"Plugin {manifest.name} raised after commit ({type(error).__name__})",
                        )
            if not writer.opened:
                async with database.transaction() as connection:
                    await complete(connection)
        except InvocationAlreadyHandled:
            return ArtifactProcessingWorkerCompletion(noop=True)
    return ArtifactProcessingWorkerCompletion(noop=not call)
