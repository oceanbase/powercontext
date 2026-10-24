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

"""OceanBase/seekDB Atomic Memory using same-table FULLTEXT and VECTOR."""

from __future__ import annotations

from pyobvector import VECTOR, VectorIndex
from sqlalchemy import bindparam, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.builtin.artifacts.memory import EmbeddingProfile
from powercontext.builtin.artifacts.search import DEFAULT_ADMISSION_FLOOR, fts_query_requirements
from powercontext.builtin.persistence.atomic_memory_index import (
    AtomicMemoryIndexError,
    AtomicMemoryIndexHit,
    AtomicMemoryRelatedRequest,
    AtomicMemorySearchChannels,
    AtomicMemorySearchRequest,
    RelationalAtomicMemoryIndex,
    atomic_memory_channel_hits,
    atomic_memory_filter_sql,
    atomic_memory_profile_fingerprint,
    atomic_memory_vector_sql,
    combine_atomic_memory_channels,
    oceanbase_fts_coverage_sql,
)
from powercontext.builtin.persistence.atomic_memory_index_schema import (
    ATOMIC_MEMORY_PROJECTION_FORMAT,
    atomic_memory_current_table,
)
from powercontext.builtin.persistence.schema import create_tables

_FTS_INDEX_NAME = "ix_pc_atomic_memory_current_fts"
_VECTOR_INDEX_NAME = "ix_pc_atomic_memory_current_embedding"
_INDEX_EXISTS = text(
    "SELECT COUNT(*) FROM information_schema.statistics "
    "WHERE table_schema = DATABASE() AND table_name = 'pc_atomic_memory_current' AND index_name = :index_name"
)
_VECTOR_TYPE = text(
    "SELECT data_type FROM information_schema.columns "
    "WHERE table_schema = DATABASE() AND table_name = 'pc_atomic_memory_current' AND column_name = 'embedding'"
)
_HIT_COLUMNS = "artifact_id, revision, state_version, kind, text"


def oceanbase_atomic_memory_vector_sql(eligibility: str, /) -> str:
    """Bounded ANN over current usable vectors with same-row qualification."""
    return (
        f"WITH candidates AS (SELECT {_HIT_COLUMNS}, l2_distance(embedding, :query_vector) AS distance "  # noqa: S608
        "FROM pc_atomic_memory_current "
        f"WHERE scope_id = :scope_id AND ({eligibility}) "
        "AND embedding IS NOT NULL AND embedding_input_hash IS NOT NULL "
        "AND profile_fingerprint = :profile AND projection_format = :projection_format "
        "ORDER BY l2_distance(embedding, :query_vector) APPROXIMATE LIMIT :result_limit "
        "PARAMETERS(ef_search=:ef_search)) "
        f"SELECT {_HIT_COLUMNS}, distance, 0 AS invalid_vectors FROM candidates "
        "WHERE greatest(-1.0, least(1.0, 1.0 - distance * distance / 2.0)) >= :min_semantic_similarity "
        "ORDER BY distance, artifact_id"
    )


class OceanBaseAtomicMemoryIndex(RelationalAtomicMemoryIndex):
    """Native same-row retrieval; complete enumeration always uses exact L2.

    Ordinary vector search uses native ANN with eligibility inside the candidate
    query. Complete related enumeration retains the separate exact-distance SQL.
    Search breadth is statement-local and conservative: aggregate neighbor recall
    alone can hide missed multilingual outliers. Calibrate it for the deployment.
    """

    def __init__(self, profile: EmbeddingProfile | None = None, *, ef_search: int = 1000) -> None:
        if isinstance(ef_search, bool) or not 1 <= ef_search <= 1000:
            raise AtomicMemoryIndexError("vector-search", "Vector search breadth must be between 1 and 1000")
        super().__init__(atomic_memory_current_table(None if profile is None else VECTOR(profile.dimension)), profile)
        self._ef_search = ef_search
        self._vector_index = (
            None
            if profile is None
            else VectorIndex(_VECTOR_INDEX_NAME, self.table.c.embedding, params="distance=l2,type=hnsw")
        )

    async def initialize(self, connection: AsyncConnection, /) -> None:
        if connection.dialect.name != "mysql":
            raise AtomicMemoryIndexError(
                "oceanbase", "OceanBase Atomic Memory requires a MySQL-compatible OceanBase tenant"
            )
        await create_tables(connection, self.tables)
        await self.require_current_schema(connection)
        try:
            if not await connection.scalar(_INDEX_EXISTS, {"index_name": _FTS_INDEX_NAME}):
                await connection.exec_driver_sql(
                    f"CREATE FULLTEXT INDEX {_FTS_INDEX_NAME} ON pc_atomic_memory_current (searchable_text) WITH PARSER SPACE"
                )
            await connection.exec_driver_sql(
                "SELECT artifact_id FROM pc_atomic_memory_current "
                "WHERE MATCH(searchable_text) AGAINST ('powercontext') > 0 LIMIT 1"
            )
            await connection.exec_driver_sql("SELECT JSON_CONTAINS('[]', JSON_QUOTE('powercontext'))")
        except SQLAlchemyError as error:
            raise AtomicMemoryIndexError(
                "oceanbase-fts",
                "OceanBase Atomic Memory requires native FULLTEXT SPACE parser and inline JSON predicates",
            ) from error
        await self._initialize_vectors(connection)

    async def _initialize_vectors(self, connection: AsyncConnection) -> None:
        if self.profile is None:
            return
        column_type = await connection.scalar(_VECTOR_TYPE)
        expected = f"VECTOR({self.profile.dimension})"
        if str(column_type).upper() != expected:
            raise AtomicMemoryIndexError(
                "vector-schema",
                f"Atomic Memory uses {column_type!r}; expected {expected}. "
                "Reconfigure the current vector column offline and rebuild vectors before vector search.",
            )
        if self._vector_index is not None and not await connection.scalar(
            _INDEX_EXISTS, {"index_name": _VECTOR_INDEX_NAME}
        ):
            vector_index = self._vector_index
            try:
                await connection.run_sync(lambda sync: vector_index.create(sync, checkfirst=False))
            except SQLAlchemyError as error:
                raise AtomicMemoryIndexError(
                    "oceanbase-vector", "OceanBase native Atomic Memory vector index is unavailable"
                ) from error

    async def reconfigure_vector_column(self, connection: AsyncConnection, /) -> None:
        """Offline derived-schema operation, followed by authoritative vector rebuild.

        Caller must stop normal writes before DDL, which OceanBase may commit
        independently. Current bodies, identities and permissions are retained;
        invalidated vectors are never retained as a historical cache.
        """

        if connection.dialect.name != "mysql" or self.profile is None:
            raise AtomicMemoryIndexError("vector-schema", "Vector reconfiguration requires an OceanBase vector profile")
        if await connection.scalar(_INDEX_EXISTS, {"index_name": _VECTOR_INDEX_NAME}):
            await connection.exec_driver_sql(f"DROP INDEX {_VECTOR_INDEX_NAME} ON pc_atomic_memory_current")
        await connection.exec_driver_sql(
            "UPDATE pc_atomic_memory_current SET embedding = NULL, profile_fingerprint = NULL, embedding_input_hash = NULL"
        )
        try:
            await connection.exec_driver_sql(
                f"ALTER TABLE pc_atomic_memory_current MODIFY COLUMN embedding VECTOR({self.profile.dimension}) NULL"
            )
        except SQLAlchemyError as error:
            raise AtomicMemoryIndexError(
                "vector-schema",
                "OceanBase could not change the current vector type; rebuild the derived current table offline",
            ) from error
        await self.initialize(connection)

    async def search(
        self, connection: AsyncConnection, scope_id: str, request: AtomicMemorySearchRequest, /
    ) -> AtomicMemorySearchChannels:
        self.validate_request(request)
        return await self._channels(connection, scope_id, request, limit=request.limit)

    async def enumerate_related_with_heads(
        self, connection: AsyncConnection, scope_id: str, request: AtomicMemoryRelatedRequest, /
    ) -> tuple[tuple[AtomicMemoryIndexHit, ...], dict[str, dict[str, object]]]:
        self.validate_request(request)
        eligibility, parameters = atomic_memory_filter_sql(request.filters, "mysql")
        parameters["scope_id"] = scope_id
        lexical = request.mode in {"fts", "hybrid"}
        vector = request.mode in {"vector", "hybrid"}
        max_distance = request.max_distance
        terms, required = fts_query_requirements(request.query, floor=request.admission) if lexical else ((), 0)
        if not terms and not vector:
            return (), {}
        if terms:
            coverage, coverage_parameters = oceanbase_fts_coverage_sql(terms)
            parameters.update(coverage_parameters)
            parameters["fts_query"] = " ".join(terms)
            score = "MATCH(searchable_text) AGAINST (:fts_query)"
        else:
            coverage = "0"
            score = "0.0"
        profile = self.profile
        if vector:
            query_vector = self._require_vectors(request)
            if profile is None:
                raise AtomicMemoryIndexError("embedding-profile", "Vector profile is unavailable")
            if max_distance is None:
                raise AtomicMemoryIndexError("threshold", "Complete vector enumeration requires an L2 threshold")
            parameters.update(
                query_vector=query_vector,
                profile=atomic_memory_profile_fingerprint(profile),
                projection_format=ATOMIC_MEMORY_PROJECTION_FORMAT,
                max_distance=max_distance,
            )
            invalid = (
                "embedding IS NULL OR profile_fingerprint IS NULL OR profile_fingerprint <> :profile "
                "OR embedding_input_hash IS NULL OR projection_format <> :projection_format"
            )
            distance = f"CASE WHEN {invalid} THEN NULL ELSE l2_distance(embedding, :query_vector) END"
            readiness = f"SUM(CASE WHEN {invalid} THEN 1 ELSE 0 END) OVER ()"
        else:
            distance = "NULL"
            readiness = "0"
        query = (
            f"SELECT {_HIT_COLUMNS}, {score} AS score, {score} AS raw_score, "  # noqa: S608
            "'oceanbase_match' AS score_metric, "
            f"{coverage} AS coverage, {distance} AS distance, {readiness} AS invalid_vectors "
            "FROM pc_atomic_memory_current WHERE scope_id = :scope_id "
            f"AND ({eligibility})"
        )
        admission = []
        if terms:
            parameters["fts_required"] = required
            admission.append("(scored.score > 0 AND scored.coverage >= :fts_required)")
        if vector:
            admission.append("scored.distance <= :max_distance")
            admission.append("scored.invalid_vectors > 0")
        query = f"SELECT * FROM ({query}) AS scored WHERE {' OR '.join(admission)}"  # noqa: S608
        columns = (
            "scope_id",
            "family",
            "artifact_id",
            "revision",
            "lifecycle_state",
            "replacement_artifact_id",
            "merged_into_id",
            "governance_generation",
        )
        authority = ", ".join(f"head.{name} AS authority_{name}" for name in columns)
        join_guard = "AND hits.invalid_vectors = 0 " if vector else ""
        query = (
            f"SELECT hits.*, {authority}, body.content AS authority_content FROM ({query}) AS hits "  # noqa: S608
            "LEFT JOIN pc_artifact_heads AS head ON head.scope_id = :scope_id "
            f"AND head.family = 'atomic-memory' AND head.artifact_id = hits.artifact_id {join_guard}"
            "LEFT JOIN pc_artifacts AS body ON body.scope_id = head.scope_id "
            "AND body.family = head.family AND body.artifact_id = head.artifact_id "
            "AND body.revision = head.revision"
        )
        statement = text(query)
        if vector and profile is not None:
            statement = statement.bindparams(bindparam("query_vector", type_=VECTOR(profile.dimension)))
        rows = tuple((await connection.execute(statement, parameters)).mappings())
        if vector and any(int(row["invalid_vectors"]) for row in rows):
            raise AtomicMemoryIndexError(
                "incomplete-vector", "Eligible Atomic Memory vectors are incomplete or use another profile"
            )
        fts_rows = sorted(
            (row for row in rows if terms and float(row["score"] or 0) > 0 and int(row["coverage"]) >= required),
            key=lambda row: (-float(row["score"]), str(row["artifact_id"])),
        )
        vector_rows = sorted(
            (
                row
                for row in rows
                if vector
                and max_distance is not None
                and row["distance"] is not None
                and float(row["distance"]) <= max_distance
            ),
            key=lambda row: (float(row["distance"]), str(row["artifact_id"])),
        )
        channels = AtomicMemorySearchChannels(
            fts=atomic_memory_channel_hits(fts_rows),
            vector=atomic_memory_channel_hits(vector_rows, vector=True),
        )
        heads = {
            str(row["artifact_id"]): {name: row[f"authority_{name}"] for name in (*columns, "content")}
            for row in rows
            if row["authority_artifact_id"] is not None
        }
        return combine_atomic_memory_channels(channels), heads

    async def _channels(
        self,
        connection: AsyncConnection,
        scope_id: str,
        request: AtomicMemorySearchRequest,
        *,
        limit: int,
    ) -> AtomicMemorySearchChannels:
        if connection.dialect.name != "mysql":
            raise AtomicMemoryIndexError("oceanbase", "OceanBase Atomic Memory requires an OceanBase tenant")
        eligibility, parameters = atomic_memory_filter_sql(request.filters, "mysql")
        parameters["scope_id"] = scope_id
        limit_sql = " LIMIT :result_limit"
        parameters["result_limit"] = limit
        fts: tuple[AtomicMemoryIndexHit, ...] = ()
        vector: tuple[AtomicMemoryIndexHit, ...] = ()
        if request.mode in {"fts", "hybrid"}:
            terms, required = fts_query_requirements(request.query, floor=request.admission)
            if terms:
                coverage, coverage_parameters = oceanbase_fts_coverage_sql(terms)
                parameters.update(coverage_parameters)
                parameters.update(fts_query=" ".join(terms), fts_required=required)
                rows = (
                    await connection.execute(
                        text(
                            f"SELECT {_HIT_COLUMNS}, MATCH(searchable_text) AGAINST (:fts_query) AS score, "  # noqa: S608
                            "MATCH(searchable_text) AGAINST (:fts_query) AS raw_score, 'oceanbase_match' AS score_metric "
                            "FROM pc_atomic_memory_current WHERE scope_id = :scope_id "
                            f"AND ({eligibility}) AND MATCH(searchable_text) AGAINST (:fts_query) > 0 "
                            f"AND ({coverage}) >= :fts_required "
                            f"ORDER BY score DESC, artifact_id{limit_sql}"
                        ),
                        parameters,
                    )
                ).mappings()
                fts = atomic_memory_channel_hits(rows)
        if request.mode in {"vector", "hybrid"}:
            query_vector = self._require_vectors(request)
            profile = self.profile
            if profile is None:
                raise AtomicMemoryIndexError("embedding-profile", "Vector profile is unavailable")
            parameters.update(
                query_vector=query_vector,
                profile=atomic_memory_profile_fingerprint(profile),
                projection_format=ATOMIC_MEMORY_PROJECTION_FORMAT,
                ef_search=self._ef_search,
                max_distance=None,
                min_semantic_similarity=(
                    DEFAULT_ADMISSION_FLOOR if request.admission is None else request.admission
                ).min_semantic_similarity,
            )
            statement = text(
                oceanbase_atomic_memory_vector_sql(eligibility)
                if limit <= 16384
                else atomic_memory_vector_sql(eligibility, "mysql", bounded=True, threshold=False)
            ).bindparams(bindparam("query_vector", type_=VECTOR(profile.dimension)))
            rows = (await connection.execute(statement, parameters)).mappings()
            vector = atomic_memory_channel_hits(rows, vector=True)
        return AtomicMemorySearchChannels(fts=fts, vector=vector)


__all__ = ["OceanBaseAtomicMemoryIndex"]
