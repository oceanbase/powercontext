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


class OceanBaseAtomicMemoryIndex(RelationalAtomicMemoryIndex):
    """Native same-row retrieval; complete enumeration always uses exact L2.

    Ordinary vector search also uses exact distance, preserving same-row kind and tag filters
    before LIMIT without relying on ANN filtering.
    The native index exists for backend-native support and future bounded modes.
    """

    def __init__(self, profile: EmbeddingProfile | None = None) -> None:
        super().__init__(atomic_memory_current_table(None if profile is None else VECTOR(profile.dimension)), profile)
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
        if self.profile is not None:
            column_type = await connection.scalar(_VECTOR_TYPE)
            expected = f"VECTOR({self.profile.dimension})"
            if str(column_type).upper() != expected:
                raise AtomicMemoryIndexError(
                    "vector-schema",
                    f"Atomic Memory uses {column_type!r}; expected {expected}. "
                    "Reconfigure the current vector column offline and rebuild vectors before vector search.",
                )
            if self._vector_index is not None:
                vector_index = self._vector_index
                try:
                    await connection.run_sync(lambda sync: vector_index.create(sync, checkfirst=True))
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

    async def enumerate_related(
        self, connection: AsyncConnection, scope_id: str, request: AtomicMemoryRelatedRequest, /
    ) -> tuple[AtomicMemoryIndexHit, ...]:
        self.validate_request(request)
        return combine_atomic_memory_channels(await self._channels(connection, scope_id, request, limit=None))

    async def _channels(
        self,
        connection: AsyncConnection,
        scope_id: str,
        request: AtomicMemorySearchRequest | AtomicMemoryRelatedRequest,
        *,
        limit: int | None,
    ) -> AtomicMemorySearchChannels:
        if connection.dialect.name != "mysql":
            raise AtomicMemoryIndexError("oceanbase", "OceanBase Atomic Memory requires an OceanBase tenant")
        eligibility, parameters = atomic_memory_filter_sql(request.filters, "mysql")
        parameters["scope_id"] = scope_id
        limit_sql = "" if limit is None else " LIMIT :result_limit"
        if limit is not None:
            parameters["result_limit"] = limit
        fts: tuple[AtomicMemoryIndexHit, ...] = ()
        vector: tuple[AtomicMemoryIndexHit, ...] = ()
        if request.mode in {"fts", "hybrid"}:
            terms, required = fts_query_requirements(request.query, floor=request.admission)
            if terms:
                coverage = []
                for index, term in enumerate(terms):
                    key = f"fts_term_{index}"
                    parameters[key] = f" {term} "
                    coverage.append(
                        f"CASE WHEN LOCATE(BINARY :{key}, BINARY CONCAT(' ', searchable_text, ' ')) > 0 THEN 1 ELSE 0 END"
                    )
                parameters.update(fts_query=" ".join(terms), fts_required=required)
                rows = (
                    await connection.execute(
                        text(
                            f"SELECT {_HIT_COLUMNS}, MATCH(searchable_text) AGAINST (:fts_query) AS score "  # noqa: S608
                            "FROM pc_atomic_memory_current WHERE scope_id = :scope_id "
                            f"AND ({eligibility}) AND MATCH(searchable_text) AGAINST (:fts_query) > 0 "
                            f"AND ({' + '.join(coverage)}) >= :fts_required "
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
                max_distance=request.max_distance if isinstance(request, AtomicMemoryRelatedRequest) else None,
                min_semantic_similarity=(
                    DEFAULT_ADMISSION_FLOOR if request.admission is None else request.admission
                ).min_semantic_similarity,
            )
            statement = text(
                atomic_memory_vector_sql(
                    eligibility,
                    "mysql",
                    bounded=limit is not None,
                    threshold=isinstance(request, AtomicMemoryRelatedRequest),
                )
            ).bindparams(bindparam("query_vector", type_=VECTOR(profile.dimension)))
            rows = (await connection.execute(statement, parameters)).mappings()
            vector = atomic_memory_channel_hits(rows, vector=True)
        return AtomicMemorySearchChannels(fts=fts, vector=vector)


__all__ = ["OceanBaseAtomicMemoryIndex"]
