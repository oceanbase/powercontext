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

"""OceanBase Topic Memory FULLTEXT and vector active projections."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pyobvector import VECTOR, VectorIndex
from sqlalchemy import (
    BigInteger,
    Column,
    MetaData,
    String,
    Table,
    UniqueConstraint,
    bindparam,
    case,
    delete,
    func,
    insert,
    literal,
    select,
    text,
)
from sqlalchemy.dialects.mysql import match
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.memory import EmbeddingProfile
from powercontext.builtin.artifacts.memory.canonical import canonical_embedding
from powercontext.builtin.artifacts.search import fts_query_requirements
from powercontext.builtin.artifacts.topic_memory import (
    TOPIC_MEMORY_CHUNK_MAX_COUNT,
    TopicMemoryCapabilities,
    TopicMemoryCapabilityError,
    TopicMemoryChannelHit,
    TopicMemoryMatchedBy,
    TopicMemoryProjection,
    TopicMemorySearchChannels,
    TopicMemorySearchRequest,
)
from powercontext.builtin.persistence.tables import (
    TOPIC_MEMORY_ACTIVE_CHUNKS_TABLE,
    TOPIC_MEMORY_ACTIVE_TOPICS_TABLE,
    identity_string,
)
from powercontext.builtin.persistence.topic_memory_index import topic_memory_embedding_profile_fingerprint
from powercontext.limits import MAX_ARTIFACT_ID_LENGTH, MAX_SCOPE_ID_LENGTH

_TOPIC_FTS_INDEX = "ix_pc_topic_memory_active_topics_fts"
_CHUNK_FTS_INDEX = "ix_pc_topic_memory_active_chunks_fts"
_TOPIC_VECTOR_TABLE = "pc_topic_memory_vector_topics"
_CHUNK_VECTOR_TABLE = "pc_topic_memory_vector_chunks"
_TOPIC_VECTOR_INDEX = "ix_pc_topic_memory_vector_topics_embedding"
_CHUNK_VECTOR_INDEX = "ix_pc_topic_memory_vector_chunks_embedding"

_INDEX_EXISTS_SQL = text(
    """
    SELECT COUNT(*) FROM information_schema.statistics
    WHERE table_schema = DATABASE() AND table_name = :table_name AND index_name = :index_name
    """
)
_VECTOR_TYPE_SQL = text(
    """
    SELECT data_type FROM information_schema.columns
    WHERE table_schema = DATABASE() AND table_name = :table_name AND column_name = 'embedding'
    """
)
_TOPIC_VECTOR_SEARCH = """
SELECT a.artifact_id, a.revision, a.title, a.summary,
       l2_distance(v.embedding, :query_vector) AS distance
FROM pc_topic_memory_vector_topics AS v
JOIN pc_topic_memory_active_topics AS a
  ON a.scope_id = v.scope_id AND a.artifact_id = v.artifact_id AND a.revision = v.revision
WHERE v.scope_id = :scope_id
ORDER BY l2_distance(v.embedding, :query_vector) APPROXIMATE, a.artifact_id, a.revision DESC
LIMIT :candidate_limit
"""
_CHUNK_VECTOR_SEARCH = """
WITH scored AS (
    SELECT a.artifact_id, a.revision, a.title, a.summary,
           c.chunk_ordinal, c.start_offset, c.chunk_text,
           l2_distance(v.embedding, :query_vector) AS distance
    FROM pc_topic_memory_vector_chunks AS v
    JOIN pc_topic_memory_active_topics AS a
      ON a.scope_id = v.scope_id AND a.artifact_id = v.artifact_id AND a.revision = v.revision
    JOIN pc_topic_memory_active_chunks AS c
      ON c.scope_id = v.scope_id AND c.artifact_id = v.artifact_id
     AND c.revision = v.revision AND c.chunk_ordinal = v.chunk_ordinal
    WHERE v.scope_id = :scope_id
    ORDER BY l2_distance(v.embedding, :query_vector) APPROXIMATE,
             v.artifact_id, v.revision DESC, v.chunk_ordinal
    LIMIT :neighbor_limit
), ranked AS (
    SELECT scored.*,
           row_number() OVER (
               PARTITION BY artifact_id, revision
               ORDER BY distance, chunk_ordinal
           ) AS topic_rank
    FROM scored
)
SELECT artifact_id, revision, title, summary,
       chunk_ordinal, start_offset, chunk_text, distance
FROM ranked
WHERE topic_rank = 1
ORDER BY distance, artifact_id, revision DESC, chunk_ordinal
LIMIT :candidate_limit
"""


class OceanBaseTopicMemoryFTSIndex:
    """Use native FULLTEXT indexes over both relational active projections."""

    capabilities = TopicMemoryCapabilities(fts=True)
    tables: tuple[Table, ...] = ()

    async def initialize(self, connection: AsyncConnection, /) -> None:
        if connection.dialect.name != "mysql":
            raise TopicMemoryCapabilityError("oceanbase-fts")
        await self._ensure_index(connection, TOPIC_MEMORY_ACTIVE_TOPICS_TABLE.name, _TOPIC_FTS_INDEX)
        await self._ensure_index(connection, TOPIC_MEMORY_ACTIVE_CHUNKS_TABLE.name, _CHUNK_FTS_INDEX)
        await connection.execute(
            select(TOPIC_MEMORY_ACTIVE_TOPICS_TABLE.c.artifact_id)
            .where(match(TOPIC_MEMORY_ACTIVE_TOPICS_TABLE.c.searchable_text, against="powercontext"))
            .limit(1)
        )

    async def replace(
        self,
        _connection: AsyncConnection,
        _scope_id: str,
        _topic_ref: ArtifactRef,
        _projection: TopicMemoryProjection,
        /,
    ) -> None:
        pass

    async def search(
        self,
        connection: AsyncConnection,
        scope_id: str,
        request: TopicMemorySearchRequest,
        /,
    ) -> TopicMemorySearchChannels:
        if request.mode not in {"fts", "hybrid"} or not request.analyzed_query:
            return TopicMemorySearchChannels()
        query_terms, coverage_required = fts_query_requirements(request.query)
        topic_score = match(TOPIC_MEMORY_ACTIVE_TOPICS_TABLE.c.searchable_text, against=request.analyzed_query)
        topic_coverage = _coverage_expression(
            TOPIC_MEMORY_ACTIVE_TOPICS_TABLE.c.searchable_text,
            query_terms,
            coverage_required,
        )
        topic_rows = (
            await connection.execute(
                select(
                    TOPIC_MEMORY_ACTIVE_TOPICS_TABLE.c.artifact_id,
                    TOPIC_MEMORY_ACTIVE_TOPICS_TABLE.c.revision,
                    TOPIC_MEMORY_ACTIVE_TOPICS_TABLE.c.title,
                    TOPIC_MEMORY_ACTIVE_TOPICS_TABLE.c.summary,
                )
                .where(
                    TOPIC_MEMORY_ACTIVE_TOPICS_TABLE.c.scope_id == scope_id,
                    topic_score,
                    topic_coverage,
                )
                .order_by(
                    topic_score.desc(),
                    TOPIC_MEMORY_ACTIVE_TOPICS_TABLE.c.artifact_id,
                    TOPIC_MEMORY_ACTIVE_TOPICS_TABLE.c.revision.desc(),
                )
                .limit(request.candidate_limit)
            )
        ).mappings()
        chunk_score = match(TOPIC_MEMORY_ACTIVE_CHUNKS_TABLE.c.searchable_text, against=request.analyzed_query)
        chunk_coverage = _coverage_expression(
            TOPIC_MEMORY_ACTIVE_CHUNKS_TABLE.c.searchable_text,
            query_terms,
            coverage_required,
        )
        chunk_candidates = (
            select(
                TOPIC_MEMORY_ACTIVE_TOPICS_TABLE.c.artifact_id.label("artifact_id"),
                TOPIC_MEMORY_ACTIVE_TOPICS_TABLE.c.revision.label("revision"),
                TOPIC_MEMORY_ACTIVE_TOPICS_TABLE.c.title.label("title"),
                TOPIC_MEMORY_ACTIVE_TOPICS_TABLE.c.summary.label("summary"),
                TOPIC_MEMORY_ACTIVE_CHUNKS_TABLE.c.chunk_ordinal.label("chunk_ordinal"),
                TOPIC_MEMORY_ACTIVE_CHUNKS_TABLE.c.start_offset.label("start_offset"),
                TOPIC_MEMORY_ACTIVE_CHUNKS_TABLE.c.chunk_text.label("chunk_text"),
                chunk_score.label("score"),
                func
                .row_number()
                .over(
                    partition_by=(
                        TOPIC_MEMORY_ACTIVE_TOPICS_TABLE.c.artifact_id,
                        TOPIC_MEMORY_ACTIVE_TOPICS_TABLE.c.revision,
                    ),
                    order_by=(chunk_score.desc(), TOPIC_MEMORY_ACTIVE_CHUNKS_TABLE.c.chunk_ordinal),
                )
                .label("topic_rank"),
            )
            .join(
                TOPIC_MEMORY_ACTIVE_CHUNKS_TABLE,
                (TOPIC_MEMORY_ACTIVE_CHUNKS_TABLE.c.scope_id == TOPIC_MEMORY_ACTIVE_TOPICS_TABLE.c.scope_id)
                & (TOPIC_MEMORY_ACTIVE_CHUNKS_TABLE.c.artifact_id == TOPIC_MEMORY_ACTIVE_TOPICS_TABLE.c.artifact_id)
                & (TOPIC_MEMORY_ACTIVE_CHUNKS_TABLE.c.revision == TOPIC_MEMORY_ACTIVE_TOPICS_TABLE.c.revision),
            )
            .where(
                TOPIC_MEMORY_ACTIVE_CHUNKS_TABLE.c.scope_id == scope_id,
                chunk_score,
                chunk_coverage,
            )
            .subquery()
        )
        chunk_rows = (
            await connection.execute(
                select(
                    chunk_candidates.c.artifact_id,
                    chunk_candidates.c.revision,
                    chunk_candidates.c.title,
                    chunk_candidates.c.summary,
                    chunk_candidates.c.chunk_ordinal,
                    chunk_candidates.c.start_offset,
                    chunk_candidates.c.chunk_text,
                )
                .where(chunk_candidates.c.topic_rank == 1)
                .order_by(
                    chunk_candidates.c.score.desc(),
                    chunk_candidates.c.artifact_id,
                    chunk_candidates.c.revision.desc(),
                    chunk_candidates.c.chunk_ordinal,
                )
                .limit(request.candidate_limit)
            )
        ).mappings()
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

    @staticmethod
    async def _ensure_index(connection: AsyncConnection, table_name: str, index_name: str) -> None:
        count = await connection.scalar(
            _INDEX_EXISTS_SQL,
            {"table_name": table_name, "index_name": index_name},
        )
        if int(count or 0) == 0:
            await connection.exec_driver_sql(
                f"CREATE FULLTEXT INDEX {index_name} ON {table_name} (searchable_text) WITH PARSER SPACE"
            )


class OceanBaseTopicMemoryVectorIndex:
    """Use native HNSW indexes for complete Topic and detail embeddings."""

    def __init__(self, profile: EmbeddingProfile) -> None:
        if profile.dimension < 1 or profile.distance != "l2" or profile.normalization != "unit":
            raise TopicMemoryCapabilityError(
                "vector",
                "OceanBase requires a positive unit-normalized L2 embedding profile",
            )
        self.profile = profile
        self.capabilities = TopicMemoryCapabilities(fts=False, vector=True, embedding_profile=profile)
        self._fingerprint = topic_memory_embedding_profile_fingerprint(profile)
        metadata = MetaData()
        self.topic_table = Table(
            _TOPIC_VECTOR_TABLE,
            metadata,
            Column("vector_id", BigInteger, primary_key=True, autoincrement=True),
            Column("scope_id", identity_string(MAX_SCOPE_ID_LENGTH), nullable=False),
            Column("artifact_id", identity_string(MAX_ARTIFACT_ID_LENGTH), nullable=False),
            Column("revision", BigInteger, nullable=False),
            Column("profile_fingerprint", String(64), nullable=False),
            Column("embedding", VECTOR(profile.dimension), nullable=False),
            UniqueConstraint("scope_id", "artifact_id", name="uq_pc_topic_memory_vector_topics_active"),
        )
        self.chunk_table = Table(
            _CHUNK_VECTOR_TABLE,
            metadata,
            Column("vector_id", BigInteger, primary_key=True, autoincrement=True),
            Column("scope_id", identity_string(MAX_SCOPE_ID_LENGTH), nullable=False),
            Column("artifact_id", identity_string(MAX_ARTIFACT_ID_LENGTH), nullable=False),
            Column("revision", BigInteger, nullable=False),
            Column("chunk_ordinal", BigInteger, nullable=False),
            Column("profile_fingerprint", String(64), nullable=False),
            Column("embedding", VECTOR(profile.dimension), nullable=False),
            UniqueConstraint(
                "scope_id",
                "artifact_id",
                "chunk_ordinal",
                name="uq_pc_topic_memory_vector_chunks_active",
            ),
        )
        self.tables: tuple[Table, ...] = (self.topic_table, self.chunk_table)
        self._topic_index = VectorIndex(
            _TOPIC_VECTOR_INDEX,
            self.topic_table.c.embedding,
            params="distance=l2,type=hnsw",
        )
        self._chunk_index = VectorIndex(
            _CHUNK_VECTOR_INDEX,
            self.chunk_table.c.embedding,
            params="distance=l2,type=hnsw",
        )
        vector_type = VECTOR(profile.dimension)
        self._topic_search = text(_TOPIC_VECTOR_SEARCH).bindparams(bindparam("query_vector", type_=vector_type))
        self._chunk_search = text(_CHUNK_VECTOR_SEARCH).bindparams(bindparam("query_vector", type_=vector_type))

    async def initialize(self, connection: AsyncConnection, /) -> None:
        if connection.dialect.name != "mysql":
            raise TopicMemoryCapabilityError("oceanbase-vector")
        expected = f"VECTOR({self.profile.dimension})"
        for table in self.tables:
            actual = await connection.scalar(_VECTOR_TYPE_SQL, {"table_name": table.name})
            if str(actual).upper() != expected:
                raise TopicMemoryCapabilityError(
                    "vector",
                    f"OceanBase projection {table.name} uses {actual!r}; expected {expected}",
                )
        await connection.run_sync(lambda sync: self._topic_index.create(sync, checkfirst=True))
        await connection.run_sync(lambda sync: self._chunk_index.create(sync, checkfirst=True))

    async def replace(
        self,
        connection: AsyncConnection,
        scope_id: str,
        topic_ref: ArtifactRef,
        projection: TopicMemoryProjection,
        /,
    ) -> None:
        if projection.topic_embedding is None or len(projection.chunk_embeddings) != len(projection.chunks):
            raise TopicMemoryCapabilityError("vector", "projection is incomplete")
        await connection.execute(
            delete(self.chunk_table).where(
                self.chunk_table.c.scope_id == scope_id,
                self.chunk_table.c.artifact_id == topic_ref.artifact_id,
            )
        )
        await connection.execute(
            delete(self.topic_table).where(
                self.topic_table.c.scope_id == scope_id,
                self.topic_table.c.artifact_id == topic_ref.artifact_id,
            )
        )
        await connection.execute(
            insert(self.topic_table).values(
                scope_id=scope_id,
                artifact_id=topic_ref.artifact_id,
                revision=topic_ref.revision,
                profile_fingerprint=self._fingerprint,
                embedding=canonical_embedding(
                    projection.topic_embedding,
                    dimension=self.profile.dimension,
                    normalization=self.profile.normalization,
                ),
            )
        )
        await connection.execute(
            insert(self.chunk_table),
            [
                {
                    "scope_id": scope_id,
                    "artifact_id": topic_ref.artifact_id,
                    "revision": topic_ref.revision,
                    "chunk_ordinal": chunk.ordinal,
                    "profile_fingerprint": self._fingerprint,
                    "embedding": canonical_embedding(
                        embedding,
                        dimension=self.profile.dimension,
                        normalization=self.profile.normalization,
                    ),
                }
                for chunk, embedding in zip(projection.chunks, projection.chunk_embeddings, strict=True)
            ],
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
        parameters = {
            "scope_id": scope_id,
            "query_vector": canonical_embedding(
                request.query_vector,
                dimension=self.profile.dimension,
                normalization=self.profile.normalization,
            ),
            "candidate_limit": request.candidate_limit,
            "neighbor_limit": request.candidate_limit * TOPIC_MEMORY_CHUNK_MAX_COUNT,
        }
        topic_rows = (await connection.execute(self._topic_search, parameters)).mappings()
        chunk_rows = (await connection.execute(self._chunk_search, parameters)).mappings()
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
        topic_count = int(
            await connection.scalar(
                select(func.count())
                .select_from(self.topic_table)
                .where(
                    self.topic_table.c.scope_id == scope_id,
                    self.topic_table.c.artifact_id == topic_ref.artifact_id,
                    self.topic_table.c.revision == topic_ref.revision,
                    self.topic_table.c.profile_fingerprint == self._fingerprint,
                )
            )
            or 0
        )
        expected_ordinals = set(
            (
                await connection.execute(
                    select(TOPIC_MEMORY_ACTIVE_CHUNKS_TABLE.c.chunk_ordinal).where(
                        TOPIC_MEMORY_ACTIVE_CHUNKS_TABLE.c.scope_id == scope_id,
                        TOPIC_MEMORY_ACTIVE_CHUNKS_TABLE.c.artifact_id == topic_ref.artifact_id,
                        TOPIC_MEMORY_ACTIVE_CHUNKS_TABLE.c.revision == topic_ref.revision,
                    )
                )
            ).scalars()
        )
        stored_ordinals = set(
            (
                await connection.execute(
                    select(self.chunk_table.c.chunk_ordinal).where(
                        self.chunk_table.c.scope_id == scope_id,
                        self.chunk_table.c.artifact_id == topic_ref.artifact_id,
                        self.chunk_table.c.revision == topic_ref.revision,
                        self.chunk_table.c.profile_fingerprint == self._fingerprint,
                    )
                )
            ).scalars()
        )
        return topic_count == 1 and bool(expected_ordinals) and stored_ordinals == expected_ordinals


def _coverage_expression(column: Any, terms: tuple[str, ...], required: int) -> Any:
    matches = (
        case(
            (
                func.instr(func.concat(literal(" "), column, literal(" ")), f" {term} ") > 0,
                1,
            ),
            else_=0,
        )
        for term in terms
    )
    return sum(matches, start=literal(0)) >= required


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


__all__ = ["OceanBaseTopicMemoryFTSIndex", "OceanBaseTopicMemoryVectorIndex"]
