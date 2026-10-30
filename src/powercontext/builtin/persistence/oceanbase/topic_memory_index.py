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

import asyncio
from collections.abc import Mapping, Sequence
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
    desc,
    func,
    insert,
    literal,
    select,
    text,
    union_all,
)
from sqlalchemy.dialects.mysql import match
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.memory import EmbeddingProfile
from powercontext.builtin.artifacts.memory.canonical import canonical_embedding
from powercontext.builtin.artifacts.search import fts_query_requirements
from powercontext.builtin.artifacts.topic_memory import (
    TopicMemoryCapabilities,
    TopicMemoryCapabilityError,
    TopicMemoryChannelHit,
    TopicMemoryMatchedBy,
    TopicMemoryProjection,
    TopicMemorySearchChannels,
    TopicMemorySearchRequest,
    TopicMemoryStorageInvariantError,
)
from powercontext.builtin.persistence.oceanbase.schema import create_vector_index
from powercontext.builtin.persistence.tables import (
    TOPIC_MEMORY_ACTIVE_CHUNKS_TABLE,
    TOPIC_MEMORY_ACTIVE_TOPICS_TABLE,
    identity_string,
)
from powercontext.builtin.persistence.topic_memory_index import (
    topic_memory_embedding_profile_fingerprint,
    validate_current_topic_vectors,
)
from powercontext.limits import MAX_ARTIFACT_ID_LENGTH, MAX_SCOPE_ID_LENGTH

_TOPIC_FTS_INDEX = "ix_pc_topic_memory_active_topics_fts"
_CHUNK_FTS_INDEX = "ix_pc_topic_memory_active_chunks_fts"
_INDEX_DDL_RETRY_DELAYS = (0.1, 0.2, 0.4, 0.8, 1.6)
_TOPIC_VECTOR_TABLE = "pc_topic_memory_vector_topics"
_CHUNK_VECTOR_TABLE = "pc_topic_memory_vector_chunks"
_TOPIC_VECTOR_INDEX = "ix_pc_topic_memory_vector_topics_embedding"

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
# Keep ANN selection join-free: CE 4.3.5.6 can lose neighbors when its
# distance-ordered vector scan participates directly in a merge join.
# Hydrate only the selected Topic identities. Chunk retrieval must collapse
# before LIMIT: a bounded ANN chunk pool cannot guarantee distinct Topic hits.
_TOPIC_VECTOR_SEARCH = """
WITH candidates AS (
    SELECT v.scope_id, v.artifact_id, v.revision,
           l2_distance(v.embedding, :query_vector) AS distance
    FROM pc_topic_memory_vector_topics AS v
    WHERE v.scope_id = :scope_id
      AND v.profile_fingerprint = :profile_fingerprint
    ORDER BY l2_distance(v.embedding, :query_vector) APPROXIMATE
    LIMIT :candidate_limit
)
SELECT n.scope_id, n.artifact_id, n.revision, a.revision AS active_revision,
       a.title, a.summary, n.distance
FROM candidates AS n
LEFT JOIN pc_topic_memory_active_topics AS a
  ON a.scope_id = n.scope_id AND a.artifact_id = n.artifact_id AND a.revision = n.revision
ORDER BY distance, artifact_id, revision DESC
"""
_CHUNK_VECTOR_SEARCH = """
WITH distances AS (
    SELECT v.scope_id, v.artifact_id, v.revision, v.chunk_ordinal,
           l2_distance(v.embedding, :query_vector) AS distance
    FROM pc_topic_memory_vector_chunks AS v
    WHERE v.scope_id = :scope_id
      AND v.profile_fingerprint = :profile_fingerprint
), ranked AS (
    SELECT n.scope_id, n.artifact_id, n.revision, n.chunk_ordinal, n.distance,
           row_number() OVER (
               PARTITION BY n.artifact_id, n.revision
               ORDER BY n.distance, n.chunk_ordinal
           ) AS topic_rank
    FROM distances AS n
), candidates AS (
    SELECT scope_id, artifact_id, revision, chunk_ordinal, distance
    FROM ranked
    WHERE topic_rank = 1
    ORDER BY distance, artifact_id, revision DESC, chunk_ordinal
    LIMIT :candidate_limit
)
SELECT n.scope_id, n.artifact_id, n.revision, a.revision AS active_revision,
       a.title, a.summary, n.chunk_ordinal, c.chunk_ordinal AS active_chunk_ordinal,
       c.start_offset, c.chunk_text, n.distance
FROM candidates AS n
LEFT JOIN pc_topic_memory_active_topics AS a
  ON a.scope_id = n.scope_id AND a.artifact_id = n.artifact_id AND a.revision = n.revision
LEFT JOIN pc_topic_memory_active_chunks AS c
  ON c.scope_id = n.scope_id AND c.artifact_id = n.artifact_id
 AND c.revision = n.revision AND c.chunk_ordinal = n.chunk_ordinal
ORDER BY n.distance, n.artifact_id, n.revision DESC, n.chunk_ordinal
"""


class OceanBaseTopicMemoryFTSIndex:
    """Use native FULLTEXT indexes over both relational active projections."""

    capabilities = TopicMemoryCapabilities(fts=True)
    tables: tuple[Table, ...] = ()

    async def validate_current(self, _connection: AsyncConnection, /) -> None:
        pass

    async def initialize(self, connection: AsyncConnection, /) -> None:
        if connection.dialect.name != "mysql":
            raise TopicMemoryCapabilityError("oceanbase-fts")
        for table, index_name in (
            (TOPIC_MEMORY_ACTIVE_TOPICS_TABLE, _TOPIC_FTS_INDEX),
            (TOPIC_MEMORY_ACTIVE_CHUNKS_TABLE, _CHUNK_FTS_INDEX),
        ):
            await self._ensure_index(connection, table.name, index_name)
            await connection.execute(
                select(table.c.artifact_id).where(match(table.c.searchable_text, against="powercontext")).limit(1)
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

    async def replace_many(self, _connection, _scope_id, _items, /) -> None:
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
        query_terms, coverage_required = fts_query_requirements(request.query, floor=request.admission)
        if coverage_required > len(query_terms):
            return TopicMemorySearchChannels()
        topic_score = match(TOPIC_MEMORY_ACTIVE_TOPICS_TABLE.c.searchable_text, against=request.analyzed_query)
        topic_coverage = _coverage_expression(
            TOPIC_MEMORY_ACTIVE_TOPICS_TABLE.c.searchable_text,
            query_terms,
            coverage_required,
        )
        topic_matches = (
            select(
                TOPIC_MEMORY_ACTIVE_TOPICS_TABLE.c.artifact_id,
                TOPIC_MEMORY_ACTIVE_TOPICS_TABLE.c.revision,
                TOPIC_MEMORY_ACTIVE_TOPICS_TABLE.c.title,
                TOPIC_MEMORY_ACTIVE_TOPICS_TABLE.c.summary,
                topic_score.label("raw_score"),
                topic_coverage.label("coverage"),
            )
            .where(TOPIC_MEMORY_ACTIVE_TOPICS_TABLE.c.scope_id == scope_id, topic_score)
            .cte("topic_matches")
        )
        topic_candidates = (
            select(*(topic_matches.c[name] for name in ("artifact_id", "revision", "title", "summary", "raw_score")))
            .where(topic_matches.c.coverage)
            .order_by(topic_matches.c.raw_score.desc(), topic_matches.c.artifact_id, topic_matches.c.revision.desc())
            .limit(request.candidate_limit)
            .cte("topic_candidates")
        )
        topic_rejected = (
            select(literal(1))
            .select_from(topic_matches)
            .where(~topic_matches.c.coverage)
            .limit(1)
            .cte("topic_rejected")
        )
        topic_rows = tuple(
            (
                await connection.execute(
                    union_all(
                        select(topic_candidates, literal(0).label("is_rejected")),
                        select(*(literal(None) for _ in range(5)), literal(1)).select_from(topic_rejected),
                    ).order_by("is_rejected", desc("raw_score"), "artifact_id", desc("revision"))
                )
            ).mappings()
        )
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
                chunk_coverage.label("coverage"),
                func
                .row_number()
                .over(
                    partition_by=(
                        TOPIC_MEMORY_ACTIVE_TOPICS_TABLE.c.artifact_id,
                        TOPIC_MEMORY_ACTIVE_TOPICS_TABLE.c.revision,
                    ),
                    order_by=(
                        chunk_coverage.desc(),
                        chunk_score.desc(),
                        TOPIC_MEMORY_ACTIVE_CHUNKS_TABLE.c.chunk_ordinal,
                    ),
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
            )
            .subquery()
        )
        chunk_matches = select(chunk_candidates).where(chunk_candidates.c.topic_rank == 1).cte("chunk_matches")
        chunk_selected = (
            select(
                *(
                    chunk_matches.c[name]
                    for name in (
                        "artifact_id",
                        "revision",
                        "title",
                        "summary",
                        "chunk_ordinal",
                        "start_offset",
                        "chunk_text",
                    )
                ),
                chunk_matches.c.score.label("raw_score"),
            )
            .where(chunk_matches.c.coverage)
            .order_by(
                chunk_matches.c.score.desc(),
                chunk_matches.c.artifact_id,
                chunk_matches.c.revision.desc(),
                chunk_matches.c.chunk_ordinal,
            )
            .limit(request.candidate_limit)
            .cte("chunk_candidates")
        )
        chunk_rejected = (
            select(literal(1))
            .select_from(chunk_matches)
            .where(~chunk_matches.c.coverage)
            .limit(1)
            .cte("chunk_rejected")
        )
        chunk_rows = tuple(
            (
                await connection.execute(
                    union_all(
                        select(chunk_selected, literal(0).label("is_rejected")),
                        select(*(literal(None) for _ in range(8)), literal(1)).select_from(chunk_rejected),
                    ).order_by("is_rejected", desc("raw_score"), "artifact_id", desc("revision"), "chunk_ordinal")
                )
            ).mappings()
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

    @staticmethod
    async def _ensure_index(connection: AsyncConnection, table_name: str, index_name: str) -> None:
        for attempt in range(len(_INDEX_DDL_RETRY_DELAYS) + 1):
            count = await connection.scalar(
                _INDEX_EXISTS_SQL,
                {"table_name": table_name, "index_name": index_name},
            )
            if int(count or 0):
                return
            try:
                await connection.exec_driver_sql(
                    f"CREATE FULLTEXT INDEX IF NOT EXISTS {index_name} ON {table_name} (searchable_text) WITH PARSER SPACE"
                )
            except OperationalError as error:
                # OceanBase maps a NOWAIT DDL lock conflict to MySQL 1205.
                # Retry only this idempotent DDL, checking for an index another
                # initializer may have created. Never replay startup writes or
                # roll back/commit the caller's transaction here.
                if (
                    error.orig is None
                    or not error.orig.args
                    or error.orig.args[0] != 1205
                    or attempt == len(_INDEX_DDL_RETRY_DELAYS)
                ):
                    raise
                await asyncio.sleep(_INDEX_DDL_RETRY_DELAYS[attempt])
            else:
                return


class OceanBaseTopicMemoryVectorIndex:
    """Use title HNSW and exact per-Topic detail ranking over current vectors."""

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
        for attempt in range(len(_INDEX_DDL_RETRY_DELAYS) + 1):
            try:
                await create_vector_index(connection, self._topic_index)
            except OperationalError as error:
                if (
                    error.orig is None
                    or not error.orig.args
                    or error.orig.args[0] != 1205
                    or attempt == len(_INDEX_DDL_RETRY_DELAYS)
                ):
                    raise
                await asyncio.sleep(_INDEX_DDL_RETRY_DELAYS[attempt])
            else:
                return

    async def validate_current(self, connection: AsyncConnection, /) -> None:
        await validate_current_topic_vectors(connection, self.topic_table, self.chunk_table, self._fingerprint)

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
        # Replacing the same identity twice has the same final projection as
        # the last write; keep that behavior without issuing per-item SQL.
        latest = {ref.artifact_id: (ref, projection) for ref, projection in items}
        topics = []
        chunks = []
        for ref, projection in latest.values():
            if projection.topic_embedding is None or len(projection.chunk_embeddings) != len(projection.chunks):
                raise TopicMemoryCapabilityError("vector", "projection is incomplete")
            values = {
                "scope_id": scope_id,
                "artifact_id": ref.artifact_id,
                "revision": ref.revision,
                "profile_fingerprint": self._fingerprint,
            }
            topics.append({
                **values,
                "embedding": canonical_embedding(
                    projection.topic_embedding,
                    dimension=self.profile.dimension,
                    normalization=self.profile.normalization,
                ),
            })
            chunks.extend(
                {
                    **values,
                    "chunk_ordinal": chunk.ordinal,
                    "embedding": canonical_embedding(
                        embedding,
                        dimension=self.profile.dimension,
                        normalization=self.profile.normalization,
                    ),
                }
                for chunk, embedding in zip(projection.chunks, projection.chunk_embeddings, strict=True)
            )
        ids = tuple(latest)
        for table in (self.chunk_table, self.topic_table):
            await connection.execute(delete(table).where(table.c.scope_id == scope_id, table.c.artifact_id.in_(ids)))
        await connection.execute(insert(self.topic_table), topics)
        if chunks:
            await connection.execute(insert(self.chunk_table), chunks)

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
            "profile_fingerprint": self._fingerprint,
            "query_vector": canonical_embedding(
                request.query_vector,
                dimension=self.profile.dimension,
                normalization=self.profile.normalization,
            ),
            "candidate_limit": request.candidate_limit,
        }
        topic_rows = (await connection.execute(self._topic_search, parameters)).mappings()
        chunk_rows = (await connection.execute(self._chunk_search, parameters)).mappings()
        return TopicMemorySearchChannels(
            topic_vector=tuple(_channel_hit(row, "topic_vector") for row in topic_rows),
            detail_vector=tuple(_channel_hit(row, "detail_vector") for row in chunk_rows),
        )


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
    ref = ArtifactRef(family="topic-memory", artifact_id=str(row["artifact_id"]), revision=int(row["revision"]))
    if channel.endswith("_vector") and row["active_revision"] is None:
        raise TopicMemoryStorageInvariantError("missing-active-head", (str(row["scope_id"]), ref.artifact_id))
    if channel == "detail_vector" and row["active_chunk_ordinal"] is None:
        raise TopicMemoryStorageInvariantError("missing-active-chunks", (str(row["scope_id"]), ref))
    return TopicMemoryChannelHit(
        artifact_ref=ref,
        title=str(row["title"]),
        summary=str(row["summary"]),
        channel=channel,
        chunk_ordinal=None if row.get("chunk_ordinal") is None else int(row["chunk_ordinal"]),
        chunk_start=None if row.get("start_offset") is None else int(row["start_offset"]),
        chunk_text=None if row.get("chunk_text") is None else str(row["chunk_text"]),
        distance=None if row.get("distance") is None else float(row["distance"]),
        raw_score=None if row.get("raw_score") is None else float(row["raw_score"]),
        metric="oceanbase_match" if channel.endswith("_fts") else None,
    )


__all__ = ["OceanBaseTopicMemoryFTSIndex", "OceanBaseTopicMemoryVectorIndex"]
