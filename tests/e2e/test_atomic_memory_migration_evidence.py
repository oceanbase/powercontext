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

"""Exact entry evidence survives frozen collection migration and reuse."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import insert, select, tuple_

from powercontext.artifacts import ArtifactLineage, ArtifactRef, MemoryCitation
from powercontext.builtin.artifacts.atomic_memory import AtomicMemoryContent
from powercontext.builtin.artifacts.atomic_memory.extraction import (
    AtomicMemoryCandidate,
    AtomicMemoryExtractionOutput,
    AtomicMemoryGenerationPipeline,
)
from powercontext.builtin.artifacts.atomic_memory.reconciliation import (
    AtomicMemoryArtifactEvidence,
    AtomicMemoryReconciliationOutput,
)
from powercontext.builtin.artifacts.experience import ExperienceContent, ExperienceDraft
from powercontext.builtin.artifacts.memory import MemoryService
from powercontext.builtin.artifacts.memory.canonical import canonical_json, entry_content_hash, normalize_refs
from powercontext.builtin.evidence.resolver import EvidenceResolver, evidence_id
from powercontext.builtin.evidence.selection import select_evidence
from powercontext.builtin.inference import GenerationResult, character_token_estimator
from powercontext.builtin.persistence.atomic_memory_identity import legacy_entry_artifact_id
from powercontext.builtin.persistence.memory import RelationalMemoryBackend
from powercontext.builtin.persistence.migrations.atomic_memory_v1 import (
    apply_atomic_memory_migration,
    verify_atomic_memory_migration,
)
from powercontext.builtin.persistence.schema import create_tables
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.persistence.sqlite.atomic_memory_index import SQLiteAtomicMemoryIndex
from powercontext.builtin.persistence.tables import (
    ARTIFACT_HEADS_TABLE,
    ARTIFACT_LINEAGE_ARTIFACTS_TABLE,
    ARTIFACT_LINEAGE_SOURCES_TABLE,
    ARTIFACTS_TABLE,
    MEMORY_ENTRY_VERSIONS_TABLE,
    SOURCES_TABLE,
)
from powercontext.builtin.runtime import BuiltinConfig, RuntimeConfig, open_builtin_contexts
from powercontext.builtin.sources.content import ContentSource, ContentSourceInternal, ContentSourceTarget
from powercontext.server.authz import ArtifactOwnerRelation, MemoryEntrySelector, ResourceRef
from powercontext.server.authz.repository import RelationalAccessRepository
from powercontext.sources import SourceMaterialization, SourceRef

SCOPE = "migration-evidence"
COLLECTION = "legacy-memory"
A = SourceRef(source_type="content", source_id="task-a")
B = SourceRef(source_type="content", source_id="task-b")
C = SourceRef(source_type="content", source_id="task-c")
INTERNAL = SourceRef(source_type="content", source_id="legacy-entry-write")
INTERNAL_TEXT = "This legacy write receipt is provenance, not task evidence."
EA = ArtifactRef(family="experience", artifact_id="task-a-experience", revision=1)
EB = ArtifactRef(family="experience", artifact_id="task-b-experience", revision=1)


def _collection(revision: int) -> ArtifactRef:
    return ArtifactRef(family="memory", artifact_id=COLLECTION, revision=revision)


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


async def _seed_and_migrate(config: SQLiteConfig) -> bytes:
    alpha = _version("alpha", 1, "Task A was completed.", (A, INTERNAL), (EA,))
    beta = _version("beta", 1, "Unrelated task B was completed.", (B,), (EB,))
    revised = _version("alpha", 2, "Tasks A and C were completed.", (A, C, INTERNAL), (EA,))
    async with open_builtin_contexts(BuiltinConfig(database=config)) as contexts:
        await contexts.get(SCOPE)
        async with contexts.database.transaction() as connection:
            await create_tables(connection, (MEMORY_ENTRY_VERSIONS_TABLE,))
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
                        memory_citations=None,
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
                        owner=contexts.atomic_memory.default_context.principal,
                        established_at=datetime(2026, 1, 1, tzinfo=UTC),
                        policy_revision="pending",
                        idempotency_key=f"owner:{entry_id}",
                    )
                )
            internal_bytes = await connection.scalar(
                select(SOURCES_TABLE.c.payload).where(
                    SOURCES_TABLE.c.scope_id == SCOPE, SOURCES_TABLE.c.source_id == INTERNAL.source_id
                )
            )
            assert isinstance(internal_bytes, bytes)
    async with SQLiteProfile.open(config, tables=()) as profile:
        result = await apply_atomic_memory_migration(
            profile.database, SQLiteAtomicMemoryIndex(), maintenance_confirmed=True
        )
        assert result.ready, result.errors
    return internal_bytes


@pytest.fixture
def migrated(tmp_path: Path) -> tuple[SQLiteConfig, bytes]:
    config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'migration-evidence.db'}")
    return config, asyncio.run(_seed_and_migrate(config))


def _resolver(contexts) -> EvidenceResolver:
    async def read_memory(connection, citation):
        service = MemoryService(
            backend=RelationalMemoryBackend(
                database=contexts.database,
                scope_id=SCOPE,
                artifacts=contexts.repositories.artifacts,
                connection=connection,
            )
        )
        return await service.validate_citation(citation)

    return EvidenceResolver(
        scope_id=SCOPE,
        sources=contexts.repositories.sources,
        artifacts=contexts.repositories.artifacts,
        memory_reader=read_memory,
    )


def _projected_sources(resolved) -> tuple[SourceRef, ...]:
    projected = {item.evidence_id for item in resolved.projection.evidence if item.kind == "source"}
    return tuple(
        sorted(
            (node.source for node in resolved.manifest.nodes if node.evidence_id in projected),
            key=lambda source: source.model_dump_json(),
        )
    )


def _projected_artifacts(resolved) -> tuple[ArtifactRef, ...]:
    projected = {item.evidence_id for item in resolved.projection.evidence}
    return tuple(
        node.artifact for node in resolved.manifest.nodes if node.evidence_id in projected and node.artifact is not None
    )


async def _imported_snapshot(connection):
    identities = tuple(
        (ref.artifact_id, ref.revision) for ref in (_atomic("alpha", 1), _atomic("alpha", 2), _atomic("beta", 1))
    )
    snapshot = {}
    for table in (ARTIFACTS_TABLE, ARTIFACT_LINEAGE_SOURCES_TABLE, ARTIFACT_LINEAGE_ARTIFACTS_TABLE):
        statement = (
            select(table)
            .where(
                table.c.scope_id == SCOPE,
                table.c.family == "atomic-memory",
                tuple_(table.c.artifact_id, table.c.revision).in_(identities),
            )
            .order_by(table.c.artifact_id, table.c.revision)
        )
        if "ordinal" in table.c:
            statement = statement.order_by(table.c.ordinal)
        snapshot[table.name] = tuple(tuple(row) for row in (await connection.execute(statement)).all())
    return snapshot


@pytest.mark.parametrize(
    ("entry_id", "revision", "expected"), [("alpha", 1, (A,)), ("beta", 1, (B,)), ("alpha", 2, (A, C))]
)
def test_migrated_entry_projects_only_its_exact_sources(migrated, entry_id, revision, expected) -> None:
    async def scenario() -> None:
        async with (
            open_builtin_contexts(BuiltinConfig(database=migrated[0])) as contexts,
            contexts.database.transaction() as connection,
        ):
            selected = _atomic(entry_id, revision)
            resolved = await _resolver(contexts).resolve(connection, artifacts=(selected,))
            assert _projected_sources(resolved) == expected
            own, unrelated = (EA, EB) if entry_id == "alpha" else (EB, EA)
            assert own in _projected_artifacts(resolved)
            assert unrelated not in _projected_artifacts(resolved)
            stored = await contexts.repositories.artifacts.get(connection, SCOPE, selected)
            assert own in stored.lineage.artifacts
            assert _collection(revision) in stored.lineage.artifacts
            if revision == 2:
                assert _atomic(entry_id, 1) in stored.lineage.artifacts
                assert _atomic(entry_id, 1) in _projected_artifacts(resolved)

    asyncio.run(scenario())


@pytest.mark.parametrize(("revision", "expected"), [(1, (A,)), (2, (A, C))])
def test_legacy_citation_preserves_exact_entry_sources(migrated, revision, expected) -> None:
    async def scenario() -> None:
        async with (
            open_builtin_contexts(BuiltinConfig(database=migrated[0])) as contexts,
            contexts.database.transaction() as connection,
        ):
            citation = MemoryCitation(
                memory_ref=_collection(revision), entry_id="alpha", entry_version_id=f"alpha-v{revision}"
            )
            resolved = await _resolver(contexts).resolve(connection, memory_citations=(citation,))
            assert _projected_sources(resolved) == expected
            assert EA in _projected_artifacts(resolved)
            assert EB not in _projected_artifacts(resolved)

    asyncio.run(scenario())


def test_legacy_entry_write_source_retains_target_and_stays_out_of_projection(migrated) -> None:
    async def scenario() -> None:
        async with (
            open_builtin_contexts(BuiltinConfig(database=migrated[0])) as contexts,
            contexts.database.transaction() as connection,
        ):
            stored = (await contexts.repositories.sources.get(connection, SCOPE, INTERNAL)).value
            assert isinstance(stored, ContentSource) and stored.internal is not None
            assert stored.internal.target == ContentSourceTarget(
                scope_id=SCOPE, family="memory", artifact_id=COLLECTION, revision=1
            )
            payload = await connection.scalar(
                select(SOURCES_TABLE.c.payload).where(
                    SOURCES_TABLE.c.scope_id == SCOPE, SOURCES_TABLE.c.source_id == INTERNAL.source_id
                )
            )
            assert payload == migrated[1]
            resolved = await _resolver(contexts).resolve(connection, artifacts=(_atomic("alpha", 2),))
            receipt = next(node for node in resolved.manifest.nodes if node.source == INTERNAL)
            assert receipt.role == "lineage_only"
            assert all(item.evidence_id != receipt.evidence_id for item in resolved.projection.evidence)
            assert all(INTERNAL_TEXT not in item.text for item in resolved.projection.evidence)

    asyncio.run(scenario())


def test_merge_keeps_migrated_input_history_without_unrelated_collection_sources(migrated) -> None:
    async def scenario() -> None:
        async with open_builtin_contexts(BuiltinConfig(database=migrated[0])) as contexts:
            memory = contexts.atomic_memory.for_scope(SCOPE)
            alpha = await memory.get(_atomic("alpha", 2).artifact_id)
            (created,) = await contexts.records.create_atomic_memories(
                SCOPE, ({"kind": "fact", "text": "Another standalone fact."},)
            )
            other = await memory.get(created.artifact_id)
            merged = await memory.merge(
                (alpha.as_read(), other.as_read()), AtomicMemoryContent(kind="fact", text="Merged task evidence.")
            )
            async with contexts.database.transaction() as connection:
                resolved = await _resolver(contexts).resolve(connection, artifacts=(merged.primary.ref,))
                assert _projected_sources(resolved) == (A, C)
                assert any(node.artifact == alpha.ref and node.historical for node in resolved.manifest.nodes)

    asyncio.run(scenario())


def test_restoration_of_migrated_history_does_not_add_unrelated_sources(migrated) -> None:
    async def scenario() -> None:
        async with open_builtin_contexts(BuiltinConfig(database=migrated[0])) as contexts:
            async with contexts.database.transaction() as connection:
                imported = await _imported_snapshot(connection)
            restored = await contexts.atomic_memory.for_scope(SCOPE).restore(
                _atomic("alpha", 2).artifact_id, revision=1
            )
            assert restored.primary.artifact.content.text == "Task A was completed."
            async with contexts.database.transaction() as connection:
                resolved = await _resolver(contexts).resolve(connection, artifacts=(restored.primary.ref,))
                # Restoration retains both exact current and selected historical revisions.
                assert _projected_sources(resolved) == (A, C)
                assert any(node.artifact == _atomic("alpha", 1) for node in resolved.manifest.nodes)
                report = await verify_atomic_memory_migration(connection, index=contexts.atomic_memory.index)
                assert report.ready, report.errors
                assert await _imported_snapshot(connection) == imported
            repeated = await apply_atomic_memory_migration(
                contexts.database, contexts.atomic_memory.index, maintenance_confirmed=True
            )
            assert repeated.ready, repeated.errors
            async with contexts.database.transaction() as connection:
                assert await _imported_snapshot(connection) == imported

    asyncio.run(scenario())


def test_undo_merge_preserves_each_migrated_entry_evidence(migrated) -> None:
    async def scenario() -> None:
        async with open_builtin_contexts(BuiltinConfig(database=migrated[0])) as contexts:
            memory = contexts.atomic_memory.for_scope(SCOPE)
            alpha = await memory.get(_atomic("alpha", 2).artifact_id)
            beta = await memory.get(_atomic("beta", 1).artifact_id)
            merged = await memory.merge(
                (alpha.as_read(), beta.as_read()), AtomicMemoryContent(kind="fact", text="Both task facts.")
            )
            await memory.restore(merged.primary.ref.artifact_id, operation="undo_merge")
            async with contexts.database.transaction() as connection:
                for original, expected in ((alpha, (A, C)), (beta, (B,))):
                    current = await memory.get(original.ref.artifact_id)
                    assert current.state.state == "active"
                    resolved = await _resolver(contexts).resolve(connection, artifacts=(current.ref,))
                    assert _projected_sources(resolved) == expected

    asyncio.run(scenario())


@pytest.mark.parametrize("other_origin", ["collection", "experience"])
def test_other_origin_does_not_broaden_migrated_entry_root_groups_or_selection(migrated, other_origin) -> None:
    async def scenario() -> None:
        async with (
            open_builtin_contexts(BuiltinConfig(database=migrated[0])) as contexts,
            contexts.database.transaction() as connection,
        ):
            other = _collection(1)
            if other_origin == "experience":
                experience = await contexts.repositories.artifacts.create(
                    connection,
                    SCOPE,
                    "whole-collection-experience",
                    ExperienceDraft(
                        content=ExperienceContent(
                            situation="Two tasks completed.",
                            action="Reviewed the task collection.",
                            outcome="Both results were recorded.",
                            lesson="Keep both task sources for this collection-wide judgment.",
                        ),
                        sources=(B,),
                        artifacts=(other,),
                    ),
                )
                other = experience.as_ref()
            selected = _atomic("alpha", 1)
            resolved = await _resolver(contexts).resolve(connection, artifacts=(selected, other))
            assert _projected_sources(resolved) == (A, B)
            alpha = next(item for item in resolved.projection.evidence if item.evidence_id == evidence_id(selected))
            groups = {group.group_id: group for group in resolved.projection.root_groups}
            assert tuple(source for group_id in alpha.root_group_ids for source in groups[group_id].sources) == (A,)
            chosen = select_evidence(resolved.manifest, (alpha.evidence_id,), skill=False, target=None)
            assert chosen.sources == (A,)
            assert chosen.artifacts == (selected,)

    asyncio.run(scenario())


def test_ordinary_atomic_explicit_collection_reference_keeps_collection_evidence(migrated) -> None:
    async def scenario() -> None:
        async with open_builtin_contexts(BuiltinConfig(database=migrated[0])) as contexts:
            application = contexts.atomic_memory
            async with contexts.database.transaction() as connection:
                plan = await application.service.inspect_change(
                    connection,
                    SCOPE,
                    "ordinary-collection-memory",
                    AtomicMemoryContent(kind="fact", text="Both tasks completed."),
                    application.default_context,
                    lineage=ArtifactLineage(artifacts=(_collection(1),)),
                )
            prepared = await application.service.prepare_change(plan)
            async with contexts.database.transaction() as connection:
                created = await application.service.commit(connection, prepared, application.default_context)
            async with contexts.database.transaction() as connection:
                resolved = await _resolver(contexts).resolve(connection, artifacts=(created.primary.ref,))
                assert _projected_sources(resolved) == (A, B)
                assert EA in _projected_artifacts(resolved) and EB in _projected_artifacts(resolved)

    asyncio.run(scenario())


class _TriggerExtractor:
    async def generate(self, request):
        return GenerationResult(
            output=AtomicMemoryExtractionOutput(
                candidates=tuple(
                    AtomicMemoryCandidate(
                        kind="fact", text="Tasks A and C were completed.", evidence_ids=(item.evidence_id,)
                    )
                    for item in request.evidence
                    if item.source_ref.source_id == "migration-trigger"
                )
            )
        )


class _CaptureReconciler:
    def __init__(self):
        self.requests = []

    async def generate(self, request):
        self.requests.append(request)
        related = next(
            (
                item
                for item in request.related
                if any(original.ref == _atomic("alpha", 2) for original in item.original_refs)
            ),
            None,
        )
        return GenerationResult(
            output=AtomicMemoryReconciliationOutput(
                action="create" if related is None else "noop",
                compared_ids=tuple(item.item_id for item in request.related),
                target_ids=() if related is None else (related.item_id,),
                content=AtomicMemoryContent(kind="fact", text=request.proposal.text) if related is None else None,
                evidence_ids=request.proposal.evidence_ids if related is None else (),
                reason="Retain the exact existing fact without altering its historical evidence.",
            )
        )


def test_source_flush_supplies_the_migrated_related_entry_current_content(migrated) -> None:
    reconciler = _CaptureReconciler()
    pipeline = AtomicMemoryGenerationPipeline(
        extractor=_TriggerExtractor(), reconciler=reconciler, estimator=character_token_estimator()
    )

    async def scenario() -> None:
        async with open_builtin_contexts(
            BuiltinConfig(database=migrated[0], runtime=RuntimeConfig(atomic_memory_related_mode="fts")),
            candidate_pipeline=pipeline,
        ) as contexts:
            context = await contexts.get(SCOPE)
            memory = contexts.atomic_memory.for_scope(SCOPE)
            original = await memory.get(_atomic("alpha", 2).artifact_id)
            async with contexts.database.transaction() as connection:
                imported = await _imported_snapshot(connection)
            await contexts.records.capture_source(SCOPE, "content", "migration-trigger", "Recheck task A and C.", {})
            await context.triggers.flush(limit=20)
            observed = []
            for request in reconciler.requests:
                evidence = {item.evidence_id: item for item in request.evidence}
                for related in request.related:
                    if any(original.ref == _atomic("alpha", 2) for original in related.original_refs):
                        observed.append(tuple(evidence[key] for key in related.evidence_ids))
            assert observed, "The public flush must compare the imported alpha identity."
            for (published,) in observed:
                assert isinstance(published, AtomicMemoryArtifactEvidence)
                assert published.artifact_ref == _atomic("alpha", 2)
                assert published.content.kind == original.artifact.content.kind
                assert published.content.text == "Tasks A and C were completed."
            assert await memory.get(original.ref.artifact_id) == original
            assert (
                await memory.get(original.ref.artifact_id, revision=original.ref.revision)
            ).artifact == original.artifact
            async with contexts.database.transaction() as connection:
                resolved = await _resolver(contexts).resolve(connection, artifacts=(original.ref,))
                assert _projected_sources(resolved) == (A, C)
                assert EA in _projected_artifacts(resolved)
                assert EB not in _projected_artifacts(resolved)
                assert await _imported_snapshot(connection) == imported

    asyncio.run(scenario())
