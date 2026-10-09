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

"""SQLite Atomic Memory: one current row, an FTS5 helper and exact L2."""

from __future__ import annotations

import struct

from aiosqlite import Connection as SQLiteConnection
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection
from typing_extensions import override

from powercontext.builtin.artifacts.memory import EmbeddingProfile
from powercontext.builtin.artifacts.search import DEFAULT_ADMISSION_FLOOR, fts_match_query, fts_query_requirements
from powercontext.builtin.persistence.atomic_memory_index import (
    AtomicMemoryIndexError,
    AtomicMemoryIndexHit,
    AtomicMemoryRelatedRequest,
    AtomicMemorySearchChannels,
    AtomicMemorySearchRequest,
    RelationalAtomicMemoryIndex,
    atomic_memory_channel_hits,
    atomic_memory_coverage_sql,
    atomic_memory_filter_sql,
    atomic_memory_profile_fingerprint,
    atomic_memory_vector_sql,
    combine_atomic_memory_channels,
    freeze_atomic_memory_query_time,
)
from powercontext.builtin.persistence.atomic_memory_index_schema import (
    ATOMIC_MEMORY_PROJECTION_FORMAT,
    atomic_memory_current_table,
)
from powercontext.builtin.persistence.schema import create_tables

_FTS_TABLE = "pc_atomic_memory_current_fts"
_FTS_COLUMNS = (
    "scope_id",
    "artifact_id",
    "revision",
    "state_version",
    "content_hash",
    "projection_format",
    "kind",
    "text",
    "searchable_text",
    "tag_keys",
    "owner_type",
    "owner_id",
    "read_grants",
    "identity_token",
)
_HIT_COLUMNS = "artifact_id, revision, state_version, kind, text"
_FTS_IDENTITY_SQL = "'i' || lower(hex(scope_id)) || 'x' || lower(hex(artifact_id))"


class SQLiteAtomicMemoryIndex(RelationalAtomicMemoryIndex):
    """Filter in the retrieved row before lexical or exact vector limits.

    The FTS virtual table is an index helper. It mirrors all body and eligibility
    fields using triggers in the current-row transaction, so its queries need
    neither a business JOIN nor post-limit authorization. Its indexed identity
    token encodes the exact Scope/Artifact pair independently of SQLite rowids,
    which VACUUM may change on current's composite-primary-key table. Vectors
    stay in current.
    """

    def __init__(self, profile: EmbeddingProfile | None = None) -> None:
        super().__init__(atomic_memory_current_table(), profile)

    @override
    def _encode_embedding(self, vector: tuple[float, ...]) -> bytes:
        try:
            return struct.pack(f"<{len(vector)}f", *vector)
        except (OverflowError, struct.error) as error:
            raise AtomicMemoryIndexError("embedding-values", "Vector values exceed SQLite's float32 range") from error

    async def initialize(self, connection: AsyncConnection, /) -> None:
        if connection.dialect.name != "sqlite":
            raise AtomicMemoryIndexError("sqlite", "SQLite Atomic Memory index requires SQLite")
        driver = (await connection.get_raw_connection()).driver_connection
        if not isinstance(driver, SQLiteConnection):
            raise AtomicMemoryIndexError("sqlite", "SQLite Atomic Memory requires the aiosqlite driver")
        if not driver.in_transaction:
            # Legacy SQLite does not BEGIN for DDL/SELECT. Keep helper upgrades,
            # their trigger replacement and backfill in the caller's transaction.
            await connection.exec_driver_sql("BEGIN")
        await create_tables(connection, self.tables)
        if self.profile is not None:
            try:
                await connection.exec_driver_sql("SELECT vec_version()")
            except SQLAlchemyError as error:
                raise AtomicMemoryIndexError(
                    "sqlite-vector", "SQLite Atomic Memory vectors require loaded sqlite-vec"
                ) from error
        await self._initialize_fts(connection)

    async def _initialize_fts(self, connection: AsyncConnection) -> None:
        existing = await connection.scalar(
            text("SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name = :name"), {"name": _FTS_TABLE}
        )
        if existing:
            existing_columns = tuple(
                row["name"]
                for row in (
                    await connection.exec_driver_sql("PRAGMA table_info(pc_atomic_memory_current_fts)")
                ).mappings()
            )
            if existing_columns != _FTS_COLUMNS:
                # Only the rebuildable helper is replaced; current is preserved.
                for event in ("insert", "update", "delete"):
                    await connection.exec_driver_sql(f"DROP TRIGGER IF EXISTS pc_atomic_memory_fts_{event}")
                await connection.exec_driver_sql("DROP TABLE pc_atomic_memory_current_fts")
                existing = 0
        columns = ", ".join(
            name if name in {"searchable_text", "identity_token"} else f"{name} UNINDEXED" for name in _FTS_COLUMNS
        )
        await connection.exec_driver_sql(
            f"CREATE VIRTUAL TABLE IF NOT EXISTS {_FTS_TABLE} USING fts5({columns}, tokenize='unicode61')"
        )
        names = ", ".join(_FTS_COLUMNS)
        new_identity = _FTS_IDENTITY_SQL.replace("scope_id", "new.scope_id").replace("artifact_id", "new.artifact_id")
        old_identity = _FTS_IDENTITY_SQL.replace("scope_id", "old.scope_id").replace("artifact_id", "old.artifact_id")
        new_values = ", ".join(new_identity if name == "identity_token" else f"new.{name}" for name in _FTS_COLUMNS)
        for event in ("INSERT", "UPDATE", "DELETE"):
            statements = []
            if event in {"UPDATE", "DELETE"}:
                statements.append(
                    f"DELETE FROM {_FTS_TABLE} WHERE identity_token MATCH ({old_identity});"  # noqa: S608
                )
            if event in {"INSERT", "UPDATE"}:
                statements.append(f"INSERT INTO {_FTS_TABLE} ({names}) VALUES ({new_values});")  # noqa: S608
            await connection.exec_driver_sql(
                f"CREATE TRIGGER IF NOT EXISTS pc_atomic_memory_fts_{event.lower()} "
                f"AFTER {event} ON pc_atomic_memory_current BEGIN {' '.join(statements)} END"
            )
        if not existing:
            await self.rebuild_fts(connection)
        try:
            await connection.exec_driver_sql(
                f"SELECT artifact_id FROM {_FTS_TABLE} WHERE searchable_text MATCH 'powercontext' LIMIT 1"  # noqa: S608
            )
        except SQLAlchemyError as error:
            raise AtomicMemoryIndexError("sqlite-fts", "SQLite Atomic Memory requires a working FTS5 index") from error

    async def rebuild_fts(self, connection: AsyncConnection, /) -> None:
        """Rebuild only the helper from current; authoritative rebuilding is external."""

        names = ", ".join(_FTS_COLUMNS)
        values = ", ".join(_FTS_IDENTITY_SQL if name == "identity_token" else name for name in _FTS_COLUMNS)
        await connection.exec_driver_sql(f"DELETE FROM {_FTS_TABLE}")  # noqa: S608
        await connection.exec_driver_sql(
            f"INSERT INTO {_FTS_TABLE} ({names}) SELECT {values} FROM pc_atomic_memory_current"  # noqa: S608
        )

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
        if connection.dialect.name != "sqlite":
            raise AtomicMemoryIndexError("sqlite", "SQLite Atomic Memory index requires SQLite")
        request = freeze_atomic_memory_query_time(request)
        eligibility, parameters = atomic_memory_filter_sql(request.filters, "sqlite")
        parameters["scope_id"] = scope_id
        limit_sql = "" if limit is None else " LIMIT :result_limit"
        if limit is not None:
            parameters["result_limit"] = limit
        fts: tuple[AtomicMemoryIndexHit, ...] = ()
        vector: tuple[AtomicMemoryIndexHit, ...] = ()
        if request.mode in {"fts", "hybrid"}:
            match_query = fts_match_query(request.query)
            terms, required = fts_query_requirements(request.query, floor=request.admission)
            if match_query is not None:
                coverage = []
                for index, term in enumerate(terms):
                    key = f"fts_term_{index}"
                    parameters[key] = f" {term} "
                    coverage.append(f"CASE WHEN instr(' ' || searchable_text || ' ', :{key}) > 0 THEN 1 ELSE 0 END")
                parameters.update(fts_query=match_query, fts_required=required)
                rows = (
                    await connection.execute(
                        text(
                            f"SELECT {_HIT_COLUMNS}, -bm25({_FTS_TABLE}) AS score, "  # noqa: S608
                            f"bm25({_FTS_TABLE}) AS raw_score, 'sqlite_bm25' AS score_metric FROM {_FTS_TABLE} "
                            "WHERE searchable_text MATCH :fts_query AND scope_id = :scope_id "
                            f"AND ({eligibility}) AND ({atomic_memory_coverage_sql(coverage)}) >= :fts_required "
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
                query_vector=self._encode_embedding(query_vector),
                profile=atomic_memory_profile_fingerprint(profile),
                projection_format=ATOMIC_MEMORY_PROJECTION_FORMAT,
                embedding_bytes=profile.dimension * 4,
                max_distance=request.max_distance if isinstance(request, AtomicMemoryRelatedRequest) else None,
                min_semantic_similarity=(
                    DEFAULT_ADMISSION_FLOOR if request.admission is None else request.admission
                ).min_semantic_similarity,
            )
            rows = (
                await connection.execute(
                    text(
                        atomic_memory_vector_sql(
                            eligibility,
                            "sqlite",
                            bounded=limit is not None,
                            threshold=isinstance(request, AtomicMemoryRelatedRequest),
                        )
                    ),
                    parameters,
                )
            ).mappings()
            vector = atomic_memory_channel_hits(rows, vector=True)
        return AtomicMemorySearchChannels(fts=fts, vector=vector)


__all__ = ["SQLiteAtomicMemoryIndex"]
