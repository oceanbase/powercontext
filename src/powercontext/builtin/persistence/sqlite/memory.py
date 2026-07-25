"""SQLite Memory persistence projections using FTS5 and Vec1."""

from __future__ import annotations

import json
import re
import struct
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    Column,
    ForeignKeyConstraint,
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
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.memory import (
    CapabilityNotSupportedError,
    EmbeddingProfile,
    MemoryCapabilities,
    MemoryProjection,
    MemorySearchChannels,
    MemorySearchRequest,
)
from powercontext.builtin.artifacts.memory.canonical import (
    embedding_content_hash,
    fts_match_query,
    validate_embedding,
)
from powercontext.builtin.persistence.memory import memory_channel_hits
from powercontext.builtin.persistence.tables import (
    MAX_MEMORY_ENTRY_ID_LENGTH,
    MAX_MEMORY_HASH_LENGTH,
    MEMORY_ENTRY_HEADS_TABLE,
    SHARED_METADATA,
)
from powercontext.limits import MAX_ARTIFACT_ID_LENGTH, MAX_SCOPE_ID_LENGTH

SQLITE_MEMORY_FTS_MARKER_TABLE = Table(
    "pc_memory_fts_index",
    SHARED_METADATA,
    Column("singleton", Integer, primary_key=True),
    Column("schema_version", Integer, nullable=False),
    CheckConstraint("singleton = 1", name="ck_pc_memory_fts_index_singleton"),
)


SQLITE_MEMORY_FTS_TABLES = (SQLITE_MEMORY_FTS_MARKER_TABLE,)


SQLITE_MEMORY_VECTOR_ENTRIES_TABLE = Table(
    "pc_memory_vector_entries",
    SHARED_METADATA,
    Column("vector_id", Integer, primary_key=True, autoincrement=True),
    Column("scope_id", String(MAX_SCOPE_ID_LENGTH), nullable=False),
    Column("memory_artifact_id", String(MAX_ARTIFACT_ID_LENGTH), nullable=False),
    Column("head_revision", Integer, nullable=False),
    Column("entry_id", String(MAX_MEMORY_ENTRY_ID_LENGTH), nullable=False),
    Column("entry_version_id", String(MAX_MEMORY_ENTRY_ID_LENGTH), nullable=False),
    Column("entry_content_hash", String(MAX_MEMORY_HASH_LENGTH), nullable=False),
    Column("embedding_content_hash", String(MAX_MEMORY_HASH_LENGTH), nullable=False),
    UniqueConstraint(
        "scope_id",
        "memory_artifact_id",
        "entry_id",
        name="uq_pc_memory_vector_entries_head",
    ),
    ForeignKeyConstraint(
        ("scope_id", "memory_artifact_id", "entry_id"),
        (
            "pc_memory_entry_heads.scope_id",
            "pc_memory_entry_heads.memory_artifact_id",
            "pc_memory_entry_heads.entry_id",
        ),
        ondelete="CASCADE",
    ),
    CheckConstraint("head_revision > 0", name="ck_pc_memory_vector_entries_revision_positive"),
    sqlite_autoincrement=True,
)


SQLITE_MEMORY_VEC1_TABLES = (SQLITE_MEMORY_VECTOR_ENTRIES_TABLE,)


_FTS_TABLE_NAME = "pc_memory_entry_fts"
_MINIMUM_VEC1_VERSION = (0, 7)
_CREATE_FTS_SQL = """
CREATE VIRTUAL TABLE IF NOT EXISTS pc_memory_entry_fts USING fts5(
    scope_id UNINDEXED,
    memory_artifact_id UNINDEXED,
    head_revision UNINDEXED,
    entry_id UNINDEXED,
    entry_version_id UNINDEXED,
    searchable_text,
    tokenize='unicode61'
)
"""
_DELETE_ALL_FTS_SQL = "DELETE FROM pc_memory_entry_fts"
_PROBE_FTS_SQL = "SELECT rowid FROM pc_memory_entry_fts WHERE pc_memory_entry_fts MATCH 'powercontext'"
_DELETE_MEMORY_FTS_SQL = text(
    "DELETE FROM pc_memory_entry_fts WHERE scope_id = :scope_id AND memory_artifact_id = :memory_artifact_id"
)
_SEARCH_FTS_SQL = text(
    """
    SELECT f.memory_artifact_id, f.head_revision, f.entry_id, f.entry_version_id, v.text
    FROM pc_memory_entry_fts AS f
    JOIN pc_memory_entry_versions AS v
      ON v.scope_id = f.scope_id
     AND v.memory_artifact_id = f.memory_artifact_id
     AND v.entry_version_id = f.entry_version_id
    WHERE pc_memory_entry_fts MATCH :query
      AND f.scope_id = :scope_id
      AND f.memory_artifact_id IN (SELECT value FROM json_each(:memory_artifact_ids))
    ORDER BY bm25(pc_memory_entry_fts), f.memory_artifact_id, f.entry_id, f.entry_version_id
    LIMIT :candidate_limit
    """
)
_INSERT_FTS_SQL = text(
    """
    INSERT INTO pc_memory_entry_fts (
        scope_id, memory_artifact_id, head_revision, entry_id,
        entry_version_id, searchable_text
    ) VALUES (
        :scope_id, :memory_artifact_id, :head_revision, :entry_id,
        :entry_version_id, :searchable_text
    )
    """
)
_CREATE_VECTOR_SQL = "CREATE VIRTUAL TABLE IF NOT EXISTS pc_memory_entry_vec USING vec1(embedding)"
_DELETE_VECTOR_SQL = text("DELETE FROM pc_memory_entry_vec WHERE rowid = :vector_id")
_INSERT_VECTOR_SQL = text("INSERT INTO pc_memory_entry_vec (rowid, embedding) VALUES (:vector_id, :embedding)")
_SELECT_VECTOR_SQL = text("SELECT embedding FROM pc_memory_entry_vec WHERE rowid = :vector_id")
_VECTOR_SEARCH_SQL = text(
    """
    WITH candidates AS (
        SELECT rowid, embedding
        FROM pc_memory_entry_vec(:query_vector, :parameters)
    )
    SELECT m.memory_artifact_id, m.head_revision, m.entry_id, m.entry_version_id, v.text
    FROM candidates AS c
    JOIN pc_memory_vector_entries AS m ON m.vector_id = c.rowid
    JOIN pc_memory_entry_versions AS v
      ON v.scope_id = m.scope_id
     AND v.memory_artifact_id = m.memory_artifact_id
     AND v.entry_version_id = m.entry_version_id
    WHERE m.scope_id = :scope_id
      AND m.memory_artifact_id IN (SELECT value FROM json_each(:memory_artifact_ids))
    ORDER BY vec1_l2_distance(:query_vector, c.embedding),
             m.memory_artifact_id, m.entry_id, m.entry_version_id
    LIMIT :candidate_limit
    """
)


class SQLiteMemoryFTSIndex:
    """SQLite FTS5 strategy over rebuildable active-head projections."""

    capabilities = MemoryCapabilities(fts=True)
    tables: tuple[Table, ...] = SQLITE_MEMORY_FTS_TABLES

    async def initialize(self, connection: AsyncConnection, /) -> None:
        """Create and rebuild the FTS5 projection, failing if FTS5 is unavailable."""

        if connection.dialect.name != "sqlite":
            raise CapabilityNotSupportedError("sqlite-fts")
        await connection.exec_driver_sql(_CREATE_FTS_SQL)
        marker = await connection.scalar(select(SQLITE_MEMORY_FTS_MARKER_TABLE.c.singleton))
        if marker is None:
            await connection.execute(insert(SQLITE_MEMORY_FTS_MARKER_TABLE).values(singleton=1, schema_version=1))
        await connection.exec_driver_sql(_DELETE_ALL_FTS_SQL)
        rows = (
            await connection.execute(
                select(
                    MEMORY_ENTRY_HEADS_TABLE.c.scope_id,
                    MEMORY_ENTRY_HEADS_TABLE.c.memory_artifact_id,
                    MEMORY_ENTRY_HEADS_TABLE.c.head_revision,
                    MEMORY_ENTRY_HEADS_TABLE.c.entry_id,
                    MEMORY_ENTRY_HEADS_TABLE.c.entry_version_id,
                    MEMORY_ENTRY_HEADS_TABLE.c.searchable_text,
                )
            )
        ).mappings()
        for row in rows:
            await self._insert_row(connection, row)
        await connection.exec_driver_sql(_PROBE_FTS_SQL)

    async def replace(
        self,
        connection: AsyncConnection,
        scope_id: str,
        memory_ref: ArtifactRef,
        projections: tuple[MemoryProjection, ...],
        /,
    ) -> None:
        await connection.execute(
            _DELETE_MEMORY_FTS_SQL,
            {"scope_id": scope_id, "memory_artifact_id": memory_ref.artifact_id},
        )
        for projection in projections:
            await self._insert_row(
                connection,
                {
                    "scope_id": scope_id,
                    "memory_artifact_id": memory_ref.artifact_id,
                    "head_revision": memory_ref.revision,
                    "entry_id": projection.entry_version.entry_id,
                    "entry_version_id": projection.entry_version.entry_version_id,
                    "searchable_text": projection.searchable_text,
                },
            )

    async def search(
        self,
        connection: AsyncConnection,
        scope_id: str,
        request: MemorySearchRequest,
        /,
    ) -> MemorySearchChannels:
        if request.mode not in {"fts", "hybrid"}:
            return MemorySearchChannels()
        query = fts_match_query(request.query)
        if query is None:
            return MemorySearchChannels()
        rows = (
            await connection.execute(
                _SEARCH_FTS_SQL,
                {
                    "query": query,
                    "scope_id": scope_id,
                    "memory_artifact_ids": json.dumps(
                        tuple(ref.artifact_id for ref in request.memories),
                        separators=(",", ":"),
                    ),
                    "candidate_limit": request.candidate_limit,
                },
            )
        ).mappings()
        return MemorySearchChannels(fts=memory_channel_hits(rows, request.memories))

    async def vector_complete(
        self,
        connection: AsyncConnection,
        scope_id: str,
        memories: tuple[ArtifactRef, ...],
        profile: EmbeddingProfile,
        /,
    ) -> bool:
        del connection, scope_id, memories, profile
        return False

    async def hydrate(
        self,
        connection: AsyncConnection,
        scope_id: str,
        projections: tuple[MemoryProjection, ...],
        /,
    ) -> tuple[MemoryProjection, ...]:
        del connection, scope_id
        return projections

    @staticmethod
    async def _insert_row(
        connection: AsyncConnection,
        row: Mapping[Any, Any],
    ) -> None:
        values = {
            field: row[field]
            for field in (
                "scope_id",
                "memory_artifact_id",
                "head_revision",
                "entry_id",
                "entry_version_id",
                "searchable_text",
            )
        }
        await connection.execute(_INSERT_FTS_SQL, values)


class SQLiteMemoryVec1Index:
    """SQLite Vec1 strategy over rebuildable active-head embeddings."""

    tables: tuple[Table, ...] = SQLITE_MEMORY_VEC1_TABLES

    def __init__(self, extension: str | Path, profile: EmbeddingProfile) -> None:
        if profile.dimension < 1 or profile.distance != "l2":
            raise CapabilityNotSupportedError("vector", "Vec1 requires a positive L2 embedding profile")
        self.extension = str(extension)
        self.profile = profile
        self.capabilities = MemoryCapabilities(vector=True, embedding_profile=profile, fts=False)

    async def initialize(self, connection: AsyncConnection, /) -> None:
        if connection.dialect.name != "sqlite":
            raise CapabilityNotSupportedError("sqlite-vec1")
        try:
            info = (await connection.exec_driver_sql("SELECT vec1_info()")).scalar_one_or_none()
        except Exception as error:
            raise CapabilityNotSupportedError("vector", "SQLite Vec1 probe failed") from error
        _validate_vec1_info(info)
        try:
            await connection.exec_driver_sql(_CREATE_VECTOR_SQL)
            probe = _pack_vector((0.0,) * self.profile.dimension)
            await connection.execute(
                _INSERT_VECTOR_SQL,
                {"vector_id": -1, "embedding": probe},
            )
            row = (
                await connection.exec_driver_sql(
                    "SELECT rowid FROM pc_memory_entry_vec(?, ?)",
                    (probe, json.dumps({"k": 1}, separators=(",", ":"))),
                )
            ).one_or_none()
            await connection.execute(_DELETE_VECTOR_SQL, {"vector_id": -1})
        except Exception as error:
            raise CapabilityNotSupportedError("vector", "SQLite Vec1 probe failed") from error
        if row is None or int(row[0]) != -1:
            raise CapabilityNotSupportedError("vector", "SQLite Vec1 probe returned an invalid row")

    async def replace(
        self,
        connection: AsyncConnection,
        scope_id: str,
        memory_ref: ArtifactRef,
        projections: tuple[MemoryProjection, ...],
        /,
    ) -> None:
        existing = (
            await connection.execute(
                select(SQLITE_MEMORY_VECTOR_ENTRIES_TABLE.c.vector_id).where(
                    SQLITE_MEMORY_VECTOR_ENTRIES_TABLE.c.scope_id == scope_id,
                    SQLITE_MEMORY_VECTOR_ENTRIES_TABLE.c.memory_artifact_id == memory_ref.artifact_id,
                )
            )
        ).scalars()
        for vector_id in existing:
            await connection.execute(_DELETE_VECTOR_SQL, {"vector_id": int(vector_id)})
        await connection.execute(
            delete(SQLITE_MEMORY_VECTOR_ENTRIES_TABLE).where(
                SQLITE_MEMORY_VECTOR_ENTRIES_TABLE.c.scope_id == scope_id,
                SQLITE_MEMORY_VECTOR_ENTRIES_TABLE.c.memory_artifact_id == memory_ref.artifact_id,
            )
        )
        for projection in projections:
            if projection.embedding is None or projection.embedding_content_hash is None:
                continue
            vector = validate_embedding(projection.embedding, dimension=self.profile.dimension)
            entry = projection.entry_version
            vector_id = (
                await connection.execute(
                    insert(SQLITE_MEMORY_VECTOR_ENTRIES_TABLE)
                    .values(
                        scope_id=scope_id,
                        memory_artifact_id=memory_ref.artifact_id,
                        head_revision=memory_ref.revision,
                        entry_id=entry.entry_id,
                        entry_version_id=entry.entry_version_id,
                        entry_content_hash=entry.entry_content_hash,
                        embedding_content_hash=projection.embedding_content_hash,
                    )
                    .returning(SQLITE_MEMORY_VECTOR_ENTRIES_TABLE.c.vector_id)
                )
            ).scalar_one()
            await connection.execute(
                _INSERT_VECTOR_SQL,
                {"vector_id": vector_id, "embedding": _pack_vector(vector)},
            )

    async def search(
        self,
        connection: AsyncConnection,
        scope_id: str,
        request: MemorySearchRequest,
        /,
    ) -> MemorySearchChannels:
        if request.mode not in {"vector", "hybrid"}:
            return MemorySearchChannels()
        if request.embedding_profile != self.profile or request.query_vector is None:
            raise CapabilityNotSupportedError("embedding-profile")
        if not await self.vector_complete(connection, scope_id, request.memories, self.profile):
            raise CapabilityNotSupportedError("vector")
        query_vector = _pack_vector(validate_embedding(request.query_vector, dimension=self.profile.dimension))
        total = int(await connection.scalar(select(func.count()).select_from(SQLITE_MEMORY_VECTOR_ENTRIES_TABLE)) or 0)
        if total == 0:
            return MemorySearchChannels()
        rows = (
            await connection.execute(
                _VECTOR_SEARCH_SQL,
                {
                    "query_vector": query_vector,
                    "parameters": json.dumps({"k": total}, separators=(",", ":")),
                    "scope_id": scope_id,
                    "memory_artifact_ids": json.dumps(
                        tuple(ref.artifact_id for ref in request.memories),
                        separators=(",", ":"),
                    ),
                    "candidate_limit": request.candidate_limit,
                },
            )
        ).mappings()
        return MemorySearchChannels(vector=memory_channel_hits(rows, request.memories))

    async def vector_complete(
        self,
        connection: AsyncConnection,
        scope_id: str,
        memories: tuple[ArtifactRef, ...],
        profile: EmbeddingProfile,
        /,
    ) -> bool:
        if profile != self.profile:
            return False
        for memory in memories:
            heads = (
                await connection.execute(
                    select(
                        MEMORY_ENTRY_HEADS_TABLE.c.entry_id,
                        MEMORY_ENTRY_HEADS_TABLE.c.entry_version_id,
                        MEMORY_ENTRY_HEADS_TABLE.c.entry_content_hash,
                    ).where(
                        MEMORY_ENTRY_HEADS_TABLE.c.scope_id == scope_id,
                        MEMORY_ENTRY_HEADS_TABLE.c.memory_artifact_id == memory.artifact_id,
                        MEMORY_ENTRY_HEADS_TABLE.c.head_revision == memory.revision,
                    )
                )
            ).all()
            metadata = (
                await connection.execute(
                    select(
                        SQLITE_MEMORY_VECTOR_ENTRIES_TABLE.c.vector_id,
                        SQLITE_MEMORY_VECTOR_ENTRIES_TABLE.c.entry_id,
                        SQLITE_MEMORY_VECTOR_ENTRIES_TABLE.c.entry_version_id,
                        SQLITE_MEMORY_VECTOR_ENTRIES_TABLE.c.entry_content_hash,
                        SQLITE_MEMORY_VECTOR_ENTRIES_TABLE.c.embedding_content_hash,
                    ).where(
                        SQLITE_MEMORY_VECTOR_ENTRIES_TABLE.c.scope_id == scope_id,
                        SQLITE_MEMORY_VECTOR_ENTRIES_TABLE.c.memory_artifact_id == memory.artifact_id,
                        SQLITE_MEMORY_VECTOR_ENTRIES_TABLE.c.head_revision == memory.revision,
                    )
                )
            ).all()
            expected = {(str(row[0]), str(row[1]), str(row[2])) for row in heads}
            actual = {(str(row[1]), str(row[2]), str(row[3])) for row in metadata}
            if actual != expected:
                return False
            for row in metadata:
                if str(row[4]) != _embedding_hash(self.profile, str(row[3])):
                    return False
                if (await connection.execute(_SELECT_VECTOR_SQL, {"vector_id": int(row[0])})).one_or_none() is None:
                    return False
        return True

    async def hydrate(
        self,
        connection: AsyncConnection,
        scope_id: str,
        projections: tuple[MemoryProjection, ...],
        /,
    ) -> tuple[MemoryProjection, ...]:
        hydrated: list[MemoryProjection] = []
        for projection in projections:
            entry = projection.entry_version
            metadata = (
                await connection.execute(
                    select(
                        SQLITE_MEMORY_VECTOR_ENTRIES_TABLE.c.vector_id,
                        SQLITE_MEMORY_VECTOR_ENTRIES_TABLE.c.entry_content_hash,
                        SQLITE_MEMORY_VECTOR_ENTRIES_TABLE.c.embedding_content_hash,
                    ).where(
                        SQLITE_MEMORY_VECTOR_ENTRIES_TABLE.c.scope_id == scope_id,
                        SQLITE_MEMORY_VECTOR_ENTRIES_TABLE.c.memory_artifact_id == entry.memory_artifact_id,
                        SQLITE_MEMORY_VECTOR_ENTRIES_TABLE.c.entry_id == entry.entry_id,
                        SQLITE_MEMORY_VECTOR_ENTRIES_TABLE.c.entry_version_id == entry.entry_version_id,
                    )
                )
            ).one_or_none()
            if metadata is None:
                hydrated.append(projection)
                continue
            if str(metadata[1]) != entry.entry_content_hash or str(metadata[2]) != _embedding_hash(
                self.profile, entry.entry_content_hash
            ):
                hydrated.append(projection)
                continue
            packed = (
                await connection.execute(_SELECT_VECTOR_SQL, {"vector_id": int(metadata[0])})
            ).scalar_one_or_none()
            if packed is None:
                hydrated.append(projection)
                continue
            hydrated.append(
                MemoryProjection(
                    entry_version=entry,
                    searchable_text=projection.searchable_text,
                    embedding=_unpack_vector(packed, self.profile.dimension),
                    embedding_content_hash=str(metadata[2]),
                )
            )
        return tuple(hydrated)


def _pack_vector(vector: tuple[float, ...]) -> bytes:
    return struct.pack(f"={len(vector)}f", *vector)


def _unpack_vector(value: object, dimension: int) -> tuple[float, ...]:
    if not isinstance(value, bytes | bytearray | memoryview):
        raise CapabilityNotSupportedError("vector", "SQLite Vec1 returned an invalid vector")
    packed = bytes(value)
    if len(packed) != struct.calcsize(f"={dimension}f"):
        raise CapabilityNotSupportedError("vector", "SQLite Vec1 returned the wrong vector dimension")
    return tuple(struct.unpack(f"={dimension}f", packed))


def _embedding_hash(profile: EmbeddingProfile, entry_hash: str) -> str:
    return embedding_content_hash(
        profile_id=profile.profile_id,
        model=profile.model,
        dimension=profile.dimension,
        distance=profile.distance,
        normalization=profile.normalization,
        entry_content_hash=entry_hash,
    )


def _validate_vec1_info(info: object) -> None:
    match = re.search(r"\bversion\s+(\d+)\.(\d+)\b", "" if info is None else str(info))
    if match is None or tuple(int(part) for part in match.groups()) < _MINIMUM_VEC1_VERSION:
        raise CapabilityNotSupportedError("vector", "SQLite Vec1 0.7 or newer is required")
