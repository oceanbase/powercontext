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

"""SQLite Topic Memory indexes using FTS5 and optional sqlite-vec."""

from __future__ import annotations

import hashlib
import struct
from collections.abc import Mapping
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    Column,
    Integer,
    String,
    Table,
    UniqueConstraint,
    delete,
    func,
    insert,
    select,
    text,
)
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.memory import EmbeddingProfile
from powercontext.builtin.artifacts.memory.canonical import validate_embedding
from powercontext.builtin.artifacts.search import analyze_text, fts_match_query
from powercontext.builtin.artifacts.topic_memory import (
    TopicMemoryCapabilities,
    TopicMemoryCapabilityError,
    TopicMemoryChannelHit,
    TopicMemoryMatchedBy,
    TopicMemoryProjection,
    TopicMemorySearchChannels,
    TopicMemorySearchRequest,
)
from powercontext.builtin.persistence.tables import (
    SHARED_METADATA,
    TOPIC_MEMORY_ACTIVE_CHUNKS_TABLE,
    TOPIC_MEMORY_ACTIVE_TOPICS_TABLE,
    identity_string,
)
from powercontext.limits import MAX_ARTIFACT_ID_LENGTH, MAX_SCOPE_ID_LENGTH

SQLITE_TOPIC_MEMORY_FTS_MARKER_TABLE = Table(
    "pc_topic_memory_fts_index",
    SHARED_METADATA,
    Column("singleton", Integer, primary_key=True),
    Column("schema_version", Integer, nullable=False),
    CheckConstraint("singleton = 1", name="ck_pc_topic_memory_fts_index_singleton"),
)

SQLITE_TOPIC_MEMORY_VECTOR_TOPICS_TABLE = Table(
    "pc_topic_memory_vector_topics",
    SHARED_METADATA,
    Column("vector_id", Integer, primary_key=True, autoincrement=True),
    Column("scope_id", identity_string(MAX_SCOPE_ID_LENGTH), nullable=False),
    Column("artifact_id", identity_string(MAX_ARTIFACT_ID_LENGTH), nullable=False),
    Column("revision", Integer, nullable=False),
    Column("profile_fingerprint", String(64), nullable=False),
    UniqueConstraint("scope_id", "artifact_id", name="uq_pc_topic_memory_vector_topics_active"),
    sqlite_autoincrement=True,
)

SQLITE_TOPIC_MEMORY_VECTOR_CHUNKS_TABLE = Table(
    "pc_topic_memory_vector_chunks",
    SHARED_METADATA,
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
        name="uq_pc_topic_memory_vector_chunks_active",
    ),
    sqlite_autoincrement=True,
)

SQLITE_TOPIC_MEMORY_FTS_TABLES = (SQLITE_TOPIC_MEMORY_FTS_MARKER_TABLE,)
SQLITE_TOPIC_MEMORY_VECTOR_TABLES = (
    SQLITE_TOPIC_MEMORY_VECTOR_TOPICS_TABLE,
    SQLITE_TOPIC_MEMORY_VECTOR_CHUNKS_TABLE,
)

_CREATE_TOPIC_FTS_SQL = """
CREATE VIRTUAL TABLE IF NOT EXISTS pc_topic_memory_topic_fts USING fts5(
    scope_id UNINDEXED, artifact_id UNINDEXED, revision UNINDEXED,
    title UNINDEXED, summary UNINDEXED, searchable_text, tokenize='unicode61'
)
"""
_CREATE_CHUNK_FTS_SQL = """
CREATE VIRTUAL TABLE IF NOT EXISTS pc_topic_memory_chunk_fts USING fts5(
    scope_id UNINDEXED, artifact_id UNINDEXED, revision UNINDEXED,
    chunk_ordinal UNINDEXED, start_offset UNINDEXED,
    title UNINDEXED, summary UNINDEXED, chunk_text UNINDEXED,
    searchable_text, tokenize='unicode61'
)
"""
_DELETE_TOPIC_FTS_SQL = text(
    "DELETE FROM pc_topic_memory_topic_fts WHERE scope_id = :scope_id AND artifact_id = :artifact_id"
)
_DELETE_CHUNK_FTS_SQL = text(
    "DELETE FROM pc_topic_memory_chunk_fts WHERE scope_id = :scope_id AND artifact_id = :artifact_id"
)
_INSERT_TOPIC_FTS_SQL = text(
    """
    INSERT INTO pc_topic_memory_topic_fts
        (scope_id, artifact_id, revision, title, summary, searchable_text)
    VALUES
        (:scope_id, :artifact_id, :revision, :title, :summary, :searchable_text)
    """
)
_INSERT_CHUNK_FTS_SQL = text(
    """
    INSERT INTO pc_topic_memory_chunk_fts
        (scope_id, artifact_id, revision, chunk_ordinal, start_offset,
         title, summary, chunk_text, searchable_text)
    VALUES
        (:scope_id, :artifact_id, :revision, :chunk_ordinal, :start_offset,
         :title, :summary, :chunk_text, :searchable_text)
    """
)
_SEARCH_TOPIC_FTS_SQL = text(
    """
    SELECT artifact_id, revision, title, summary
    FROM pc_topic_memory_topic_fts
    WHERE pc_topic_memory_topic_fts MATCH :query AND scope_id = :scope_id
    ORDER BY bm25(pc_topic_memory_topic_fts), artifact_id, revision DESC
    LIMIT :candidate_limit
    """
)
_SEARCH_CHUNK_FTS_SQL = text(
    """
    SELECT artifact_id, revision, title, summary, chunk_ordinal, start_offset, chunk_text
    FROM pc_topic_memory_chunk_fts
    WHERE pc_topic_memory_chunk_fts MATCH :query AND scope_id = :scope_id
    ORDER BY bm25(pc_topic_memory_chunk_fts), artifact_id, revision DESC, chunk_ordinal
    LIMIT :candidate_limit
    """
)

_DELETE_TOPIC_VECTOR_SQL = text("DELETE FROM pc_topic_memory_topic_vec WHERE rowid = :vector_id")
_DELETE_CHUNK_VECTOR_SQL = text("DELETE FROM pc_topic_memory_chunk_vec WHERE rowid = :vector_id")
_INSERT_TOPIC_VECTOR_SQL = text(
    "INSERT INTO pc_topic_memory_topic_vec (rowid, embedding) VALUES (:vector_id, :embedding)"
)
_INSERT_CHUNK_VECTOR_SQL = text(
    "INSERT INTO pc_topic_memory_chunk_vec (rowid, embedding) VALUES (:vector_id, :embedding)"
)
_PROBE_TOPIC_VECTOR_SQL = "SELECT rowid FROM pc_topic_memory_topic_vec WHERE embedding MATCH ? AND k = 1"
_PROBE_CHUNK_VECTOR_SQL = "SELECT rowid FROM pc_topic_memory_chunk_vec WHERE embedding MATCH ? AND k = 1"
_TOPIC_VECTOR_SEARCH_SQL = text(
    """
    WITH nearest AS (
        SELECT rowid, distance FROM pc_topic_memory_topic_vec
        WHERE embedding MATCH :query_vector AND k = :neighbor_limit
    )
    SELECT a.artifact_id, a.revision, a.title, a.summary, nearest.distance
    FROM nearest
    JOIN pc_topic_memory_vector_topics AS v ON v.vector_id = nearest.rowid
    JOIN pc_topic_memory_active_topics AS a
      ON a.scope_id = v.scope_id AND a.artifact_id = v.artifact_id AND a.revision = v.revision
    WHERE v.scope_id = :scope_id
    ORDER BY nearest.distance, a.artifact_id, a.revision DESC
    LIMIT :candidate_limit
    """
)
_CHUNK_VECTOR_SEARCH_SQL = text(
    """
    WITH nearest AS (
        SELECT rowid, distance FROM pc_topic_memory_chunk_vec
        WHERE embedding MATCH :query_vector AND k = :neighbor_limit
    )
    SELECT a.artifact_id, a.revision, a.title, a.summary,
           c.chunk_ordinal, c.start_offset, c.chunk_text, nearest.distance
    FROM nearest
    JOIN pc_topic_memory_vector_chunks AS v ON v.vector_id = nearest.rowid
    JOIN pc_topic_memory_active_topics AS a
      ON a.scope_id = v.scope_id AND a.artifact_id = v.artifact_id AND a.revision = v.revision
    JOIN pc_topic_memory_active_chunks AS c
      ON c.scope_id = v.scope_id AND c.artifact_id = v.artifact_id
     AND c.revision = v.revision AND c.chunk_ordinal = v.chunk_ordinal
    WHERE v.scope_id = :scope_id
    ORDER BY nearest.distance, a.artifact_id, a.revision DESC, c.chunk_ordinal
    LIMIT :candidate_limit
    """
)


class SQLiteTopicMemoryFTSIndex:
    """Maintain both active Topic and detail-chunk FTS5 channels."""

    capabilities = TopicMemoryCapabilities(fts=True)
    tables: tuple[Table, ...] = SQLITE_TOPIC_MEMORY_FTS_TABLES

    async def initialize(self, connection: AsyncConnection, /) -> None:
        if connection.dialect.name != "sqlite":
            raise TopicMemoryCapabilityError("sqlite-fts")
        await connection.exec_driver_sql(_CREATE_TOPIC_FTS_SQL)
        await connection.exec_driver_sql(_CREATE_CHUNK_FTS_SQL)
        marker = await connection.scalar(select(SQLITE_TOPIC_MEMORY_FTS_MARKER_TABLE.c.singleton))
        if marker is None:
            await connection.execute(insert(SQLITE_TOPIC_MEMORY_FTS_MARKER_TABLE).values(singleton=1, schema_version=1))
        await connection.exec_driver_sql("DELETE FROM pc_topic_memory_topic_fts")
        await connection.exec_driver_sql("DELETE FROM pc_topic_memory_chunk_fts")
        topics = (await connection.execute(select(TOPIC_MEMORY_ACTIVE_TOPICS_TABLE))).mappings()
        for row in topics:
            await connection.execute(_INSERT_TOPIC_FTS_SQL, _topic_fts_values(row))
        chunks = (
            await connection.execute(
                select(
                    TOPIC_MEMORY_ACTIVE_CHUNKS_TABLE,
                    TOPIC_MEMORY_ACTIVE_TOPICS_TABLE.c.title,
                    TOPIC_MEMORY_ACTIVE_TOPICS_TABLE.c.summary,
                ).join(
                    TOPIC_MEMORY_ACTIVE_TOPICS_TABLE,
                    (TOPIC_MEMORY_ACTIVE_TOPICS_TABLE.c.scope_id == TOPIC_MEMORY_ACTIVE_CHUNKS_TABLE.c.scope_id)
                    & (TOPIC_MEMORY_ACTIVE_TOPICS_TABLE.c.artifact_id == TOPIC_MEMORY_ACTIVE_CHUNKS_TABLE.c.artifact_id)
                    & (TOPIC_MEMORY_ACTIVE_TOPICS_TABLE.c.revision == TOPIC_MEMORY_ACTIVE_CHUNKS_TABLE.c.revision),
                )
            )
        ).mappings()
        for row in chunks:
            await connection.execute(_INSERT_CHUNK_FTS_SQL, _chunk_fts_values(row))
        await connection.exec_driver_sql(
            "SELECT rowid FROM pc_topic_memory_topic_fts WHERE pc_topic_memory_topic_fts MATCH 'powercontext'"
        )

    async def replace(
        self,
        connection: AsyncConnection,
        scope_id: str,
        topic_ref: ArtifactRef,
        projection: TopicMemoryProjection,
        /,
    ) -> None:
        identity = {"scope_id": scope_id, "artifact_id": topic_ref.artifact_id}
        await connection.execute(_DELETE_TOPIC_FTS_SQL, identity)
        await connection.execute(_DELETE_CHUNK_FTS_SQL, identity)
        topic_values = {
            **identity,
            "revision": topic_ref.revision,
            "title": projection.content.title,
            "summary": projection.content.summary,
            "searchable_text": projection.topic_searchable_text,
        }
        await connection.execute(_INSERT_TOPIC_FTS_SQL, topic_values)
        for chunk in projection.chunks:
            await connection.execute(
                _INSERT_CHUNK_FTS_SQL,
                {
                    **topic_values,
                    "chunk_ordinal": chunk.ordinal,
                    "start_offset": chunk.start_offset,
                    "chunk_text": chunk.text,
                    "searchable_text": analyze_text(chunk.text),
                },
            )

    async def search(
        self,
        connection: AsyncConnection,
        scope_id: str,
        request: TopicMemorySearchRequest,
        /,
    ) -> TopicMemorySearchChannels:
        if request.mode not in {"fts", "hybrid"}:
            return TopicMemorySearchChannels()
        query = fts_match_query(request.query)
        if query is None:
            return TopicMemorySearchChannels()
        parameters = {
            "query": query,
            "scope_id": scope_id,
            "candidate_limit": request.candidate_limit,
        }
        topic_rows = (await connection.execute(_SEARCH_TOPIC_FTS_SQL, parameters)).mappings()
        chunk_rows = (await connection.execute(_SEARCH_CHUNK_FTS_SQL, parameters)).mappings()
        return TopicMemorySearchChannels(
            topic_fts=tuple(_channel_hit(row, "topic_fts") for row in topic_rows),
            detail_fts=tuple(_channel_hit(row, "detail_fts") for row in chunk_rows),
        )

    async def vector_complete(
        self,
        _connection: AsyncConnection,
        _scope_id: str,
        _topic_ref: ArtifactRef,
        /,
    ) -> bool:
        return False


class SQLiteTopicMemoryVectorIndex:
    """Maintain complete active Topic and chunk embeddings in sqlite-vec."""

    tables: tuple[Table, ...] = SQLITE_TOPIC_MEMORY_VECTOR_TABLES

    def __init__(self, profile: EmbeddingProfile) -> None:
        if profile.dimension < 1 or profile.distance != "l2" or profile.normalization != "unit":
            raise TopicMemoryCapabilityError(
                "vector",
                "sqlite-vec requires a positive unit-normalized L2 embedding profile",
            )
        self.profile = profile
        self.capabilities = TopicMemoryCapabilities(fts=False, vector=True, embedding_profile=profile)
        self._fingerprint = _profile_fingerprint(profile)

    async def initialize(self, connection: AsyncConnection, /) -> None:
        if connection.dialect.name != "sqlite":
            raise TopicMemoryCapabilityError("sqlite-vec")
        try:
            await connection.exec_driver_sql("SELECT vec_version()")
            for table_name in ("pc_topic_memory_topic_vec", "pc_topic_memory_chunk_vec"):
                await connection.exec_driver_sql(
                    f"CREATE VIRTUAL TABLE IF NOT EXISTS {table_name} "
                    f"USING vec0(embedding float[{self.profile.dimension}])"
                )
            probe = _pack_vector((0.0,) * self.profile.dimension)
            probes = (
                (
                    _DELETE_TOPIC_VECTOR_SQL,
                    _INSERT_TOPIC_VECTOR_SQL,
                    _PROBE_TOPIC_VECTOR_SQL,
                    "pc_topic_memory_topic_vec",
                ),
                (
                    _DELETE_CHUNK_VECTOR_SQL,
                    _INSERT_CHUNK_VECTOR_SQL,
                    _PROBE_CHUNK_VECTOR_SQL,
                    "pc_topic_memory_chunk_vec",
                ),
            )
            for delete_probe, insert_probe, select_probe, table_name in probes:
                await connection.execute(delete_probe, {"vector_id": -1})
                await connection.execute(insert_probe, {"vector_id": -1, "embedding": probe})
                row = (
                    await connection.exec_driver_sql(
                        select_probe,
                        (probe,),
                    )
                ).one_or_none()
                await connection.execute(delete_probe, {"vector_id": -1})
                if row is None or int(row[0]) != -1:
                    raise TopicMemoryCapabilityError(
                        "vector",
                        f"sqlite-vec probe returned an invalid row for {table_name}",
                    )
        except SQLAlchemyError as error:
            raise TopicMemoryCapabilityError("vector", f"sqlite-vec probe failed: {error}") from error

    async def replace(
        self,
        connection: AsyncConnection,
        scope_id: str,
        topic_ref: ArtifactRef,
        projection: TopicMemoryProjection,
        /,
    ) -> None:
        await self._delete_existing(connection, scope_id, topic_ref.artifact_id)
        if projection.topic_embedding is None or len(projection.chunk_embeddings) != len(projection.chunks):
            raise TopicMemoryCapabilityError("vector", "projection is incomplete")
        topic_id = (
            await connection.execute(
                insert(SQLITE_TOPIC_MEMORY_VECTOR_TOPICS_TABLE)
                .values(
                    scope_id=scope_id,
                    artifact_id=topic_ref.artifact_id,
                    revision=topic_ref.revision,
                    profile_fingerprint=self._fingerprint,
                )
                .returning(SQLITE_TOPIC_MEMORY_VECTOR_TOPICS_TABLE.c.vector_id)
            )
        ).scalar_one()
        await connection.execute(
            _INSERT_TOPIC_VECTOR_SQL,
            {
                "vector_id": topic_id,
                "embedding": _pack_vector(
                    validate_embedding(projection.topic_embedding, dimension=self.profile.dimension)
                ),
            },
        )
        for chunk, embedding in zip(projection.chunks, projection.chunk_embeddings, strict=True):
            vector_id = (
                await connection.execute(
                    insert(SQLITE_TOPIC_MEMORY_VECTOR_CHUNKS_TABLE)
                    .values(
                        scope_id=scope_id,
                        artifact_id=topic_ref.artifact_id,
                        revision=topic_ref.revision,
                        chunk_ordinal=chunk.ordinal,
                        profile_fingerprint=self._fingerprint,
                    )
                    .returning(SQLITE_TOPIC_MEMORY_VECTOR_CHUNKS_TABLE.c.vector_id)
                )
            ).scalar_one()
            await connection.execute(
                _INSERT_CHUNK_VECTOR_SQL,
                {
                    "vector_id": vector_id,
                    "embedding": _pack_vector(validate_embedding(embedding, dimension=self.profile.dimension)),
                },
            )

    async def search(
        self,
        connection: AsyncConnection,
        scope_id: str,
        request: TopicMemorySearchRequest,
        /,
    ) -> TopicMemorySearchChannels:
        if request.mode not in {"vector", "hybrid"}:
            return TopicMemorySearchChannels()
        if request.embedding_profile != self.profile or request.query_vector is None:
            raise TopicMemoryCapabilityError("embedding-profile")
        query_vector = _pack_vector(validate_embedding(request.query_vector, dimension=self.profile.dimension))
        topic_total = int(
            await connection.scalar(select(func.count()).select_from(SQLITE_TOPIC_MEMORY_VECTOR_TOPICS_TABLE)) or 0
        )
        chunk_total = int(
            await connection.scalar(select(func.count()).select_from(SQLITE_TOPIC_MEMORY_VECTOR_CHUNKS_TABLE)) or 0
        )
        parameters = {
            "query_vector": query_vector,
            "scope_id": scope_id,
            "candidate_limit": request.candidate_limit,
        }
        topic_rows = (
            ()
            if topic_total == 0
            else (
                await connection.execute(_TOPIC_VECTOR_SEARCH_SQL, {**parameters, "neighbor_limit": topic_total})
            ).mappings()
        )
        chunk_rows = (
            ()
            if chunk_total == 0
            else (
                await connection.execute(_CHUNK_VECTOR_SEARCH_SQL, {**parameters, "neighbor_limit": chunk_total})
            ).mappings()
        )
        return TopicMemorySearchChannels(
            topic_vector=tuple(_channel_hit(row, "topic_vector") for row in topic_rows),
            detail_vector=tuple(_channel_hit(row, "detail_vector") for row in chunk_rows),
        )

    async def vector_complete(
        self,
        connection: AsyncConnection,
        scope_id: str,
        topic_ref: ArtifactRef,
        /,
    ) -> bool:
        topic = (
            await connection.execute(
                select(
                    SQLITE_TOPIC_MEMORY_VECTOR_TOPICS_TABLE.c.vector_id,
                    SQLITE_TOPIC_MEMORY_VECTOR_TOPICS_TABLE.c.profile_fingerprint,
                ).where(
                    SQLITE_TOPIC_MEMORY_VECTOR_TOPICS_TABLE.c.scope_id == scope_id,
                    SQLITE_TOPIC_MEMORY_VECTOR_TOPICS_TABLE.c.artifact_id == topic_ref.artifact_id,
                    SQLITE_TOPIC_MEMORY_VECTOR_TOPICS_TABLE.c.revision == topic_ref.revision,
                )
            )
        ).one_or_none()
        if topic is None or str(topic[1]) != self._fingerprint:
            return False
        expected_chunks = int(
            await connection.scalar(
                select(func.count())
                .select_from(TOPIC_MEMORY_ACTIVE_CHUNKS_TABLE)
                .where(
                    TOPIC_MEMORY_ACTIVE_CHUNKS_TABLE.c.scope_id == scope_id,
                    TOPIC_MEMORY_ACTIVE_CHUNKS_TABLE.c.artifact_id == topic_ref.artifact_id,
                    TOPIC_MEMORY_ACTIVE_CHUNKS_TABLE.c.revision == topic_ref.revision,
                )
            )
            or 0
        )
        chunks = (
            await connection.execute(
                select(
                    SQLITE_TOPIC_MEMORY_VECTOR_CHUNKS_TABLE.c.vector_id,
                    SQLITE_TOPIC_MEMORY_VECTOR_CHUNKS_TABLE.c.profile_fingerprint,
                ).where(
                    SQLITE_TOPIC_MEMORY_VECTOR_CHUNKS_TABLE.c.scope_id == scope_id,
                    SQLITE_TOPIC_MEMORY_VECTOR_CHUNKS_TABLE.c.artifact_id == topic_ref.artifact_id,
                    SQLITE_TOPIC_MEMORY_VECTOR_CHUNKS_TABLE.c.revision == topic_ref.revision,
                )
            )
        ).all()
        if expected_chunks == 0 or len(chunks) != expected_chunks:
            return False
        if str(topic[1]) != self._fingerprint or any(str(row[1]) != self._fingerprint for row in chunks):
            return False
        topic_vector = await connection.scalar(
            text("SELECT rowid FROM pc_topic_memory_topic_vec WHERE rowid = :vector_id"),
            {"vector_id": int(topic[0])},
        )
        if topic_vector is None:
            return False
        for row in chunks:
            if (
                await connection.scalar(
                    text("SELECT rowid FROM pc_topic_memory_chunk_vec WHERE rowid = :vector_id"),
                    {"vector_id": int(row[0])},
                )
                is None
            ):
                return False
        return True

    async def _delete_existing(self, connection: AsyncConnection, scope_id: str, artifact_id: str) -> None:
        topics = (
            await connection.execute(
                select(SQLITE_TOPIC_MEMORY_VECTOR_TOPICS_TABLE.c.vector_id).where(
                    SQLITE_TOPIC_MEMORY_VECTOR_TOPICS_TABLE.c.scope_id == scope_id,
                    SQLITE_TOPIC_MEMORY_VECTOR_TOPICS_TABLE.c.artifact_id == artifact_id,
                )
            )
        ).scalars()
        for vector_id in topics:
            await connection.execute(_DELETE_TOPIC_VECTOR_SQL, {"vector_id": int(vector_id)})
        chunks = (
            await connection.execute(
                select(SQLITE_TOPIC_MEMORY_VECTOR_CHUNKS_TABLE.c.vector_id).where(
                    SQLITE_TOPIC_MEMORY_VECTOR_CHUNKS_TABLE.c.scope_id == scope_id,
                    SQLITE_TOPIC_MEMORY_VECTOR_CHUNKS_TABLE.c.artifact_id == artifact_id,
                )
            )
        ).scalars()
        for vector_id in chunks:
            await connection.execute(_DELETE_CHUNK_VECTOR_SQL, {"vector_id": int(vector_id)})
        await connection.execute(
            delete(SQLITE_TOPIC_MEMORY_VECTOR_CHUNKS_TABLE).where(
                SQLITE_TOPIC_MEMORY_VECTOR_CHUNKS_TABLE.c.scope_id == scope_id,
                SQLITE_TOPIC_MEMORY_VECTOR_CHUNKS_TABLE.c.artifact_id == artifact_id,
            )
        )
        await connection.execute(
            delete(SQLITE_TOPIC_MEMORY_VECTOR_TOPICS_TABLE).where(
                SQLITE_TOPIC_MEMORY_VECTOR_TOPICS_TABLE.c.scope_id == scope_id,
                SQLITE_TOPIC_MEMORY_VECTOR_TOPICS_TABLE.c.artifact_id == artifact_id,
            )
        )


def _topic_fts_values(row: Mapping[Any, Any]) -> dict[str, object]:
    return {key: row[key] for key in ("scope_id", "artifact_id", "revision", "title", "summary", "searchable_text")}


def _chunk_fts_values(row: Mapping[Any, Any]) -> dict[str, object]:
    return {
        key: row[key]
        for key in (
            "scope_id",
            "artifact_id",
            "revision",
            "chunk_ordinal",
            "start_offset",
            "title",
            "summary",
            "chunk_text",
            "searchable_text",
        )
    }


def _channel_hit(row: Mapping[Any, Any], channel: TopicMemoryMatchedBy) -> TopicMemoryChannelHit:
    return TopicMemoryChannelHit(
        artifact_ref=ArtifactRef(
            family="topic-memory",
            artifact_id=str(row["artifact_id"]),
            revision=int(row["revision"]),
        ),
        title=str(row["title"]),
        summary=str(row["summary"]),
        channel=channel,
        chunk_ordinal=None if row.get("chunk_ordinal") is None else int(row["chunk_ordinal"]),
        chunk_start=None if row.get("start_offset") is None else int(row["start_offset"]),
        chunk_text=None if row.get("chunk_text") is None else str(row["chunk_text"]),
        distance=None if row.get("distance") is None else float(row["distance"]),
    )


def _profile_fingerprint(profile: EmbeddingProfile) -> str:
    payload = (
        f"{profile.profile_id}\0{profile.model}\0{profile.dimension}\0{profile.distance}\0{profile.normalization}"
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def _pack_vector(vector: tuple[float, ...]) -> bytes:
    return struct.pack(f"<{len(vector)}f", *vector)


__all__ = [
    "SQLITE_TOPIC_MEMORY_FTS_TABLES",
    "SQLITE_TOPIC_MEMORY_VECTOR_TABLES",
    "SQLiteTopicMemoryFTSIndex",
    "SQLiteTopicMemoryVectorIndex",
]
