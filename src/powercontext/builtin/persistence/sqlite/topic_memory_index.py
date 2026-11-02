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

import struct
from collections.abc import Mapping, Sequence
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    CheckConstraint,
    Column,
    Integer,
    String,
    Table,
    UniqueConstraint,
    column,
    delete,
    insert,
    select,
    table,
    text,
)
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.memory import EmbeddingProfile
from powercontext.builtin.artifacts.memory.canonical import canonical_embedding
from powercontext.builtin.artifacts.search import analyze_text, fts_match_query, fts_query_requirements
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
from powercontext.builtin.persistence.topic_memory_index import (
    topic_memory_embedding_profile_fingerprint,
    validate_current_topic_vectors,
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
_SEARCH_TOPIC_FTS_SQL = """
WITH scored AS MATERIALIZED (
    SELECT rowid, artifact_id, revision,
           bm25(pc_topic_memory_topic_fts) AS raw_score,
           ({coverage}) AS coverage
    FROM pc_topic_memory_topic_fts
    WHERE pc_topic_memory_topic_fts MATCH :query AND scope_id = :scope_id
), candidates AS (
    SELECT rowid, artifact_id, revision, raw_score
    FROM scored
    WHERE coverage >= :required_matches
    ORDER BY raw_score, artifact_id, revision DESC
    LIMIT :candidate_limit
), rejected AS (
    SELECT 1 FROM scored WHERE coverage < :required_matches LIMIT 1
)
SELECT c.artifact_id AS artifact_id, c.revision AS revision, f.title, f.summary,
       c.raw_score AS raw_score, 0 AS is_rejected
FROM candidates AS c
JOIN pc_topic_memory_topic_fts AS f ON f.rowid = c.rowid
UNION ALL
SELECT NULL, NULL, NULL, NULL, NULL, 1 FROM rejected
ORDER BY is_rejected, raw_score, artifact_id, revision DESC
"""
_SEARCH_CHUNK_FTS_SQL = """
WITH scored AS MATERIALIZED (
    SELECT rowid, artifact_id, revision, chunk_ordinal,
           bm25(pc_topic_memory_chunk_fts) AS raw_score,
           ({coverage}) AS coverage
    FROM pc_topic_memory_chunk_fts
    WHERE pc_topic_memory_chunk_fts MATCH :query AND scope_id = :scope_id
), ranked AS (
    SELECT scored.*,
           row_number() OVER (
               PARTITION BY artifact_id, revision
               ORDER BY coverage DESC, raw_score, chunk_ordinal
           ) AS topic_rank
    FROM scored
), matched AS MATERIALIZED (
    SELECT rowid, artifact_id, revision, chunk_ordinal, raw_score, coverage
    FROM ranked
    WHERE topic_rank = 1
), candidates AS (
    SELECT rowid, artifact_id, revision, chunk_ordinal, raw_score
    FROM matched
    WHERE coverage >= :required_matches
    ORDER BY raw_score, artifact_id, revision DESC, chunk_ordinal
    LIMIT :candidate_limit
), rejected AS (
    SELECT 1 FROM matched WHERE coverage < :required_matches LIMIT 1
)
SELECT c.artifact_id AS artifact_id, c.revision AS revision, f.title, f.summary,
       c.chunk_ordinal AS chunk_ordinal, f.start_offset, f.chunk_text,
       c.raw_score AS raw_score, 0 AS is_rejected
FROM candidates AS c
JOIN pc_topic_memory_chunk_fts AS f ON f.rowid = c.rowid
UNION ALL
SELECT NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, 1 FROM rejected
ORDER BY is_rejected, raw_score, artifact_id, revision DESC, chunk_ordinal
"""

_DELETE_TOPIC_VECTOR_SQL = text("DELETE FROM pc_topic_memory_topic_vec WHERE rowid = :vector_id")
_DELETE_CHUNK_VECTOR_SQL = text("DELETE FROM pc_topic_memory_chunk_vec WHERE rowid = :vector_id")
_INSERT_TOPIC_VECTOR_SQL = text(
    "INSERT INTO pc_topic_memory_topic_vec (rowid, scope_id, embedding) VALUES (:vector_id, :scope_id, :embedding)"
)
_INSERT_CHUNK_VECTOR_SQL = text(
    "INSERT INTO pc_topic_memory_chunk_vec (rowid, scope_id, embedding) VALUES (:vector_id, :scope_id, :embedding)"
)
_PROBE_TOPIC_VECTOR_SQL = (
    "SELECT rowid FROM pc_topic_memory_topic_vec WHERE scope_id = ? AND embedding MATCH ? AND k = 1"
)
_PROBE_CHUNK_VECTOR_SQL = (
    "SELECT rowid FROM pc_topic_memory_chunk_vec WHERE scope_id = ? AND embedding MATCH ? AND k = 1"
)


def _vector_search_sql(*, detail: bool, exact: bool = False) -> str:
    """Rank vector identities first; hydrate only the selected exact Topics/chunks."""
    channel = "chunk" if detail else "topic"
    metadata = f"pc_topic_memory_vector_{'chunks' if detail else 'topics'}"
    vectors = f"pc_topic_memory_{channel}_vec"
    # Startup validates each metadata ID's native Scope; publication maintains
    # that mapping atomically. Exact point reads need only the scoped metadata.
    nearest = (
        f"SELECT v.vector_id AS rowid, vec_distance_l2(r.embedding, :query_vector) AS distance "  # noqa: S608
        f"FROM {metadata} AS v JOIN {vectors} AS r ON r.rowid=v.vector_id "
        "WHERE v.scope_id=:scope_id AND v.profile_fingerprint=:profile_fingerprint"
        if exact
        else f"SELECT rowid, distance FROM {vectors} "  # noqa: S608
        "WHERE scope_id=:scope_id AND embedding MATCH :query_vector AND k=:neighbor_limit "
        f"AND rowid IN (SELECT vector_id FROM {metadata} "
        "WHERE scope_id=:scope_id AND profile_fingerprint=:profile_fingerprint)"
    )
    observations = "0 AS neighbor_count" if exact else "COUNT(*) OVER () AS neighbor_count"
    ordinal = ", v.chunk_ordinal" if detail else ""
    rank = (
        ", row_number() OVER (PARTITION BY v.artifact_id, v.revision "
        "ORDER BY n.distance, v.chunk_ordinal) AS topic_rank"
        if detail
        else ""
    )
    order = "distance, artifact_id, revision DESC" + (", chunk_ordinal" if detail else "")
    chunk_columns = ", c.chunk_ordinal, c.start_offset, c.chunk_text" if detail else ""
    chunk_join = (
        "JOIN pc_topic_memory_active_chunks AS c ON c.scope_id=:scope_id "
        "AND c.artifact_id=s.artifact_id AND c.revision=s.revision AND c.chunk_ordinal=s.chunk_ordinal "
        if detail
        else ""
    )
    eligible_chunks = chunk_join.replace("s.", "v.")
    representative = "WHERE topic_rank=1" if detail else ""
    final_order = "s.distance, s.artifact_id, s.revision DESC" + (", s.chunk_ordinal" if detail else "")
    return (
        f"WITH nearest AS MATERIALIZED ({nearest}), "  # noqa: S608
        f"observed AS (SELECT rowid, distance, {observations} FROM nearest), "
        f"ranked AS (SELECT v.artifact_id, v.revision{ordinal}, n.distance, "
        f"n.neighbor_count{rank} FROM observed AS n "
        f"JOIN {metadata} AS v ON v.vector_id=n.rowid "
        "JOIN pc_topic_memory_active_topics AS a ON a.scope_id=v.scope_id "
        "AND a.artifact_id=v.artifact_id AND a.revision=v.revision "
        f"{eligible_chunks}WHERE v.scope_id=:scope_id), "
        f"selected AS (SELECT * FROM ranked {representative} ORDER BY {order} LIMIT :candidate_limit) "
        f"SELECT s.artifact_id, s.revision, a.title, a.summary{chunk_columns}, "
        "s.distance, s.neighbor_count FROM selected AS s "
        "JOIN pc_topic_memory_active_topics AS a ON a.scope_id=:scope_id "
        f"AND a.artifact_id=s.artifact_id AND a.revision=s.revision {chunk_join}ORDER BY {final_order}"
    )


_TOPIC_VECTOR_SEARCH_SQL = text(_vector_search_sql(detail=False))
_CHUNK_VECTOR_SEARCH_SQL = text(_vector_search_sql(detail=True))
_TOPIC_VECTOR_EXACT_SQL = text(_vector_search_sql(detail=False, exact=True))
_CHUNK_VECTOR_EXACT_SQL = text(_vector_search_sql(detail=True, exact=True))


class SQLiteTopicMemoryFTSIndex:
    """Maintain both active Topic and detail-chunk FTS5 channels."""

    capabilities = TopicMemoryCapabilities(fts=True)
    tables: tuple[Table, ...] = SQLITE_TOPIC_MEMORY_FTS_TABLES

    async def validate_current(self, _connection: AsyncConnection, /) -> None:
        pass

    async def initialize(self, connection: AsyncConnection, /) -> None:
        if connection.dialect.name != "sqlite":
            raise TopicMemoryCapabilityError("sqlite-fts")
        existing = await connection.scalar(
            text(
                "SELECT count(*) FROM sqlite_master WHERE type = 'table' AND name IN "
                "('pc_topic_memory_topic_fts', 'pc_topic_memory_chunk_fts')"
            )
        )
        marker = await connection.scalar(select(SQLITE_TOPIC_MEMORY_FTS_MARKER_TABLE.c.schema_version))
        if marker == 1 and existing == 2:
            return
        if marker is not None and marker != 1:
            raise TopicMemoryCapabilityError("sqlite-fts", "unsupported Topic FTS schema version")
        await connection.exec_driver_sql(_CREATE_TOPIC_FTS_SQL)
        await connection.exec_driver_sql(_CREATE_CHUNK_FTS_SQL)
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
        if marker is None:
            await connection.execute(insert(SQLITE_TOPIC_MEMORY_FTS_MARKER_TABLE).values(singleton=1, schema_version=1))

    async def replace(
        self,
        connection: AsyncConnection,
        scope_id: str,
        topic_ref: ArtifactRef,
        projection: TopicMemoryProjection,
        /,
    ) -> None:
        await self.replace_many(connection, scope_id, ((topic_ref, projection),))

    async def replace_many(
        self,
        connection: AsyncConnection,
        scope_id: str,
        items: Sequence[tuple[ArtifactRef, TopicMemoryProjection]],
        /,
    ) -> None:
        if not items:
            return
        identities = [{"scope_id": scope_id, "artifact_id": ref.artifact_id} for ref, _ in items]
        await connection.execute(_DELETE_TOPIC_FTS_SQL, identities)
        await connection.execute(_DELETE_CHUNK_FTS_SQL, identities)
        topics = []
        chunks = []
        for (ref, projection), identity in zip(items, identities, strict=True):
            values = {
                **identity,
                "revision": ref.revision,
                "title": projection.content.title,
                "summary": projection.content.summary,
                "searchable_text": projection.topic_searchable_text,
            }
            topics.append(values)
            chunks.extend(
                {
                    **values,
                    "chunk_ordinal": chunk.ordinal,
                    "start_offset": chunk.start_offset,
                    "chunk_text": chunk.text,
                    "searchable_text": analyze_text(chunk.text),
                }
                for chunk in projection.chunks
            )
        await connection.execute(_INSERT_TOPIC_FTS_SQL, topics)
        if chunks:
            await connection.execute(_INSERT_CHUNK_FTS_SQL, chunks)

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
        query_terms, required_matches = fts_query_requirements(request.query, floor=request.admission)
        if required_matches > len(query_terms):
            return TopicMemorySearchChannels()
        coverage = " + ".join(
            f"CASE WHEN instr(' ' || searchable_text || ' ', :coverage_term_{position}) > 0 THEN 1 ELSE 0 END"
            for position, _term in enumerate(query_terms)
        )
        parameters = {
            "query": query,
            "scope_id": scope_id,
            "candidate_limit": request.candidate_limit,
            "required_matches": required_matches,
            **{f"coverage_term_{position}": f" {term} " for position, term in enumerate(query_terms)},
        }
        topic_rows = tuple(
            (await connection.execute(text(_SEARCH_TOPIC_FTS_SQL.format(coverage=coverage)), parameters)).mappings()
        )
        chunk_rows = tuple(
            (await connection.execute(text(_SEARCH_CHUNK_FTS_SQL.format(coverage=coverage)), parameters)).mappings()
        )
        topic_hits = tuple(_channel_hit(row, "topic_fts") for row in topic_rows if not row["is_rejected"])
        chunk_hits = tuple(_channel_hit(row, "detail_fts") for row in chunk_rows if not row["is_rejected"])
        return TopicMemorySearchChannels(
            topic_fts=topic_hits,
            detail_fts=chunk_hits,
            topic_fts_retrieved=len(topic_rows),
            detail_fts_retrieved=len(chunk_rows),
            topic_fts_eligible=len(topic_hits),
            detail_fts_eligible=len(chunk_hits),
        )


class SQLiteTopicMemoryVectorIndex:
    """Maintain complete active Topic and chunk embeddings in sqlite-vec."""

    tables: tuple[Table, ...] = SQLITE_TOPIC_MEMORY_VECTOR_TABLES

    async def validate_current(self, connection: AsyncConnection, /) -> None:
        topic_rows = table("pc_topic_memory_topic_vec", column("rowid"), column("scope_id"))
        chunk_rows = table("pc_topic_memory_chunk_vec", column("rowid"), column("scope_id"))
        topics = SQLITE_TOPIC_MEMORY_VECTOR_TOPICS_TABLE
        chunks = SQLITE_TOPIC_MEMORY_VECTOR_CHUNKS_TABLE
        await validate_current_topic_vectors(
            connection,
            topics,
            chunks,
            self._fingerprint,
            topic_present=select(topic_rows.c.rowid)
            .where(topic_rows.c.rowid == topics.c.vector_id, topic_rows.c.scope_id == topics.c.scope_id)
            .correlate(topics)
            .exists(),
            chunk_present=select(chunk_rows.c.rowid)
            .where(chunk_rows.c.rowid == chunks.c.vector_id, chunk_rows.c.scope_id == chunks.c.scope_id)
            .correlate(chunks)
            .exists(),
        )

    def __init__(self, profile: EmbeddingProfile) -> None:
        if profile.dimension < 1 or profile.distance != "l2" or profile.normalization != "unit":
            raise TopicMemoryCapabilityError(
                "vector",
                "sqlite-vec requires a positive unit-normalized L2 embedding profile",
            )
        self.profile = profile
        self.capabilities = TopicMemoryCapabilities(fts=False, vector=True, embedding_profile=profile)
        self._fingerprint = topic_memory_embedding_profile_fingerprint(profile)

    async def initialize(self, connection: AsyncConnection, /) -> None:
        if connection.dialect.name != "sqlite":
            raise TopicMemoryCapabilityError("sqlite-vec")
        try:
            await connection.exec_driver_sql("SELECT vec_version()")
            for table_name in ("pc_topic_memory_topic_vec", "pc_topic_memory_chunk_vec"):
                await connection.exec_driver_sql(
                    f"CREATE VIRTUAL TABLE IF NOT EXISTS {table_name} "
                    f"USING vec0(scope_id TEXT partition key, embedding float[{self.profile.dimension}])"
                )
            probe = _pack_vector((0.0,) * self.profile.dimension)
            probe_scope = f"__powercontext_probe_{uuid4().hex}__"
            probes = (
                (
                    SQLITE_TOPIC_MEMORY_VECTOR_TOPICS_TABLE,
                    {},
                    _DELETE_TOPIC_VECTOR_SQL,
                    _INSERT_TOPIC_VECTOR_SQL,
                    _PROBE_TOPIC_VECTOR_SQL,
                    "pc_topic_memory_topic_vec",
                ),
                (
                    SQLITE_TOPIC_MEMORY_VECTOR_CHUNKS_TABLE,
                    {"chunk_ordinal": 0},
                    _DELETE_CHUNK_VECTOR_SQL,
                    _INSERT_CHUNK_VECTOR_SQL,
                    _PROBE_CHUNK_VECTOR_SQL,
                    "pc_topic_memory_chunk_vec",
                ),
            )
            for metadata, extra_values, delete_probe, insert_probe, select_probe, table_name in probes:
                await connection.execute(delete_probe, {"vector_id": -1})
                # Exercise metadata ID allocation and its vector row together without
                # retaining probe rows or advancing the persistent ID sequence.
                async with connection.begin_nested() as transaction:
                    inserted = await connection.execute(
                        insert(metadata).values(
                            scope_id=probe_scope,
                            artifact_id="probe",
                            revision=1,
                            profile_fingerprint=self._fingerprint,
                            **extra_values,
                        )
                    )
                    vector_id = inserted.lastrowid
                    await connection.execute(
                        insert_probe,
                        {"vector_id": vector_id, "scope_id": probe_scope, "embedding": probe},
                    )
                    row = (await connection.exec_driver_sql(select_probe, (probe_scope, probe))).one_or_none()
                    if row is None or int(row[0]) != vector_id:
                        raise TopicMemoryCapabilityError(
                            "vector",
                            f"sqlite-vec probe returned an invalid row for {table_name}",
                        )
                    await transaction.rollback()
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
        inserted = await connection.execute(
            insert(SQLITE_TOPIC_MEMORY_VECTOR_TOPICS_TABLE).values(
                scope_id=scope_id,
                artifact_id=topic_ref.artifact_id,
                revision=topic_ref.revision,
                profile_fingerprint=self._fingerprint,
            )
        )
        topic_id = inserted.lastrowid
        await connection.execute(
            _INSERT_TOPIC_VECTOR_SQL,
            {
                "vector_id": topic_id,
                "scope_id": scope_id,
                "embedding": _pack_vector(
                    canonical_embedding(
                        projection.topic_embedding,
                        dimension=self.profile.dimension,
                        normalization=self.profile.normalization,
                    )
                ),
            },
        )
        for chunk, embedding in zip(projection.chunks, projection.chunk_embeddings, strict=True):
            inserted = await connection.execute(
                insert(SQLITE_TOPIC_MEMORY_VECTOR_CHUNKS_TABLE).values(
                    scope_id=scope_id,
                    artifact_id=topic_ref.artifact_id,
                    revision=topic_ref.revision,
                    chunk_ordinal=chunk.ordinal,
                    profile_fingerprint=self._fingerprint,
                )
            )
            vector_id = inserted.lastrowid
            await connection.execute(
                _INSERT_CHUNK_VECTOR_SQL,
                {
                    "vector_id": vector_id,
                    "scope_id": scope_id,
                    "embedding": _pack_vector(
                        canonical_embedding(
                            embedding,
                            dimension=self.profile.dimension,
                            normalization=self.profile.normalization,
                        )
                    ),
                },
            )

    async def replace_many(
        self,
        connection: AsyncConnection,
        scope_id: str,
        items: Sequence[tuple[ArtifactRef, TopicMemoryProjection]],
        /,
    ) -> None:
        for ref, projection in items:
            await self.replace(connection, scope_id, ref, projection)

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
        query_vector = _pack_vector(
            canonical_embedding(
                request.query_vector,
                dimension=self.profile.dimension,
                normalization=self.profile.normalization,
            )
        )
        parameters = {
            "query_vector": query_vector,
            "scope_id": scope_id,
            "profile_fingerprint": self._fingerprint,
            "candidate_limit": request.candidate_limit,
        }
        return TopicMemorySearchChannels(
            topic_vector=await self._vector_hits(connection, parameters, detail=False),
            detail_vector=await self._vector_hits(connection, parameters, detail=True),
        )

    async def _vector_hits(
        self, connection: AsyncConnection, parameters: Mapping[str, Any], *, detail: bool
    ) -> tuple[TopicMemoryChannelHit, ...]:
        limit = parameters["candidate_limit"]
        neighbor_limit = limit * 4 if detail else limit
        statement = _CHUNK_VECTOR_SEARCH_SQL if detail else _TOPIC_VECTOR_SEARCH_SQL
        rows = tuple((await connection.execute(statement, {**parameters, "neighbor_limit": neighbor_limit})).mappings())
        if rows and rows[0]["neighbor_count"] == neighbor_limit and len(rows) < limit:
            rows = tuple(
                (
                    await connection.execute(_CHUNK_VECTOR_EXACT_SQL if detail else _TOPIC_VECTOR_EXACT_SQL, parameters)
                ).mappings()
            )
        channel = "detail_vector" if detail else "topic_vector"
        return tuple(_channel_hit(row, channel) for row in rows)

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
        raw_score=None if row.get("raw_score") is None else float(row["raw_score"]),
        metric="sqlite_bm25" if channel.endswith("_fts") else None,
    )


def _pack_vector(vector: tuple[float, ...]) -> bytes:
    return struct.pack(f"<{len(vector)}f", *vector)


__all__ = [
    "SQLITE_TOPIC_MEMORY_FTS_TABLES",
    "SQLITE_TOPIC_MEMORY_VECTOR_TABLES",
    "SQLiteTopicMemoryFTSIndex",
    "SQLiteTopicMemoryVectorIndex",
]
