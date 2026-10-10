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

"""Frozen historical entry rows shared by migration and post-migration rebuild acceptance."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import insert, select

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.experience import ExperienceContent, ExperienceDraft
from powercontext.builtin.artifacts.memory.canonical import canonical_json, entry_content_hash, normalize_refs
from powercontext.builtin.inference import EmbeddingModel
from powercontext.builtin.persistence.atomic_memory_identity import legacy_entry_artifact_id
from powercontext.builtin.persistence.migrations.atomic_memory_v1 import (
    apply_atomic_memory_migration,
)
from powercontext.builtin.persistence.schema import create_tables
from powercontext.builtin.persistence.tables import (
    ARTIFACT_HEADS_TABLE,
    ARTIFACT_LINEAGE_ARTIFACTS_TABLE,
    ARTIFACT_LINEAGE_SOURCES_TABLE,
    ARTIFACTS_TABLE,
    MEMORY_ENTRY_VERSIONS_TABLE,
    SOURCES_TABLE,
)
from powercontext.builtin.runtime import BuiltinConfig, open_builtin_contexts
from powercontext.builtin.runtime.config import DatabaseConfig
from powercontext.builtin.sources.content import ContentSource, ContentSourceInternal, ContentSourceTarget
from powercontext.server.authz import ArtifactOwnerRelation, MemoryEntrySelector, PrincipalRef, ResourceRef
from powercontext.server.authz.repository import RelationalAccessRepository
from powercontext.sources import SourceMaterialization, SourceRef
from tests.e2e.atomic_memory_migration_backend import migration_index, migration_profile
from tests.legacy_memory import add_legacy_citation_columns

SCOPE = "migration-evidence"
COLLECTION = "legacy-memory"
A = SourceRef(source_type="content", source_id="task-a")
B = SourceRef(source_type="content", source_id="task-b")
C = SourceRef(source_type="content", source_id="task-c")
INTERNAL = SourceRef(source_type="content", source_id="legacy-entry-write")
INTERNAL_TEXT = "This legacy write receipt is provenance, not task evidence."
EA = ArtifactRef(family="experience", artifact_id="task-a-experience", revision=1)
EB = ArtifactRef(family="experience", artifact_id="task-b-experience", revision=1)


def _collection(revision: int) -> dict[str, Any]:
    """A legacy collection reference; today's ArtifactRef rejects this family."""

    return {"family": "memory", "artifact_id": COLLECTION, "revision": revision}


def _atomic(entry_id: str, revision: int) -> ArtifactRef:
    return ArtifactRef(
        family="atomic-memory",
        artifact_id=legacy_entry_artifact_id(SCOPE, COLLECTION, entry_id),
        revision=revision,
    )


def _version(
    entry_id: str, version: int, text: str, sources: tuple[SourceRef, ...], artifacts: tuple[ArtifactRef, ...]
) -> dict[str, Any]:
    refs = normalize_refs(tuple(source.model_dump(mode="json") for source in sources))
    artifact_refs = normalize_refs(tuple(artifact.model_dump(mode="json") for artifact in artifacts))
    return {
        "entry_id": entry_id,
        "entry_version_id": f"{entry_id}-v{version}",
        "version": version,
        "previous_version_id": None if version == 1 else f"{entry_id}-v{version - 1}",
        "kind": "fact",
        "text": text,
        "source_refs": canonical_json(refs),
        "artifact_refs": canonical_json(artifact_refs),
        "entry_content_hash": entry_content_hash(kind="fact", text=text, source_refs=refs, artifact_refs=artifact_refs),
        "created_in_revision": version,
    }


async def _seed(config: DatabaseConfig, extra=None, *, embedding_model: EmbeddingModel | None = None) -> bytes:
    alpha = _version("alpha", 1, "Task A was completed.", (A, INTERNAL), (EA,))
    beta = _version("beta", 1, "Unrelated task B was completed.", (B,), (EB,))
    revised = _version("alpha", 2, "Tasks A and C were completed.", (A, C, INTERNAL), (EA,))
    async with open_builtin_contexts(BuiltinConfig(database=config), embedding_model=embedding_model) as contexts:
        await contexts.get(SCOPE)
        async with contexts.database.transaction() as connection:
            await create_tables(connection, (MEMORY_ENTRY_VERSIONS_TABLE,))
            await add_legacy_citation_columns(connection)
            for source in (A, B, C):
                await contexts.repositories.sources.add(
                    connection,
                    SCOPE,
                    ContentSource(
                        name=source.source_id,
                        materialization=SourceMaterialization.CAPTURED,
                        content=f"Observed task evidence {source.source_id}.",
                    ),
                )
            await contexts.repositories.sources.add(
                connection,
                SCOPE,
                ContentSource(
                    name=INTERNAL.source_id,
                    materialization=SourceMaterialization.CAPTURED,
                    content=INTERNAL_TEXT,
                    internal=ContentSourceInternal(
                        role="lineage_only",
                        operation="artifact_create",
                        target=ContentSourceTarget(scope_id=SCOPE, family="memory", artifact_id=COLLECTION, revision=1),
                    ),
                ),
            )
            for ref, source in ((EA, A), (EB, B)):
                await contexts.repositories.artifacts.create(
                    connection,
                    SCOPE,
                    ref.artifact_id,
                    ExperienceDraft(
                        content=ExperienceContent(
                            situation=f"Task {source.source_id} was requested.",
                            action=f"Executed task {source.source_id}.",
                            outcome=f"Observed task {source.source_id} completion.",
                            lesson=f"Preserve the exact evidence of task {source.source_id}.",
                        ),
                        sources=(source,),
                    ),
                )
            for revision, entries, sources in (
                (1, (alpha, beta), (A, B, INTERNAL)),
                (2, (revised, beta), (C,)),
            ):
                changes = [
                    {
                        "op": "add" if revision == 1 else "revise",
                        "entry_id": entry["entry_id"],
                        "from_entry_version_id": entry["previous_version_id"],
                        "to_entry_version_id": entry["entry_version_id"],
                        "reason": None,
                    }
                    for entry in entries
                    if entry["created_in_revision"] == revision
                ]
                content = {
                    "schema": "powercontext.memory.v1",
                    "manifest": {
                        "format": "flat-v1",
                        "entries": [
                            {key: entry[key] for key in ("entry_id", "entry_version_id", "entry_content_hash")}
                            | {"state": "active"}
                            for entry in entries
                        ],
                    },
                    "changes": changes,
                }
                # Immutable legacy bytes bypass current collection-write rejection.
                await connection.execute(
                    insert(ARTIFACTS_TABLE).values(
                        scope_id=SCOPE,
                        family="memory",
                        artifact_id=COLLECTION,
                        revision=revision,
                        content=json.dumps(content).encode(),
                    )
                )
                await connection.execute(
                    insert(ARTIFACT_LINEAGE_SOURCES_TABLE),
                    [
                        {
                            "scope_id": SCOPE,
                            "family": "memory",
                            "artifact_id": COLLECTION,
                            "revision": revision,
                            "ordinal": ordinal,
                            "source_type": source.source_type,
                            "source_id": source.source_id,
                        }
                        for ordinal, source in enumerate(sources)
                    ],
                )
                if revision == 1:
                    await connection.execute(
                        insert(ARTIFACT_LINEAGE_ARTIFACTS_TABLE),
                        [
                            {
                                "scope_id": SCOPE,
                                "family": "memory",
                                "artifact_id": COLLECTION,
                                "revision": revision,
                                "ordinal": ordinal,
                                "upstream_family": ref.family,
                                "upstream_artifact_id": ref.artifact_id,
                                "upstream_revision": ref.revision,
                            }
                            for ordinal, ref in enumerate((EA, EB))
                        ],
                    )
            await connection.execute(
                insert(ARTIFACT_HEADS_TABLE).values(scope_id=SCOPE, family="memory", artifact_id=COLLECTION, revision=2)
            )
            await connection.execute(
                insert(MEMORY_ENTRY_VERSIONS_TABLE),
                [
                    {"scope_id": SCOPE, "family": "memory", "memory_artifact_id": COLLECTION, **entry}
                    for entry in (alpha, beta, revised)
                ],
            )
            access = RelationalAccessRepository(contexts.database, connection=connection)
            for entry_id in ("alpha", "beta"):
                await access.establish_artifact_owner(
                    ArtifactOwnerRelation(
                        resource=ResourceRef.artifact(
                            SCOPE,
                            family="memory",
                            artifact_id=COLLECTION,
                            selector=MemoryEntrySelector(entry_id=entry_id),
                        ),
                        owner=PrincipalRef(type="service", id="migration-owner"),
                        established_at=datetime(2026, 1, 1, tzinfo=UTC),
                        policy_revision="pending",
                        idempotency_key=f"owner:{entry_id}",
                    )
                )
            if extra is not None:
                await extra(contexts, connection)
            internal_bytes = await connection.scalar(
                select(SOURCES_TABLE.c.payload).where(
                    SOURCES_TABLE.c.scope_id == SCOPE, SOURCES_TABLE.c.source_id == INTERNAL.source_id
                )
            )
            assert isinstance(internal_bytes, bytes)
    return internal_bytes


async def _migrate(config: DatabaseConfig, decisions=None):
    async with migration_profile(config) as profile:
        return await apply_atomic_memory_migration(
            profile.database, migration_index(config), maintenance_confirmed=True, decisions=decisions
        )


async def _seed_and_migrate(config: DatabaseConfig, extra=None) -> bytes:
    internal_bytes = await _seed(config, extra)
    result = await _migrate(config)
    assert result.ready, result.errors
    return internal_bytes
