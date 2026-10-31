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

"""Execute validated Artifact plugins in an owned generation Worker."""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import AsyncExitStack
from dataclasses import dataclass
from typing import Any

from jsonschema import Draft202012Validator
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext._logging import log_safely
from powercontext.artifact_plugins import (
    ArtifactDraft,
    GenerationBatch,
    GenerationContext,
    GenerationSource,
)
from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifact_plugin_validation import (
    _MAX_INPUT_BYTES,
    _MAX_OUTPUT_BYTES,
    _PluginManifest,
    code_fingerprint,
    describe_module,
    load_plugin,
)
from powercontext.builtin.persistence.artifacts import RepositoryArtifactDraft
from powercontext.builtin.persistence.cursors import SourceCursorRepository
from powercontext.builtin.persistence.custom_artifacts import (
    StoredArtifactContent,
    custom_artifact_repository,
)
from powercontext.builtin.persistence.generation_sources import GenerationSourceAccess
from powercontext.builtin.persistence.sources import SourceRepository
from powercontext.builtin.persistence.tables import (
    ARTIFACT_FAMILY_SCHEMAS_TABLE,
    ARTIFACT_PROCESSING_BINDING_STATES_TABLE,
)
from powercontext.builtin.runtime.artifact_reads import ArtifactReadSession
from powercontext.builtin.runtime.config import BuiltinConfig, DatabaseConfig, InferenceConfig, RuntimeConfig
from powercontext.builtin.runtime.processing_contracts import (
    ArtifactProcessingWorkAssignment,
    ArtifactProcessingWorkerCompletion,
)
from powercontext.builtin.runtime.processing_execution import InvocationAlreadyHandled, ScopeInvocation
from powercontext.builtin.sources import BUILTIN_SOURCE_REGISTRY, SourceCursor
from powercontext.sources import SourceProjectionKey

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ArtifactPluginWorkerSpec:
    database: DatabaseConfig
    binding_name: str
    generation: int
    definition: dict[str, Any]
    security: dict[str, Any] | None = None
    inference_config: Any = None
    runtime_config: Any = None


async def require_current_binding(connection: AsyncConnection, spec: ArtifactPluginWorkerSpec, scope_id: str) -> None:
    table = ARTIFACT_PROCESSING_BINDING_STATES_TABLE
    locked = await connection.execute(
        update(table)
        .where(
            table.c.binding_name == spec.binding_name,
            table.c.binding_generation == spec.generation,
            table.c.phase == "enabled",
        )
        .values(binding_generation=table.c.binding_generation)
    )
    scope_ids = spec.definition["manifest"]["scope_ids"]
    if locked.rowcount != 1 or (scope_ids is not None and scope_id not in scope_ids):
        raise ValueError("plugin invocation has been revoked")  # noqa: TRY003


def run_artifact_plugin_worker(
    spec: ArtifactPluginWorkerSpec,
    assignment: ArtifactProcessingWorkAssignment,
) -> ArtifactProcessingWorkerCompletion:
    return asyncio.run(_process_window(spec, assignment))


async def _restore_access_families(database) -> None:
    from powercontext.server.authz.profiles import register_custom_artifact_access_family

    async with database.transaction() as connection:
        families = (await connection.execute(select(ARTIFACT_FAMILY_SCHEMAS_TABLE.c.family).distinct())).scalars().all()
    for family in families:
        register_custom_artifact_access_family(family)


async def _process_window(spec: ArtifactPluginWorkerSpec, assignment: ArtifactProcessingWorkAssignment):  # noqa: C901 - One authorized window with preparation outside its commit.
    # Server authorization is reconstructed in this child using deployment identity.
    from powercontext.server.processing_security import open_worker_security

    definition = spec.definition
    declaration = definition["declaration"]
    if assignment.binding_name != spec.binding_name or assignment.artifact_family != declaration["family"]:
        raise ValueError("assignment does not match the registered plugin")  # noqa: TRY003
    sources = SourceRepository(BUILTIN_SOURCE_REGISTRY)
    cursors = SourceCursorRepository()
    async with AsyncExitStack() as resources:
        from powercontext.builtin.persistence.artifact_indexes import ArtifactProjectionPublisher
        from powercontext.builtin.runtime.composition import (
            _embedding_models,
            compose_artifact_search,
            open_builtin_contexts,
        )

        embedding = None
        if spec.inference_config is not None:
            embedding, _ = await _embedding_models(spec.inference_config, resources, None)
        config = BuiltinConfig(
            database=spec.database,
            inference=spec.inference_config or InferenceConfig(),
            runtime=spec.runtime_config or RuntimeConfig(),
        )
        storage = await resources.enter_async_context(
            open_builtin_contexts(config, embedding_model=embedding, _topic_memory_worker=True)
        )
        search_service, _ = compose_artifact_search(storage, embedding)
        publisher = ArtifactProjectionPublisher(storage.database, embedding)
        await _restore_access_families(storage.database)
        security = await resources.enter_async_context(open_worker_security(spec.security, storage.database))

        async def authorize(connection):
            await require_current_binding(connection, spec, assignment.scope_id)
            if security is not None:
                await security.authorize_transaction(connection, assignment.scope_id)

        invocation = ScopeInvocation(assignment, authorize_transaction=authorize)
        try:
            async with storage.database.transaction() as connection:
                await invocation.start(connection)
                cursor = await cursors.load(connection, assignment.scope_id, spec.binding_name)
                after = 0 if cursor is None else cursor.cursor.sequence
                high = await sources.journal_position(connection, assignment.scope_id)
                through = min(high, after + definition["manifest"]["max_sources"])
                rows = await GenerationSourceAccess(sources).list_window_for_generation(
                    connection,
                    assignment.scope_id,
                    after=after,
                    through=through,
                )
            # Desired index builds belong to maintenance. Generation prepares only the
            # current serving projections, so an unavailable future index cannot block writes.
            projections = {
                name: SourceProjectionKey.model_validate(key) for name, key in declaration["projections"].items()
            }
            batch = GenerationBatch(
                sources=tuple(
                    GenerationSource(
                        ref=row.ref,
                        projection=projections[row.ref.source_type],
                        value=BUILTIN_SOURCE_REGISTRY.project(row.value, projections[row.ref.source_type]),
                    )
                    for row in rows
                    if row.ref.source_type in projections
                )
            )
            if (
                len(
                    json.dumps(
                        [source.model_dump(mode="json") for source in batch.sources],
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ).encode()
                )
                > _MAX_INPUT_BYTES
            ):
                raise ValueError("input evidence exceeds the window budget")  # noqa: TRY003
            source_evidence = frozenset((source.ref.source_type, source.ref.source_id) for source in batch.sources)
            artifact_evidence = ArtifactReadSession(
                storage.database,
                assignment.scope_id,
                {declaration["family"], *declaration.get("artifact_families", ())},
                access=None if security is None else security.access,
                principal=None if security is None else security.principal,
                audit=None if security is None else security.context,
                guard=invocation.guard,
                search_service=search_service,
            )

            drafts = []
            if batch.sources:
                if code_fingerprint(definition["directory"]) != declaration["fingerprint"]:
                    raise ValueError("plugin files changed after registration")  # noqa: TRY003
                manifest = _PluginManifest.model_validate(definition["manifest"])
                module = load_plugin(definition["directory"], manifest)
                if describe_module(module, manifest, definition["directory"]) != {
                    "manifest": definition["manifest"],
                    "declaration": declaration,
                }:
                    raise ValueError("Worker declaration differs from the registered definition")  # noqa: TRY003
                ctx = GenerationContext(
                    assignment.scope_id,
                    resources,
                    artifact_evidence.read,
                    list_artifacts=artifact_evidence.list,
                    search_artifacts=artifact_evidence.search,
                )
                pipeline = await module.create_pipeline(
                    ctx, module.Config.model_validate_json(json.dumps(manifest.config, ensure_ascii=False))
                )
                drafts = await pipeline.generate(batch)
            validated = _validate_drafts(declaration, source_evidence, artifact_evidence.refs, drafts)
            prepared = {}
            for draft in validated:
                prepared[draft.artifact_id] = await publisher.prepare(
                    assignment.scope_id, draft.family, draft.schema_version, draft.content
                )
            await _commit_window(
                storage.database,
                spec,
                assignment,
                declaration,
                invocation,
                sources,
                cursor,
                through,
                source_evidence,
                artifact_evidence.refs,
                drafts,
                security,
                prepared,
            )
        except InvocationAlreadyHandled:
            pass
    return ArtifactProcessingWorkerCompletion()


def _validate_drafts(declaration, source_evidence, artifact_evidence, drafts) -> list[ArtifactDraft]:
    if not isinstance(drafts, list):
        raise TypeError("plugin must return a list of drafts")  # noqa: TRY003
    validated = [ArtifactDraft.model_validate(draft) for draft in drafts]
    if (
        len(json.dumps([draft.model_dump(mode="json") for draft in validated], ensure_ascii=False).encode())
        > _MAX_OUTPUT_BYTES
    ):
        raise ValueError("output exceeds the window budget")  # noqa: TRY003
    validator = Draft202012Validator(declaration["content_schema"])
    identities = set()
    for draft in validated:
        if (draft.family, draft.schema_version) != (declaration["family"], declaration["schema_version"]):
            raise ValueError("draft does not match the registered family/schema")  # noqa: TRY003
        if draft.artifact_id in identities or not {
            (ref.source_type, ref.source_id) for ref in draft.source_refs
        }.issubset(source_evidence):
            raise ValueError("duplicate artifact or Source evidence outside this invocation")  # noqa: TRY003
        if not {(ref.family, ref.artifact_id, ref.revision) for ref in draft.artifact_refs}.issubset(artifact_evidence):
            raise ValueError("Artifact evidence was not read through this invocation")  # noqa: TRY003
        if (
            draft.expected_revision > 0
            and (draft.family, draft.artifact_id, draft.expected_revision) not in artifact_evidence
        ):
            raise ValueError("update revision must have been read through this invocation")  # noqa: TRY003
        identities.add(draft.artifact_id)
        validator.validate(draft.content)
    return validated


async def _commit_window(
    database,
    spec,
    assignment,
    declaration,
    invocation,
    sources,
    cursor,
    through,
    source_evidence,
    artifact_evidence,
    drafts,
    security,
    prepared_indexes=None,
):
    from powercontext.server.authz import AccessAction, ResourceRef

    validated = _validate_drafts(declaration, source_evidence, artifact_evidence, drafts)
    repository = custom_artifact_repository(declaration["family"], sources, prepared=prepared_indexes)
    async with database.transaction() as connection:
        # The Worker guard rechecks Scope contribution, which includes evidence reads.
        await invocation.guard(connection)
        for draft in validated:
            resource = ResourceRef.artifact(assignment.scope_id, family=draft.family, artifact_id=draft.artifact_id)
            bound = None if security is None else security.access.with_connection(connection)
            prepared = RepositoryArtifactDraft(
                family=draft.family,
                content=StoredArtifactContent(schema_version=draft.schema_version, content=draft.content),
                sources=draft.source_refs,
                artifacts=draft.artifact_refs,
            )
            if draft.expected_revision == 0:
                await repository.create(connection, assignment.scope_id, draft.artifact_id, prepared)
                if bound is not None:
                    await bound.establish_artifact_owner(
                        resource,
                        security.principal,
                        idempotency_key=f"background-custom-owner:{assignment.scope_id}:{draft.family}:{draft.artifact_id}",
                        context=security.context,
                    )
            else:
                if bound is not None:
                    await bound.require(
                        security.principal, AccessAction.ARTIFACT_WRITE, resource, context=security.context
                    )
                previous = await repository.get(
                    connection,
                    assignment.scope_id,
                    ArtifactRef(family=draft.family, artifact_id=draft.artifact_id, revision=draft.expected_revision),
                )
                await repository.revise(connection, assignment.scope_id, previous, prepared)
        if through > (0 if cursor is None else cursor.cursor.sequence):
            await SourceCursorRepository().save(
                connection,
                assignment.scope_id,
                spec.binding_name,
                SourceCursor(sequence=through),
                expected_generation=None if cursor is None else cursor.generation,
            )
        high = await sources.journal_position(connection, assignment.scope_id)
        await invocation.complete(connection, remaining_work=high > through)
    log_safely(logger, logging.DEBUG, "Artifact plugin window committed")
