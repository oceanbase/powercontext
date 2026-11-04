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

import asyncio
import re
from weakref import WeakKeyDictionary

from pyobvector import VECTOR, VectorIndex
from sqlalchemy import bindparam, text
from sqlalchemy.exc import DBAPIError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection
from sqlalchemy.pool import Pool
from typing_extensions import override

from powercontext.builtin.artifacts.memory import EmbeddingProfile
from powercontext.builtin.artifacts.search import DEFAULT_ADMISSION_FLOOR, AdmissionFloor, fts_query_requirements
from powercontext.builtin.persistence.atomic_memory_index import (
    AtomicMemoryIndexError,
    AtomicMemoryIndexHit,
    AtomicMemoryRelatedCursor,
    AtomicMemoryRelatedPage,
    AtomicMemoryRelatedRank,
    AtomicMemoryRelatedRequest,
    AtomicMemorySearchChannels,
    AtomicMemorySearchRequest,
    RelationalAtomicMemoryIndex,
    atomic_memory_bodies_from_rows,
    atomic_memory_body_select,
    atomic_memory_channel_hits,
    atomic_memory_channel_ranks,
    atomic_memory_filter_sql,
    atomic_memory_keyset_sql,
    atomic_memory_next_cursor,
    atomic_memory_page_from_ranks,
    atomic_memory_profile_fingerprint,
    atomic_memory_vector_sql,
    combine_atomic_memory_related_ranks,
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
_NATIVE_FTS_PROBE = (
    "SELECT SCORE() FROM pc_atomic_memory_current "
    "WHERE scope_id = '__powercontext_fts_capability__' "
    "AND MATCH(searchable_text^1, 'powercontext', 'operator=or;minimum_should_match=1') LIMIT 1"
)
_NATIVE_FTS_MATCH = "MATCH(searchable_text^1, :fts_query, :fts_options)"
# The registered SPACE parser uses its built-in stoplist. Analyzer terms are
# encoded to fit its token-size bounds, but retain these words (including quoted
# identifiers). Native term counts cannot stand in for Analyzer coverage then.
_SPACE_STOPWORDS = frozenset([
    "a",
    "about",
    "an",
    "are",
    "as",
    "at",
    "be",
    "by",
    "com",
    "de",
    "en",
    "for",
    "from",
    "how",
    "i",
    "in",
    "is",
    "it",
    "la",
    "of",
    "on",
    "or",
    "that",
    "the",
    "this",
    "to",
    "was",
    "what",
    "when",
    "where",
    "who",
    "will",
    "with",
    "und",
    "www",
])


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
        self._native_fts_support: WeakKeyDictionary[Pool, bool] = WeakKeyDictionary()
        self._native_long_fts_support: WeakKeyDictionary[Pool, bool] = WeakKeyDictionary()
        self._native_fts_locks: WeakKeyDictionary[Pool, asyncio.Lock] = WeakKeyDictionary()
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
            await self._supports_native_fts(connection)
        except SQLAlchemyError as error:
            raise AtomicMemoryIndexError(
                "oceanbase-fts",
                "OceanBase Atomic Memory requires native FULLTEXT SPACE parser and inline JSON predicates",
            ) from error
        await self._initialize_vectors(connection)

    async def _supports_native_fts(self, connection: AsyncConnection, *, terms: tuple[str, ...] = ()) -> bool:
        # Managed-schema startup can skip initialize(). Cache by pool so reusing
        # this index with another backend, or disposing its engine, cannot carry
        # a capability decision into a different connection lifecycle.
        if _SPACE_STOPWORDS.intersection(terms):
            return False
        # run_sync is also implemented by migration's pinned connection; it keeps
        # the capability probe on the executor's existing connection and lock.
        pool = await connection.run_sync(lambda sync: sync.engine.pool)
        term_count = len(terms)
        native = self._native_fts_support.get(pool)
        if native is False or (native is True and term_count <= 256):
            return native
        if native and pool in self._native_long_fts_support:
            return self._native_long_fts_support[pool]
        lock = self._native_fts_locks.setdefault(pool, asyncio.Lock())
        async with lock:
            if pool not in self._native_fts_support:
                try:
                    result = await connection.exec_driver_sql(_NATIVE_FTS_PROBE)
                    result.close()
                except DBAPIError as error:
                    arguments = getattr(error.orig, "args", ())
                    if not arguments or arguments[0] not in {1064, 1235, 1305}:
                        raise
                    self._native_fts_support[pool] = False
                else:
                    self._native_fts_support[pool] = True
            if not self._native_fts_support[pool] or term_count <= 256:
                return self._native_fts_support[pool]
            if pool not in self._native_long_fts_support:
                # CE 4.4.2.1 accepts MATCH but ignores minimum_should_match
                # above 256 query terms. Keep complete-word coverage until a
                # qualified OceanBase version supports the long-query path.
                # SeekDB's MySQL/OceanBase compatibility version is not proof.
                version = str((await connection.exec_driver_sql("SELECT VERSION()")).scalar_one())
                match = re.search(r"OceanBase(?:_CE|_EE|\s+AI)?[\s_-]*v?(\d+)\.(\d+)\.", version, re.IGNORECASE)
                self._native_long_fts_support[pool] = bool(
                    "seekdb" not in version.lower() and match is not None and (int(match[1]), int(match[2])) >= (4, 6)
                )
            return self._native_long_fts_support[pool]

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

    @override
    async def probe_recoverable(
        self,
        connection: AsyncConnection,
        scope_id: str,
        request: AtomicMemorySearchRequest,
        floor: AdmissionFloor,
        /,
    ) -> bool:
        terms, required = (
            fts_query_requirements(request.query, floor=request.admission)
            if request.mode in {"fts", "hybrid"}
            else ((), 0)
        )
        if not terms or not await self._supports_native_fts(connection, terms=terms):
            return await super().probe_recoverable(connection, scope_id, request, floor)
        _, lower_required = fts_query_requirements(request.query, floor=floor)
        eligibility, parameters = atomic_memory_filter_sql(request.filters, "mysql")
        parameters["scope_id"] = scope_id
        eligible = f"scope_id = :scope_id AND ({eligibility})"
        common_tables = ""
        recoverable = []
        if lower_required < required and lower_required <= len(terms):
            parameters.update(
                fts_query=" ".join(terms),
                fts_current_options=f"operator=or;minimum_should_match={required}",
                fts_lower_options=f"operator=or;minimum_should_match={lower_required}",
            )
            # Each MATCH stays in a strict WHERE. A difference of admitted IDs
            # detects an additional lexical channel even for existing vector hits.
            current_match = (
                "MATCH(searchable_text^1, :fts_query, :fts_current_options)" if required <= len(terms) else "0 = 1"
            )
            current_score = "SCORE()" if required <= len(terms) else "0.0"
            # OceanBase cannot push SCORE predicates into a native MATCH block.
            # MySQL's unbounded LIMIT keeps these blocks from being merged while
            # retaining every admitted row for the difference below.
            common_tables = (
                f"WITH fts_current AS (SELECT artifact_id, {current_score} AS score "  # noqa: S608
                "FROM pc_atomic_memory_current "
                f"WHERE {eligible} AND {current_match} LIMIT 18446744073709551615), "
                "fts_lower AS (SELECT artifact_id, SCORE() AS score FROM pc_atomic_memory_current "
                f"WHERE {eligible} AND MATCH(searchable_text^1, :fts_query, :fts_lower_options) "
                "LIMIT 18446744073709551615) "
            )
            recoverable.append(
                "EXISTS (SELECT 1 FROM fts_lower AS lower_hit WHERE lower_hit.score > 0 AND NOT EXISTS "
                "(SELECT 1 FROM fts_current AS current_hit WHERE current_hit.artifact_id = lower_hit.artifact_id "
                "AND current_hit.score > 0) LIMIT 1)"
            )
        invalid = "0 = 1"
        profile = self.profile
        vector = request.mode == "hybrid"
        if vector:
            query_vector = self._require_vectors(request)
            if profile is None:
                raise AtomicMemoryIndexError("embedding-profile", "Vector profile is unavailable")
            parameters.update(
                probe_vector=query_vector,
                probe_similarity=(
                    DEFAULT_ADMISSION_FLOOR if request.admission is None else request.admission
                ).min_semantic_similarity,
                probe_lower_similarity=floor.min_semantic_similarity,
                profile=atomic_memory_profile_fingerprint(profile),
                projection_format=ATOMIC_MEMORY_PROJECTION_FORMAT,
            )
            similarity = "(1.0 - POWER(l2_distance(embedding, :probe_vector), 2) / 2.0)"
            recoverable.append(
                f"EXISTS (SELECT 1 FROM pc_atomic_memory_current WHERE {eligible} "  # noqa: S608
                f"AND {similarity} >= :probe_lower_similarity "
                f"AND NOT ({similarity} >= :probe_similarity) LIMIT 1)"
            )
            invalid = (
                "EXISTS (SELECT 1 FROM pc_atomic_memory_current "  # noqa: S608
                f"WHERE {eligible} AND (embedding IS NULL OR embedding_input_hash IS NULL "
                "OR profile_fingerprint IS NULL OR profile_fingerprint <> :profile "
                "OR projection_format <> :projection_format) LIMIT 1)"
            )
        recovered = " OR ".join(recoverable) if recoverable else "0 = 1"
        statement = text(f"{common_tables}SELECT CASE WHEN {invalid} THEN -1 ELSE ({recovered}) END")
        if vector and profile is not None:
            statement = statement.bindparams(bindparam("probe_vector", type_=VECTOR(profile.dimension)))
        result = await connection.scalar(statement, parameters)
        if result == -1:
            raise AtomicMemoryIndexError(
                "incomplete-vector", "Eligible Atomic Memory vectors are incomplete or use another profile"
            )
        return bool(result)

    async def enumerate_related_with_heads(
        self, connection: AsyncConnection, scope_id: str, request: AtomicMemoryRelatedRequest, /
    ) -> tuple[tuple[AtomicMemoryIndexHit, ...], dict[str, dict[str, object]]]:
        """Drain complete keyset pages for callers that need the whole related set."""
        self.validate_request(request)
        hits: list[AtomicMemoryIndexHit] = []
        heads: dict[str, dict[str, object]] = {}
        cursor: AtomicMemoryRelatedCursor | None = None
        while True:
            page = await self.related_page(connection, scope_id, request, cursor)
            if page is None:
                break
            hits.extend(page.hits)
            heads.update(page.heads)
            cursor = page.cursor
            if cursor is None:
                break
        return tuple(hits), heads

    async def related_page(
        self,
        connection: AsyncConnection,
        scope_id: str,
        request: AtomicMemoryRelatedRequest,
        cursor: AtomicMemoryRelatedCursor | None = None,
        /,
    ) -> AtomicMemoryRelatedPage | None:
        self.validate_request(request)
        eligibility, parameters = atomic_memory_filter_sql(request.filters, "mysql")
        parameters["scope_id"] = scope_id
        parameters["page_size"] = request.page_size
        if request.mode == "fts":
            return await self._related_fts_page(connection, scope_id, request, eligibility, parameters, cursor)
        if request.mode == "vector":
            return await self._related_vector_page(connection, request, eligibility, parameters, cursor)
        return await self._related_hybrid_page(connection, scope_id, request, eligibility, parameters, cursor)

    async def _related_fts_page(
        self,
        connection: AsyncConnection,
        scope_id: str,
        request: AtomicMemoryRelatedRequest,
        eligibility: str,
        parameters: dict[str, object],
        cursor: AtomicMemoryRelatedCursor | None,
    ) -> AtomicMemoryRelatedPage | None:
        rank_sql = self._related_fts_rank_sql(eligibility, request, parameters)
        if rank_sql is None:
            return None
        keyset = atomic_memory_keyset_sql(parameters, cursor, vector=False)
        sql = (
            f"WITH ranked AS ({rank_sql}) SELECT * FROM ranked{keyset} "  # noqa: S608
            "ORDER BY score DESC, artifact_id LIMIT :page_size"
        )
        rows = tuple((await connection.execute(text(sql), parameters)).mappings())
        if not rows:
            return None
        return await self._related_page_from_ranks(
            connection,
            scope_id,
            atomic_memory_channel_ranks(rows),
            atomic_memory_next_cursor(rows, request.page_size, vector=False),
        )

    async def _related_vector_page(
        self,
        connection: AsyncConnection,
        request: AtomicMemoryRelatedRequest,
        eligibility: str,
        parameters: dict[str, object],
        cursor: AtomicMemoryRelatedCursor | None,
    ) -> AtomicMemoryRelatedPage | None:
        await self._require_related_vectors(connection, request, eligibility, parameters)
        keyset = atomic_memory_keyset_sql(parameters, cursor, vector=True)
        sql = (
            "WITH ranked AS (SELECT artifact_id, revision, state_version, kind, "  # noqa: S608
            "l2_distance(embedding, :query_vector) AS distance "
            "FROM pc_atomic_memory_current WHERE scope_id = :scope_id "
            f"AND ({eligibility}) AND embedding IS NOT NULL AND profile_fingerprint = :profile "
            "AND embedding_input_hash IS NOT NULL AND projection_format = :projection_format) "
            f"SELECT * FROM ranked WHERE distance <= :max_distance{keyset} "
            "ORDER BY distance, artifact_id LIMIT :page_size"
        )
        statement = text(sql)
        profile = self.profile
        if profile is not None:
            statement = statement.bindparams(bindparam("query_vector", type_=VECTOR(profile.dimension)))
        rows = tuple((await connection.execute(statement, parameters)).mappings())
        if not rows:
            return None
        return await self._related_page_from_ranks(
            connection,
            str(parameters["scope_id"]),
            atomic_memory_channel_ranks(rows, vector=True),
            atomic_memory_next_cursor(rows, request.page_size, vector=True),
        )

    async def _related_hybrid_page(
        self,
        connection: AsyncConnection,
        scope_id: str,
        request: AtomicMemoryRelatedRequest,
        eligibility: str,
        parameters: dict[str, object],
        cursor: AtomicMemoryRelatedCursor | None,
    ) -> AtomicMemoryRelatedPage | None:
        if cursor is not None and cursor.fused_ranks is not None:
            ranks = cursor.fused_ranks
            offset = cursor.offset
        else:
            await self._require_related_vectors(connection, request, eligibility, parameters)
            fts_ranks: tuple[AtomicMemoryRelatedRank, ...] = ()
            rank_sql = self._related_fts_rank_sql(eligibility, request, parameters)
            if rank_sql is not None:
                fts_sql = (
                    f"WITH ranked AS ({rank_sql}) SELECT * FROM ranked "  # noqa: S608
                    "ORDER BY score DESC, artifact_id"
                )
                fts_rows = tuple((await connection.execute(text(fts_sql), parameters)).mappings())
                fts_ranks = atomic_memory_channel_ranks(fts_rows)
            vector_sql = (
                "WITH ranked AS (SELECT artifact_id, revision, state_version, kind, "  # noqa: S608
                "l2_distance(embedding, :query_vector) AS distance "
                "FROM pc_atomic_memory_current WHERE scope_id = :scope_id "
                f"AND ({eligibility}) AND embedding IS NOT NULL AND profile_fingerprint = :profile "
                "AND embedding_input_hash IS NOT NULL AND projection_format = :projection_format) "
                "SELECT * FROM ranked WHERE distance <= :max_distance ORDER BY distance, artifact_id"
            )
            statement = text(vector_sql)
            profile = self.profile
            if profile is not None:
                statement = statement.bindparams(bindparam("query_vector", type_=VECTOR(profile.dimension)))
            vector_rows = tuple((await connection.execute(statement, parameters)).mappings())
            ranks = combine_atomic_memory_related_ranks(
                fts_ranks, atomic_memory_channel_ranks(vector_rows, vector=True)
            )
            offset = 0
        page_ranks = ranks[offset : offset + request.page_size]
        if not page_ranks:
            return None
        next_offset = offset + len(page_ranks)
        next_cursor = (
            AtomicMemoryRelatedCursor(fused_ranks=ranks, offset=next_offset) if next_offset < len(ranks) else None
        )
        return await self._related_page_from_ranks(connection, scope_id, page_ranks, next_cursor)

    def _related_fts_rank_sql(
        self, eligibility: str, request: AtomicMemoryRelatedRequest, parameters: dict[str, object]
    ) -> str | None:
        terms, required = fts_query_requirements(request.query, floor=request.admission)
        if not terms:
            return None
        coverage, coverage_parameters = oceanbase_fts_coverage_sql(terms)
        parameters.update(coverage_parameters)
        parameters.update(fts_query=" ".join(terms), fts_required=required)
        score = "MATCH(searchable_text) AGAINST (:fts_query)"
        return (
            "SELECT artifact_id, revision, state_version, kind, "  # noqa: S608
            f"{score} AS score, {score} AS raw_score, 'oceanbase_match' AS score_metric, "
            f"{coverage} AS coverage FROM pc_atomic_memory_current "
            "WHERE scope_id = :scope_id "
            f"AND ({eligibility}) AND {score} > 0 AND ({coverage}) >= :fts_required"
        )

    async def _require_related_vectors(
        self,
        connection: AsyncConnection,
        request: AtomicMemoryRelatedRequest,
        eligibility: str,
        parameters: dict[str, object],
    ) -> None:
        profile = self.profile
        if profile is None:
            raise AtomicMemoryIndexError("embedding-profile", "Vector profile is unavailable")
        max_distance = request.max_distance
        if max_distance is None:
            raise AtomicMemoryIndexError("threshold", "Related vector recall requires an L2 threshold")
        parameters.update(
            query_vector=self._require_vectors(request),
            profile=atomic_memory_profile_fingerprint(profile),
            projection_format=ATOMIC_MEMORY_PROJECTION_FORMAT,
            max_distance=max_distance,
        )
        invalid = (
            "embedding IS NULL OR profile_fingerprint IS NULL OR profile_fingerprint <> :profile "
            "OR embedding_input_hash IS NULL OR projection_format <> :projection_format"
        )
        count = await connection.scalar(
            text(
                "SELECT COUNT(*) FROM pc_atomic_memory_current WHERE scope_id = :scope_id "  # noqa: S608
                f"AND ({eligibility}) AND ({invalid})"
            ),
            parameters,
        )
        if count:
            raise AtomicMemoryIndexError(
                "incomplete-vector", "Eligible Atomic Memory vectors are incomplete or use another profile"
            )

    async def _related_page_from_ranks(
        self,
        connection: AsyncConnection,
        scope_id: str,
        ranks: tuple[AtomicMemoryRelatedRank, ...],
        cursor: AtomicMemoryRelatedCursor | None,
    ) -> AtomicMemoryRelatedPage | None:
        if not ranks:
            return None
        parameters = {
            "scope_id": scope_id,
            "body_ids": [rank.artifact_ref.artifact_id for rank in ranks],
        }
        rows = tuple(
            (
                await connection.execute(
                    text(atomic_memory_body_select()).bindparams(bindparam("body_ids", expanding=True)),
                    parameters,
                )
            ).mappings()
        )
        texts, heads = atomic_memory_bodies_from_rows(rows)
        hits, page_heads = atomic_memory_page_from_ranks(ranks, texts, heads)
        return AtomicMemoryRelatedPage(hits=hits, heads=page_heads, cursor=cursor)

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
            if terms and required <= len(terms):
                parameters["fts_query"] = " ".join(terms)
                if await self._supports_native_fts(connection, terms=terms):
                    score = "SCORE()"
                    admission = _NATIVE_FTS_MATCH
                    parameters["fts_options"] = f"operator=or;minimum_should_match={required}"
                else:
                    coverage, coverage_parameters = oceanbase_fts_coverage_sql(terms)
                    parameters.update(coverage_parameters)
                    parameters["fts_required"] = required
                    score = "MATCH(searchable_text) AGAINST (:fts_query)"
                    admission = f"{score} > 0 AND ({coverage}) >= :fts_required"
                rows = (
                    await connection.execute(
                        text(
                            f"SELECT {_HIT_COLUMNS}, {score} AS score, "  # noqa: S608
                            f"{score} AS raw_score, 'oceanbase_match' AS score_metric "
                            "FROM pc_atomic_memory_current WHERE scope_id = :scope_id "
                            f"AND ({eligibility}) AND {admission} "
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
