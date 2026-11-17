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

"""Run one collection by the Server's own runner inside a Supervisor Worker.

One invocation collects one Scope: the enabled targets of this plugin assigned to the
``server`` runner, least recently attempted first. The collected Sources, each target's
new checkpoint, and each target's status commit in one transaction; a target the plugin
reports as failed keeps its checkpoint while the others still commit.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any

from powercontext._logging import log_safely
from powercontext.builtin.persistence.connectors import ConnectorCheckpointRepository
from powercontext.builtin.persistence.source_collection import (
    SERVER_RUNNER,
    TARGET_CHECKPOINT_VERSION,
    SourceCollectionError,
    SourceCollectionRepository,
)
from powercontext.builtin.persistence.sources import SourceRepository
from powercontext.builtin.plugins.executor import PluginWorkerSpec, open_plugin_database
from powercontext.builtin.plugins.families import register_source_binding
from powercontext.builtin.plugins.manifest import PluginManifest, load_plugin
from powercontext.builtin.plugins.observations import observe
from powercontext.builtin.runtime.processing_contracts import (
    ArtifactProcessingWorkAssignment,
    ArtifactProcessingWorkerCompletion,
)
from powercontext.builtin.runtime.processing_execution import InvocationAlreadyHandled, ScopeInvocation
from powercontext.builtin.sources import BUILTIN_SOURCE_REGISTRY
from powercontext.plugins import CollectRequest, CollectResult, CollectTarget, PluginContext, SourcePlugin
from powercontext.sources import ConnectorBinding

logger = logging.getLogger(__name__)


class CollectionContractError(ValueError):
    """The plugin's collect result breaks the collection contract; the whole call fails."""


def collection_family(plugin: str) -> str:
    """The Supervisor accounts bindings by family; a collection binding owns no Artifacts."""

    return f"source-{plugin}"


def run_source_collect_worker(
    spec: PluginWorkerSpec, assignment: ArtifactProcessingWorkAssignment, /
) -> ArtifactProcessingWorkerCompletion:
    # Collected Sources are inputs of the loaded Artifact plugins, as Sources written through the Server are.
    for binding, scopes, source_types in spec.source_bindings:
        register_source_binding(binding, None if scopes is None else frozenset(scopes), frozenset(source_types))
    return asyncio.run(execute_collect(spec, assignment))


async def execute_collect(  # noqa: C901 - one collection from target selection through commit
    spec: PluginWorkerSpec, assignment: ArtifactProcessingWorkAssignment, /, *, database=None
) -> ArtifactProcessingWorkerCompletion:
    manifest = PluginManifest.model_validate(spec.manifest)
    declared = manifest.source
    if declared is None or declared.collect is None:
        raise ValueError("only collecting Source plugins run collection Workers")  # noqa: TRY003
    if assignment.binding_name != manifest.binding_name:
        raise ValueError("assignment does not match the plugin declaration")  # noqa: TRY003
    collect = declared.collect
    scope_id = assignment.scope_id
    targets_repository = SourceCollectionRepository()
    checkpoints = ConnectorCheckpointRepository()
    sources = SourceRepository(BUILTIN_SOURCE_REGISTRY)

    def binding(target_id: str) -> ConnectorBinding:
        return ConnectorBinding(
            scope_id=scope_id,
            binding_id=target_id,
            connector_name=manifest.name,
            connector_version=TARGET_CHECKPOINT_VERSION,
        )

    async with AsyncExitStack() as resources:
        if database is None:
            database = await resources.enter_async_context(open_plugin_database(spec.database))
        from powercontext.builtin.authz.worker import open_worker_security

        security = await resources.enter_async_context(open_worker_security(spec.security, database))

        async def authorize(connection) -> None:
            if declared.scopes is not None and scope_id not in declared.scopes:
                raise CollectionContractError("the Scope is outside the plugin's scopes")  # noqa: TRY003
            if security is not None:
                await security.authorize_transaction(connection, scope_id)

        invocation = ScopeInvocation(assignment, authorize_transaction=authorize)
        try:
            async with database.transaction() as connection:
                await invocation.start(connection)
                due = await targets_repository.due(
                    connection, scope_id, SERVER_RUNNER, manifest.name, collect.max_targets
                )
                requested = tuple([
                    CollectTarget(
                        target_id=target.target_id,
                        parameters=target.parameters,
                        checkpoint=await checkpoints.load(connection, binding(target.target_id)),
                    )
                    for target in due
                ])
                # A result commits only for the configuration version it was collected under.
                versions = {target.target_id: target.config_version for target in due}
            if not requested:
                async with database.transaction() as connection:
                    await invocation.complete(connection, remaining_work=False)
                return ArtifactProcessingWorkerCompletion(noop=True)
            try:
                directory = Path(spec.directory)
                loaded, declaration = load_plugin(directory, manifest.name)
                if loaded != manifest or not isinstance(declaration, SourcePlugin) or declaration.setup is None:
                    raise ValueError("the plugin changed after the Server checked it")  # noqa: TRY003, TRY301
                async with AsyncExitStack() as plugin_resources:
                    from powercontext.builtin.plugins.storage import open_plugin_resources
                    from powercontext.sources.definitions import SourceDefinitionRegistry

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
                    collector = await declaration.setup(context)
                    result = await collector.collect(
                        CollectRequest(scope_id=scope_id, targets=requested, max_items=collect.max_items)
                    )
                    if not isinstance(result, CollectResult):
                        raise CollectionContractError("collect must return a CollectResult")  # noqa: TRY003, TRY301
                    known = {target.target_id: target for target in requested}
                    if set(result.targets) - set(known):
                        raise CollectionContractError("collect returned a target that was not requested")  # noqa: TRY003, TRY301
                    if sum(len(item.items) for item in result.targets.values()) > collect.max_items:
                        raise CollectionContractError("collect returned more items than max_items")  # noqa: TRY003, TRY301
                    registry = SourceDefinitionRegistry(declaration.definitions)
                    observed = {
                        target_id: [
                            await observe(registry, manifest.source_manifests, item.definition, item.value)
                            for item in outcome.items
                        ]
                        for target_id, outcome in result.targets.items()
                        if outcome.error_code is None
                    }
            except InvocationAlreadyHandled:
                raise
            except Exception as error:
                # The whole collection failed: record it on every target of this call, so they rotate behind
                # targets not yet attempted and the failure is visible, then let the Supervisor back off.
                await _record_failure(database, targets_repository, scope_id, versions, error)
                raise
            async with database.transaction() as connection:
                await invocation.guard(connection)
                for target_id, outcome in result.targets.items():
                    try:
                        await targets_repository.committable(
                            connection, scope_id, target_id, runner=SERVER_RUNNER, config_version=versions[target_id]
                        )
                    except SourceCollectionError:
                        # Changed, transferred, disabled, or deleted while it was collected: nothing of it commits.
                        continue
                    if outcome.error_code is not None:
                        await targets_repository.record_status(
                            connection,
                            scope_id,
                            target_id,
                            SERVER_RUNNER,
                            error_code=outcome.error_code,
                            error_message=outcome.error_message,
                        )
                        continue
                    for observation in observed[target_id]:
                        await sources.add(connection, scope_id, observation)
                    await checkpoints.save(
                        connection,
                        binding(target_id),
                        outcome.checkpoint,
                        expected=known[target_id].checkpoint,
                    )
                    await targets_repository.record_status(
                        connection, scope_id, target_id, SERVER_RUNNER, has_more=outcome.has_more
                    )
                remaining = any(outcome.has_more for outcome in result.targets.values())
                await invocation.complete(connection, remaining_work=remaining)
        except InvocationAlreadyHandled:
            return ArtifactProcessingWorkerCompletion(noop=True)
    return ArtifactProcessingWorkerCompletion(noop=False)


async def _record_failure(
    database: Any, repository: SourceCollectionRepository, scope_id: str, versions: dict[str, int], error: Exception
) -> None:
    """Mark every target of a failed collection as attempted, without masking the failure itself."""

    try:
        async with database.transaction() as connection:
            for target_id in versions:
                # Only the error type: its text may carry connection strings or paths.
                await repository.record_status(
                    connection,
                    scope_id,
                    target_id,
                    SERVER_RUNNER,
                    error_code="collect_failed",
                    error_message=type(error).__name__,
                )
    except Exception as status_error:
        log_safely(
            logger, logging.WARNING, f"Collection failure status was not recorded ({type(status_error).__name__})"
        )
