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
import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, ForeignKeyConstraint, MetaData, event, insert, inspect, select, text, tuple_

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.atomic_memory import AtomicMemoryContent
from powercontext.builtin.artifacts.atomic_memory.extraction import (
    AtomicMemoryCandidate,
    AtomicMemoryExtractionOutput,
    AtomicMemoryGenerationPipeline,
)
from powercontext.builtin.artifacts.atomic_memory.reconciliation import (
    AtomicMemoryReconciliationContent,
    AtomicMemoryReconciliationOutput,
)
from powercontext.builtin.artifacts.experience import ExperienceContent, ExperienceDraft
from powercontext.builtin.artifacts.experience.recurrence import (
    RecurrenceMatch,
    TaskOutcomeItemRef,
    candidate_set_digest,
    item_digest,
)
from powercontext.builtin.artifacts.handoff.models import (
    HandoffArtifactCitation,
    HandoffContent,
    HandoffSourceCitation,
    HandoffStatement,
)
from powercontext.builtin.dream.models import CreateDreamRunRequest, DreamError, DreamRecord, DreamRun
from powercontext.builtin.evidence.models import EvidenceManifest, EvidenceNode, EvidenceResolutionError, content_digest
from powercontext.builtin.evidence.resolver import EvidenceResolver, evidence_id
from powercontext.builtin.evidence.selection import select_evidence
from powercontext.builtin.inference import GenerationResult, character_token_estimator
from powercontext.builtin.persistence.dream import DreamRepository
from powercontext.builtin.persistence.migrations.atomic_memory_archive import ARCHIVE_TABLE
from powercontext.builtin.persistence.migrations.atomic_memory_references import (
    DECISIONS_FORMAT,
    legacy_citation_columns,
    legacy_foreign_keys,
    load_decisions,
    table_names,
)
from powercontext.builtin.persistence.migrations.atomic_memory_v1 import (
    AtomicMemoryMigrationError,
    apply_atomic_memory_migration,
    verify_atomic_memory_migration,
)
from powercontext.builtin.persistence.recurrence import RecurrenceRepository
from powercontext.builtin.persistence.schema import create_tables
from powercontext.builtin.persistence.tables import (
    ARTIFACT_CANDIDATE_HEADS_TABLE,
    ARTIFACT_CANDIDATE_VERSIONS_TABLE,
    ARTIFACT_HEADS_TABLE,
    ARTIFACT_LINEAGE_ARTIFACTS_TABLE,
    ARTIFACT_LINEAGE_SOURCES_TABLE,
    ARTIFACTS_TABLE,
    DREAM_RUNS_TABLE,
    MEMORY_ENTRY_HEADS_TABLE,
    MEMORY_ENTRY_VERSIONS_TABLE,
    SCOPES_TABLE,
    SOURCES_TABLE,
)
from powercontext.builtin.records import BaseOperationNotSupportedError
from powercontext.builtin.runtime import BuiltinConfig, RuntimeConfig, open_builtin_contexts
from powercontext.builtin.runtime.atomic_memory_rebuild import rebuild_atomic_memory_projection
from powercontext.builtin.runtime.config import DatabaseConfig
from powercontext.builtin.sources.content import ContentSource, ContentSourceTarget
from powercontext.builtin.work.models import HandoffReceipt, TaskOutcome
from powercontext.sources import SourceMaterialization, SourceRef
from tests.e2e.atomic_memory_migration_backend import MigrationBackend, migration_index, migration_profile
from tests.e2e.atomic_memory_migration_fixture import (
    COLLECTION,
    EA,
    EB,
    INTERNAL,
    INTERNAL_TEXT,
    SCOPE,
    A,
    B,
    C,
    _atomic,
    _collection,
    _migrate,
    _seed,
    _seed_and_migrate,
)
from tests.legacy_memory import add_legacy_citation_columns


@pytest.fixture(autouse=True)
def sqlite_like_does_not_match_blobs() -> Iterator[None]:
    """Exercise SQLITE_LIKE_DOESNT_MATCH_BLOBS even on builds that coerce blobs to text."""

    native = sqlite3.connect(":memory:", check_same_thread=False)

    def like(pattern, value):
        if isinstance(pattern, bytes) or isinstance(value, bytes):
            return 0
        return native.execute("SELECT ? LIKE ?", (value, pattern)).fetchone()[0]

    def configure(connection, _record):
        if type(connection).__module__ == "sqlalchemy.dialects.sqlite.aiosqlite":
            connection.create_function("like", 2, like)

    event.listen(Engine, "connect", configure)
    try:
        yield
    finally:
        event.remove(Engine, "connect", configure)
        native.close()


@pytest.fixture
def migrated(migration_backend: MigrationBackend) -> tuple[DatabaseConfig, bytes]:
    config = migration_backend.config
    return config, asyncio.run(_seed_and_migrate(config))


async def _public_collection_rows(connection) -> list[Any]:
    statement = select(ARTIFACTS_TABLE.c.revision).where(
        ARTIFACTS_TABLE.c.scope_id == SCOPE, ARTIFACTS_TABLE.c.family == "memory"
    )
    return list((await connection.execute(statement)).scalars())


async def _archive_metadata(connection, revision: int) -> dict[str, Any]:
    value = await connection.scalar(
        select(ARCHIVE_TABLE.c.metadata).where(
            ARCHIVE_TABLE.c.scope_id == SCOPE,
            ARCHIVE_TABLE.c.artifact_id == COLLECTION,
            ARCHIVE_TABLE.c.revision == revision,
        )
    )
    return json.loads(value)


async def _restore_public_collection(connection) -> None:
    for row in (await connection.execute(select(ARCHIVE_TABLE))).mappings():
        await connection.execute(
            insert(ARTIFACTS_TABLE).values(
                scope_id=row["scope_id"],
                family=row["family"],
                artifact_id=row["artifact_id"],
                revision=row["revision"],
                content=row["content"],
            )
        )
    await connection.execute(
        insert(ARTIFACT_HEADS_TABLE).values(scope_id=SCOPE, family="memory", artifact_id=COLLECTION, revision=2)
    )


def _resolver(contexts) -> EvidenceResolver:
    return EvidenceResolver(
        scope_id=SCOPE, sources=contexts.repositories.sources, artifacts=contexts.repositories.artifacts
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
            assert stored.lineage.artifacts == (own,)
            assert all(source in stored.lineage.sources for source in expected)

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


def test_apply_replaces_the_collection_anchor_left_by_an_earlier_import(migrated) -> None:
    async def scenario() -> None:
        async with open_builtin_contexts(BuiltinConfig(database=migrated[0])) as contexts:
            target = _atomic("alpha", 2)
            identity = {"scope_id": SCOPE, "family": "atomic-memory", "artifact_id": target.artifact_id, "revision": 2}
            async with contexts.database.transaction() as connection:
                direct = await _imported_snapshot(connection)
                # An interrupted earlier run left the collection online with anchored imports.
                await _restore_public_collection(connection)
                for table in (ARTIFACT_LINEAGE_SOURCES_TABLE, ARTIFACT_LINEAGE_ARTIFACTS_TABLE):
                    await connection.execute(
                        table.delete().where(*(table.c[key] == value for key, value in identity.items()))
                    )
                for ordinal, ref in enumerate((_collection(2), EA.model_dump(), _atomic("alpha", 1).model_dump())):
                    await connection.execute(
                        insert(ARTIFACT_LINEAGE_ARTIFACTS_TABLE).values(
                            **identity,
                            ordinal=ordinal,
                            upstream_family=ref["family"],
                            upstream_artifact_id=ref["artifact_id"],
                            upstream_revision=ref["revision"],
                        )
                    )
                report = await verify_atomic_memory_migration(connection, index=contexts.atomic_memory.index)
                assert not report.ready
                assert any("collection anchor" in error for error in report.errors), report.errors
            repaired = await apply_atomic_memory_migration(
                contexts.database, contexts.atomic_memory.index, maintenance_confirmed=True
            )
            assert repaired.ready, repaired.errors
            async with contexts.database.transaction() as connection:
                assert await _imported_snapshot(connection) == direct
                assert not await _public_collection_rows(connection)

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


@pytest.mark.parametrize("other_origin", ["atomic", "experience"])
def test_other_origin_does_not_broaden_migrated_entry_root_groups_or_selection(migrated, other_origin) -> None:
    async def scenario() -> None:
        async with (
            open_builtin_contexts(BuiltinConfig(database=migrated[0])) as contexts,
            contexts.database.transaction() as connection,
        ):
            other = _atomic("beta", 1)
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


def test_migrated_collection_leaves_public_artifact_reads(migrated) -> None:
    async def scenario() -> None:
        async with (
            open_builtin_contexts(BuiltinConfig(database=migrated[0])) as contexts,
            contexts.database.transaction() as connection,
        ):
            assert not await _public_collection_rows(connection)
        for read in (
            contexts.records.get_artifact_revision(SCOPE, "memory", COLLECTION, 2),
            contexts.records.list_artifact_revisions(SCOPE, "memory", COLLECTION, limit=10, cursor=None),
        ):
            with pytest.raises(BaseOperationNotSupportedError):
                await read

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
                content=(
                    AtomicMemoryReconciliationContent(kind="fact", text=request.proposal.text)
                    if related is None
                    else None
                ),
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
                assert published.artifact_ref == _atomic("alpha", 2)
                assert published.content.text == "Tasks A and C were completed."

    asyncio.run(scenario())


ALPHA_2 = {"memory_ref": _collection(2), "entry_id": "alpha", "entry_version_id": "alpha-v2"}
ALPHA_2_CITATION = {"kind": "memory", "memory_citation": ALPHA_2}
OUTCOME = SourceRef(source_type="content", source_id="legacy-outcome")
RECEIPT = SourceRef(source_type="content", source_id="legacy-receipt")
OWNER = "migration-owner"


def _experience(name: str) -> ExperienceContent:
    return ExperienceContent(
        situation=f"{name} was requested.",
        action=f"Executed {name}.",
        outcome=f"Observed {name} completion.",
        lesson=f"Preserve the exact evidence of {name}.",
    )


def _compact(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()


# Legacy values are frozen JSON in the field order the removed models serialized.
LEGACY_OBSERVATION = {"text": "Deploy failed.", "basis": "verified", "evidence": [ALPHA_2_CITATION]}
LEGACY_OUTCOME = {
    "schema": "powercontext.task-outcome.v1",
    "trust": "untrusted_observation",
    "objective": "Ship task A.",
    "status": "failed",
    "summary": "Deployment failed.",
    "handoff_receipt_ref": None,
    "observations": [LEGACY_OBSERVATION],
    "checks": [],
    "produced_artifacts": [],
    "remaining_work": [],
}


def _legacy_request(idempotency_key: str, memory_citations: list[Any]) -> dict[str, Any]:
    return {
        "operation": "refine_experience",
        "artifacts": [],
        "memory_citations": memory_citations,
        "sources": [],
        "target": None,
        "idempotency_key": idempotency_key,
    }


def _legacy_request_digest(request: dict[str, Any]) -> str:
    return content_digest(_compact({name: value for name, value in request.items() if name != "idempotency_key"}))


async def _legacy_dream(connection, run_id: str, status: str, request: dict[str, Any], manifest=None) -> None:
    run = DreamRun.model_validate({
        "scope_id": SCOPE,
        "run_id": run_id,
        "operation": "refine_experience",
        "status": status,
        "accepted_at": datetime(2026, 1, 2, tzinfo=UTC),
    }).model_dump(mode="json", exclude={"historical_data"})
    payload = {
        "run": {**run, "input_manifest": manifest},
        "request": request,
        "principal_id": OWNER,
        "generation": 0,
        "request_generation": 0,
        "deadline_at": None,
    }
    await connection.execute(
        insert(DREAM_RUNS_TABLE).values(
            scope_id=SCOPE,
            run_id=run_id,
            principal_key=content_digest(OWNER.encode())[7:],
            idempotency_key=request["idempotency_key"],
            request_digest=_legacy_request_digest(request),
            operation="refine_experience",
            status=status,
            accepted_at=0,
            generation=0,
            request_generation=0,
            payload=_compact(payload),
        )
    )


async def _set_citations(connection, table: str, where: str, citations: list[Any], **identity: Any) -> None:
    await connection.execute(
        text(f"UPDATE {table} SET memory_citations = :citations WHERE {where}"),  # noqa: S608
        {**identity, "citations": _compact(citations)},
    )


async def _candidate(connection, candidate_id: str, *, artifact_refs: list[Any], citations: list[Any] | None) -> None:
    await connection.execute(
        insert(ARTIFACT_CANDIDATE_VERSIONS_TABLE).values(
            scope_id=SCOPE,
            candidate_id=candidate_id,
            version=1,
            family="experience",
            proposal=_experience(candidate_id).model_dump_json(by_alias=True).encode(),
            source_refs=b"[]",
            artifact_refs=json.dumps(artifact_refs).encode(),
        )
    )
    if citations is not None:
        await _set_citations(
            connection,
            "pc_artifact_candidate_versions",
            "scope_id = :scope AND candidate_id = :candidate",
            citations,
            scope=SCOPE,
            candidate=candidate_id,
        )
    await connection.execute(
        insert(ARTIFACT_CANDIDATE_HEADS_TABLE).values(
            scope_id=SCOPE, candidate_id=candidate_id, family="experience", version=1, status="pending"
        )
    )


async def _scope_row(connection) -> None:
    await connection.execute(
        insert(SCOPES_TABLE).values(
            scope_id=SCOPE,
            title="Migration evidence",
            summary="Legacy reference fixture.",
            scope_id_search=SCOPE,
            title_search="migration evidence",
            summary_search="legacy reference fixture",
            version=1,
        )
    )


def _handoff_content(*citations: Any) -> dict[str, Any]:
    """Serialize a Handoff whose single statement cites the given raw citation values."""

    content = HandoffContent(
        objective="Continue task A.",
        state=(HandoffStatement(text="Task A shipped.", citations=(HandoffSourceCitation(source_ref=A),)),),
        disposition="complete",
    ).model_dump(mode="json", by_alias=True)
    content["state"][0]["citations"] = list(citations)
    return content


def _insert_handoff(connection, artifact_id: str, content: dict[str, Any]):
    return connection.execute(
        insert(ARTIFACTS_TABLE).values(
            scope_id=SCOPE, family="handoff", artifact_id=artifact_id, revision=1, content=_compact(content)
        )
    )


async def _work_source(contexts, connection, ref: SourceRef, kind: str, schema: str, value: dict[str, Any]):
    return await contexts.repositories.sources.add(
        connection,
        SCOPE,
        ContentSource(
            name=ref.source_id,
            materialization=SourceMaterialization.CAPTURED,
            content=json.dumps(value, ensure_ascii=False, indent=2),
            metadata={"kind": kind, "schema": schema},
        ),
    )


def _legacy_receipt(*unavailable: Any) -> dict[str, Any]:
    return {
        "schema": "powercontext.handoff-receipt.v1",
        "trust": "untrusted_observation",
        "receiver": "next-agent",
        "status": "needs_clarification",
        "selection": "exact",
        "selected_revision": {"family": "handoff", "artifact_id": "legacy-handoff", "revision": 1},
        "prepared_digest": None,
        "receiver_checks": None,
        "evidence_status": "unavailable",
        "unavailable_evidence": list(unavailable),
        "message": "Evidence was missing.",
    }


async def _legacy_references(contexts, connection) -> None:
    for artifact_id, draft in (
        ("cited-experience", ExperienceDraft(content=_experience("cited"), sources=(A,))),
        ("collection-experience", ExperienceDraft(content=_experience("collection"), sources=(B,))),
    ):
        await contexts.repositories.artifacts.create(connection, SCOPE, artifact_id, draft)
    await _set_citations(
        connection,
        "pc_artifacts",
        "scope_id = :scope AND family = 'experience' AND artifact_id = 'cited-experience'",
        [ALPHA_2],
        scope=SCOPE,
    )
    await connection.execute(
        insert(ARTIFACT_LINEAGE_ARTIFACTS_TABLE).values(
            scope_id=SCOPE,
            family="experience",
            artifact_id="collection-experience",
            revision=1,
            ordinal=0,
            upstream_family="memory",
            upstream_artifact_id=COLLECTION,
            upstream_revision=1,
        )
    )
    await _candidate(connection, "cited-candidate", artifact_refs=[], citations=[ALPHA_2])
    await _insert_handoff(connection, "legacy-handoff", _handoff_content(ALPHA_2_CITATION))
    await connection.execute(
        insert(ARTIFACT_LINEAGE_ARTIFACTS_TABLE).values(
            scope_id=SCOPE,
            family="handoff",
            artifact_id="legacy-handoff",
            revision=1,
            ordinal=0,
            upstream_family="memory",
            upstream_artifact_id=COLLECTION,
            upstream_revision=2,
        )
    )
    await connection.execute(
        insert(ARTIFACT_HEADS_TABLE).values(scope_id=SCOPE, family="handoff", artifact_id="legacy-handoff", revision=1)
    )
    await _scope_row(connection)
    stored = await _work_source(
        contexts, connection, OUTCOME, "task-outcome", "powercontext.task-outcome.v1", LEGACY_OUTCOME
    )
    await RecurrenceRepository().append_match(
        connection,
        RecurrenceMatch(
            scope_id=SCOPE,
            task_outcome_ref=OUTCOME,
            task_outcome_position=stored.journal_position,
            failure_ref=TaskOutcomeItemRef(
                task_outcome_ref=OUTCOME,
                item_kind="observation",
                item_index=0,
                item_digest=content_digest(_compact(LEGACY_OBSERVATION)),
            ),
            candidate_set_mode="scope_heads",
            candidate_refs=(),
            candidate_set_digest=candidate_set_digest(()),
            result="unmatched",
        ),
    )
    await _work_source(
        contexts,
        connection,
        RECEIPT,
        "handoff-receipt",
        "powercontext.handoff-receipt.v1",
        _legacy_receipt(ALPHA_2_CITATION, {"kind": "artifact", "artifact_ref": _collection(7)}),
    )
    await _legacy_dream(connection, "cited-dream", "succeeded", _legacy_request("cited-dream", [ALPHA_2]))
    plain_request = {**_legacy_request("plain-dream", []), "artifacts": [EA.model_dump(mode="json")]}
    await _legacy_dream(connection, "plain-dream", "failed", plain_request)
    pending_request = {**_legacy_request("pending-dream", []), "artifacts": [EA.model_dump(mode="json")]}
    await _legacy_dream(connection, "pending-dream", "queued", pending_request)


def _new_request(idempotency_key: str, *artifacts: ArtifactRef) -> CreateDreamRunRequest:
    return CreateDreamRunRequest(operation="refine_experience", artifacts=artifacts, idempotency_key=idempotency_key)


def test_migration_converts_exact_citations_and_archives_collection_relationships(
    migration_backend: MigrationBackend,
) -> None:
    config = migration_backend.config
    alpha = _atomic("alpha", 2)

    async def scenario() -> None:
        await _seed_and_migrate(config, _legacy_references)
        async with (
            open_builtin_contexts(BuiltinConfig(database=config)) as contexts,
            contexts.database.transaction() as connection,
        ):
            assert not await _public_collection_rows(connection)
            report = await verify_atomic_memory_migration(connection, index=contexts.atomic_memory.index)
            assert report.ready, report.errors
            for table in ("pc_artifacts", "pc_artifact_candidate_versions"):
                columns = await connection.run_sync(lambda sync, name=table: inspect(sync).get_columns(name))
                assert "memory_citations" not in {column["name"] for column in columns}

            cited = await contexts.repositories.artifacts.get(
                connection, SCOPE, ArtifactRef(family="experience", artifact_id="cited-experience", revision=1)
            )
            assert cited.lineage.artifacts == (alpha,)

            whole = await contexts.repositories.artifacts.get(
                connection, SCOPE, ArtifactRef(family="experience", artifact_id="collection-experience", revision=1)
            )
            assert whole.lineage.artifacts == ()
            (incoming,) = (await _archive_metadata(connection, 1))["incoming_references"]
            assert incoming["referrer"]["artifact_id"] == "collection-experience"
            assert incoming["value"] == _collection(1)

            candidate = await contexts.review(SCOPE).get_candidate("cited-candidate")
            assert candidate.artifacts == (alpha,)

            handoff = await contexts.repositories.artifacts.get(
                connection, SCOPE, ArtifactRef(family="handoff", artifact_id="legacy-handoff", revision=1)
            )
            assert handoff.content.state[0].citations == (HandoffArtifactCitation(artifact_ref=alpha),)
            assert handoff.lineage.artifacts == (alpha,)

            source = (await contexts.repositories.sources.get(connection, SCOPE, OUTCOME)).value
            assert isinstance(source, ContentSource)
            converted = TaskOutcome.model_validate_json(source.content)
            assert converted.observations[0].evidence == (HandoffArtifactCitation(artifact_ref=alpha),)
            match = await RecurrenceRepository().find_match(
                connection,
                SCOPE,
                OUTCOME,
                TaskOutcomeItemRef(
                    task_outcome_ref=OUTCOME,
                    item_kind="observation",
                    item_index=0,
                    item_digest=item_digest(converted.observations[0]),
                ),
            )
            assert match is not None
            assert match.failure_ref.item_digest == item_digest(converted.observations[0])

            receipt_source = (await contexts.repositories.sources.get(connection, SCOPE, RECEIPT)).value
            assert isinstance(receipt_source, ContentSource)
            receipt = HandoffReceipt.model_validate_json(receipt_source.content)
            assert receipt.model_dump(mode="json")["unavailable_evidence"] == [
                {"kind": "artifact", "artifact_ref": alpha.model_dump(mode="json")},
                {"kind": "artifact", "artifact_ref": _collection(7)},
            ]

            repository = DreamRepository()
            cited_dream = await repository.get(connection, SCOPE, "cited-dream")
            assert cited_dream.request is None and cited_dream.run.input_manifest is None
            history = cited_dream.run.historical_data
            assert history is not None and history["request"] == _legacy_request("cited-dream", [ALPHA_2])
            # A request that cited legacy entries cannot be sent again; its key keeps the accepted digest.
            with pytest.raises(DreamError, match="idempotency_conflict"):
                await repository.find_request(connection, SCOPE, OWNER, _new_request("cited-dream", EA))
            replay = await repository.find_request(connection, SCOPE, OWNER, _new_request("plain-dream", EA))
            assert replay is not None and replay.run.run_id == "plain-dream" and replay.request is None
            pending = await repository.find_request(connection, SCOPE, OWNER, _new_request("pending-dream", EA))
            assert pending is not None and pending.request == _new_request("pending-dream", EA)
            assert pending.run.input_manifest is None

    asyncio.run(scenario())


def test_candidate_left_without_evidence_blocks_until_an_explicit_replacement(
    migration_backend: MigrationBackend, tmp_path: Path
) -> None:
    config = migration_backend.config

    async def blocked(_contexts, connection) -> None:
        await _candidate(connection, "collection-candidate", artifact_refs=[_collection(1)], citations=None)

    async def scenario() -> None:
        await _seed(config, blocked)
        refused = await _migrate(config)
        assert not refused.ready
        assert any("collection-candidate" in error and "replace decision" in error for error in refused.errors)
        async with migration_profile(config) as profile, profile.database.transaction() as connection:
            assert await _public_collection_rows(connection) == [1, 2]
        decisions = load_decisions({
            "format": DECISIONS_FORMAT,
            "decisions": [
                {
                    "carrier": "candidate",
                    "scope_id": SCOPE,
                    "candidate_id": "collection-candidate",
                    "version": 1,
                    "field": "artifact_refs",
                    "action": "replace",
                    "artifact_refs": [EA.model_dump(mode="json")],
                }
            ],
        })
        applied = await _migrate(config, decisions)
        assert applied.ready, applied.errors
        async with migration_profile(config) as profile, profile.database.transaction() as connection:
            refs = await connection.scalar(
                select(ARTIFACT_CANDIDATE_VERSIONS_TABLE.c.artifact_refs).where(
                    ARTIFACT_CANDIDATE_VERSIONS_TABLE.c.candidate_id == "collection-candidate"
                )
            )
            assert isinstance(refs, bytes) and json.loads(refs) == [EA.model_dump(mode="json")]
            (incoming,) = (await _archive_metadata(connection, 1))["incoming_references"]
            assert incoming["carrier"] == "candidate" and incoming["decision"] == "replace"
        rerun = await _migrate(config, decisions)
        assert rerun.ready, rerun.errors

    asyncio.run(scenario())


def test_handoff_collection_citation_is_archived_and_removed(migration_backend: MigrationBackend) -> None:
    config = migration_backend.config

    async def cited(_contexts, connection) -> None:
        await _insert_handoff(
            connection,
            "mixed-handoff",
            _handoff_content(
                {"kind": "artifact", "artifact_ref": _collection(2)}, HandoffSourceCitation(source_ref=A).model_dump()
            ),
        )

    async def scenario() -> None:
        await _seed_and_migrate(config, cited)
        async with (
            open_builtin_contexts(BuiltinConfig(database=config)) as contexts,
            contexts.database.transaction() as connection,
        ):
            handoff = await contexts.repositories.artifacts.get(
                connection, SCOPE, ArtifactRef(family="handoff", artifact_id="mixed-handoff", revision=1)
            )
            assert handoff.content.state[0].citations == (HandoffSourceCitation(source_ref=A),)
            (incoming,) = (await _archive_metadata(connection, 2))["incoming_references"]
            assert incoming["carrier"] == "handoff" and incoming["path"] == "/state/0/citations/0"
            report = await verify_atomic_memory_migration(connection, index=contexts.atomic_memory.index)
            assert report.ready, report.errors

    asyncio.run(scenario())


def test_handoff_supported_only_by_a_collection_blocks_before_any_change(migration_backend: MigrationBackend) -> None:
    config = migration_backend.config

    async def cited(_contexts, connection) -> None:
        await _insert_handoff(
            connection, "orphan-handoff", _handoff_content({"kind": "artifact", "artifact_ref": _collection(2)})
        )

    async def scenario() -> None:
        await _seed(config, cited)
        refused = await _migrate(config)
        assert not refused.ready
        assert any("orphan-handoff" in error for error in refused.errors), refused.errors
        async with migration_profile(config) as profile, profile.database.transaction() as connection:
            assert await _public_collection_rows(connection) == [1, 2]

    asyncio.run(scenario())


def test_collections_citing_each_other_are_removed_regardless_of_identifier_order(
    migration_backend: MigrationBackend,
) -> None:
    config = migration_backend.config
    cited = "a-cited-memory"

    async def chain(_contexts, connection) -> None:
        empty = {"schema": "powercontext.memory.v1", "manifest": {"format": "flat-v1", "entries": []}, "changes": []}
        await connection.execute(
            insert(ARTIFACTS_TABLE).values(
                scope_id=SCOPE,
                family="memory",
                artifact_id=cited,
                revision=1,
                content=json.dumps(empty).encode(),
            )
        )
        await connection.execute(
            insert(ARTIFACT_HEADS_TABLE).values(scope_id=SCOPE, family="memory", artifact_id=cited, revision=1)
        )
        await connection.execute(
            insert(ARTIFACT_LINEAGE_ARTIFACTS_TABLE).values(
                scope_id=SCOPE,
                family="memory",
                artifact_id=COLLECTION,
                revision=1,
                ordinal=2,
                upstream_family="memory",
                upstream_artifact_id=cited,
                upstream_revision=1,
            )
        )

    async def scenario() -> None:
        await _seed_and_migrate(config, chain)
        async with migration_profile(config) as profile, profile.database.transaction() as connection:
            assert not await _public_collection_rows(connection)
            lineage = (await _archive_metadata(connection, 1))["lineage"]["artifacts"]
            assert {row["upstream_artifact_id"] for row in lineage} == {EA.artifact_id, EB.artifact_id, cited}

    asyncio.run(scenario())


def test_unfinished_dream_pinning_a_rewritten_source_blocks_migration(migration_backend: MigrationBackend) -> None:
    config = migration_backend.config

    async def pinned(contexts, connection) -> None:
        await _scope_row(connection)
        await _work_source(
            contexts, connection, OUTCOME, "task-outcome", "powercontext.task-outcome.v1", LEGACY_OUTCOME
        )
        manifest = EvidenceManifest(
            artifacts=(EA,),
            sources=(OUTCOME,),
            nodes=(EvidenceNode(evidence_id="outcome", kind="source", digest="0" * 64, source=OUTCOME, role="root"),),
            projection_digest="0" * 64,
            projection_bytes=0,
        )
        await DreamRepository().create(
            connection,
            DreamRecord(
                run=DreamRun(
                    scope_id=SCOPE,
                    run_id="pending-dream",
                    operation="refine_experience",
                    status="running",
                    accepted_at=datetime(2026, 1, 2, tzinfo=UTC),
                    input_manifest=manifest,
                ),
                request=CreateDreamRunRequest(
                    operation="refine_experience", artifacts=(EA,), idempotency_key="pending-dream"
                ),
                principal_id="migration-owner",
            ),
        )

    async def scenario() -> None:
        await _seed(config, pinned)
        refused = await _migrate(config)
        assert not refused.ready
        assert any("pending-dream" in error and "finish" in error for error in refused.errors), refused.errors

    asyncio.run(scenario())


@pytest.mark.parametrize("legacy_snapshot", [True, False])
def test_unfinished_dream_requires_current_artifact_snapshot_format(
    migration_backend: MigrationBackend, legacy_snapshot: bool
) -> None:
    config = migration_backend.config

    async def pinned(contexts, connection) -> None:
        await _scope_row(connection)
        resolved = await _resolver(contexts).resolve(connection, artifacts=(EA,))
        manifest = resolved.manifest.model_dump(mode="json")
        request = _new_request("pending-dream", EA).model_dump(mode="json")
        if legacy_snapshot:
            artifact = await contexts.repositories.artifacts.get(connection, SCOPE, EA)
            old_value = artifact.model_dump(mode="json")
            old_value["lineage"] = {
                "sources": old_value["lineage"]["sources"],
                "artifacts": old_value["lineage"]["artifacts"],
                "memory_citations": [],
                "publication_source": None,
                "publication_digest": None,
            }
            old_digest = content_digest(_compact(old_value))
            old_nodes = tuple(
                node.model_copy(update={"digest": old_digest}) if node.artifact == EA else node
                for node in resolved.manifest.nodes
            )
            # Released snapshots hashed the full Artifact, including its empty legacy lineage field.
            with pytest.raises(EvidenceResolutionError, match="evidence_unavailable"):
                await _resolver(contexts).resolve(
                    connection, artifacts=(EA,), pinned=resolved.manifest.model_copy(update={"nodes": old_nodes})
                )
            request = {**_legacy_request("pending-dream", []), "artifacts": [EA.model_dump(mode="json")]}
            manifest["memory_citations"] = []
            for node in manifest["nodes"]:
                node["memory_citations"] = []
                node["current_entry_version_id"] = None
                if node["artifact"] == EA.model_dump(mode="json"):
                    node["digest"] = old_digest
        await _legacy_dream(connection, "pending-dream", "running", request, manifest)

    async def scenario() -> None:
        await _seed(config, pinned)
        report = await _migrate(config)
        if legacy_snapshot:
            assert not report.ready
            assert any("pending-dream" in error and "finish" in error for error in report.errors), report.errors
            async with migration_profile(config) as profile, profile.database.transaction() as connection:
                assert await _public_collection_rows(connection) == [1, 2]
        else:
            assert report.ready, report.errors
            async with (
                open_builtin_contexts(BuiltinConfig(database=config)) as contexts,
                contexts.database.transaction() as connection,
            ):
                record = await DreamRepository().get(connection, SCOPE, "pending-dream")
                restored = await _resolver(contexts).resolve(
                    connection, artifacts=(EA,), pinned=record.run.input_manifest
                )
                assert restored.manifest == record.run.input_manifest

    asyncio.run(scenario())


def test_runtime_start_does_not_read_the_archive_or_legacy_entry_tables(migrated) -> None:
    config, _internal = migrated

    async def scenario() -> None:
        async with migration_profile(config) as profile, profile.database.transaction() as connection:
            await connection.execute(text(f"DROP TABLE {ARCHIVE_TABLE.name}"))
            await connection.execute(text(f"DROP TABLE {MEMORY_ENTRY_HEADS_TABLE.name}"))
            await connection.execute(text(f"DROP TABLE {MEMORY_ENTRY_VERSIONS_TABLE.name}"))
        async with open_builtin_contexts(BuiltinConfig(database=config)) as contexts:
            record = await contexts.atomic_memory.for_scope(SCOPE).get(_atomic("alpha", 2).artifact_id)
            assert record.artifact.as_ref() == _atomic("alpha", 2)

    asyncio.run(scenario())


def test_unfinished_dream_receipt_address_stays_readable_without_rewriting_its_source(
    migration_backend: MigrationBackend,
) -> None:
    config = migration_backend.config
    receipt = _legacy_receipt({"kind": "artifact", "artifact_ref": _collection(7)})
    original_content = json.dumps(receipt, ensure_ascii=False, indent=2)

    async def pinned(contexts, connection) -> None:
        await _scope_row(connection)
        await _work_source(contexts, connection, RECEIPT, "handoff-receipt", "powercontext.handoff-receipt.v1", receipt)
        await DreamRepository().create(
            connection,
            DreamRecord(
                run=DreamRun(
                    scope_id=SCOPE,
                    run_id="receipt-dream",
                    operation="refine_experience",
                    status="queued",
                    accepted_at=datetime(2026, 1, 2, tzinfo=UTC),
                ),
                request=CreateDreamRunRequest(
                    operation="refine_experience", artifacts=(EA,), sources=(RECEIPT,), idempotency_key="receipt-dream"
                ),
                principal_id=OWNER,
            ),
        )

    async def scenario() -> None:
        await _seed_and_migrate(config, pinned)
        async with migration_profile(config) as profile, profile.database.transaction() as connection:
            await connection.execute(text(f"DROP TABLE {ARCHIVE_TABLE.name}"))
            await connection.execute(text(f"DROP TABLE {MEMORY_ENTRY_HEADS_TABLE.name}"))
            await connection.execute(text(f"DROP TABLE {MEMORY_ENTRY_VERSIONS_TABLE.name}"))
        async with (
            open_builtin_contexts(BuiltinConfig(database=config)) as contexts,
            contexts.database.transaction() as connection,
        ):
            stored = (await contexts.repositories.sources.get(connection, SCOPE, RECEIPT)).value
            assert isinstance(stored, ContentSource) and stored.content == original_content
            decoded = HandoffReceipt.model_validate_json(stored.content)
            assert decoded.model_dump(mode="json")["unavailable_evidence"] == receipt["unavailable_evidence"]
            run = await DreamRepository().get(connection, SCOPE, "receipt-dream")
            assert run.run.status == "queued" and run.request is not None

    asyncio.run(scenario())


def test_development_projection_layout_is_recreated_and_rebuilt_after_apply(
    migration_backend: MigrationBackend,
) -> None:
    config = migration_backend.config

    async def development_layout(_contexts, connection) -> None:
        await connection.execute(text("DROP TABLE pc_atomic_memory_current"))
        await connection.execute(text("DROP TABLE IF EXISTS pc_atomic_memory_current_fts"))
        await connection.execute(
            text(
                "CREATE TABLE pc_atomic_memory_current (scope_id VARCHAR(128) NOT NULL, artifact_id VARCHAR(128) NOT NULL, "
                "owner_type VARCHAR(128) NOT NULL, owner_id VARCHAR(128) NOT NULL, PRIMARY KEY (scope_id, artifact_id))"
            )
        )

    async def scenario() -> None:
        await _seed(config, development_layout)
        applied = await _migrate(config)
        assert not applied.ready
        assert len(applied.errors) == 1 and "atomic-memory-rebuild-projection" in applied.errors[0]
        async with migration_profile(config) as profile:
            rebuilt = await rebuild_atomic_memory_projection(
                profile.database, migration_index(config), maintenance_confirmed=True
            )
            assert rebuilt.ready, rebuilt.errors
            async with profile.database.transaction() as connection:
                report = await verify_atomic_memory_migration(connection, index=migration_index(config))
                assert report.ready, report.errors

    asyncio.run(scenario())


def test_rerun_completes_a_database_left_by_an_earlier_reference_conversion(migrated) -> None:
    config, _internal = migrated
    left = _legacy_receipt({"kind": "artifact", "artifact_ref": _collection(7)})

    async def receipt_payload(connection) -> bytes:
        return await connection.scalar(
            select(SOURCES_TABLE.c.payload).where(
                SOURCES_TABLE.c.scope_id == SCOPE, SOURCES_TABLE.c.source_id == RECEIPT.source_id
            )
        )

    async def scenario() -> None:
        async with (
            open_builtin_contexts(BuiltinConfig(database=config)) as contexts,
            contexts.database.transaction() as connection,
        ):
            # The earlier conversion kept emptied citation columns and whole-collection receipt evidence.
            await add_legacy_citation_columns(connection)
            await _work_source(
                contexts, connection, RECEIPT, "handoff-receipt", "powercontext.handoff-receipt.v1", left
            )
        with pytest.raises(AtomicMemoryMigrationError, match="memory_citations"):
            async with open_builtin_contexts(BuiltinConfig(database=config)):
                pytest.fail("Startup must wait for the reference conversion to finish")

        applied = await _migrate(config)
        assert applied.ready, applied.errors
        async with (
            open_builtin_contexts(BuiltinConfig(database=config)) as contexts,
            contexts.database.transaction() as connection,
        ):
            stored = (await contexts.repositories.sources.get(connection, SCOPE, RECEIPT)).value
            assert isinstance(stored, ContentSource)
            receipt = HandoffReceipt.model_validate_json(stored.content)
            assert receipt.evidence_status == "unavailable"
            assert receipt.model_dump(mode="json")["unavailable_evidence"] == left["unavailable_evidence"]
            converted = await receipt_payload(connection)
            report = await verify_atomic_memory_migration(connection, index=contexts.atomic_memory.index)
            assert report.ready, report.errors

        repeated = await _migrate(config)
        assert repeated.ready, repeated.errors
        async with migration_profile(config) as profile, profile.database.transaction() as connection:
            assert await receipt_payload(connection) == converted

    asyncio.run(scenario())


def test_clean_migration_retries_after_interrupted_public_column_removal(migration_backend: MigrationBackend) -> None:
    config = migration_backend.config

    async def legacy_schema(contexts, connection) -> None:
        # Restore the historical foreign keys with frozen SQL rows. Current table
        # metadata intentionally no longer attaches these tables to public Artifacts.
        versions = [dict(row) for row in (await connection.execute(select(MEMORY_ENTRY_VERSIONS_TABLE))).mappings()]
        await connection.execute(text("DROP TABLE pc_memory_entry_heads"))
        await connection.execute(text("DROP TABLE pc_memory_entry_versions"))
        metadata = MetaData()
        ARTIFACTS_TABLE.to_metadata(metadata)
        legacy_versions = MEMORY_ENTRY_VERSIONS_TABLE.to_metadata(metadata)
        legacy_heads = MEMORY_ENTRY_HEADS_TABLE.to_metadata(metadata)
        for table, revision_column in ((legacy_versions, "created_in_revision"), (legacy_heads, "head_revision")):
            table.append_constraint(
                ForeignKeyConstraint(
                    ("scope_id", "family", "memory_artifact_id", revision_column),
                    (
                        "pc_artifacts.scope_id",
                        "pc_artifacts.family",
                        "pc_artifacts.artifact_id",
                        "pc_artifacts.revision",
                    ),
                    ondelete="RESTRICT",
                )
            )
        await create_tables(connection, (legacy_versions, legacy_heads))
        await connection.execute(insert(legacy_versions), versions)
        await connection.execute(
            insert(legacy_heads),
            [
                {
                    "scope_id": SCOPE,
                    "family": "memory",
                    "memory_artifact_id": COLLECTION,
                    "head_revision": 2,
                    "entry_id": row["entry_id"],
                    "entry_version_id": row["entry_version_id"],
                    "entry_content_hash": row["entry_content_hash"],
                    "searchable_text": row["text"],
                }
                for row in versions
                if row["entry_version_id"] in {"alpha-v2", "beta-v1"}
            ],
        )
        assert set(await legacy_foreign_keys(connection, await table_names(connection))) == {
            "pc_memory_entry_versions",
            "pc_memory_entry_heads",
        }
        await _legacy_references(contexts, connection)

    interrupted = False

    def interrupt_after_ddl(_connection, _cursor, statement, _parameters, _context, _many):
        nonlocal interrupted
        if "DROP COLUMN memory_citations" in statement:
            interrupted = True
            raise RuntimeError("interrupted after a public citation column was dropped")  # noqa: TRY003

    async def scenario() -> None:
        await _seed(config, legacy_schema)
        async with migration_profile(config) as profile, profile.database.transaction() as connection:
            before = (await connection.execute(select(MEMORY_ENTRY_HEADS_TABLE))).all()
        event.listen(Engine, "after_cursor_execute", interrupt_after_ddl)
        try:
            with pytest.raises(RuntimeError, match="interrupted after a public citation"):
                await _migrate(config)
        finally:
            event.remove(Engine, "after_cursor_execute", interrupt_after_ddl)
        assert interrupted
        async with migration_profile(config) as profile, profile.database.transaction() as connection:
            tables = await table_names(connection)
            assert await legacy_citation_columns(connection, tables)
            if connection.dialect.name == "mysql":
                assert len(await legacy_citation_columns(connection, tables)) == 1
            assert not await legacy_foreign_keys(connection, tables)
            assert (await connection.execute(select(MEMORY_ENTRY_HEADS_TABLE))).all() == before
        with pytest.raises(AtomicMemoryMigrationError, match="memory_citations"):
            async with open_builtin_contexts(BuiltinConfig(database=config)):
                pytest.fail("The remaining public citation column must keep startup blocked")
        resumed = await _migrate(config)
        assert resumed.ready, resumed.errors
        assert resumed.counts["imported_entries"] == 0
        repeated = await _migrate(config)
        assert repeated.ready, repeated.errors
        async with open_builtin_contexts(BuiltinConfig(database=config)) as contexts:
            historical = await contexts.atomic_memory.for_scope(SCOPE).get(_atomic("alpha", 1).artifact_id, revision=1)
            assert historical.ref == _atomic("alpha", 1)
            async with contexts.database.transaction() as connection:
                tables = await table_names(connection)
                assert not await legacy_citation_columns(connection, tables)
                assert not await legacy_foreign_keys(connection, tables)
                assert (await connection.execute(select(MEMORY_ENTRY_HEADS_TABLE))).all() == before

    asyncio.run(scenario())
