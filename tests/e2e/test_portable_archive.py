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

"""Portable archive acceptance through the public built-in Runtime."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import importlib.util
import json
import os
import zipfile
from collections import Counter
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import JsonValue, SecretStr
from sqlalchemy import create_engine, event, select, update
from sqlalchemy.engine import make_url

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.memory import EmbeddingProfile, MemoryEntryInput
from powercontext.builtin.artifacts.skill import capture_skill_directory, package_file
from powercontext.builtin.inference import EmbeddingResult
from powercontext.builtin.persistence.oceanbase import OceanBaseConfig
from powercontext.builtin.persistence.seekdb import SeekDBConfig
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.persistence.tables import SCOPES_TABLE, SOURCE_JOURNAL_HEADS_TABLE, SOURCES_TABLE
from powercontext.builtin.persistence.topic_memory import TopicMemoryStorageInvariantError
from powercontext.builtin.portability import BundleConflictError, BundleFormatError, PortableBundleService
from powercontext.builtin.records import ArtifactWrite
from powercontext.builtin.runtime import (
    BuiltinConfig,
    CaptureSource,
    HandoffDraft,
    HandoffSourceCitation,
    HandoffStatement,
    RememberMemoryRequest,
    SubmitSourceObservation,
    open_builtin_contexts,
    open_builtin_runtime,
)
from powercontext.builtin.scope import ScopeDraft
from powercontext.builtin.sources import ContentCapture
from powercontext.builtin.tags import MemoryEntryTagTarget, TagFilter
from powercontext.builtin.work import AcknowledgeHandoff, ReceiverChecks
from powercontext.sources import (
    AdapterSourceDefinition,
    SourceDefinitionRegistry,
    SourceRef,
    manifest_for_definition,
    project_source_for_transport,
)
from tests.builtin.persistence.contract import NoteAdapter, NoteInput, NoteSource


def test_topic_restore_can_retry_after_projection_failure_and_restart(tmp_path: Path) -> None:
    async def authorize(_scopes: tuple[str, ...]) -> None:
        pass

    async def fail_projection(_scopes: tuple[str, ...]) -> None:
        raise RuntimeError("interrupted projection rebuild")  # noqa: TRY003

    async def scenario() -> None:
        archive = tmp_path / "topic.pcb"
        target_config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'target.db'}"))
        async with open_builtin_contexts(BuiltinConfig()) as source:
            scope = await source.scopes.create(ScopeDraft(title="Recovery", summary="Retry", idempotency_key="retry"))
            topic = await source.records.create_artifact(
                scope.scope_id,
                "topic-memory",
                ArtifactWrite(content={"title": "Recovery", "summary": "Retry", "detail": "Retry after interruption."}),
            )
            await source.portability.export([scope.scope_id], archive, authorize=authorize)
        async with open_builtin_contexts(target_config) as target:
            with pytest.raises(RuntimeError, match="interrupted"):
                await PortableBundleService(target.database, projection_rebuilder=fail_projection).restore(archive)
        with pytest.raises(TopicMemoryStorageInvariantError):
            async with open_builtin_contexts(target_config):
                pass
        async with open_builtin_contexts(target_config, _archive_recovery=True) as target:
            assert (await target.portability.restore(archive)).projections_ready
        async with open_builtin_contexts(target_config) as target:
            restored = await target.records.get_artifact(scope.scope_id, "topic-memory", topic.artifact_id)
            assert restored.artifact_id == topic.artifact_id

    asyncio.run(scenario())


def test_sqlite_portable_archive_restores_revisions_lineage_and_handoff_receipts(tmp_path: Path) -> None:
    async def scenario() -> None:
        archive_path = tmp_path / "portable.pcb"
        source_config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'source.db'}"))
        target_config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'target.db'}"))

        async with open_builtin_runtime(source_config) as source_runtime:
            assert source_runtime.scopes is not None
            scope = await source_runtime.scopes.create(
                ScopeDraft(
                    title="Portable archive", summary="Archive recovery fixture", idempotency_key="portable-archive"
                )
            )
            scope_id = scope.scope_id
            source = await source_runtime.sources.for_scope(scope_id).capture(
                CaptureSource(
                    source_id="turn-1",
                    content="The archive must preserve the handoff citation.",
                    metadata={"origin": "portable-archive-e2e"},
                )
            )
            memory = await source_runtime.memory.for_scope(scope_id).remember(
                RememberMemoryRequest(
                    entries=(MemoryEntryInput(kind="decision", text="Use the portable archive for migration."),)
                )
            )
            assert memory.entry is not None
            handoffs = source_runtime.handoff.for_scope(scope_id)
            prepared = await handoffs.finalize(
                HandoffDraft(
                    objective="Restore an exact logical handoff.",
                    state=(
                        HandoffStatement(
                            text="The source citation is durable.",
                            citations=(HandoffSourceCitation(source_ref=source.source_ref),),
                        ),
                    ),
                    disposition="continuable",
                    next_action=HandoffStatement(
                        text="Open the restored handoff.",
                        citations=(HandoffSourceCitation(source_ref=source.source_ref),),
                    ),
                )
            )
            committed = await handoffs.commit(prepared)
            acknowledgement = await source_runtime.work.for_scope(scope_id).acknowledge(
                AcknowledgeHandoff(
                    source_id="handoff-receipt-1",
                    receiver="portable-target",
                    status="accepted",
                    selection="exact",
                    revision=committed.as_ref(),
                    receiver_checks=ReceiverChecks(
                        live_state="confirmed",
                        capability="confirmed",
                        authorization="confirmed",
                    ),
                )
            )
            assert source_runtime.archive is not None

            async def authorize(scopes: tuple[str, ...]) -> None:
                assert scopes == (scope_id,)

            exported = await source_runtime.archive.export([scope_id], archive_path, authorize=authorize)

        async with open_builtin_runtime(target_config) as target_runtime:
            assert target_runtime.archive is not None
            restored = await target_runtime.archive.restore(archive_path)
            latest = await target_runtime.handoff.for_scope(scope_id).latest()
            continuity = await target_runtime.work.for_scope(scope_id).continuity()

            assert restored.record_count == exported.record_count
            assert restored.projections_ready is True
            assert latest == committed
            assert latest is not None
            assert latest.revision == 1
            assert latest.lineage.sources == (source.source_ref,)
            assert continuity.coverage.acknowledgement_records == 1
            assert continuity.coverage.active_receipt_ref == acknowledgement.receipt.source_ref

    asyncio.run(scenario())


def test_archive_restores_tagged_memory_vector_search_and_survives_restart(
    tmp_path: Path, target_config: BuiltinConfig
) -> None:
    class Embedding:
        profile = EmbeddingProfile(profile_id="archive-test", model="archive-test", dimension=3)

        async def embed(self, texts: tuple[str, ...], /) -> EmbeddingResult:
            return EmbeddingResult(vectors=tuple((1.0, 0.0, 0.0) for _ in texts))

    async def scenario() -> None:
        archive = tmp_path / "tagged.pcb"
        source_config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'source.db'}"))
        async with open_builtin_contexts(source_config, embedding_model=Embedding()) as source:
            scope = await source.scopes.create(
                ScopeDraft(title="Archive", summary="Vector recovery", idempotency_key="archive-vector")
            )
            service = (await source.get(scope.scope_id)).artifacts.memory
            memory = await service.remember(
                memory=None, entries=(MemoryEntryInput(kind="fact", text="Portable vector recovery."),), mode="append"
            )
            assert memory is not None
            entry = memory.content.manifest.entries[0]
            tag_target = MemoryEntryTagTarget(artifact_id=memory.artifact_id, entry_id=entry.entry_id)
            empty = await source.records.get_tags(scope.scope_id, tag_target)
            tagged = await source.records.replace_tags(
                scope.scope_id, tag_target, ("archive",), expected_etag=empty.etag
            )

            async def authorize(_scopes: tuple[str, ...]) -> None:
                pass

            await source.portability.export([scope.scope_id], archive, authorize=authorize)
        async with open_builtin_contexts(target_config, embedding_model=Embedding()) as target:
            receipt = await target.portability.restore(archive)
            assert receipt.projections_ready
            assert await target.records.get_tags(scope.scope_id, tag_target) == tagged
        async with open_builtin_contexts(target_config, embedding_model=Embedding()) as target:
            service = (await target.get(scope.scope_id)).artifacts.memory
            for mode in ("fts", "vector", "hybrid"):
                result = await service.search(
                    "portable", memories=(memory,), mode=mode, tag_filter=TagFilter(tags=("archive",))
                )
                assert [hit.entry_id for hit in result.hits] == [entry.entry_id]

    asyncio.run(scenario())


def test_archive_restores_package_files_and_topic_search_after_restart(
    tmp_path: Path, target_config: BuiltinConfig
) -> None:
    async def scenario() -> None:
        package_dir = tmp_path / "portable"
        package_dir.mkdir()
        (package_dir / "SKILL.md").write_text("---\nname: portable\ndescription: Portable guide\n---\nRead guide.md.\n")
        (package_dir / "guide.md").write_text("Exact portable instructions.\n")
        package = capture_skill_directory(package_dir)
        archive = tmp_path / "families.pcb"
        source_config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'source.db'}"))
        async with open_builtin_contexts(source_config) as source:
            scope = await source.scopes.create(
                ScopeDraft(title="Families", summary="Portable families", idempotency_key="families")
            )
            await source.upload_skill_package(scope.scope_id, package.archive_bytes, None, None)
            skill = await source.records.create_artifact(
                scope.scope_id, "skill", ArtifactWrite(content=package.as_skill_content().model_dump(mode="json"))
            )
            topic = await source.records.create_artifact(
                scope.scope_id,
                "topic-memory",
                ArtifactWrite(
                    content={
                        "title": "Portable archives",
                        "summary": "Recovery",
                        "detail": "Portable archive recovery details.",
                    }
                ),
            )
            profile = await source.records.create_artifact(
                scope.scope_id, "profile", ArtifactWrite(content={"content": "# Profile\nUse portable archives.\n"})
            )
            prompt = await source.records.create_artifact(
                scope.scope_id,
                "prompt",
                ArtifactWrite(
                    prompt_key="memory.extract",
                    content={
                        "schema_version": "powercontext.prompt.v1",
                        "mode": "auto",
                        "instructions": "",
                        "demonstrations": [],
                    },
                ),
            )
            expected_profile = await source.records.get_artifact(scope.scope_id, "profile", profile.artifact_id)
            expected_prompt = await source.records.get_artifact(scope.scope_id, "prompt", prompt.artifact_id)

            async def authorize(_scopes: tuple[str, ...]) -> None:
                pass

            exported = await source.portability.export([scope.scope_id], archive, authorize=authorize)
        if target_config.database.kind != "sqlite":
            async with (
                open_builtin_contexts(target_config) as legacy,
                legacy.database.transaction() as connection,
            ):
                await connection.exec_driver_sql(
                    "ALTER TABLE pc_topic_memory_revision_publications MODIFY COLUMN published_at DATETIME NOT NULL"
                )
                await connection.exec_driver_sql(
                    "ALTER TABLE pc_profile_policies MODIFY COLUMN updated_at DATETIME NOT NULL"
                )
        async with open_builtin_contexts(target_config) as target:
            receipt = await target.portability.restore(archive)
            assert receipt.projections_ready
            repeated = await target.portability.restore(archive)
            assert repeated.inserted == 0
        async with open_builtin_contexts(target_config) as target:
            restored = await target.skill_package(
                scope.scope_id, ArtifactRef(family="skill", artifact_id=skill.artifact_id, revision=skill.revision)
            )
            assert restored.archive_bytes == package.archive_bytes
            assert package_file(restored, "guide.md") == b"Exact portable instructions.\n"
            assert await target.records.get_artifact(scope.scope_id, "profile", profile.artifact_id) == expected_profile
            assert await target.records.get_artifact(scope.scope_id, "prompt", prompt.artifact_id) == expected_prompt
            async with target.database.transaction() as connection:
                found = await target.repositories.topic_memories.search(connection, scope.scope_id, "portable", limit=5)
            assert [hit.artifact_ref.artifact_id for hit in found.hits] == [topic.artifact_id]
            roundtrip = tmp_path / "roundtrip.pcb"
            reexported = await target.portability.export([scope.scope_id], roundtrip, authorize=authorize)
            assert reexported.total_digest == exported.total_digest
        reverse_config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'reverse.db'}"))
        async with open_builtin_contexts(reverse_config) as reverse:
            assert (await reverse.portability.restore(roundtrip)).projections_ready
            assert (
                await reverse.records.get_artifact(scope.scope_id, "profile", profile.artifact_id) == expected_profile
            )

    asyncio.run(scenario())


@pytest.fixture(params=["sqlite", "seekdb", "server"])
def target_config(tmp_path: Path, short_tmp_path: Path, request: pytest.FixtureRequest) -> Iterator[BuiltinConfig]:
    if request.param == "seekdb":
        if importlib.util.find_spec("pylibseekdb") is None:
            pytest.skip("install powercontext[seekdb] for the embedded database")
        yield BuiltinConfig(database=SeekDBConfig(path=short_tmp_path / "seekdb"))
    elif request.param == "server":
        live_url = os.environ.get("POWERCONTEXT_TEST_OCEANBASE_URL")
        if not live_url:
            pytest.skip("set POWERCONTEXT_TEST_OCEANBASE_URL for an isolated server database")
        url = make_url(live_url)
        name = "pc_archive_" + uuid4().hex
        engine = create_engine(url.set(drivername="mysql+pymysql"), hide_parameters=True)
        try:
            with engine.begin() as connection:
                connection.exec_driver_sql(f"CREATE DATABASE `{name}`")
            try:
                yield BuiltinConfig(
                    database=OceanBaseConfig(
                        url=SecretStr(url.set(database=name).render_as_string(hide_password=False))
                    )
                )
            finally:
                with engine.begin() as connection:
                    connection.exec_driver_sql(f"DROP DATABASE `{name}`")
        finally:
            engine.dispose()
    else:
        yield BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'target.db'}"))


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("target_definition", ["absent", "identical", "conflicting"])
def test_archive_preserves_remote_observations_without_worker_adapter(
    tmp_path: Path, legacy: bool, target_definition: str, target_config: BuiltinConfig
) -> None:

    async def scenario() -> None:
        definition = AdapterSourceDefinition(NoteAdapter())
        registry = SourceDefinitionRegistry((definition,))
        manifest = manifest_for_definition(definition)
        observation = project_source_for_transport(
            registry, await registry.resolve(NoteInput(note_id="one", body="remote"))
        )
        archive = tmp_path / "remote.pcb"

        async def authorize(_scopes: tuple[str, ...]) -> None:
            pass

        async with open_builtin_contexts(BuiltinConfig()) as source:
            scope = await source.scopes.create(
                ScopeDraft(title="Remote", summary="Remote sources", idempotency_key="remote")
            )
            await source.register_source_definition(manifest)

            class UnrelatedAdapter(NoteAdapter):
                name = "unrelated-note"

            await source.register_source_definition(
                manifest_for_definition(AdapterSourceDefinition(UnrelatedAdapter()))
            )
            await source.submit_source_observation(
                SubmitSourceObservation(scope_id=scope.scope_id, observation=observation)
            )
            if legacy:
                async with source.database.transaction() as connection:
                    await connection.execute(
                        update(SOURCES_TABLE).values(payload=observation.model_dump_json().encode())
                    )
            await source.portability.export([scope.scope_id], archive, authorize=authorize)
            assert (await source.portability.inspect(archive)).records_by_type["source_definition"] == 1
        async with open_builtin_contexts(target_config) as target:
            if target_definition == "identical":
                await target.register_source_definition(manifest)
            elif target_definition == "conflicting":

                class DifferentNoteSource(NoteSource):
                    category: str = "changed"

                class DifferentNoteAdapter(NoteAdapter):
                    source_class = DifferentNoteSource

                await target.register_source_definition(
                    manifest_for_definition(AdapterSourceDefinition(DifferentNoteAdapter()))
                )
            validation = await target.portability.validate(archive)
            if target_definition == "conflicting":
                assert not validation.compatible
                assert validation.conflicts == 1
                with pytest.raises(BundleConflictError):
                    await target.portability.restore(archive)
                async with target.database.transaction() as connection:
                    assert (
                        await connection.scalar(
                            select(SCOPES_TABLE.c.scope_id).where(SCOPES_TABLE.c.scope_id == scope.scope_id)
                        )
                        is None
                    )
                return
            assert validation.compatible
            assert validation.unsupported_source_types == ()
            assert (await target.portability.restore(archive)).projections_ready
        async with open_builtin_contexts(target_config) as target:
            async with target.database.transaction() as connection:
                stored = await target.repositories.sources.get(
                    connection,
                    scope.scope_id,
                    SourceRef(source_type=observation.source_type, source_id=observation.name),
                )
                assert stored.value == observation
                assert (
                    await target.repositories.source_definitions.get(connection, manifest.name, manifest.version)
                    == manifest
                )
            # Continuing ingestion must also work without registering the manifest again.
            assert (
                await target.submit_source_observation(
                    SubmitSourceObservation(scope_id=scope.scope_id, observation=observation)
                )
            ).sequence == 1
            roundtrip = tmp_path / "remote-roundtrip.pcb"
            await target.portability.export([scope.scope_id], roundtrip, authorize=authorize)
        async with open_builtin_contexts(BuiltinConfig()) as reverse:
            assert (await reverse.portability.restore(roundtrip)).projections_ready
            async with reverse.database.transaction() as connection:
                restored = await reverse.repositories.sources.get(
                    connection,
                    scope.scope_id,
                    SourceRef(source_type=observation.source_type, source_id=observation.name),
                )
                assert restored.value == observation

    asyncio.run(scenario())


def test_archive_snapshot_survives_concurrent_mysql_capture(tmp_path: Path, target_config: BuiltinConfig) -> None:

    if target_config.database.kind == "sqlite":
        pytest.skip("SQLite snapshot has a separate concurrent-writer regression")

    async def scenario() -> None:
        async with open_builtin_contexts(target_config) as source:
            scope = await source.scopes.create(
                ScopeDraft(title="Snapshot", summary="Concurrent writes", idempotency_key="snapshot")
            )
            context = await source.get(scope.scope_id)

            await context.sources.capture(ContentCapture(source_id="before", content="before"))
            async with source.database.transaction() as connection:
                await connection.exec_driver_sql("SET SESSION TRANSACTION ISOLATION LEVEL READ COMMITTED")
            captured = False

            async def write_concurrently(_driver):
                # A separate task owns a separate transaction/connection.
                await asyncio.create_task(context.sources.capture(ContentCapture(source_id="after", content="after")))

            def after_read(connection, _cursor, statement, _parameters, _context, _many):
                nonlocal captured
                if not captured and statement.startswith("SELECT pc_source_journal_heads."):
                    captured = True
                    connection.connection.dbapi_connection.run_async(write_concurrently)

            async def authorize(_scopes: tuple[str, ...]) -> None:
                pass

            event.listen(source.database.engine.sync_engine, "after_cursor_execute", after_read)
            try:
                archive = tmp_path / "snapshot.pcb"
                await source.portability.export([scope.scope_id], archive, authorize=authorize)
            finally:
                event.remove(source.database.engine.sync_engine, "after_cursor_execute", after_read)
            assert captured
        async with open_builtin_contexts(BuiltinConfig()) as target:
            assert (await target.portability.validate(archive)).compatible
            await target.portability.restore(archive)
            async with target.database.transaction() as connection:
                assert (
                    await connection.scalar(
                        select(SOURCE_JOURNAL_HEADS_TABLE.c.position).where(
                            SOURCE_JOURNAL_HEADS_TABLE.c.scope_id == scope.scope_id
                        )
                    )
                    == 1
                )
                assert (
                    await connection.execute(
                        select(SOURCES_TABLE.c.source_id).where(SOURCES_TABLE.c.scope_id == scope.scope_id)
                    )
                ).scalars().all() == ["before"]

    asyncio.run(scenario())


@pytest.mark.parametrize("damage", ["missing_definition", "fingerprint", "identity", "native"])
def test_restore_rejects_invalid_remote_source_dependencies_before_writing(tmp_path: Path, damage: str) -> None:

    async def scenario() -> None:
        definition = AdapterSourceDefinition(NoteAdapter())
        registry = SourceDefinitionRegistry((definition,))
        observation = project_source_for_transport(
            registry, await registry.resolve(NoteInput(note_id="one", body="remote"))
        )
        archive = tmp_path / "invalid.pcb"

        async def authorize(_scopes: tuple[str, ...]) -> None:
            pass

        async with open_builtin_contexts(BuiltinConfig()) as source:
            scope = await source.scopes.create(
                ScopeDraft(title="Remote", summary="Dependencies", idempotency_key="remote")
            )
            await source.register_source_definition(manifest_for_definition(definition))
            await source.submit_source_observation(
                SubmitSourceObservation(scope_id=scope.scope_id, observation=observation)
            )
            await source.portability.export([scope.scope_id], archive, authorize=authorize)
        with zipfile.ZipFile(archive) as bundle:
            manifest = json.loads(bundle.read("manifest.json"))
            records = [json.loads(line) for line in bundle.read("records.ndjson").splitlines()]
        if damage == "missing_definition":
            records = [record for record in records if record["record_type"] != "source_definition"]
        else:
            record = next(record for record in records if record["record_type"] == "source")
            envelope = json.loads(base64.b64decode(record["payload"]["payload"]["base64"]))
            if damage == "fingerprint":
                envelope["value"]["definition_fingerprint"] = "sha256:" + "0" * 64
            elif damage == "identity":
                record["identity"]["source_id"] = "another"
            else:
                envelope["representation"] = "native"
                envelope["value"] = envelope["value"]["payload"]
            record["payload"]["payload"]["base64"] = base64.b64encode(json.dumps(envelope).encode()).decode()
        for record in records:
            record.pop("digest")
            record["digest"] = (
                "sha256:"
                + hashlib.sha256(
                    json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
                ).hexdigest()
            )
        manifest["record_count"] = len(records)
        manifest["records_by_type"] = dict(Counter(record["record_type"] for record in records))
        manifest["total_digest"] = (
            "sha256:" + hashlib.sha256("\n".join(record["digest"] for record in records).encode()).hexdigest()
        )
        with zipfile.ZipFile(archive, "w") as bundle:
            bundle.writestr("manifest.json", json.dumps(manifest))
            bundle.writestr("records.ndjson", "\n".join(json.dumps(record) for record in records) + "\n")
        async with open_builtin_contexts(BuiltinConfig()) as target:
            if damage == "native":
                validation = await target.portability.validate(archive)
                assert not validation.compatible
                assert validation.unsupported_source_types == ("note",)
            else:
                with pytest.raises(BundleFormatError):
                    await target.portability.validate(archive)
            with pytest.raises(BundleFormatError):
                await target.portability.restore(archive)
            async with target.database.transaction() as connection:
                assert (
                    await connection.scalar(
                        select(SCOPES_TABLE.c.scope_id).where(SCOPES_TABLE.c.scope_id == scope.scope_id)
                    )
                    is None
                )

    asyncio.run(scenario())


async def _replay_recurrence(contexts, scope_id):
    from powercontext.builtin.evidence.resolver import EvidenceResolver
    from powercontext.builtin.runtime.recurrence import RelationalRecurrenceLedger

    async def no_memory(*_args):
        raise AssertionError("fixture has no Memory citations")  # noqa: TRY003

    repositories = contexts.repositories
    ledger = RelationalRecurrenceLedger(
        database=contexts.database,
        scope_id=scope_id,
        sources=repositories.sources,
        artifacts=repositories.artifacts,
        recurrence=repositories.recurrence,
        evidence=EvidenceResolver(
            scope_id=scope_id,
            sources=repositories.sources,
            artifacts=repositories.artifacts,
            memory_reader=no_memory,
        ),
    )
    async with contexts.database.transaction() as connection:
        position = await repositories.sources.journal_position(connection, scope_id)
        rows = await repositories.sources.list_window(connection, scope_id, after=0, through=position)
        await ledger.record_window(connection, rows)
        return (
            await repositories.recurrence.matches(connection, scope_id),
            await repositories.recurrence.observations(connection, scope_id),
        )


async def _recurrence_archive(tmp_path):
    from powercontext.builtin.artifacts.handoff.models import HandoffArtifactCitation
    from powercontext.builtin.work.models import HandoffReceipt, TaskCheck, TaskOutcome, WorkClaim

    archive = tmp_path / "recurrence.pcb"
    async with open_builtin_contexts(BuiltinConfig()) as source:
        scope = await source.scopes.create(
            ScopeDraft(title="Recurrence", summary="Frozen decisions", idempotency_key="recurrence")
        )
        sid = scope.scope_id
        content: dict[str, JsonValue] = {
            "situation": "Contract changed",
            "action": "Regenerate client",
            "outcome": "Client agrees",
            "lesson": "Regenerate after changing the contract",
            "failure": {
                "signature": {"recall_cue": "client is stale", "symptom": "client diverges"},
                "repair_surface": "experience_content",
                "verification": {"condition": "contract changed", "check_subject": "client agrees"},
            },
        }
        experience = await source.records.create_artifact(sid, "experience", ArtifactWrite(content=content))
        ref = ArtifactRef(family="experience", artifact_id=experience.artifact_id, revision=1)
        citation = HandoffArtifactCitation(artifact_ref=ref)
        handoff = await source.records.create_artifact(
            sid,
            "handoff",
            ArtifactWrite(
                content={
                    "objective": "Regenerate client",
                    "disposition": "continuable",
                    "state": [{"text": "Use this experience", "citations": [citation.model_dump(mode="json")]}],
                }
            ),
        )
        context = await source.get(sid)
        receipt = HandoffReceipt(
            receiver="archive-test",
            status="accepted",
            selection="exact",
            evidence_status="available",
            selected_revision=ArtifactRef(family="handoff", artifact_id=handoff.artifact_id, revision=1),
        )
        await context.sources.capture(
            ContentCapture(
                source_id="receipt",
                content=receipt.model_dump_json(),
                metadata={"kind": "handoff-receipt"},
            )
        )
        receipt_ref = SourceRef(source_type="content", source_id="receipt")
        for name, linked in (("linked-failure", True), ("unlinked-failure", False)):
            outcome = TaskOutcome(
                objective="Regenerate client",
                summary="Failed",
                status="failed",
                observations=(WorkClaim(text="Client regeneration failed", basis="declared"),),
                handoff_receipt_ref=receipt_ref if linked else None,
                checks=(TaskCheck(name="client is stale", status="failed", basis="verified", evidence=(citation,)),),
            )
            await context.sources.capture(
                ContentCapture(
                    source_id=name,
                    content=outcome.model_dump_json(),
                    metadata={"kind": "task-outcome"},
                )
            )
        success = TaskOutcome(
            objective="Regenerate client",
            summary="Passed",
            status="succeeded",
            handoff_receipt_ref=receipt_ref,
            observations=(WorkClaim(text="contract changed", basis="verified", evidence=(citation,)),),
            checks=(TaskCheck(name="client agrees", status="passed", basis="verified", evidence=(citation,)),),
        )
        await context.sources.capture(
            ContentCapture(
                source_id="success",
                content=success.model_dump_json(),
                metadata={"kind": "task-outcome"},
            )
        )
        expected = await _replay_recurrence(source, sid)
        assert len(expected[0]) == 2
        assert len(expected[1]) == 5
        assert {event.event for event in expected[1]} == {"selected", "recurred", "avoided"}
        assert all(match.artifact_ref == ref for match in expected[0])
        # Remove the signature from the current head. Recomputing an unlinked
        # failure against this head would lose the original revision-1 match.
        content["failure"] = None
        await source.records.replace_artifact(
            sid,
            "experience",
            experience.artifact_id,
            '"revision:1"',
            ArtifactWrite(content=content),
        )
        assert await _replay_recurrence(source, sid) == expected

        async def authorize(_scopes):
            pass

        await source.portability.export([sid], archive, authorize=authorize)
    return archive, sid, experience.artifact_id, expected


def test_archive_preserves_frozen_recurrence_after_restart(tmp_path: Path, target_config: BuiltinConfig) -> None:
    async def scenario():
        archive, sid, artifact_id, expected = await _recurrence_archive(tmp_path)
        async with open_builtin_contexts(target_config) as target:
            assert (await target.portability.validate(archive)).compatible
            assert (await target.portability.restore(archive)).projections_ready
            assert (await target.portability.restore(archive)).inserted == 0
        async with open_builtin_contexts(target_config) as target:
            assert (await target.records.get_artifact(sid, "experience", artifact_id)).revision == 2
            async with target.database.transaction() as connection:
                assert await target.repositories.recurrence.matches(connection, sid) == expected[0]
                assert await target.repositories.recurrence.observations(connection, sid) == expected[1]
            assert await _replay_recurrence(target, sid) == expected
            assert await _replay_recurrence(target, sid) == expected

            async def authorize(_scopes):
                pass

            roundtrip = tmp_path / "recurrence-roundtrip.pcb"
            await target.portability.export([sid], roundtrip, authorize=authorize)
        async with open_builtin_contexts(BuiltinConfig()) as reverse:
            await reverse.portability.restore(roundtrip)
            assert await _replay_recurrence(reverse, sid) == expected

    asyncio.run(scenario())


def _rewrite_archive_records(archive, transform):
    with zipfile.ZipFile(archive) as bundle:
        manifest = json.loads(bundle.read("manifest.json"))
        records = [json.loads(line) for line in bundle.read("records.ndjson").splitlines()]
    records = transform(records)
    for record in records:
        record.pop("digest", None)
        record["digest"] = (
            "sha256:"
            + hashlib.sha256(
                json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
        )
    manifest["record_count"] = len(records)
    manifest["records_by_type"] = dict(Counter(record["record_type"] for record in records))
    manifest["total_digest"] = (
        "sha256:" + hashlib.sha256("\n".join(record["digest"] for record in records).encode()).hexdigest()
    )
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("manifest.json", json.dumps(manifest))
        bundle.writestr("records.ndjson", "\n".join(json.dumps(record) for record in records) + "\n")


@pytest.mark.parametrize("damage", ["outcome", "receipt", "revision", "handoff", "match", "payload", "key", "position"])
def test_archive_rejects_broken_recurrence_before_writing(tmp_path: Path, damage: str) -> None:
    async def scenario():
        archive, sid, _, _ = await _recurrence_archive(tmp_path)

        def damage_records(records):
            if damage in {"outcome", "receipt"}:
                source_id = "unlinked-failure" if damage == "outcome" else "receipt"
                return [
                    r for r in records if not (r["record_type"] == "source" and r["identity"]["source_id"] == source_id)
                ]
            if damage in {"revision", "handoff"}:
                family = "experience" if damage == "revision" else "handoff"
                return [
                    r
                    for r in records
                    if not (
                        r["record_type"] == "artifact_revision"
                        and r["identity"]["family"] == family
                        and r["identity"]["revision"] == 1
                    )
                ]
            if damage == "match":
                return [r for r in records if r["record_type"] != "recurrence_match"]
            if damage == "position":
                record = next(
                    r
                    for r in records
                    if r["record_type"] == "source" and r["identity"]["source_id"] == "unlinked-failure"
                )
                record["payload"]["journal_position"] = 1
                return records
            record = next(r for r in records if r["record_type"] == "recurrence_observation")
            if damage == "payload":
                record["payload"]["payload"] = {"base64": base64.b64encode(b"{}").decode()}
            else:
                record["payload"]["selection_key"] = "sha256:" + "0" * 64
            return records

        _rewrite_archive_records(archive, damage_records)
        async with open_builtin_contexts(BuiltinConfig()) as target:
            with pytest.raises(BundleFormatError):
                await target.portability.validate(archive)
            with pytest.raises(BundleFormatError):
                await target.portability.restore(archive)
            async with target.database.transaction() as connection:
                assert (
                    await connection.scalar(select(SCOPES_TABLE.c.scope_id).where(SCOPES_TABLE.c.scope_id == sid))
                    is None
                )

    asyncio.run(scenario())


def test_archive_rejects_recomputed_recurrence_without_overwriting_history(tmp_path: Path) -> None:
    async def scenario():
        archive, sid, _, expected = await _recurrence_archive(tmp_path)
        without_history = tmp_path / "without-history.pcb"
        without_history.write_bytes(archive.read_bytes())
        _rewrite_archive_records(
            without_history,
            lambda records: [r for r in records if not r["record_type"].startswith("recurrence_")],
        )
        async with open_builtin_contexts(BuiltinConfig()) as target:
            await target.portability.restore(without_history)
            recomputed = await _replay_recurrence(target, sid)
            assert recomputed != expected
            assert any(match.result == "unmatched" for match in recomputed[0])
            validation = await target.portability.validate(archive)
            assert not validation.compatible
            assert validation.conflicts > 0
            with pytest.raises(BundleConflictError):
                await target.portability.restore(archive)
            assert await _replay_recurrence(target, sid) == recomputed

    asyncio.run(scenario())
