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

"""SQLite Atomic Memory: transactional FTS5 and partitioned sqlite-vec KNN helpers."""

from __future__ import annotations

import re
import struct

import sqlite_vec
from aiosqlite import Connection as SQLiteConnection
from sqlalchemy import bindparam, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection
from typing_extensions import override

from powercontext.builtin.artifacts.memory import EmbeddingProfile
from powercontext.builtin.artifacts.search import DEFAULT_ADMISSION_FLOOR, fts_match_query, fts_query_requirements
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
    sqlite_fts_coverage_sql,
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
    "identity_token",
)
_HIT_COLUMNS = "artifact_id, revision, state_version, kind, text"
_FTS_IDENTITY_SQL = "'i' || lower(hex(scope_id)) || 'x' || lower(hex(artifact_id))"
_VECTOR_TABLE = "pc_atomic_memory_current_vec"
# Avoid reserving 1024 vector slots for every small Scope.
_VECTOR_CHUNK_SIZE = 128
_VECTOR_COLUMNS = "identity, scope_id, kind, embedding, artifact_id, revision, state_version, text"


class SQLiteAtomicMemoryIndex(RelationalAtomicMemoryIndex):
    """Filter in the retrieved row before lexical or exact vector limits.

    The FTS virtual table is an index helper. It mirrors all body and eligibility
    fields using triggers in the current-row transaction, so its queries need
    neither a business JOIN nor post-limit authorization. Its indexed identity
    token encodes the exact Scope/Artifact pair independently of SQLite rowids,
    which VACUUM may change on current's composite-primary-key table. Vectors
    use a Scope-partitioned vec0 helper with the same stable identity. Current
    remains the rebuild source; helper writes and rollback share its transaction.
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
        await self.require_current_schema(connection)
        if self.profile is not None:
            try:
                await connection.exec_driver_sql("SELECT vec_version()")
            except SQLAlchemyError as error:
                raise AtomicMemoryIndexError(
                    "sqlite-vector", "SQLite Atomic Memory vectors require loaded sqlite-vec"
                ) from error
        await self._initialize_fts(connection)
        await self._initialize_vectors(connection, driver)

    def _invalid_vectors_sql(self) -> str:
        profile = self.profile
        if profile is None:
            raise AtomicMemoryIndexError("embedding-profile", "Vector profile is unavailable")
        # Constants are owned schema/profile values, never request input.
        # Only usable current vectors are copied into the native helper.
        return (
            "(embedding IS NULL OR embedding_input_hash IS NULL OR profile_fingerprint IS NULL "
            f"OR profile_fingerprint <> '{atomic_memory_profile_fingerprint(profile)}' "
            f"OR projection_format <> '{ATOMIC_MEMORY_PROJECTION_FORMAT}' "
            f"OR LENGTH(embedding) <> {profile.dimension * 4})"
        )

    async def _initialize_vectors(self, connection: AsyncConnection, driver: SQLiteConnection) -> None:
        existing = await connection.scalar(
            text("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = :name"), {"name": _VECTOR_TABLE}
        )
        profile = self.profile
        definition = (
            None
            if profile is None
            else (
                f"CREATE VIRTUAL TABLE {_VECTOR_TABLE} USING vec0("
                "identity TEXT PRIMARY KEY, scope_id TEXT PARTITION KEY, kind TEXT, "
                f"embedding float[{profile.dimension}], +artifact_id TEXT, +revision INTEGER, "
                f"+state_version INTEGER, +text TEXT, chunk_size={_VECTOR_CHUNK_SIZE} "
                f"/* {atomic_memory_profile_fingerprint(profile)} */)"
            )
        )
        if existing and existing != definition:
            # Disabling embeddings must also remove persisted vector triggers.
            # Load only on this connection to drop the old virtual helper safely.
            if profile is None:
                await driver.enable_load_extension(True)
                try:
                    await driver.load_extension(sqlite_vec.loadable_path())
                finally:
                    await driver.enable_load_extension(False)
            for event in ("insert", "update", "delete"):
                await connection.exec_driver_sql(f"DROP TRIGGER IF EXISTS pc_atomic_memory_vec_{event}")
            await connection.exec_driver_sql(f"DROP TABLE {_VECTOR_TABLE}")
            existing = None
        if definition is None:
            return
        invalid = self._invalid_vectors_sql()
        if not existing:
            await connection.exec_driver_sql(definition)
            await connection.exec_driver_sql(
                f"INSERT INTO {_VECTOR_TABLE} ({_VECTOR_COLUMNS}) "  # noqa: S608
                f"SELECT {_FTS_IDENTITY_SQL}, scope_id, kind, embedding, artifact_id, revision, state_version, text "
                f"FROM pc_atomic_memory_current WHERE NOT {invalid}"
            )
        await self._initialize_vector_triggers(connection, invalid)

    async def _initialize_vector_triggers(self, connection: AsyncConnection, invalid: str) -> None:
        new_identity = _FTS_IDENTITY_SQL.replace("scope_id", "new.scope_id").replace("artifact_id", "new.artifact_id")
        old_identity = _FTS_IDENTITY_SQL.replace("scope_id", "old.scope_id").replace("artifact_id", "old.artifact_id")
        new_values = ", ".join(
            new_identity if name == "identity" else f"new.{name}" for name in _VECTOR_COLUMNS.split(", ")
        )
        for event in ("INSERT", "UPDATE", "DELETE"):
            statements = []
            if event in {"UPDATE", "DELETE"}:
                statements.append(f"DELETE FROM {_VECTOR_TABLE} WHERE identity = ({old_identity});")  # noqa: S608
            if event in {"INSERT", "UPDATE"}:
                valid_new = re.sub(
                    r"\b(embedding_input_hash|profile_fingerprint|projection_format|embedding)\b", r"new.\1", invalid
                )
                statements.append(
                    f"INSERT INTO {_VECTOR_TABLE} ({_VECTOR_COLUMNS}) SELECT {new_values} WHERE NOT {valid_new};"
                )
            name = f"pc_atomic_memory_vec_{event.lower()}"
            if event == "UPDATE":
                dependencies = (
                    "scope_id, artifact_id, kind, embedding, revision, state_version, text, "
                    "embedding_input_hash, profile_fingerprint, projection_format"
                )
                changed = " OR ".join(f"old.{column} IS NOT new.{column}" for column in dependencies.split(", "))
                definition = (
                    f"CREATE TRIGGER {name} AFTER UPDATE OF {dependencies} ON pc_atomic_memory_current "
                    f"WHEN {changed} BEGIN {' '.join(statements)} END"
                )
                existing_trigger = await connection.scalar(
                    text("SELECT sql FROM sqlite_master WHERE type = 'trigger' AND name = :name"), {"name": name}
                )
                if existing_trigger == definition:
                    continue
                await connection.exec_driver_sql(f"DROP TRIGGER IF EXISTS {name}")
                await connection.exec_driver_sql(definition)
            else:
                await connection.exec_driver_sql(
                    f"CREATE TRIGGER IF NOT EXISTS {name} "
                    f"AFTER {event} ON pc_atomic_memory_current BEGIN {' '.join(statements)} END"
                )

    def _knn_sql(self, request: AtomicMemorySearchRequest | AtomicMemoryRelatedRequest, eligibility: str) -> str:
        filters = " AND kind = :kind_filter" if request.filters.kind is not None else ""
        if request.filters.tag_filter is not None:
            filters += (
                f" AND identity IN (SELECT {_FTS_IDENTITY_SQL} FROM pc_atomic_memory_current "  # noqa: S608
                f"WHERE scope_id = :scope_id AND ({eligibility}))"
            )
        return (
            f"SELECT {_HIT_COLUMNS}, distance, 0 AS invalid_vectors FROM {_VECTOR_TABLE} "  # noqa: S608
            f"WHERE embedding MATCH :query_vector AND k = :neighbor_limit AND scope_id = :scope_id{filters} "
            "AND max(-1.0, min(1.0, 1.0 - distance * distance / 2.0)) >= :min_semantic_similarity "
            "ORDER BY distance"
        )

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

    async def fts_matches_current(self, connection: AsyncConnection, /) -> bool:
        """Compare all helper values and multiplicities, including stable identity tokens."""
        names = ", ".join(_FTS_COLUMNS)
        values = ", ".join(_FTS_IDENTITY_SQL if name == "identity_token" else name for name in _FTS_COLUMNS)
        expected = f"SELECT {values}, COUNT(*) FROM pc_atomic_memory_current GROUP BY {values}"  # noqa: S608
        actual = f"SELECT {names}, COUNT(*) FROM {_FTS_TABLE} GROUP BY {names}"  # noqa: S608
        for left, right in ((expected, actual), (actual, expected)):
            if (await connection.exec_driver_sql(f"SELECT 1 FROM ({left} EXCEPT {right}) LIMIT 1")).first():  # noqa: S608
                return False
        return True

    async def search(
        self, connection: AsyncConnection, scope_id: str, request: AtomicMemorySearchRequest, /
    ) -> AtomicMemorySearchChannels:
        self.validate_request(request)
        return await self._channels(connection, scope_id, request, limit=request.limit)

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
        eligibility, parameters = atomic_memory_filter_sql(request.filters, "sqlite")
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
        rank_sql = self._related_fts_rank_sql(scope_id, request, eligibility, parameters)
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
            "vec_distance_l2(embedding, :query_vector) AS distance "
            "FROM pc_atomic_memory_current WHERE scope_id = :scope_id "
            f"AND ({eligibility}) AND embedding IS NOT NULL AND embedding_input_hash IS NOT NULL "
            "AND profile_fingerprint = :profile AND projection_format = :projection_format "
            "AND LENGTH(embedding) = :embedding_bytes) "
            f"SELECT * FROM ranked WHERE distance <= :max_distance{keyset} "
            "ORDER BY distance, artifact_id LIMIT :page_size"
        )
        rows = tuple((await connection.execute(text(sql), parameters)).mappings())
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
            rank_sql = self._related_fts_rank_sql(scope_id, request, eligibility, parameters)
            if rank_sql is not None:
                fts_sql = (
                    f"WITH ranked AS ({rank_sql}) SELECT * FROM ranked "  # noqa: S608
                    "ORDER BY score DESC, artifact_id"
                )
                fts_rows = tuple((await connection.execute(text(fts_sql), parameters)).mappings())
                fts_ranks = atomic_memory_channel_ranks(fts_rows)
            vector_sql = (
                "WITH ranked AS (SELECT artifact_id, revision, state_version, kind, "  # noqa: S608
                "vec_distance_l2(embedding, :query_vector) AS distance "
                "FROM pc_atomic_memory_current WHERE scope_id = :scope_id "
                f"AND ({eligibility}) AND embedding IS NOT NULL AND embedding_input_hash IS NOT NULL "
                "AND profile_fingerprint = :profile AND projection_format = :projection_format "
                "AND LENGTH(embedding) = :embedding_bytes) "
                "SELECT * FROM ranked WHERE distance <= :max_distance ORDER BY distance, artifact_id"
            )
            vector_rows = tuple((await connection.execute(text(vector_sql), parameters)).mappings())
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
        self,
        scope_id: str,
        request: AtomicMemoryRelatedRequest,
        eligibility: str,
        parameters: dict[str, object],
    ) -> str | None:
        match_query = fts_match_query(request.query)
        if match_query is None:
            return None
        terms, required = fts_query_requirements(request.query, floor=request.admission)
        coverage, coverage_parameters = sqlite_fts_coverage_sql(terms)
        parameters.update(coverage_parameters)
        grouped = f"searchable_text:({match_query})"
        parameters.update(
            fts_query=grouped,
            fts_scope_query=f'({grouped}) AND identity_token:"i{scope_id.encode("utf-8").hex()}x"*',
            fts_required=required,
        )
        rank = f"bm25({_FTS_TABLE}, 0, 0, 0, 0, 0, 0, 0, 0, 1, 0, 0)"
        return (
            f"SELECT artifact_id, revision, state_version, kind, -{rank} AS score, "  # noqa: S608
            f"{rank} AS raw_score, 'sqlite_bm25' AS score_metric FROM {_FTS_TABLE} "
            f"WHERE {_FTS_TABLE} MATCH (CASE WHEN EXISTS (SELECT 1 "
            "FROM pc_atomic_memory_current WHERE scope_id = :scope_id LIMIT 1 OFFSET 64) "
            "THEN :fts_query ELSE :fts_scope_query END) AND scope_id = :scope_id "
            f"AND ({eligibility}) AND {coverage} >= :fts_required"
        )

    async def _require_related_vectors(
        self,
        connection: AsyncConnection,
        request: AtomicMemoryRelatedRequest,
        eligibility: str,
        parameters: dict[str, object],
    ) -> None:
        query_vector = self._require_vectors(request)
        profile = self.profile
        if profile is None:
            raise AtomicMemoryIndexError("embedding-profile", "Vector profile is unavailable")
        parameters.update(
            query_vector=self._encode_embedding(query_vector),
            profile=atomic_memory_profile_fingerprint(profile),
            projection_format=ATOMIC_MEMORY_PROJECTION_FORMAT,
            embedding_bytes=profile.dimension * 4,
            max_distance=request.max_distance,
        )
        invalid = await connection.scalar(
            text(
                "SELECT COUNT(*) FROM pc_atomic_memory_current WHERE scope_id = :scope_id "  # noqa: S608
                f"AND ({eligibility}) AND (embedding IS NULL OR embedding_input_hash IS NULL "
                "OR profile_fingerprint IS NULL OR profile_fingerprint <> :profile "
                "OR projection_format <> :projection_format OR LENGTH(embedding) <> :embedding_bytes)"
            ),
            parameters,
        )
        if invalid:
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
        if connection.dialect.name != "sqlite":
            raise AtomicMemoryIndexError("sqlite", "SQLite Atomic Memory index requires SQLite")
        eligibility, parameters = atomic_memory_filter_sql(request.filters, "sqlite")
        parameters["scope_id"] = scope_id
        limit_sql = " LIMIT :result_limit"
        parameters["result_limit"] = limit
        fts: tuple[AtomicMemoryIndexHit, ...] = ()
        vector: tuple[AtomicMemoryIndexHit, ...] = ()
        if request.mode in {"fts", "hybrid"}:
            match_query = fts_match_query(request.query)
            terms, required = fts_query_requirements(request.query, floor=request.admission)
            if match_query is not None:
                coverage, coverage_parameters = sqlite_fts_coverage_sql(terms)
                parameters.update(coverage_parameters)
                grouped = f"searchable_text:({match_query})"
                parameters.update(
                    fts_query=grouped,
                    fts_scope_query=f'({grouped}) AND identity_token:"i{scope_id.encode("utf-8").hex()}x"*',
                    fts_required=required,
                )
                # Prefix expansion is cheap only for a small Scope. The PK probe
                # reads at most 65 identities; both branches have identical ranks.
                rank = f"bm25({_FTS_TABLE}, 0, 0, 0, 0, 0, 0, 0, 0, 1, 0, 0)"
                rows = (
                    await connection.execute(
                        text(
                            f"SELECT {_HIT_COLUMNS}, -{rank} AS score, "  # noqa: S608
                            f"{rank} AS raw_score, 'sqlite_bm25' AS score_metric FROM {_FTS_TABLE} "
                            f"WHERE {_FTS_TABLE} MATCH (CASE WHEN EXISTS (SELECT 1 "
                            "FROM pc_atomic_memory_current WHERE scope_id = :scope_id LIMIT 1 OFFSET 64) "
                            "THEN :fts_query ELSE :fts_scope_query END) AND scope_id = :scope_id "
                            f"AND ({eligibility}) AND {coverage} >= :fts_required "
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
                max_distance=None,
                min_semantic_similarity=(
                    DEFAULT_ADMISSION_FLOOR if request.admission is None else request.admission
                ).min_semantic_similarity,
            )
            vector = await self._vector_hits(connection, request, eligibility, parameters, limit)
        return AtomicMemorySearchChannels(fts=fts, vector=vector)

    async def _vector_hits(
        self,
        connection: AsyncConnection,
        request: AtomicMemorySearchRequest,
        eligibility: str,
        parameters: dict[str, object],
        limit: int,
    ) -> tuple[AtomicMemoryIndexHit, ...]:
        """Use native KNN, with one exact fallback when boundary ties affect Top-K."""
        exact_sql = atomic_memory_vector_sql(
            eligibility,
            "sqlite",
            bounded=True,
            threshold=False,
        )
        # vec0's native k ceiling is 4096. One extra neighbor detects boundary ties.
        knn = limit < 4096
        neighbor_limit = limit + 1
        if knn:
            parameters["neighbor_limit"] = neighbor_limit
        sql = self._knn_sql(request, eligibility) if knn else exact_sql
        rows = tuple((await connection.execute(text(sql), parameters)).mappings())
        if knn:
            if len(rows) == neighbor_limit and rows[limit - 1]["distance"] == rows[-1]["distance"]:
                # Avoid repeatedly scanning vectors when a large tie group needs the ID tie break.
                rows = tuple((await connection.execute(text(exact_sql), parameters)).mappings())
            rows = tuple(sorted(rows, key=lambda row: (row["distance"], row["artifact_id"])))[:limit]
        return atomic_memory_channel_hits(rows, vector=True)


__all__ = ["SQLiteAtomicMemoryIndex"]
