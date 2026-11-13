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

"""Real sqlite-vec behavior for the Topic Memory reindex backend."""

from __future__ import annotations

import asyncio
import struct
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

import pytest
from sqlalchemy import delete, func, insert, inspect, select, update
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.builtin.artifacts.atomic_memory import (
    AtomicMemory,
    AtomicMemoryContent,
    AtomicMemoryDraft,
    AtomicMemoryRecord,
)
from powercontext.builtin.artifacts.memory import EmbeddingProfile
from powercontext.builtin.artifacts.topic_memory import TopicMemoryCapabilityError, TopicMemoryStorageInvariantError
from powercontext.builtin.inference import EmbeddingResult
from powercontext.builtin.persistence.artifacts import ArtifactRepository
from powercontext.builtin.persistence.atomic_memory import AtomicMemoryStateRepository
from powercontext.builtin.persistence.atomic_memory_index import (
    AtomicMemoryProjectionPublisher,
    atomic_memory_profile_fingerprint,
)
from powercontext.builtin.persistence.atomic_memory_index_schema import (
    ATOMIC_MEMORY_EVIDENCE_PROJECTION_TABLE,
    ATOMIC_MEMORY_PROJECTION_FORMAT,
)
from powercontext.builtin.persistence.database import AsyncDatabase
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.persistence.sqlite.atomic_memory_index import SQLiteAtomicMemoryIndex
from powercontext.builtin.persistence.sqlite.topic_memory_index import (
    SQLITE_TOPIC_MEMORY_VECTOR_CHUNKS_TABLE,
    SQLITE_TOPIC_MEMORY_VECTOR_TOPICS_TABLE,
    SQLiteTopicMemoryVectorIndex,
)
from powercontext.builtin.persistence.sqlite.topic_memory_reindex import SQLiteTopicMemoryReindexBackend
from powercontext.builtin.persistence.tables import (
    ARTIFACT_HEADS_TABLE,
    ARTIFACTS_TABLE,
    BUILTIN_TABLES,
    TOPIC_MEMORY_ACTIVE_CHUNKS_TABLE,
    TOPIC_MEMORY_ACTIVE_TOPICS_TABLE,
    TOPIC_MEMORY_RETRIEVAL_SHAPE_TABLE,
)
from powercontext.builtin.persistence.topic_memory_index import topic_memory_embedding_profile_fingerprint
from powercontext.builtin.persistence.topic_memory_reindex import (
    TOPIC_MEMORY_REINDEX_STORAGE_PROFILE_TABLE,
    TopicMemoryReindexChunk,
    TopicMemoryReindexSource,
    reindex_topic_memory,
    staged_fingerprint,
)
from powercontext.builtin.runtime.atomic_memory_rebuild import (
    rebuild_atomic_memory_projection,
    verify_atomic_memory_current,
)
from powercontext.server.authz.repository import ACCESS_TABLES

_OLD = EmbeddingProfile(profile_id="old", model="test", dimension=2, distance="l2", normalization="unit")
_TARGET = EmbeddingProfile(profile_id="target", model="test", dimension=3, distance="l2", normalization="unit")
_SOURCES = (
    TopicMemoryReindexSource(
        scope_id="scope-a",
        artifact_id="topic-a",
        revision=4,
        title="Alpha",
        summary="First topic",
        chunks=(
            TopicMemoryReindexChunk(ordinal=0, text="Alpha chunk zero"),
            TopicMemoryReindexChunk(ordinal=1, text="Alpha chunk one"),
        ),
    ),
    TopicMemoryReindexSource(
        scope_id="scope-a",
        artifact_id="topic-b",
        revision=7,
        title="Beta",
        summary="Second topic",
        chunks=(TopicMemoryReindexChunk(ordinal=0, text="Beta chunk zero"),),
    ),
)
_OLD_VECTORS = ((1.0, 0.0), (0.0, 1.0), (1.0, 0.0), (0.0, 1.0), (1.0, 0.0))
_TARGET_VECTORS = (
    (3.0, 4.0, 0.0),
    (0.0, 2.0, 0.0),
    (0.0, 0.0, 4.0),
    (1.0, 0.0, 0.0),
    (0.0, 3.0, 0.0),
)


@dataclass(frozen=True)
class _Store:
    database: AsyncDatabase
    sources: tuple[TopicMemoryReindexSource, ...]


class _TargetEmbedding:
    profile = _TARGET

    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []

    async def embed(self, texts: tuple[str, ...], /) -> EmbeddingResult:
        self.calls.append(texts)
        return EmbeddingResult(vectors=((1.0, 0.0, 0.0),) * len(texts))


class _OldEmbedding:
    profile = _OLD

    async def embed(self, texts: tuple[str, ...], /) -> EmbeddingResult:
        return EmbeddingResult(vectors=((1.0, 0.0),) * len(texts))


async def _create_atomic_authority(connection: AsyncConnection, *, project: bool = True) -> None:
    artifact = await ArtifactRepository((AtomicMemory,)).create(
        connection,
        "scope-a",
        "atomic-authoritative",
        AtomicMemoryDraft(
            content=AtomicMemoryContent(
                kind="fact",
                text="Atomic authority survives coordinated reindexing.",
                description="Coordinated reindex fixture",
            )
        ),
    )
    assert isinstance(artifact, AtomicMemory)
    state = await AtomicMemoryStateRepository().create(connection, "scope-a", artifact.artifact_id)
    if project:
        index = SQLiteAtomicMemoryIndex(_OLD)
        publisher = AtomicMemoryProjectionPublisher(index, embedding_model=_OldEmbedding())
        prepared = await publisher.prepare(artifact.content)
        await publisher.publish(
            connection,
            "scope-a",
            AtomicMemoryRecord(artifact=artifact, state=state),
            prepared,
        )


@asynccontextmanager
async def _open_store(tmp_path: Path, *, managed: bool = False) -> AsyncIterator[_Store]:
    active_index = SQLiteTopicMemoryVectorIndex(_OLD)
    atomic_index = SQLiteAtomicMemoryIndex(_OLD) if managed else None
    tables = BUILTIN_TABLES + ACCESS_TABLES + active_index.tables
    if atomic_index is not None:
        tables += atomic_index.tables
    config = SQLiteConfig(
        url=f"sqlite+aiosqlite:///{tmp_path / ('managed.db' if managed else 'reindex.db')}",
        # These backend tests insert the authoritative projection rows directly;
        # publication and Artifact foreign keys are covered by repository tests.
        foreign_keys=False,
    )
    async with SQLiteProfile.open(config, tables=tables, load_vector_extension=True) as profile:
        async with profile.database.transaction() as connection:
            await active_index.initialize(connection)
            await connection.execute(
                insert(TOPIC_MEMORY_RETRIEVAL_SHAPE_TABLE).values(
                    singleton=1,
                    shape="hybrid",
                    profile_fingerprint=topic_memory_embedding_profile_fingerprint(_OLD),
                )
            )
            await _insert_active_rows(connection)
            if atomic_index is not None:
                await connection.exec_driver_sql(
                    "CREATE TABLE pc_server_storage_profile ("
                    "singleton INTEGER NOT NULL PRIMARY KEY CHECK (singleton=1), "
                    "embedding_profile TEXT NOT NULL)"
                )
                await connection.exec_driver_sql(
                    "INSERT INTO pc_server_storage_profile (singleton, embedding_profile) VALUES (1, ?)",
                    (_OLD.model_dump_json(),),
                )
                await atomic_index.initialize(connection)
        yield _Store(profile.database, _SOURCES)


async def _create_target_atomic_with_damaged_evidence(store: _Store) -> None:
    async with store.database.transaction() as connection:
        await _create_atomic_authority(connection)
    rebuilt = await rebuild_atomic_memory_projection(
        store.database,
        SQLiteAtomicMemoryIndex(_TARGET),
        maintenance_confirmed=True,
        embedding_model=_TargetEmbedding(),
    )
    assert rebuilt.ready, rebuilt.errors
    async with store.database.transaction() as connection:
        await connection.execute(delete(ATOMIC_MEMORY_EVIDENCE_PROJECTION_TABLE))
        reduced = await verify_atomic_memory_current(connection, SQLiteAtomicMemoryIndex(_TARGET))
        complete = await verify_atomic_memory_current(
            connection,
            SQLiteAtomicMemoryIndex(_TARGET),
            require_evidence=True,
            require_evidence_projection=True,
        )
        assert reduced.ready, reduced.errors
        assert not complete.ready
        assert any("evidence projections differ" in error for error in complete.errors)


async def _insert_active_rows(connection: AsyncConnection) -> None:
    fingerprint = topic_memory_embedding_profile_fingerprint(_OLD)
    vector_offset = 0
    chunk_id = 101
    for topic_index, source in enumerate(_SOURCES):
        await connection.execute(
            insert(TOPIC_MEMORY_ACTIVE_TOPICS_TABLE).values(
                scope_id=source.scope_id,
                family="topic-memory",
                artifact_id=source.artifact_id,
                revision=source.revision,
                title=source.title,
                summary=source.summary,
                searchable_text=f"{source.title} {source.summary}",
                source_count=1,
            )
        )
        topic_id = 11 + topic_index
        await connection.execute(
            insert(SQLITE_TOPIC_MEMORY_VECTOR_TOPICS_TABLE).values(
                vector_id=topic_id,
                scope_id=source.scope_id,
                artifact_id=source.artifact_id,
                revision=source.revision,
                profile_fingerprint=fingerprint,
            )
        )
        await _insert_active_vector(
            connection,
            "pc_topic_memory_topic_vec",
            topic_id,
            source.scope_id,
            _OLD_VECTORS[vector_offset],
        )
        vector_offset += 1
        for chunk in source.chunks:
            await connection.execute(
                insert(TOPIC_MEMORY_ACTIVE_CHUNKS_TABLE).values(
                    scope_id=source.scope_id,
                    family="topic-memory",
                    artifact_id=source.artifact_id,
                    revision=source.revision,
                    chunk_ordinal=chunk.ordinal,
                    start_offset=chunk.ordinal * 20,
                    end_offset=chunk.ordinal * 20 + len(chunk.text),
                    chunk_text=chunk.text,
                    searchable_text=chunk.text,
                    policy_version="v1",
                )
            )
            await connection.execute(
                insert(SQLITE_TOPIC_MEMORY_VECTOR_CHUNKS_TABLE).values(
                    vector_id=chunk_id,
                    scope_id=source.scope_id,
                    artifact_id=source.artifact_id,
                    revision=source.revision,
                    chunk_ordinal=chunk.ordinal,
                    profile_fingerprint=fingerprint,
                )
            )
            await _insert_active_vector(
                connection,
                "pc_topic_memory_chunk_vec",
                chunk_id,
                source.scope_id,
                _OLD_VECTORS[vector_offset],
            )
            vector_offset += 1
            chunk_id += 1


async def _insert_active_vector(
    connection: AsyncConnection,
    name: str,
    vector_id: int,
    scope_id: str,
    vector: tuple[float, ...],
) -> None:
    await connection.exec_driver_sql(
        f"INSERT INTO {name} (rowid, scope_id, embedding) VALUES (?, ?, ?)",  # noqa: S608
        (vector_id, scope_id, struct.pack(f"<{len(vector)}f", *vector)),
    )


async def _stage(
    database: AsyncDatabase,
    backend: SQLiteTopicMemoryReindexBackend,
    *,
    update_storage_profile: bool = False,
) -> None:
    async with database.transaction() as connection:
        await backend.prepare(connection, update_storage_profile=update_storage_profile)
        await backend.write_staging(connection, _SOURCES, _TARGET_VECTORS)


async def _active_snapshot(connection: AsyncConnection) -> tuple[object, ...]:
    shape = (
        await connection.execute(
            select(
                TOPIC_MEMORY_RETRIEVAL_SHAPE_TABLE.c.shape,
                TOPIC_MEMORY_RETRIEVAL_SHAPE_TABLE.c.profile_fingerprint,
            )
        )
    ).one()
    statements = (
        "SELECT vector_id, scope_id, artifact_id, revision, profile_fingerprint "
        "FROM pc_topic_memory_vector_topics ORDER BY vector_id",
        "SELECT vector_id, scope_id, artifact_id, revision, chunk_ordinal, profile_fingerprint "
        "FROM pc_topic_memory_vector_chunks ORDER BY vector_id",
        "SELECT m.artifact_id, m.revision, v.embedding FROM pc_topic_memory_vector_topics AS m "
        "JOIN pc_topic_memory_topic_vec AS v ON v.rowid=m.vector_id ORDER BY m.artifact_id",
        "SELECT m.artifact_id, m.chunk_ordinal, v.embedding FROM pc_topic_memory_vector_chunks AS m "
        "JOIN pc_topic_memory_chunk_vec AS v ON v.rowid=m.vector_id ORDER BY m.artifact_id, m.chunk_ordinal",
        "SELECT name, sql FROM sqlite_master WHERE type='table' "
        "AND name IN ('pc_topic_memory_topic_vec', 'pc_topic_memory_chunk_vec') ORDER BY name",
    )
    rows = []
    for statement in statements:
        result = await connection.exec_driver_sql(statement)
        rows.append(tuple(tuple(row) for row in result.all()))
    return (tuple(shape), *rows)


async def _atomic_authority_snapshot(connection: AsyncConnection) -> tuple[object, ...]:
    snapshots = []
    for table in (ARTIFACTS_TABLE, ARTIFACT_HEADS_TABLE):
        rows = (
            await connection.execute(
                select(table).where(table.c.family == AtomicMemory.family).order_by(*table.primary_key.columns)
            )
        ).all()
        snapshots.append(tuple(rows))
    return tuple(snapshots)


async def _table_names(connection: AsyncConnection) -> set[str]:
    return set(await connection.run_sync(lambda sync: inspect(sync).get_table_names()))


def _unpack(vector: bytes) -> tuple[float, ...]:
    return struct.unpack(f"<{len(vector) // 4}f", vector)


def test_prepare_resumes_and_replaces_only_selected_staged_topics(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with _open_store(tmp_path) as store:
            backend = SQLiteTopicMemoryReindexBackend(_TARGET)
            async with store.database.transaction() as connection:
                before = await _active_snapshot(connection)
                await backend.prepare(connection, update_storage_profile=False)
                await backend.write_staging(connection, store.sources, _TARGET_VECTORS)
                other_before = (
                    await connection.exec_driver_sql(
                        "SELECT m.vector_id, v.embedding FROM pc_topic_memory_reindex_topics AS m "
                        "JOIN pc_topic_memory_reindex_topic_vec AS v ON v.rowid=m.vector_id "
                        "WHERE m.artifact_id='topic-b'"
                    )
                ).one()
                await backend.validate_staging(connection)
                await backend.validate_active(connection, topic_memory_embedding_profile_fingerprint(_OLD))
                with pytest.raises(TopicMemoryStorageInvariantError, match="incomplete-vector"):
                    await backend.validate_active(connection, backend.fingerprint)

            replacement = ((8.0, 6.0, 0.0), (0.0, 0.0, 9.0), (0.0, 5.0, 0.0))
            async with store.database.transaction() as connection:
                await backend.prepare(connection, update_storage_profile=False)
                await backend.write_staging(connection, (store.sources[0],), replacement)
                with pytest.raises(TopicMemoryCapabilityError, match="embedding-result"):
                    await backend.write_staging(connection, (store.sources[0],), (*replacement, (1.0, 0.0, 0.0)))
                other_after = (
                    await connection.exec_driver_sql(
                        "SELECT m.vector_id, v.embedding FROM pc_topic_memory_reindex_topics AS m "
                        "JOIN pc_topic_memory_reindex_topic_vec AS v ON v.rowid=m.vector_id "
                        "WHERE m.artifact_id='topic-b'"
                    )
                ).one()
                assert tuple(other_after) == tuple(other_before)
                rewritten = (
                    await connection.exec_driver_sql(
                        "SELECT v.embedding FROM pc_topic_memory_reindex_topics AS m "
                        "JOIN pc_topic_memory_reindex_topic_vec AS v ON v.rowid=m.vector_id "
                        "WHERE m.artifact_id='topic-a'"
                    )
                ).scalar_one()
                assert _unpack(rewritten) == pytest.approx((0.8, 0.6, 0.0))
                await backend.validate_staging(connection)
                await backend.cleanup(connection)
                assert await staged_fingerprint(connection, backend) == backend.fingerprint
                assert await _active_snapshot(connection) == before

    asyncio.run(scenario())


def test_activate_copies_staging_and_recreates_target_vec0(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with _open_store(tmp_path) as store:
            backend = SQLiteTopicMemoryReindexBackend(_TARGET)
            await _stage(store.database, backend)
            async with store.database.transaction() as connection:
                await backend.activate(connection, update_storage_profile=False)

            async with store.database.transaction() as connection:
                assert not {
                    "pc_topic_memory_reindex_topics",
                    "pc_topic_memory_reindex_chunks",
                    "pc_topic_memory_reindex_shape",
                    "pc_topic_memory_reindex_topic_vec",
                    "pc_topic_memory_reindex_chunk_vec",
                } & await _table_names(connection)
                shape = (
                    await connection.execute(
                        select(
                            TOPIC_MEMORY_RETRIEVAL_SHAPE_TABLE.c.shape,
                            TOPIC_MEMORY_RETRIEVAL_SHAPE_TABLE.c.profile_fingerprint,
                        )
                    )
                ).one()
                assert tuple(shape) == ("hybrid", backend.fingerprint)
                rows = (
                    await connection.exec_driver_sql(
                        "SELECT m.artifact_id, v.embedding FROM pc_topic_memory_vector_topics AS m "
                        "JOIN pc_topic_memory_topic_vec AS v ON v.rowid=m.vector_id ORDER BY m.artifact_id"
                    )
                ).all()
                assert [row[0] for row in rows] == ["topic-a", "topic-b"]
                assert _unpack(rows[0][1]) == pytest.approx((0.6, 0.8, 0.0))
                assert _unpack(rows[1][1]) == pytest.approx((1.0, 0.0, 0.0))
                assert (
                    int(
                        await connection.scalar(
                            select(func.count()).select_from(SQLITE_TOPIC_MEMORY_VECTOR_CHUNKS_TABLE)
                        )
                        or 0
                    )
                    == 3
                )
                definitions = (
                    await connection.exec_driver_sql(
                        "SELECT sql FROM sqlite_master WHERE type='table' "
                        "AND name IN ('pc_topic_memory_topic_vec', 'pc_topic_memory_chunk_vec')"
                    )
                ).all()
                assert len(definitions) == 2
                assert all("embedding float[3]" in row[0] for row in definitions)
                await backend.validate_active(connection, backend.fingerprint)
                activated = await _active_snapshot(connection)
                await backend.cleanup(connection)
                assert await _active_snapshot(connection) == activated

    asyncio.run(scenario())


def test_managed_activation_rolls_back_topic_profile_and_atomic_shape(tmp_path: Path) -> None:
    class AbortActivation(RuntimeError):
        pass

    async def scenario() -> None:
        async with _open_store(tmp_path, managed=True) as store:
            backend = SQLiteTopicMemoryReindexBackend(_TARGET)
            await _stage(store.database, backend, update_storage_profile=True)
            async with store.database.transaction() as connection:
                before = await _active_snapshot(connection)
                old_atomic_sql = (
                    await connection.exec_driver_sql(
                        "SELECT sql FROM sqlite_master WHERE name='pc_atomic_memory_current_vec'"
                    )
                ).scalar_one()
                assert "embedding float[2]" in old_atomic_sql

            with pytest.raises(AbortActivation):
                async with store.database.transaction() as connection:
                    await backend.activate(connection, update_storage_profile=True)
                    changed_atomic_sql = (
                        await connection.exec_driver_sql(
                            "SELECT sql FROM sqlite_master WHERE name='pc_atomic_memory_current_vec'"
                        )
                    ).scalar_one()
                    assert "embedding float[3]" in changed_atomic_sql
                    raise AbortActivation

            async with store.database.transaction() as connection:
                assert await _active_snapshot(connection) == before
                assert (
                    await connection.exec_driver_sql("SELECT embedding_profile FROM pc_server_storage_profile")
                ).scalar_one() == _OLD.model_dump_json()
                assert (
                    await connection.exec_driver_sql(
                        "SELECT sql FROM sqlite_master WHERE name='pc_atomic_memory_current_vec'"
                    )
                ).scalar_one() == old_atomic_sql
                await backend.validate_staging(connection)

            # The rolled-back staging area remains valid and can be retried.
            async with store.database.transaction() as connection:
                await backend.activate(connection, update_storage_profile=True)
            async with store.database.transaction() as connection:
                assert (
                    await connection.exec_driver_sql("SELECT embedding_profile FROM pc_server_storage_profile")
                ).scalar_one() == _TARGET.model_dump_json()
                changed_atomic_sql = (
                    await connection.exec_driver_sql(
                        "SELECT sql FROM sqlite_master WHERE name='pc_atomic_memory_current_vec'"
                    )
                ).scalar_one()
                assert "embedding float[3]" in changed_atomic_sql
                assert TOPIC_MEMORY_REINDEX_STORAGE_PROFILE_TABLE not in await _table_names(connection)
                await backend.validate_active(connection, backend.fingerprint)

    asyncio.run(scenario())


def test_managed_reindex_rejects_incompatible_atomic_vectors_before_inference(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with _open_store(tmp_path, managed=True) as store:
            backend = SQLiteTopicMemoryReindexBackend(_TARGET)
            model = _TargetEmbedding()
            async with store.database.transaction() as connection:
                before = await _active_snapshot(connection)
                atomic = SQLiteAtomicMemoryIndex(_OLD)
                await connection.execute(
                    insert(atomic.table).values(
                        scope_id="scope-a",
                        artifact_id="atomic-old",
                        revision=1,
                        state_version=1,
                        content_hash="a" * 64,
                        projection_format=ATOMIC_MEMORY_PROJECTION_FORMAT,
                        kind="fact",
                        text="Retain the old Atomic vector",
                        searchable_text="Retain the old Atomic vector",
                        tag_keys="[]",
                        embedding=struct.pack("<2f", 1.0, 0.0),
                        profile_fingerprint=atomic_memory_profile_fingerprint(_OLD),
                        embedding_input_hash="b" * 64,
                    )
                )

            report = await reindex_topic_memory(
                store.database,
                backend,
                embedding_model=model,
                maintenance_confirmed=True,
            )

            assert not report.ready and not report.activated
            assert any("Atomic Memory vectors are not target-compatible" in error for error in report.errors)
            assert model.calls == []
            async with store.database.transaction() as connection:
                assert await staged_fingerprint(connection, backend) is None
                assert await _active_snapshot(connection) == before

    asyncio.run(scenario())


def test_managed_reindex_activates_with_an_empty_compatible_atomic_projection(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with _open_store(tmp_path, managed=True) as store:
            backend = SQLiteTopicMemoryReindexBackend(_TARGET)
            model = _TargetEmbedding()

            report = await reindex_topic_memory(
                store.database,
                backend,
                embedding_model=model,
                maintenance_confirmed=True,
                batch_size=1,
            )

            assert report.ready and report.activated
            assert report.embedded_vectors == 5
            assert len(model.calls) == 2
            async with store.database.transaction() as connection:
                assert (
                    await connection.exec_driver_sql("SELECT embedding_profile FROM pc_server_storage_profile")
                ).scalar_one() == _TARGET.model_dump_json()
                atomic_sql = (
                    await connection.exec_driver_sql(
                        "SELECT sql FROM sqlite_master WHERE name='pc_atomic_memory_current_vec'"
                    )
                ).scalar_one()
                assert "embedding float[3]" in atomic_sql
                await backend.validate_active(connection, backend.fingerprint)

    asyncio.run(scenario())


@pytest.mark.parametrize("damage", ["missing-row", "null-embedding"])
def test_managed_dry_run_detects_incomplete_atomic_projection(tmp_path: Path, damage: str) -> None:
    async def scenario() -> None:
        async with _open_store(tmp_path, managed=True) as store:
            backend = SQLiteTopicMemoryReindexBackend(_TARGET)
            async with store.database.transaction() as connection:
                await _create_atomic_authority(connection, project=damage == "null-embedding")
                if damage == "null-embedding":
                    await connection.execute(
                        update(SQLiteAtomicMemoryIndex(_OLD).table).values(
                            embedding=None,
                            profile_fingerprint=None,
                            embedding_input_hash=None,
                        )
                    )
                before = await _atomic_authority_snapshot(connection)
                active_before = await _active_snapshot(connection)

            blocked = await reindex_topic_memory(
                store.database,
                backend,
                embedding_model=None,
                maintenance_confirmed=False,
                dry_run=True,
            )
            planned = await reindex_topic_memory(
                store.database,
                backend,
                embedding_model=None,
                maintenance_confirmed=False,
                dry_run=True,
                rebuild_atomic_memory=True,
            )

            assert not blocked.ready
            assert any("explicit Atomic Memory rebuild is required" in error for error in blocked.errors)
            assert planned.ready and planned.atomic_memory_rebuild_required
            assert planned.atomic_memory_rebuilt_rows == 0
            async with store.database.transaction() as connection:
                assert await _atomic_authority_snapshot(connection) == before
                assert await _active_snapshot(connection) == active_before
                assert await staged_fingerprint(connection, backend) is None

    asyncio.run(scenario())


def test_managed_reindex_requires_atomic_evidence_before_activation(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with _open_store(tmp_path, managed=True) as store:
            await _create_target_atomic_with_damaged_evidence(store)
            backend = SQLiteTopicMemoryReindexBackend(_TARGET)
            model = _TargetEmbedding()
            async with store.database.transaction() as connection:
                active_before = await _active_snapshot(connection)
                authority_before = await _atomic_authority_snapshot(connection)

            blocked = await reindex_topic_memory(
                store.database,
                backend,
                embedding_model=model,
                maintenance_confirmed=True,
            )
            planned = await reindex_topic_memory(
                store.database,
                backend,
                embedding_model=None,
                maintenance_confirmed=False,
                dry_run=True,
                rebuild_atomic_memory=True,
            )

            assert not blocked.ready and not blocked.activated
            assert any("explicit Atomic Memory rebuild is required" in error for error in blocked.errors)
            assert planned.ready and planned.atomic_memory_rebuild_required
            assert model.calls == []
            async with store.database.transaction() as connection:
                assert await _active_snapshot(connection) == active_before
                assert await _atomic_authority_snapshot(connection) == authority_before
                assert await staged_fingerprint(connection, backend) is None

    asyncio.run(scenario())


def test_managed_recovery_does_not_cleanup_with_damaged_atomic_evidence(tmp_path: Path) -> None:
    class TrackingBackend(SQLiteTopicMemoryReindexBackend):
        def __init__(self) -> None:
            super().__init__(_TARGET)
            self.cleanup_calls = 0

        async def cleanup(self, connection: AsyncConnection) -> None:
            self.cleanup_calls += 1
            await super().cleanup(connection)

    async def scenario() -> None:
        async with _open_store(tmp_path, managed=True) as store:
            await _create_target_atomic_with_damaged_evidence(store)
            backend = TrackingBackend()
            await _stage(store.database, backend, update_storage_profile=True)
            async with store.database.transaction() as connection:
                await backend.activate(connection, update_storage_profile=True)
                await connection.exec_driver_sql(
                    "CREATE TABLE pc_topic_memory_reindex_previous_shape (singleton INTEGER)"
                )
                active_before = await _active_snapshot(connection)
                complete = await verify_atomic_memory_current(
                    connection,
                    SQLiteAtomicMemoryIndex(_TARGET),
                    require_evidence=True,
                    require_evidence_projection=True,
                )
                assert not complete.ready

            model = _TargetEmbedding()
            report = await reindex_topic_memory(
                store.database,
                backend,
                embedding_model=model,
                maintenance_confirmed=True,
            )

            assert not report.ready and report.activated
            assert any("explicit Atomic Memory rebuild is required" in error for error in report.errors)
            assert backend.cleanup_calls == 0
            assert model.calls == []
            async with store.database.transaction() as connection:
                assert await _active_snapshot(connection) == active_before
                assert "pc_topic_memory_reindex_previous_shape" in await _table_names(connection)

    asyncio.run(scenario())


def test_managed_reindex_can_coordinate_atomic_and_topic_rebuilding(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with _open_store(tmp_path, managed=True) as store:
            backend = SQLiteTopicMemoryReindexBackend(_TARGET)
            async with store.database.transaction() as connection:
                await _create_atomic_authority(connection)
                before = await _atomic_authority_snapshot(connection)
            model = _TargetEmbedding()

            report = await reindex_topic_memory(
                store.database,
                backend,
                embedding_model=model,
                maintenance_confirmed=True,
                rebuild_atomic_memory=True,
            )

            assert report.ready and report.activated
            assert report.atomic_memory_rebuild_required
            assert report.atomic_memory_rebuilt_rows == 1
            assert report.embedded_vectors == 5
            assert len(model.calls) == 2
            async with store.database.transaction() as connection:
                assert await _atomic_authority_snapshot(connection) == before
                assert (
                    await connection.exec_driver_sql("SELECT embedding_profile FROM pc_server_storage_profile")
                ).scalar_one() == _TARGET.model_dump_json()
                verification = await verify_atomic_memory_current(
                    connection,
                    SQLiteAtomicMemoryIndex(_TARGET),
                    require_evidence=True,
                    require_evidence_projection=True,
                )
                assert verification.ready, verification.errors
                await backend.validate_active(connection, backend.fingerprint)

    asyncio.run(scenario())


def test_atomic_commit_survives_topic_failure_and_resumes_without_provider(tmp_path: Path) -> None:
    class FailingBackend(SQLiteTopicMemoryReindexBackend):
        async def activate(self, connection, *, update_storage_profile: bool) -> None:
            await super().activate(connection, update_storage_profile=update_storage_profile)
            raise RuntimeError("injected post-activation interruption")  # noqa: TRY003

    async def scenario() -> None:
        async with _open_store(tmp_path, managed=True) as store:
            failing = FailingBackend(_TARGET)
            async with store.database.transaction() as connection:
                await _create_atomic_authority(connection)
                before = await _atomic_authority_snapshot(connection)

            with pytest.raises(RuntimeError, match="injected post-activation interruption"):
                await reindex_topic_memory(
                    store.database,
                    failing,
                    embedding_model=_TargetEmbedding(),
                    maintenance_confirmed=True,
                    rebuild_atomic_memory=True,
                )

            async with store.database.transaction() as connection:
                shape = (
                    await connection.execute(
                        select(
                            TOPIC_MEMORY_RETRIEVAL_SHAPE_TABLE.c.shape,
                            TOPIC_MEMORY_RETRIEVAL_SHAPE_TABLE.c.profile_fingerprint,
                        )
                    )
                ).one()
                assert tuple(shape) == (
                    "hybrid",
                    topic_memory_embedding_profile_fingerprint(_OLD),
                )
                assert (
                    await connection.exec_driver_sql("SELECT embedding_profile FROM pc_server_storage_profile")
                ).scalar_one() == _OLD.model_dump_json()
                verification = await verify_atomic_memory_current(
                    connection,
                    SQLiteAtomicMemoryIndex(_TARGET),
                    require_evidence=True,
                    require_evidence_projection=True,
                )
                assert verification.ready, verification.errors

            backend = SQLiteTopicMemoryReindexBackend(_TARGET)
            resumed = await reindex_topic_memory(
                store.database,
                backend,
                embedding_model=None,
                maintenance_confirmed=True,
                rebuild_atomic_memory=True,
            )

            assert resumed.ready and resumed.activated
            assert resumed.embedded_vectors == 0
            assert not resumed.atomic_memory_rebuild_required
            assert resumed.atomic_memory_rebuilt_rows == 0
            async with store.database.transaction() as connection:
                assert await _atomic_authority_snapshot(connection) == before
                await backend.validate_active(connection, backend.fingerprint)

    asyncio.run(scenario())


def test_changed_target_rejects_staging_without_replacing_it(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with _open_store(tmp_path) as store:
            first = SQLiteTopicMemoryReindexBackend(_TARGET)
            await _stage(store.database, first, update_storage_profile=True)
            changed_profile = _TARGET.model_copy(update={"profile_id": "changed", "dimension": 4})
            changed = SQLiteTopicMemoryReindexBackend(changed_profile)
            async with store.database.transaction() as connection:
                before = await _active_snapshot(connection)
                with pytest.raises(TopicMemoryStorageInvariantError, match="reindex-staging-shape"):
                    await changed.prepare(connection, update_storage_profile=False)
                assert await staged_fingerprint(connection, changed) == first.fingerprint
                assert int(await connection.scalar(select(func.count()).select_from(first.topic_table)) or 0) == 2
                assert TOPIC_MEMORY_REINDEX_STORAGE_PROFILE_TABLE in await _table_names(connection)
                await first.validate_staging(connection)
                await changed.cleanup(connection)
                assert await staged_fingerprint(connection, changed) == first.fingerprint
                assert await _active_snapshot(connection) == before

    asyncio.run(scenario())
