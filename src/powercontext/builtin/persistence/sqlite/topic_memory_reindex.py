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

"""Transactional sqlite-vec staging and activation for Topic Memory reindexing."""

from __future__ import annotations

import re
import struct
from collections.abc import Sequence
from typing import Any

from aiosqlite import Connection as SQLiteConnection
from sqlalchemy import (
    CheckConstraint,
    Column,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    column,
    delete,
    func,
    insert,
    inspect,
    select,
    table,
    text,
)
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncConnection
from sqlalchemy.sql import ColumnElement

from powercontext.builtin.artifacts.memory import EmbeddingProfile
from powercontext.builtin.artifacts.memory.canonical import canonical_embedding
from powercontext.builtin.artifacts.topic_memory import TopicMemoryCapabilityError, TopicMemoryStorageInvariantError
from powercontext.builtin.persistence.sqlite.atomic_memory_index import SQLiteAtomicMemoryIndex
from powercontext.builtin.persistence.sqlite.topic_memory_index import (
    SQLITE_TOPIC_MEMORY_VECTOR_CHUNKS_TABLE,
    SQLITE_TOPIC_MEMORY_VECTOR_TOPICS_TABLE,
)
from powercontext.builtin.persistence.tables import (
    TOPIC_MEMORY_RETRIEVAL_SHAPE_TABLE,
    identity_string,
)
from powercontext.builtin.persistence.topic_memory_index import (
    topic_memory_embedding_profile_fingerprint,
    validate_current_topic_vectors,
)
from powercontext.builtin.persistence.topic_memory_reindex import (
    TOPIC_MEMORY_REINDEX_CHUNK_TABLE,
    TOPIC_MEMORY_REINDEX_CHUNK_VEC_TABLE,
    TOPIC_MEMORY_REINDEX_SHAPE_TABLE,
    TOPIC_MEMORY_REINDEX_STORAGE_PROFILE_TABLE,
    TOPIC_MEMORY_REINDEX_TOPIC_TABLE,
    TOPIC_MEMORY_REINDEX_TOPIC_VEC_TABLE,
    TopicMemoryReindexSource,
    staged_fingerprint,
    validate_staging_vectors,
)
from powercontext.limits import MAX_ARTIFACT_ID_LENGTH, MAX_SCOPE_ID_LENGTH

_ACTIVE_TOPIC_VEC_TABLE = "pc_topic_memory_topic_vec"
_ACTIVE_CHUNK_VEC_TABLE = "pc_topic_memory_chunk_vec"
_TOPIC_COLUMNS = "vector_id, scope_id, artifact_id, revision, profile_fingerprint"
_CHUNK_COLUMNS = "vector_id, scope_id, artifact_id, revision, chunk_ordinal, profile_fingerprint"
_VEC0_DIMENSION = re.compile(r"\bembedding\s+float\s*\[\s*(\d+)\s*\]", re.IGNORECASE)
_STAGING_TABLE_NAMES = frozenset({
    TOPIC_MEMORY_REINDEX_TOPIC_TABLE,
    TOPIC_MEMORY_REINDEX_CHUNK_TABLE,
    TOPIC_MEMORY_REINDEX_SHAPE_TABLE,
    TOPIC_MEMORY_REINDEX_STORAGE_PROFILE_TABLE,
    TOPIC_MEMORY_REINDEX_TOPIC_VEC_TABLE,
    TOPIC_MEMORY_REINDEX_CHUNK_VEC_TABLE,
})


class SQLiteTopicMemoryReindexBackend:
    """Build a complete target vector projection without changing active vectors."""

    staging_progress_tables = _STAGING_TABLE_NAMES - {TOPIC_MEMORY_REINDEX_STORAGE_PROFILE_TABLE}

    def __init__(self, profile: EmbeddingProfile) -> None:
        if profile.dimension < 1 or profile.distance != "l2" or profile.normalization != "unit":
            raise TopicMemoryCapabilityError(
                "vector",
                "sqlite-vec requires a positive unit-normalized L2 embedding profile",
            )
        self.profile = profile
        self.fingerprint = topic_memory_embedding_profile_fingerprint(profile)
        metadata = MetaData()
        self.topic_table = Table(
            TOPIC_MEMORY_REINDEX_TOPIC_TABLE,
            metadata,
            Column("vector_id", Integer, primary_key=True, autoincrement=True),
            Column("scope_id", identity_string(MAX_SCOPE_ID_LENGTH), nullable=False),
            Column("artifact_id", identity_string(MAX_ARTIFACT_ID_LENGTH), nullable=False),
            Column("revision", Integer, nullable=False),
            Column("profile_fingerprint", String(64), nullable=False),
            UniqueConstraint("scope_id", "artifact_id", name="uq_pc_topic_memory_reindex_topics_active"),
            sqlite_autoincrement=True,
        )
        self.chunk_table = Table(
            TOPIC_MEMORY_REINDEX_CHUNK_TABLE,
            metadata,
            Column("vector_id", Integer, primary_key=True, autoincrement=True),
            Column("scope_id", identity_string(MAX_SCOPE_ID_LENGTH), nullable=False),
            Column("artifact_id", identity_string(MAX_ARTIFACT_ID_LENGTH), nullable=False),
            Column("revision", Integer, nullable=False),
            Column("chunk_ordinal", Integer, nullable=False),
            Column("profile_fingerprint", String(64), nullable=False),
            UniqueConstraint(
                "scope_id",
                "artifact_id",
                "chunk_ordinal",
                name="uq_pc_topic_memory_reindex_chunks_active",
            ),
            sqlite_autoincrement=True,
        )
        self.shape_table = Table(
            TOPIC_MEMORY_REINDEX_SHAPE_TABLE,
            metadata,
            Column("singleton", Integer, primary_key=True, autoincrement=False),
            Column("shape", identity_string(16), nullable=False),
            Column("profile_fingerprint", identity_string(64), nullable=False),
            CheckConstraint("singleton = 1", name="ck_pc_topic_memory_reindex_shape_singleton"),
            CheckConstraint("shape = 'hybrid'", name="ck_pc_topic_memory_reindex_shape_value"),
        )
        self.storage_profile_table = Table(
            TOPIC_MEMORY_REINDEX_STORAGE_PROFILE_TABLE,
            metadata,
            Column("singleton", Integer, primary_key=True, autoincrement=False),
            Column("embedding_profile", Text, nullable=False),
            CheckConstraint("singleton = 1", name="ck_pc_topic_memory_reindex_storage_profile_singleton"),
        )
        self.tables = (self.topic_table, self.chunk_table, self.shape_table)

    def topic_vector_present(self, vectors: Any, /) -> ColumnElement[bool]:
        return _vector_present(
            vectors,
            TOPIC_MEMORY_REINDEX_TOPIC_VEC_TABLE,
            dimension=self.profile.dimension,
        )

    def chunk_vector_present(self, vectors: Any, /) -> ColumnElement[bool]:
        return _vector_present(
            vectors,
            TOPIC_MEMORY_REINDEX_CHUNK_VEC_TABLE,
            dimension=self.profile.dimension,
        )

    async def validate_active(self, connection: AsyncConnection, fingerprint: str | None, /) -> None:
        """Validate active metadata and native rows against the current profile."""

        if fingerprint is None:
            # FTS-only generations have no vector projection to validate. The
            # authoritative Topic/chunk rows remain the reindex source.
            return
        dimension = self.profile.dimension if fingerprint == self.fingerprint else None
        await validate_current_topic_vectors(
            connection,
            SQLITE_TOPIC_MEMORY_VECTOR_TOPICS_TABLE,
            SQLITE_TOPIC_MEMORY_VECTOR_CHUNKS_TABLE,
            fingerprint,
            topic_present=_vector_present(
                SQLITE_TOPIC_MEMORY_VECTOR_TOPICS_TABLE,
                _ACTIVE_TOPIC_VEC_TABLE,
                dimension=dimension,
            ),
            chunk_present=_vector_present(
                SQLITE_TOPIC_MEMORY_VECTOR_CHUNKS_TABLE,
                _ACTIVE_CHUNK_VEC_TABLE,
                dimension=dimension,
            ),
        )

    async def validate_staging(self, connection: AsyncConnection, /) -> None:
        """Require current staged rows and native vectors in the target retrieval space."""

        await self._require_target_staging(connection)
        await _require_vec0_dimensions(
            connection,
            (TOPIC_MEMORY_REINDEX_TOPIC_VEC_TABLE, TOPIC_MEMORY_REINDEX_CHUNK_VEC_TABLE),
            self.profile.dimension,
        )
        await validate_staging_vectors(connection, self)
        if TOPIC_MEMORY_REINDEX_STORAGE_PROFILE_TABLE in await _table_names(connection):
            await self._require_staged_storage_profile(connection)

    async def prepare(self, connection: AsyncConnection, *, update_storage_profile: bool) -> None:
        """Create a target staging area or resume its matching committed progress."""

        _require_sqlite(connection)
        names = await _table_names(connection)
        current = await staged_fingerprint(connection, self)
        if current is None and names & _STAGING_TABLE_NAMES:
            # A missing declaration row or table makes every staged row and
            # native vector untrustworthy. Only derived staging is discarded.
            await _drop_staging(connection)
        if current is not None and current != self.fingerprint:
            raise TopicMemoryStorageInvariantError("reindex-staging-shape", 1)
        for metadata in self.tables:
            await connection.run_sync(lambda sync, selected=metadata: selected.create(sync, checkfirst=True))
        for name in (TOPIC_MEMORY_REINDEX_TOPIC_VEC_TABLE, TOPIC_MEMORY_REINDEX_CHUNK_VEC_TABLE):
            await _create_vec0(connection, name, self.profile.dimension, if_not_exists=True)
        await connection.execute(
            sqlite_insert(self.shape_table)
            .values(singleton=1, shape="hybrid", profile_fingerprint=self.fingerprint)
            .on_conflict_do_nothing()
        )
        if update_storage_profile:
            await connection.run_sync(lambda sync: self.storage_profile_table.create(sync, checkfirst=True))
            await connection.execute(
                sqlite_insert(self.storage_profile_table)
                .values(singleton=1, embedding_profile=self.profile.model_dump_json())
                .on_conflict_do_update(
                    index_elements=("singleton",),
                    set_={"embedding_profile": self.profile.model_dump_json()},
                )
            )
        await self._require_target_staging(connection)

    async def write_staging(
        self,
        connection: AsyncConnection,
        sources: Sequence[TopicMemoryReindexSource],
        vectors: Sequence[tuple[float, ...]],
        /,
    ) -> None:
        """Replace only the supplied Topics; the caller owns the transaction."""

        await self._require_target_staging(connection)
        expected = sum(1 + len(source.chunks) for source in sources)
        if len(vectors) != expected:
            raise TopicMemoryCapabilityError(
                "embedding-result",
                f"expected {expected} flattened Topic Memory embeddings, received {len(vectors)}",
            )
        canonical = tuple(
            canonical_embedding(
                vector,
                dimension=self.profile.dimension,
                normalization=self.profile.normalization,
            )
            for vector in vectors
        )
        offset = 0
        for source in sources:
            count = 1 + len(source.chunks)
            embeddings = canonical[offset : offset + count]
            offset += count
            identity = {"scope_id": source.scope_id, "artifact_id": source.artifact_id}
            for vector_table, metadata_table in (
                (TOPIC_MEMORY_REINDEX_TOPIC_VEC_TABLE, self.topic_table),
                (TOPIC_MEMORY_REINDEX_CHUNK_VEC_TABLE, self.chunk_table),
            ):
                await connection.execute(
                    text(
                        f"DELETE FROM {vector_table} WHERE rowid IN "  # noqa: S608 -- Fixed internal table name.
                        f"(SELECT vector_id FROM {metadata_table.name} "
                        "WHERE scope_id = :scope_id AND artifact_id = :artifact_id)"
                    ),
                    identity,
                )
            await connection.execute(
                delete(self.chunk_table).where(
                    self.chunk_table.c.scope_id == source.scope_id,
                    self.chunk_table.c.artifact_id == source.artifact_id,
                )
            )
            await connection.execute(
                delete(self.topic_table).where(
                    self.topic_table.c.scope_id == source.scope_id,
                    self.topic_table.c.artifact_id == source.artifact_id,
                )
            )
            inserted = await connection.execute(
                insert(self.topic_table).values(
                    scope_id=source.scope_id,
                    artifact_id=source.artifact_id,
                    revision=source.revision,
                    profile_fingerprint=self.fingerprint,
                )
            )
            await _insert_vector(
                connection,
                TOPIC_MEMORY_REINDEX_TOPIC_VEC_TABLE,
                int(inserted.lastrowid),
                source.scope_id,
                embeddings[0],
            )
            for chunk, embedding in zip(source.chunks, embeddings[1:], strict=True):
                inserted = await connection.execute(
                    insert(self.chunk_table).values(
                        scope_id=source.scope_id,
                        artifact_id=source.artifact_id,
                        revision=source.revision,
                        chunk_ordinal=chunk.ordinal,
                        profile_fingerprint=self.fingerprint,
                    )
                )
                await _insert_vector(
                    connection,
                    TOPIC_MEMORY_REINDEX_CHUNK_VEC_TABLE,
                    int(inserted.lastrowid),
                    source.scope_id,
                    embedding,
                )

    async def activate(self, connection: AsyncConnection, *, update_storage_profile: bool) -> None:
        """Atomically replace active vectors with the validated staged projection."""

        _require_sqlite(connection)
        driver = (await connection.get_raw_connection()).driver_connection
        if not isinstance(driver, SQLiteConnection):
            raise TopicMemoryCapabilityError("sqlite-vec", "Topic Memory reindexing requires the aiosqlite driver")
        if not driver.in_transaction:
            # SQLAlchemy does not BEGIN for SQLite DDL/SELECT. Activation and all
            # of its validation must share one native transaction for rollback.
            await connection.exec_driver_sql("BEGIN")
        # FTS-only sources have no active vector metadata yet. Creating empty
        # derived tables inside this transaction does not change their FTS shape.
        for active in (SQLITE_TOPIC_MEMORY_VECTOR_TOPICS_TABLE, SQLITE_TOPIC_MEMORY_VECTOR_CHUNKS_TABLE):
            await connection.run_sync(lambda sync, selected=active: selected.create(sync, checkfirst=True))
        await self.validate_staging(connection)
        if update_storage_profile:
            await self._require_staged_storage_profile(connection)

        await connection.execute(delete(SQLITE_TOPIC_MEMORY_VECTOR_CHUNKS_TABLE))
        await connection.execute(delete(SQLITE_TOPIC_MEMORY_VECTOR_TOPICS_TABLE))
        for name in (_ACTIVE_TOPIC_VEC_TABLE, _ACTIVE_CHUNK_VEC_TABLE):
            await connection.exec_driver_sql(f"DROP TABLE IF EXISTS {name}")
            await _create_vec0(connection, name, self.profile.dimension, if_not_exists=False)

        await connection.exec_driver_sql(
            f"INSERT INTO {SQLITE_TOPIC_MEMORY_VECTOR_TOPICS_TABLE.name} ({_TOPIC_COLUMNS}) "  # noqa: S608
            f"SELECT {_TOPIC_COLUMNS} FROM {TOPIC_MEMORY_REINDEX_TOPIC_TABLE}"
        )
        await connection.exec_driver_sql(
            f"INSERT INTO {SQLITE_TOPIC_MEMORY_VECTOR_CHUNKS_TABLE.name} ({_CHUNK_COLUMNS}) "  # noqa: S608
            f"SELECT {_CHUNK_COLUMNS} FROM {TOPIC_MEMORY_REINDEX_CHUNK_TABLE}"
        )
        for active, staged in (
            (_ACTIVE_TOPIC_VEC_TABLE, TOPIC_MEMORY_REINDEX_TOPIC_VEC_TABLE),
            (_ACTIVE_CHUNK_VEC_TABLE, TOPIC_MEMORY_REINDEX_CHUNK_VEC_TABLE),
        ):
            await connection.exec_driver_sql(
                f"INSERT INTO {active} (rowid, scope_id, embedding) "  # noqa: S608 -- Frozen internal table names.
                f"SELECT rowid, scope_id, embedding FROM {staged}"
            )

        await connection.execute(
            sqlite_insert(TOPIC_MEMORY_RETRIEVAL_SHAPE_TABLE)
            .values(singleton=1, shape="hybrid", profile_fingerprint=self.fingerprint)
            .on_conflict_do_update(
                index_elements=("singleton",),
                set_={"shape": "hybrid", "profile_fingerprint": self.fingerprint},
            )
        )
        if update_storage_profile:
            await connection.exec_driver_sql(
                "INSERT INTO pc_server_storage_profile (singleton, embedding_profile) "  # noqa: S608
                f"SELECT singleton, embedding_profile FROM {TOPIC_MEMORY_REINDEX_STORAGE_PROFILE_TABLE} "
                "WHERE singleton = 1 "
                "ON CONFLICT(singleton) DO UPDATE SET embedding_profile = excluded.embedding_profile"
            )
            # Atomic Memory has no embeddings to compute here. Initialization only
            # reconciles its rebuildable native helper with the newly managed profile.
            await SQLiteAtomicMemoryIndex(self.profile).initialize(connection)
        await _drop_staging(connection)

    async def cleanup(self, connection: AsyncConnection) -> None:
        """Remove only target-matching staging left after a committed activation."""

        active = (
            await connection.execute(
                select(
                    TOPIC_MEMORY_RETRIEVAL_SHAPE_TABLE.c.shape,
                    TOPIC_MEMORY_RETRIEVAL_SHAPE_TABLE.c.profile_fingerprint,
                ).where(TOPIC_MEMORY_RETRIEVAL_SHAPE_TABLE.c.singleton == 1)
            )
        ).one_or_none()
        if active is None or (active[0], active[1]) != ("hybrid", self.fingerprint):
            return
        if not _STAGING_TABLE_NAMES & await _table_names(connection):
            return
        try:
            current = await staged_fingerprint(connection, self)
        except TopicMemoryStorageInvariantError:
            return
        if current == self.fingerprint:
            await _drop_staging(connection)

    async def _require_target_staging(self, connection: AsyncConnection) -> None:
        current = await staged_fingerprint(connection, self)
        if current != self.fingerprint:
            raise TopicMemoryStorageInvariantError("reindex-staging-fingerprint", current)

    async def _require_staged_storage_profile(self, connection: AsyncConnection) -> str:
        if TOPIC_MEMORY_REINDEX_STORAGE_PROFILE_TABLE not in await _table_names(connection):
            raise TopicMemoryStorageInvariantError("reindex-staging-storage-profile", 1)
        row = (
            await connection.execute(
                select(self.storage_profile_table.c.embedding_profile).where(
                    self.storage_profile_table.c.singleton == 1
                )
            )
        ).one_or_none()
        target = self.profile.model_dump_json()
        if row is None or row[0] != target:
            raise TopicMemoryStorageInvariantError("reindex-staging-storage-profile", 1)
        return target


def _require_sqlite(connection: AsyncConnection) -> None:
    if connection.dialect.name != "sqlite":
        raise TopicMemoryCapabilityError("sqlite-vec", "Topic Memory reindexing requires SQLite")


def _vector_present(vectors: Any, native_name: str, *, dimension: int | None = None) -> ColumnElement[bool]:
    native = table(native_name, column("rowid"), column("scope_id"), column("embedding"))
    predicates = [native.c.rowid == vectors.c.vector_id, native.c.scope_id == vectors.c.scope_id]
    if dimension is not None:
        predicates.append(func.length(native.c.embedding) == dimension * 4)
    return select(native.c.rowid).where(*predicates).correlate(vectors).exists()


async def _table_names(connection: AsyncConnection) -> set[str]:
    return set(await connection.run_sync(lambda sync: inspect(sync).get_table_names()))


async def _create_vec0(connection: AsyncConnection, name: str, dimension: int, *, if_not_exists: bool) -> None:
    conditional = "IF NOT EXISTS " if if_not_exists else ""
    await connection.exec_driver_sql(
        f"CREATE VIRTUAL TABLE {conditional}{name} "
        f"USING vec0(scope_id TEXT partition key, embedding float[{dimension}])"
    )


async def _require_vec0_dimensions(connection: AsyncConnection, names: Sequence[str], dimension: int) -> None:
    for name in names:
        definition = await connection.scalar(
            text("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = :name"), {"name": name}
        )
        match = _VEC0_DIMENSION.search(str(definition or ""))
        if match is None or int(match.group(1)) != dimension:
            raise TopicMemoryStorageInvariantError("reindex-staging-shape", name)


async def _insert_vector(
    connection: AsyncConnection,
    name: str,
    vector_id: int,
    scope_id: str,
    embedding: tuple[float, ...],
) -> None:
    await connection.execute(
        text(f"INSERT INTO {name} (rowid, scope_id, embedding) VALUES (:vector_id, :scope_id, :embedding)"),  # noqa: S608
        {
            "vector_id": vector_id,
            "scope_id": scope_id,
            "embedding": struct.pack(f"<{len(embedding)}f", *embedding),
        },
    )


async def _drop_staging(connection: AsyncConnection) -> None:
    for name in (
        TOPIC_MEMORY_REINDEX_TOPIC_VEC_TABLE,
        TOPIC_MEMORY_REINDEX_CHUNK_VEC_TABLE,
        TOPIC_MEMORY_REINDEX_STORAGE_PROFILE_TABLE,
        TOPIC_MEMORY_REINDEX_SHAPE_TABLE,
        TOPIC_MEMORY_REINDEX_CHUNK_TABLE,
        TOPIC_MEMORY_REINDEX_TOPIC_TABLE,
    ):
        await connection.exec_driver_sql(f"DROP TABLE IF EXISTS {name}")


__all__ = ["SQLiteTopicMemoryReindexBackend"]
