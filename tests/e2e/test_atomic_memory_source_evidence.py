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

"""Versioned source evidence acceptance on disposable SQLite and OceanBase."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from typing import cast

import pytest
from pydantic import Field, field_serializer, field_validator
from sqlalchemy import event, text, update
from sqlalchemy.exc import SQLAlchemyError

from powercontext.artifacts import ArtifactLineage
from powercontext.builtin.artifacts.atomic_memory import AtomicMemoryContent
from powercontext.builtin.artifacts.atomic_memory.extraction import (
    AtomicMemoryCandidate,
    AtomicMemoryExtractionOutput,
)
from powercontext.builtin.inference import GenerationResult
from powercontext.builtin.persistence import InvalidStoredPayloadError
from powercontext.builtin.persistence.tables import SOURCES_TABLE
from powercontext.builtin.runtime import (
    BuiltinConfig,
    SubmitSourceObservation,
    open_builtin_contexts,
    open_builtin_runtime,
)
from powercontext.builtin.runtime.atomic_memory_rebuild import (
    rebuild_atomic_memory_current,
    rebuild_atomic_memory_projection,
    verify_atomic_memory_current,
)
from powercontext.builtin.runtime.relational import RelationalContexts
from powercontext.builtin.scope import ScopeDraft
from powercontext.builtin.sources import CONTENT_SOURCE_DEFINITION
from powercontext.sources import AdapterSourceDefinition, SourceDefinitionRegistry, SourceMaterialization, SourceRef
from powercontext.sources.evidence import MemoryEvidenceDeclaration
from powercontext.sources.observations import manifest_for_definition, project_source_for_transport
from tests.builtin.persistence.contract import NoteAdapter, NoteInput, NoteSource
from tests.e2e.atomic_memory_migration_backend import MigrationBackend
from tests.e2e.dream_support import atomic_memory_pipeline

pytestmark = pytest.mark.parametrize("migration_backend", ["sqlite", "oceanbase"], indirect=True)


async def _commit(contexts, scope_id, artifact_id, text_value, *, sources=(), artifacts=(), expected_revision=None):
    application = contexts.atomic_memory
    async with contexts.database.transaction() as connection:
        plan = await application.service.inspect_change(
            connection,
            scope_id,
            artifact_id,
            AtomicMemoryContent(kind="fact", text=text_value),
            None,
            expected_revision=expected_revision,
            lineage=ArtifactLineage(sources=sources, artifacts=artifacts),
        )
    prepared = await application.service.prepare_change(plan)
    async with contexts.database.transaction() as connection:
        result = await application.service.commit(connection, prepared, None)
    return result.primary


@pytest.mark.parametrize("declared", [False, True])
def test_unverifiable_native_serializer_keeps_memory_evidence_neutral(migration_backend, declared) -> None:
    class SerializedNote(NoteSource):
        count: int = 12

        @field_serializer("count")
        def serialize_count(self, value):
            return str(value)

        @field_validator("count", mode="before")
        @classmethod
        def parse_count(cls, value):
            return int(value)

    class SerializedAdapter(NoteAdapter):
        source_class = SerializedNote

    declaration = MemoryEvidenceDeclaration(authority="repository_attested", verification="verified")
    registry = SourceDefinitionRegistry((
        AdapterSourceDefinition(SerializedAdapter(), memory_evidence=declaration if declared else None),
    ))
    config = BuiltinConfig(database=migration_backend.config)

    async def scenario():
        async with open_builtin_contexts(config, source_registry=registry) as contexts:
            context = await contexts.get("scope")
            source = SerializedNote(name="serialized", materialization=SourceMaterialization.CAPTURED, body="Retained.")
            await context.sources.add(source)
            assert await context.sources.get(source) == source
            assert await context.sources.add(source) == source
            ref = SourceRef(source_type="note", source_id=source.name)
            record = await _commit(contexts, "scope", "serialized", "Retained fact.", sources=(ref,))
            evidence = await contexts.atomic_memory.for_scope("scope").evidence("serialized", revision=1)
            assert evidence.artifact_ref == record.ref
            assert evidence.sources[0].source_ref == ref
            assert evidence.sources[0].declaration == MemoryEvidenceDeclaration()
            assert evidence.sources[0].manifest_fingerprint is None
            if declared:
                # Retain the binding shape accepted before schema compatibility checks.
                async with contexts.database.transaction() as connection:
                    await connection.execute(
                        text(
                            "UPDATE pc_source_evidence_bindings SET manifest_fingerprint = "
                            "(SELECT fingerprint FROM pc_source_definition_manifests WHERE definition_name = 'note'), "
                            "declaration = :declaration WHERE scope_id = 'scope' AND source_id = 'serialized'"
                        ),
                        {"declaration": declaration.model_dump_json().encode()},
                    )
                await _commit(contexts, "scope", "legacy", "Previously accepted fact.", sources=(ref,))
                legacy = await contexts.atomic_memory.for_scope("scope").evidence("legacy", revision=1)
                assert legacy.sources == evidence.sources
        async with open_builtin_contexts(config, source_registry=SourceDefinitionRegistry(())) as contexts:
            report = await rebuild_atomic_memory_projection(
                contexts.database,
                contexts.atomic_memory.index,
                maintenance_confirmed=True,
                sources=contexts.repositories.sources,
            )
            assert report.ready, report.errors
            assert await contexts.atomic_memory.for_scope("scope").evidence("serialized", revision=1) == evidence

    asyncio.run(scenario())


@pytest.mark.parametrize("has_header", [False, True])
@pytest.mark.parametrize("historical_version", ["1", "2"])
def test_canonical_historical_evidence_survives_business_alias_rebuild(
    migration_backend: MigrationBackend, has_header: bool, historical_version: str
) -> None:
    class CurrentNote(NoteSource):
        name: str = Field(alias="source_name")
        label: str = Field(alias="name")

    class CurrentAdapter(NoteAdapter):
        source_class = CurrentNote

    config = BuiltinConfig(database=migration_backend.config)
    old_registry = SourceDefinitionRegistry((AdapterSourceDefinition(NoteAdapter(), version=historical_version),))
    current_registry = SourceDefinitionRegistry((
        AdapterSourceDefinition(CurrentAdapter(), version=str(int(historical_version) + 1)),
    ))

    async def scenario():
        source = NoteSource(
            name=f"canonical-v{historical_version}",
            definition_version=historical_version,
            materialization=SourceMaterialization.CAPTURED,
            body="Retained.",
        )
        ref = SourceRef(source_type="note", source_id=source.name)
        async with open_builtin_contexts(config, source_registry=old_registry) as contexts:
            context = await contexts.get("scope")
            await context.sources.add(source)
            await _commit(contexts, "scope", "canonical", "Canonical historical fact.", sources=(ref,))
            material = source.model_dump(mode="json", by_alias=True)
            payload_value = {
                "encoding": "powercontext-source-v1",
                "representation": "native",
                "value": material,
            }
            if has_header:
                payload_value.update(
                    source=source.model_dump(
                        mode="json",
                        by_alias=False,
                        include={"name", "definition_version", "materialization"},
                    ),
                    source_keys={
                        "name": "name",
                        "definition_version": "definition_version",
                        "materialization": "materialization",
                    },
                )
            payload = json.dumps(payload_value).encode()
            async with contexts.database.transaction() as connection:
                await connection.execute(
                    update(SOURCES_TABLE).where(SOURCES_TABLE.c.source_id == source.name).values(payload=payload)
                )

        async with open_builtin_contexts(config, source_registry=current_registry) as contexts:
            before = await contexts.atomic_memory.for_scope("scope").evidence("canonical", revision=1)
            assert before.sources[0].source_ref == ref
            assert before.sources[0].declaration == MemoryEvidenceDeclaration()
            report = await rebuild_atomic_memory_projection(
                contexts.database,
                contexts.atomic_memory.index,
                maintenance_confirmed=True,
                sources=contexts.repositories.sources,
            )
            assert report.ready, report.errors
            after = await contexts.atomic_memory.for_scope("scope").evidence("canonical", revision=1)
            assert after == before

    asyncio.run(scenario())


def test_mixed_evidence_survives_failed_revision_restart_and_rebuild(migration_backend: MigrationBackend) -> None:
    strong = MemoryEvidenceDeclaration(authority="repository_attested", verification="verified")
    asserted = MemoryEvidenceDeclaration(authority="user_asserted", verification="not_verified")
    remote = MemoryEvidenceDeclaration(authority="system_attested", verification="verified")

    class RemoteNoteAdapter(NoteAdapter):
        name = "remote-note"

    worker = SourceDefinitionRegistry((AdapterSourceDefinition(RemoteNoteAdapter(), memory_evidence=remote),))
    manifest = manifest_for_definition(worker.definition_for_name("remote-note"))
    registry = SourceDefinitionRegistry(
        (
            replace(CONTENT_SOURCE_DEFINITION, memory_evidence=strong),
            AdapterSourceDefinition(NoteAdapter(), memory_evidence=asserted),
        ),
        remote_declarations={(manifest.name, manifest.version, manifest.fingerprint): remote},
    )
    config = BuiltinConfig(database=migration_backend.config)
    refs = (
        SourceRef(source_type="content", source_id="cafe\u0301"),
        SourceRef(source_type="note", source_id="native"),
        SourceRef(source_type="remote-note", source_id="remote"),
    )

    async def scenario() -> None:
        async with open_builtin_contexts(config, source_registry=registry) as contexts:
            context = await contexts.get("scope")
            await contexts.records.capture_source(
                "scope", "content", "cafe\u0301", "Release received.", {"authority": "untrusted"}
            )
            native = await context.sources.resolve(NoteInput(note_id="native", body="User correction."))
            await context.sources.add(native)
            source = await worker.resolve(NoteInput(note_id="remote", body="Remote release evidence."))
            await contexts.register_source_definition(manifest)
            await contexts.submit_source_observation(
                SubmitSourceObservation(scope_id="scope", observation=project_source_for_transport(worker, source))
            )
            original = await _commit(contexts, "scope", "rule", "Release rule one.", sources=refs)
            memory = contexts.atomic_memory.for_scope("scope")
            annotation = await memory.evidence("rule", revision=1)
            assert annotation.artifact_ref == original.ref
            assert tuple((item.source_ref, item.declaration) for item in annotation.sources) == (
                (refs[0], strong),
                (refs[1], asserted),
                (refs[2], remote),
            )
            async with contexts.database.transaction() as connection:
                assert (
                    await contexts.atomic_memory.index.read_evidence_projection(connection, "scope", original.ref)
                    == annotation
                )

            def reject_snapshot(connection, cursor, statement, *args):
                if statement.lstrip().upper().startswith("INSERT INTO PC_ATOMIC_MEMORY_EVIDENCE_HISTORY"):
                    raise SQLAlchemyError(statement)

            engine = contexts.database.engine.sync_engine
            event.listen(engine, "before_cursor_execute", reject_snapshot)
            try:
                with pytest.raises(SQLAlchemyError):
                    await _commit(contexts, "scope", "rule", "Failed revision.", expected_revision=1)
            finally:
                event.remove(engine, "before_cursor_execute", reject_snapshot)
            assert await memory.get("rule") == original
            revised = await _commit(contexts, "scope", "rule", "Rule two.", sources=(refs[2],), expected_revision=1)
            current = await memory.evidence("rule", revision=2)
            assert current.sources == (annotation.sources[2],)
        neutral_registry = SourceDefinitionRegistry((CONTENT_SOURCE_DEFINITION, AdapterSourceDefinition(NoteAdapter())))
        async with open_builtin_contexts(config, source_registry=neutral_registry) as contexts:
            await contexts.records.capture_source(
                "other", refs[0].source_type, refs[0].source_id, "Other Scope release.", {}
            )
            other = await _commit(contexts, "other", "rule", "Other Scope rule.", sources=(refs[0],))
            other_annotation = await contexts.atomic_memory.for_scope("other").evidence("rule", revision=1)
            assert other_annotation.scope_id == "other"
            assert tuple((item.source_ref, item.declaration) for item in other_annotation.sources) == (
                (refs[0], MemoryEvidenceDeclaration()),
            )
            assert other_annotation.sources[0].material_digest != annotation.sources[0].material_digest

            async def assert_retained(contexts, historical):
                memory = contexts.atomic_memory.for_scope("scope")
                assert await memory.evidence("rule", revision=1) == historical
                assert await memory.evidence("rule", revision=2) == current
                assert (await memory.get("rule", revision=1)).artifact == original.artifact
                assert (await memory.get("rule")).artifact == revised.artifact
                other_memory = contexts.atomic_memory.for_scope("other")
                assert await other_memory.evidence("rule", revision=1) == other_annotation
                assert (await other_memory.get("rule", revision=1)).artifact == other.artifact
                for scope, body in (("scope", "Release received."), ("other", "Other Scope release.")):
                    stored = await contexts.records.get_source(scope, refs[0].source_type, refs[0].source_id)
                    assert stored.content == body

            await assert_retained(contexts, annotation)
            async with contexts.database.transaction() as connection:
                await connection.execute(text("DELETE FROM pc_atomic_memory_current"))
                await connection.execute(text("DELETE FROM pc_atomic_memory_evidence"))
                damaged = await verify_atomic_memory_current(
                    connection, contexts.atomic_memory.index, require_evidence_projection=True
                )
                assert not damaged.ready
                assert damaged.counts["evidence_projection_differences"] == 3
            report = await rebuild_atomic_memory_projection(
                contexts.database, contexts.atomic_memory.index, maintenance_confirmed=True, batch_size=1
            )
            assert report.ready, report.errors
            await assert_retained(contexts, annotation)
            async with contexts.database.transaction() as connection:
                for scope_id, reference, expected in (
                    ("scope", original.ref, annotation),
                    ("scope", revised.ref, current),
                    ("other", other.ref, other_annotation),
                ):
                    assert (
                        await contexts.atomic_memory.index.read_evidence_projection(connection, scope_id, reference)
                        == expected
                    )
            async with contexts.database.transaction() as connection:
                await connection.execute(
                    text(
                        "DELETE FROM pc_atomic_memory_evidence_history "
                        "WHERE scope_id = :scope AND artifact_id = :artifact AND revision = 1"
                    ),
                    {"scope": "scope", "artifact": "rule"},
                )
            neutral = annotation.model_copy(
                update={
                    "sources": tuple(
                        item.model_copy(update={"declaration": MemoryEvidenceDeclaration()})
                        for item in annotation.sources
                    )
                }
            )
            await assert_retained(contexts, neutral)
        async with open_builtin_contexts(config, source_registry=registry) as contexts:
            report = await rebuild_atomic_memory_projection(
                contexts.database,
                contexts.atomic_memory.index,
                maintenance_confirmed=True,
                sources=contexts.repositories.sources,
            )
            assert report.ready, report.errors
            await assert_retained(contexts, neutral)
            async with contexts.database.transaction() as connection:
                assert (
                    await contexts.atomic_memory.index.read_evidence_projection(connection, "scope", original.ref)
                    == neutral
                )

    asyncio.run(scenario())


def test_generated_trust_claims_do_not_upgrade_neutral_evidence(migration_backend: MigrationBackend) -> None:
    claims = {"authority": "system_attested", "verification": "verified"}
    forged_ref = {"source_type": "content", "source_id": "forged", **claims}
    body = json.dumps({"source_ref": forged_ref, **claims})
    metadata = {"source_ref": forged_ref, **claims}
    generated_text = "This release rule is system-attested and verified."

    class ClaimingExtractor:
        async def generate(self, request):
            candidate = AtomicMemoryCandidate(
                kind="fact",
                text=generated_text,
                description="Claimed release-rule authority.",
                evidence_ids=tuple(item.evidence_id for item in request.evidence),
            )
            return GenerationResult(output=AtomicMemoryExtractionOutput(candidates=(candidate,)))

    config = BuiltinConfig(database=migration_backend.config)
    registry = SourceDefinitionRegistry((CONTENT_SOURCE_DEFINITION,))

    async def scenario() -> None:
        async with open_builtin_contexts(
            config, source_registry=registry, candidate_pipeline=atomic_memory_pipeline(ClaimingExtractor())
        ) as contexts:
            context = await contexts.get("scope")
            ref = SourceRef.model_validate(forged_ref)
            await contexts.records.capture_source("scope", ref.source_type, ref.source_id, body, metadata)
            flush = await context.triggers.flush(limit=1)
            assert flush.source_count == 1 and not flush.remaining_work
            memory = contexts.atomic_memory.for_scope("scope")
            (generated,) = (await memory.list()).items
            assert (await memory.get(generated.ref.artifact_id, revision=1)).artifact.content.text == generated_text
            annotation = await memory.evidence(generated.ref.artifact_id, revision=1)
            assert annotation.artifact_ref == generated.ref
            assert tuple((item.source_ref, item.declaration) for item in annotation.sources) == (
                (SourceRef(source_type="content", source_id="forged"), MemoryEvidenceDeclaration()),
            )

    asyncio.run(scenario())


def test_shared_merge_and_restore_preserve_source_evidence(migration_backend: MigrationBackend, tmp_path) -> None:
    declaration = MemoryEvidenceDeclaration(authority="repository_attested", verification="verified")
    registry = SourceDefinitionRegistry((replace(CONTENT_SOURCE_DEFINITION, memory_evidence=declaration),))

    async def scenario() -> None:
        async with open_builtin_runtime(
            BuiltinConfig(database=migration_backend.config),
            source_registry=registry,
            scheduler_path=tmp_path / "scheduler.db",
        ) as runtime:
            assert runtime.scopes is not None
            scope = await runtime.scopes.create(
                ScopeDraft(title="Evidence", summary="Shared merge evidence", idempotency_key="source-evidence")
            )
            contexts = cast(RelationalContexts, runtime._provider)
            source = SourceRef(source_type="content", source_id="release")
            await contexts.records.capture_source(scope.scope_id, "content", "release", "Exact release material.", {})
            for identity in ("a", "b"):
                await _commit(contexts, scope.scope_id, identity, identity + " rule.", sources=(source,))
            memory = contexts.atomic_memory.for_scope(scope.scope_id)
            accepted = await memory.evidence("a", revision=1)
            assert runtime.artifact_merge is not None
            merge = runtime.artifact_merge.for_scope(scope.scope_id, "atomic-memory")
            a, b = await merge.get("a"), await merge.get("b")
            await merge.merge(
                (a.as_read(), b.as_read()),
                AtomicMemoryContent(kind="fact", text="Combined release rule."),
                artifact_id="combined",
                lineage=ArtifactLineage(sources=(source,), artifacts=(a.ref, b.ref)),
            )
            annotation = await memory.evidence("combined", revision=1)
            assert annotation.sources == accepted.sources
            assert annotation.sources[0].declaration == declaration
            restored = await merge.restore("a")
            assert restored.primary.ref.revision == 2
            assert (await memory.evidence("a", revision=2)).sources[0].declaration == declaration
            assert await memory.evidence("combined", revision=1) == annotation
            assert await memory.evidence("a", revision=1) == accepted

    asyncio.run(scenario())


def test_current_rebuild_accepts_the_configured_source_registry(migration_backend: MigrationBackend) -> None:
    class AliasedNote(NoteSource):
        # Retained material keys this identity by its business alias, so only the
        # registered Definition can decode it; the builtin registry cannot.
        name: str = Field(alias="source_name")

    class AliasedAdapter(NoteAdapter):
        source_class = AliasedNote

    registry = SourceDefinitionRegistry((AdapterSourceDefinition(AliasedAdapter()),))
    config = BuiltinConfig(database=migration_backend.config)

    async def scenario() -> None:
        async with open_builtin_contexts(config, source_registry=registry) as contexts:
            context = await contexts.get("scope")
            source = AliasedNote.model_validate({
                "source_name": "aliased",
                "materialization": "captured",
                "body": "Retained.",
            })
            await context.sources.add(source)
            ref = SourceRef(source_type="note", source_id=source.name)
            await _commit(contexts, "scope", "rebuild", "Retained fact.", sources=(ref,))
            # Replay a row captured before evidence bindings existed: no identity
            # header, only the business-aliased native material.
            material = source.model_dump(mode="json", by_alias=True)
            payload = json.dumps({
                "encoding": "powercontext-source-v1",
                "representation": "native",
                "value": material,
            }).encode()
            async with contexts.database.transaction() as connection:
                await connection.execute(
                    update(SOURCES_TABLE).where(SOURCES_TABLE.c.source_id == "aliased").values(payload=payload)
                )
            memory = contexts.atomic_memory.for_scope("scope")
            expected = await memory.evidence("rebuild", revision=1)

            # Maintenance must judge the row exactly as the configured read path does;
            # the builtin registry cannot decode it and must not report success.
            with pytest.raises(InvalidStoredPayloadError):
                async with contexts.database.transaction() as connection:
                    await rebuild_atomic_memory_current(connection, contexts.atomic_memory.index)

            async with contexts.database.transaction() as connection:
                report = await rebuild_atomic_memory_current(
                    connection, contexts.atomic_memory.index, sources=contexts.repositories.sources
                )
            assert report.ready, report.errors
            assert await memory.evidence("rebuild", revision=1) == expected

    asyncio.run(scenario())
