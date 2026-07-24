"""APSW-backed authoritative Memory storage with FTS5 projections."""

from __future__ import annotations

import asyncio
import json
import re
import struct
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import apsw

from powercontext.artifacts import ArtifactRef
from powercontext.errors import (
    ArtifactNotFoundError,
    CapabilityNotSupportedError,
    MemoryBackendConfigurationError,
    RevisionConflictError,
)
from powercontext.inference import EmbeddingModel
from powercontext.memory.backends._sql import (
    decode_entry_version,
    encode_entry_refs,
    encode_lineage,
    encode_memory_content,
)
from powercontext.memory.backends.base import DatabaseMemoryBackend
from powercontext.memory.canonical import (
    analyze_text,
    embedding_content_hash,
    fts_match_query,
    validate_embedding,
)
from powercontext.memory.models import (
    EmbeddingProfile,
    Memory,
    MemoryCapabilities,
    MemoryChannelHit,
    MemoryEntryVersion,
    MemoryRevisionChanges,
)
from powercontext.memory.protocols import (
    MemoryCommit,
    MemoryEvidenceCodec,
    MemoryProjection,
    MemorySearchChannels,
    MemorySearchRequest,
)

_MINIMUM_SQLITE_VERSION = (3, 38, 0)
_SCHEMA_VERSION = 1
_MINIMUM_VEC1_VERSION = (0, 7)
_ENTRY_COLUMNS = """
    memory_artifact_id,
    entry_id,
    entry_version_id,
    version,
    previous_version_id,
    kind,
    text,
    source_refs,
    artifact_refs,
    entry_content_hash,
    created_in_revision
"""
_INSERT_ENTRY_SQL = """
    INSERT INTO memory_entry_versions (
        memory_artifact_id, entry_id, entry_version_id, version, previous_version_id,
        kind, text, source_refs, artifact_refs, entry_content_hash, created_in_revision
    )
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""
_SELECT_ENTRY_SQL = """
    SELECT
        memory_artifact_id, entry_id, entry_version_id, version, previous_version_id,
        kind, text, source_refs, artifact_refs, entry_content_hash, created_in_revision
    FROM memory_entry_versions
    WHERE memory_artifact_id = ? AND entry_version_id = ?
"""

_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS powercontext_schema (
        version INTEGER NOT NULL PRIMARY KEY
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS artifact_revisions (
        artifact_id TEXT NOT NULL,
        revision INTEGER NOT NULL CHECK (revision > 0),
        family TEXT NOT NULL,
        content TEXT NOT NULL,
        content_hash TEXT NOT NULL CHECK (length(content_hash) = 64),
        lineage TEXT NOT NULL,
        PRIMARY KEY (artifact_id, revision)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS artifact_heads (
        artifact_id TEXT NOT NULL PRIMARY KEY,
        revision INTEGER NOT NULL,
        UNIQUE (artifact_id, revision),
        FOREIGN KEY (artifact_id, revision)
            REFERENCES artifact_revisions (artifact_id, revision)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS memory_entry_versions (
        memory_artifact_id TEXT NOT NULL,
        entry_id TEXT NOT NULL,
        entry_version_id TEXT NOT NULL PRIMARY KEY,
        version INTEGER NOT NULL CHECK (version > 0),
        previous_version_id TEXT,
        kind TEXT NOT NULL,
        text TEXT NOT NULL,
        source_refs TEXT NOT NULL,
        artifact_refs TEXT NOT NULL,
        entry_content_hash TEXT NOT NULL CHECK (length(entry_content_hash) = 64),
        created_in_revision INTEGER NOT NULL,
        UNIQUE (memory_artifact_id, entry_id, version),
        UNIQUE (memory_artifact_id, entry_id, entry_version_id),
        FOREIGN KEY (previous_version_id)
            REFERENCES memory_entry_versions (entry_version_id),
        FOREIGN KEY (memory_artifact_id, created_in_revision)
            REFERENCES artifact_revisions (artifact_id, revision)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_memory_entry_versions_hash
        ON memory_entry_versions (memory_artifact_id, entry_content_hash)
    """,
    """
    CREATE TABLE IF NOT EXISTS memory_entry_heads (
        projection_id INTEGER PRIMARY KEY,
        memory_artifact_id TEXT NOT NULL,
        head_revision INTEGER NOT NULL,
        entry_id TEXT NOT NULL,
        entry_version_id TEXT NOT NULL,
        entry_content_hash TEXT NOT NULL,
        searchable_text TEXT NOT NULL,
        UNIQUE (memory_artifact_id, entry_id),
        FOREIGN KEY (memory_artifact_id, entry_id, entry_version_id)
            REFERENCES memory_entry_versions (memory_artifact_id, entry_id, entry_version_id)
    )
    """,
    """
    CREATE VIRTUAL TABLE IF NOT EXISTS memory_entry_search_fts USING fts5(
        searchable_text,
        tokenize='unicode61'
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS memory_entry_vector_metadata (
        projection_id INTEGER PRIMARY KEY,
        entry_version_id TEXT NOT NULL,
        entry_content_hash TEXT NOT NULL,
        embedding_content_hash TEXT NOT NULL,
        UNIQUE (entry_version_id),
        FOREIGN KEY (projection_id)
            REFERENCES memory_entry_heads (projection_id) ON DELETE CASCADE
    )
    """,
)


@dataclass(frozen=True, slots=True)
class _ProjectionRebuildRow:
    memory_ref: ArtifactRef
    entry_version: MemoryEntryVersion
    searchable_text: str


class _SQLiteBackendStateError(MemoryBackendConfigurationError):
    def __init__(self, code: str, detail: object | None = None) -> None:
        messages = {
            "not-initialized": "SQLite Memory backend has not been initialized",
            "closed": "SQLite Memory backend is closed",
            "sqlite-version": f"SQLite 3.38.0 or newer is required, found {detail}",
            "schema-version": f"unsupported SQLite Memory schema version: {detail}",
            "content-hash": "stored Memory content hash does not match its canonical content",
            "family": "stored Artifact row is not a Memory Artifact",
            "manifest": "Memory manifest is not canonical or contains duplicate identities",
            "entry-hash": "stored Memory entry hash does not match its canonical content",
            "entry-link": "stored Memory entry does not match its manifest anchor",
            "commit": f"invalid Memory commit: {detail}",
            "transaction": "SQLite Memory transaction is already complete",
            "vector-config": f"invalid SQLite vector configuration: {detail}",
            "projection-rebuild": f"SQLite projection rebuild failed: {detail}",
        }
        super().__init__(messages[code])


class SQLiteMemoryBackend(DatabaseMemoryBackend):
    """Persist immutable Memory history and active FTS projections in SQLite."""

    def __init__(
        self,
        database: str | Path,
        *,
        evidence_codec: MemoryEvidenceCodec | None = None,
        embedding_profile: EmbeddingProfile | None = None,
        vec1_extension: str | Path | None = None,
    ) -> None:
        super().__init__(evidence_codec=evidence_codec)
        self._database = str(database)
        self._embedding_profile = embedding_profile
        self._vec1_extension = None if vec1_extension is None else str(vec1_extension)
        if (embedding_profile is None) != (vec1_extension is None):
            raise _SQLiteBackendStateError("vector-config", "profile and extension path must be configured together")
        if embedding_profile is not None and (embedding_profile.dimension < 1 or embedding_profile.distance != "l2"):
            raise _SQLiteBackendStateError("vector-config", "profile must use a positive dimension and L2 distance")
        self._connection: apsw.Connection | None = None

    async def initialize(self) -> None:
        """Probe required SQLite features and install the fixed RFC schema."""

        async with self._lock:
            if self._closed:
                raise _SQLiteBackendStateError("closed")
            if self._connection is not None:
                return
            await asyncio.to_thread(self._initialize_sync)

    async def close(self) -> None:
        """Close the serialized APSW connection."""

        async with self._lock:
            connection = self._connection
            self._connection = None
            self._closed = True
            if connection is not None:
                await asyncio.to_thread(connection.close)

    async def foreign_keys_enabled(self) -> bool:
        """Return whether the adapter connection enforces foreign keys."""

        async with self._lock:
            return bool(await asyncio.to_thread(self._foreign_keys_enabled_sync))

    def _capabilities_sync(self) -> MemoryCapabilities:
        """Report only capabilities that passed startup probes."""

        self._require_connection()
        profile = self._embedding_profile
        return MemoryCapabilities(
            fts=True,
            vector=profile is not None,
            hybrid=profile is not None,
            embedding_profile=profile,
        )

    async def rebuild_projections(self, embedding_model: EmbeddingModel | None = None, /) -> None:
        """Offline-rebuild FTS and optional vectors from authoritative current heads."""

        async with self._lock:
            rows = await asyncio.to_thread(self._authoritative_projection_rows)
            vectors: tuple[tuple[float, ...] | None, ...] = tuple(None for _ in rows)
            if embedding_model is not None:
                profile = self._require_vector_profile()
                if embedding_model.profile != profile:
                    raise CapabilityNotSupportedError("embedding-profile")
                result = await embedding_model.embed(tuple(row.entry_version.text for row in rows))
                if len(result.vectors) != len(rows):
                    raise _SQLiteBackendStateError(
                        "projection-rebuild",
                        "embedding model returned the wrong vector count",
                    )
                vectors = tuple(validate_embedding(vector, dimension=profile.dimension) for vector in result.vectors)
            await asyncio.to_thread(self._rebuild_projections_sync, rows, vectors)

    def _initialize_sync(self) -> None:
        found_version = apsw.sqlitelibversion()
        if _version_tuple(found_version) < _MINIMUM_SQLITE_VERSION:
            raise _SQLiteBackendStateError("sqlite-version", found_version)
        connection = apsw.Connection(
            self._database,
            flags=apsw.SQLITE_OPEN_READWRITE | apsw.SQLITE_OPEN_CREATE | apsw.SQLITE_OPEN_FULLMUTEX,
        )
        try:
            connection.set_busy_timeout(30_000)
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA journal_mode = WAL")
            self._probe_fts(connection)
            if self._embedding_profile is not None:
                self._load_vec1(connection)
            for statement in _SCHEMA_STATEMENTS:
                connection.execute(statement)
            if self._embedding_profile is not None:
                connection.execute(
                    "CREATE VIRTUAL TABLE IF NOT EXISTS memory_entry_search_vector USING vec1(embedding)"
                )
                self._probe_vec1_table(connection, self._embedding_profile)
            connection.execute(
                "INSERT OR IGNORE INTO powercontext_schema (version) VALUES (?)",
                (_SCHEMA_VERSION,),
            )
            versions = tuple(row[0] for row in connection.execute("SELECT version FROM powercontext_schema"))
            self._validate_initialized_connection(connection, versions)
        except BaseException:
            connection.close()
            raise
        self._connection = connection

    @staticmethod
    def _validate_initialized_connection(connection: apsw.Connection, versions: tuple[object, ...]) -> None:
        if versions != (_SCHEMA_VERSION,):
            raise _SQLiteBackendStateError("schema-version", versions)
        if connection.pragma("foreign_keys") != 1:
            raise _SQLiteBackendStateError("commit", "foreign key enforcement is unavailable")

    @staticmethod
    def _probe_fts(connection: apsw.Connection) -> None:
        try:
            connection.execute("CREATE VIRTUAL TABLE temp.powercontext_fts_probe USING fts5(value)")
            connection.execute("INSERT INTO powercontext_fts_probe (value) VALUES ('probe')")
            row = connection.execute(
                "SELECT rowid FROM powercontext_fts_probe WHERE powercontext_fts_probe MATCH ?",
                ('"probe"',),
            ).fetchone()
            connection.execute("DELETE FROM powercontext_fts_probe")
            connection.execute("DROP TABLE powercontext_fts_probe")
        except apsw.Error as error:
            raise CapabilityNotSupportedError("fts", "SQLite FTS5 probe failed") from error
        if row is None:
            raise CapabilityNotSupportedError("fts", "SQLite FTS5 probe returned no match")

    def _load_vec1(self, connection: apsw.Connection) -> None:
        extension = self._vec1_extension
        if extension is None:
            raise _SQLiteBackendStateError("vector-config", "Vec1 extension path is missing")
        try:
            connection.enable_load_extension(True)
            connection.load_extension(extension)
        except apsw.Error as error:
            raise CapabilityNotSupportedError("vector", "SQLite Vec1 load failed") from error
        finally:
            connection.enable_load_extension(False)
        row = connection.execute("SELECT vec1_info()").fetchone()
        info = "" if row is None else str(row[0])
        match = re.search(r"\bversion\s+(\d+)\.(\d+)\b", info)
        if match is None or tuple(int(part) for part in match.groups()) < _MINIMUM_VEC1_VERSION:
            raise CapabilityNotSupportedError("vector", "SQLite Vec1 0.7 or newer is required")

    @staticmethod
    def _probe_vec1_table(connection: apsw.Connection, profile: EmbeddingProfile) -> None:
        probe_rowid = -1
        vector = _pack_vector((0.0,) * profile.dimension)
        parameters = json.dumps({"k": 1}, separators=(",", ":"))
        try:
            connection.execute("CREATE VIRTUAL TABLE temp.powercontext_vec1_probe USING vec1(embedding)")
            connection.execute(
                "INSERT INTO powercontext_vec1_probe (rowid, embedding) VALUES (?, ?)",
                (probe_rowid, vector),
            )
            row = connection.execute(
                "SELECT rowid FROM powercontext_vec1_probe(?, ?)",
                (vector, parameters),
            ).fetchone()
            connection.execute("DELETE FROM powercontext_vec1_probe WHERE rowid = ?", (probe_rowid,))
            connection.execute("DROP TABLE powercontext_vec1_probe")
            connection.execute(
                "INSERT INTO memory_entry_search_vector (rowid, embedding) VALUES (?, ?)",
                (probe_rowid, vector),
            )
            connection.execute("DELETE FROM memory_entry_search_vector WHERE rowid = ?", (probe_rowid,))
        except apsw.Error as error:
            raise CapabilityNotSupportedError("vector", "SQLite Vec1 insert/query/delete probe failed") from error
        if row is None or row[0] != probe_rowid:
            raise CapabilityNotSupportedError("vector", "SQLite Vec1 probe returned an invalid row")

    def _foreign_keys_enabled_sync(self) -> int:
        return int(self._require_connection().pragma("foreign_keys"))

    def _get_sync(self, memory: ArtifactRef) -> Memory:
        connection = self._require_connection()
        row = connection.execute(
            """
            SELECT artifact_id, revision, family, content, content_hash, lineage
            FROM artifact_revisions
            WHERE artifact_id = ? AND revision = ?
            """,
            (memory.artifact_id, memory.revision),
        ).fetchone()
        if row is None:
            raise ArtifactNotFoundError(memory)
        return self._decode_memory_row(row)

    def _projections_sync(self, memory: ArtifactRef) -> tuple[MemoryProjection, ...]:
        revision = self._get_sync(memory)
        connection = self._require_connection()
        rows = connection.execute(
            """
            SELECT heads.entry_version_id, heads.searchable_text, heads.projection_id,
                   metadata.embedding_content_hash
            FROM memory_entry_heads AS heads
            LEFT JOIN memory_entry_vector_metadata AS metadata
              ON metadata.projection_id = heads.projection_id
            WHERE heads.memory_artifact_id = ? AND heads.head_revision = ?
            ORDER BY heads.entry_id
            """,
            (memory.artifact_id, memory.revision),
        ).fetchall()
        profile = self._embedding_profile
        projections: list[MemoryProjection] = []
        for entry_version_id, searchable_text, projection_id, embedding_hash in rows:
            version = self._load_entry_version(memory.artifact_id, str(entry_version_id))
            embedding = None
            stored_hash = None if embedding_hash is None else str(embedding_hash)
            if profile is not None and stored_hash is not None:
                packed_row = connection.execute(
                    "SELECT embedding FROM memory_entry_search_vector WHERE rowid = ?",
                    (projection_id,),
                ).fetchone()
                if packed_row is not None:
                    embedding = _unpack_vector(packed_row[0], profile.dimension)
                    self._validate_projection_vector(embedding, stored_hash, version)
            projections.append(
                MemoryProjection(
                    entry_version=version,
                    searchable_text=str(searchable_text),
                    embedding=embedding,
                    embedding_content_hash=stored_hash if embedding is not None else None,
                )
            )
        active_ids = {item.entry_version_id for item in revision.content.manifest.entries if item.state == "active"}
        if {projection.entry_version.entry_version_id for projection in projections} != active_ids:
            raise _SQLiteBackendStateError("commit", "active projections do not match the manifest")
        return tuple(projections)

    def _latest_sync(self, artifact_id: str) -> Memory:
        connection = self._require_connection()
        row = connection.execute(
            """
            SELECT revisions.artifact_id, revisions.revision, revisions.family,
                   revisions.content, revisions.content_hash, revisions.lineage
            FROM artifact_heads AS heads
            JOIN artifact_revisions AS revisions
              ON revisions.artifact_id = heads.artifact_id
             AND revisions.revision = heads.revision
            WHERE heads.artifact_id = ?
            """,
            (artifact_id,),
        ).fetchone()
        if row is None:
            raise ArtifactNotFoundError(artifact_id)
        return self._decode_memory_row(row)

    def _changes_sync(
        self,
        memory: ArtifactRef,
        since_revision: int | None,
    ) -> tuple[MemoryRevisionChanges, ...]:
        target = self._get_sync(memory)
        lower = target.revision - 1 if since_revision is None else since_revision
        rows = self._require_connection().execute(
            """
            SELECT artifact_id, revision, family, content, content_hash, lineage
            FROM artifact_revisions
            WHERE artifact_id = ? AND revision > ? AND revision <= ?
            ORDER BY revision
            """,
            (target.artifact_id, lower, target.revision),
        )
        revisions = tuple(self._decode_memory_row(row) for row in rows)
        return tuple(
            MemoryRevisionChanges(memory_ref=revision.ref, changes=revision.content.changes) for revision in revisions
        )

    def _vector_complete_sync(
        self,
        memories: tuple[ArtifactRef, ...],
        profile: EmbeddingProfile,
        *,
        validate_heads: bool = True,
    ) -> bool:
        configured = self._embedding_profile
        if configured is None or profile != configured:
            return False
        if validate_heads:
            self._assert_current_refs(memories)
        connection = self._require_connection()
        for memory in memories:
            revision = self._get_sync(memory)
            active = {item.entry_id: item for item in revision.content.manifest.entries if item.state == "active"}
            seen: set[str] = set()
            rows = connection.execute(
                """
                SELECT heads.projection_id, heads.entry_id, heads.entry_version_id, heads.entry_content_hash,
                       metadata.entry_version_id, metadata.entry_content_hash,
                       metadata.embedding_content_hash,
                       vectors.rowid
                FROM memory_entry_heads AS heads
                LEFT JOIN memory_entry_vector_metadata AS metadata
                  ON metadata.projection_id = heads.projection_id
                LEFT JOIN memory_entry_search_vector AS vectors
                  ON vectors.rowid = heads.projection_id
                WHERE heads.memory_artifact_id = ? AND heads.head_revision = ?
                """,
                (memory.artifact_id, memory.revision),
            )
            for row in rows:
                (
                    projection_id,
                    entry_id,
                    entry_version_id,
                    entry_hash,
                    metadata_version_id,
                    metadata_entry_hash,
                    stored_embedding_hash,
                    vector_rowid,
                ) = row
                item = active.get(str(entry_id))
                expected_embedding_hash = _embedding_hash(configured, str(entry_hash))
                if (
                    item is None
                    or item.entry_version_id != entry_version_id
                    or item.entry_content_hash != entry_hash
                    or metadata_version_id != entry_version_id
                    or metadata_entry_hash != entry_hash
                    or stored_embedding_hash != expected_embedding_hash
                    or vector_rowid != projection_id
                ):
                    return False
                seen.add(str(entry_id))
            if seen != set(active):
                return False
        return True

    def _authoritative_projection_rows(self) -> tuple[_ProjectionRebuildRow, ...]:
        rows: list[_ProjectionRebuildRow] = []
        heads = tuple(
            self._require_connection().execute("SELECT artifact_id, revision FROM artifact_heads ORDER BY artifact_id")
        )
        for artifact_id, revision in heads:
            memory_ref = ArtifactRef(str(artifact_id), _stored_int(revision))
            memory = self._get_sync(memory_ref)
            versions = {version.entry_id: version for version in self._entries_sync(memory_ref)}
            for item in memory.content.manifest.entries:
                if item.state != "active":
                    continue
                version = versions[item.entry_id]
                rows.append(
                    _ProjectionRebuildRow(
                        memory_ref=memory_ref,
                        entry_version=version,
                        searchable_text=analyze_text(version.text),
                    )
                )
        return tuple(rows)

    def _rebuild_projections_sync(
        self,
        rows: tuple[_ProjectionRebuildRow, ...],
        vectors: tuple[tuple[float, ...] | None, ...],
    ) -> None:
        connection = self._require_connection()
        connection.execute("BEGIN IMMEDIATE")
        try:
            self._assert_projection_rebuild_snapshot(rows)
            if self._embedding_profile is not None:
                connection.execute("DELETE FROM memory_entry_search_vector")
            connection.execute("DELETE FROM memory_entry_vector_metadata")
            connection.execute("DELETE FROM memory_entry_search_fts")
            connection.execute("DELETE FROM memory_entry_heads")
            for row, vector in zip(rows, vectors, strict=True):
                version = row.entry_version
                connection.execute(
                    """
                    INSERT INTO memory_entry_heads
                        (memory_artifact_id, head_revision, entry_id, entry_version_id,
                         entry_content_hash, searchable_text)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row.memory_ref.artifact_id,
                        row.memory_ref.revision,
                        version.entry_id,
                        version.entry_version_id,
                        version.entry_content_hash,
                        row.searchable_text,
                    ),
                )
                projection_id = connection.last_insert_rowid()
                connection.execute(
                    "INSERT INTO memory_entry_search_fts (rowid, searchable_text) VALUES (?, ?)",
                    (projection_id, row.searchable_text),
                )
                if vector is None:
                    continue
                profile = self._require_vector_profile()
                connection.execute(
                    "INSERT INTO memory_entry_search_vector (rowid, embedding) VALUES (?, ?)",
                    (projection_id, _pack_vector(vector)),
                )
                connection.execute(
                    """
                    INSERT INTO memory_entry_vector_metadata
                        (projection_id, entry_version_id, entry_content_hash, embedding_content_hash)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        projection_id,
                        version.entry_version_id,
                        version.entry_content_hash,
                        _embedding_hash(profile, version.entry_content_hash),
                    ),
                )
            connection.execute("COMMIT")
        except BaseException:
            connection.execute("ROLLBACK")
            raise

    def _assert_projection_rebuild_snapshot(self, rows: tuple[_ProjectionRebuildRow, ...]) -> None:
        if self._authoritative_projection_rows() != rows:
            raise _SQLiteBackendStateError(
                "projection-rebuild",
                "authoritative heads changed while embeddings were generated",
            )

    def _search_sync(self, request: MemorySearchRequest) -> MemorySearchChannels:
        connection = self._require_connection()
        connection.execute("BEGIN")
        try:
            self._assert_current_refs(request.memories)
            fts = self._fts_search_sync(request) if request.mode in {"fts", "hybrid"} else ()
            vector = self._vector_search_sync(request) if request.mode in {"vector", "hybrid"} else ()
            result = MemorySearchChannels(fts=fts, vector=vector)
            connection.execute("COMMIT")
        except BaseException:
            connection.execute("ROLLBACK")
            raise
        return result

    def _fts_search_sync(self, request: MemorySearchRequest) -> tuple[MemoryChannelHit, ...]:
        match_query = fts_match_query(request.query)
        if match_query is None:
            return ()
        parameters: tuple[object, ...] = (
            match_query,
            json.dumps(tuple(memory.artifact_id for memory in request.memories)),
            request.candidate_limit,
        )
        search_sql = """
            SELECT heads.memory_artifact_id, heads.head_revision, heads.entry_id,
                   heads.entry_version_id, heads.entry_content_hash, versions.text
            FROM memory_entry_search_fts
            JOIN memory_entry_heads AS heads
              ON heads.projection_id = memory_entry_search_fts.rowid
            JOIN memory_entry_versions AS versions
              ON versions.entry_version_id = heads.entry_version_id
            JOIN artifact_heads
              ON artifact_heads.artifact_id = heads.memory_artifact_id
             AND artifact_heads.revision = heads.head_revision
            WHERE memory_entry_search_fts MATCH ?
              AND heads.memory_artifact_id IN (SELECT value FROM json_each(?))
            ORDER BY bm25(memory_entry_search_fts),
                     heads.memory_artifact_id,
                     heads.entry_id,
                     heads.entry_version_id
            LIMIT ?
            """
        rows = self._require_connection().execute(search_sql, parameters)
        return self._validated_channel_hits(rows, request.memories)

    def _vector_search_sync(self, request: MemorySearchRequest) -> tuple[MemoryChannelHit, ...]:
        profile = self._require_vector_profile()
        if request.embedding_profile != profile or request.query_vector is None:
            raise CapabilityNotSupportedError("embedding-profile")
        vector = validate_embedding(request.query_vector, dimension=profile.dimension)
        if not self._vector_complete_sync(request.memories, profile, validate_heads=False):
            raise CapabilityNotSupportedError("vector")
        connection = self._require_connection()
        vector_count = _fetch_int(connection, "SELECT count(*) FROM memory_entry_search_vector")
        if vector_count == 0:
            return ()
        packed = _pack_vector(vector)
        parameters: tuple[object, ...] = (
            packed,
            json.dumps({"k": vector_count}, separators=(",", ":")),
            json.dumps(tuple(memory.artifact_id for memory in request.memories)),
            packed,
            request.candidate_limit,
        )
        rows = connection.execute(
            """
            WITH candidates AS (
                SELECT rowid, embedding
                FROM memory_entry_search_vector(?, ?)
            )
            SELECT heads.memory_artifact_id, heads.head_revision, heads.entry_id,
                   heads.entry_version_id, heads.entry_content_hash, versions.text
            FROM candidates
            JOIN memory_entry_heads AS heads
              ON heads.projection_id = candidates.rowid
            JOIN memory_entry_versions AS versions
              ON versions.entry_version_id = heads.entry_version_id
            JOIN artifact_heads
              ON artifact_heads.artifact_id = heads.memory_artifact_id
             AND artifact_heads.revision = heads.head_revision
            WHERE heads.memory_artifact_id IN (SELECT value FROM json_each(?))
            ORDER BY vec1_l2_distance(?, candidates.embedding),
                     heads.memory_artifact_id,
                     heads.entry_id,
                     heads.entry_version_id
            LIMIT ?
            """,
            parameters,
        )
        return self._validated_channel_hits(rows, request.memories)

    def _commit_sync(self, value: MemoryCommit) -> Memory:
        connection = self._require_connection()
        connection.execute("BEGIN IMMEDIATE")
        try:
            self._validate_commit(value)
            current = self._current_or_none(value.memory.artifact_id)
            self._assert_commit_base(value, current)

            memory = value.memory
            connection.execute(
                """
                INSERT INTO artifact_revisions
                    (artifact_id, revision, family, content, content_hash, lineage)
                VALUES (?, ?, 'memory', ?, ?, ?)
                """,
                (
                    memory.artifact_id,
                    memory.revision,
                    encode_memory_content(memory.content),
                    value.content_hash,
                    encode_lineage(memory.lineage, self._evidence_codec),
                ),
            )
            for version in value.entry_versions:
                source_refs, artifact_refs = encode_entry_refs(version, self._evidence_codec)
                connection.execute(
                    _INSERT_ENTRY_SQL,
                    (
                        version.memory_artifact_id,
                        version.entry_id,
                        version.entry_version_id,
                        version.version,
                        version.previous_version_id,
                        version.kind,
                        version.text,
                        source_refs,
                        artifact_refs,
                        version.entry_content_hash,
                        version.created_in_revision,
                    ),
                )

            if value.base is None:
                connection.execute(
                    "INSERT INTO artifact_heads (artifact_id, revision) VALUES (?, ?)",
                    (memory.artifact_id, memory.revision),
                )
            else:
                connection.execute(
                    """
                    UPDATE artifact_heads
                    SET revision = ?
                    WHERE artifact_id = ? AND revision = ?
                    """,
                    (memory.revision, memory.artifact_id, value.base.revision),
                )
                self._assert_cas_updated(value, connection.changes())

            self._replace_projections(value)
            connection.execute("COMMIT")
        except BaseException:
            connection.execute("ROLLBACK")
            raise
        return value.memory

    def _assert_cas_updated(self, value: MemoryCommit, changed_rows: int) -> None:
        if changed_rows != 1:
            raise RevisionConflictError(value.base, self._current_or_none(value.memory.artifact_id))

    def _validate_projection_vector(
        self,
        embedding: tuple[float, ...] | None,
        stored_hash: str | None,
        version: MemoryEntryVersion,
    ) -> None:
        if embedding is None and stored_hash is None:
            return
        if embedding is None or stored_hash is None:
            raise _SQLiteBackendStateError("commit", "vector and embedding hash must be written together")
        profile = self._require_vector_profile()
        validate_embedding(embedding, dimension=profile.dimension)
        if stored_hash != _embedding_hash(profile, version.entry_content_hash):
            raise _SQLiteBackendStateError("commit", "embedding hash is not bound to the entry content")

    def _replace_projections(self, value: MemoryCommit) -> None:
        connection = self._require_connection()
        projection_ids = tuple(
            int(row[0])
            for row in connection.execute(
                "SELECT projection_id FROM memory_entry_heads WHERE memory_artifact_id = ?",
                (value.memory.artifact_id,),
            )
        )
        for projection_id in projection_ids:
            connection.execute("DELETE FROM memory_entry_search_fts WHERE rowid = ?", (projection_id,))
            if self._embedding_profile is not None:
                connection.execute("DELETE FROM memory_entry_search_vector WHERE rowid = ?", (projection_id,))
        connection.execute(
            "DELETE FROM memory_entry_heads WHERE memory_artifact_id = ?",
            (value.memory.artifact_id,),
        )
        for projection in value.projections:
            version = projection.entry_version
            connection.execute(
                """
                INSERT INTO memory_entry_heads
                    (memory_artifact_id, head_revision, entry_id, entry_version_id,
                     entry_content_hash, searchable_text)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    version.memory_artifact_id,
                    value.memory.revision,
                    version.entry_id,
                    version.entry_version_id,
                    version.entry_content_hash,
                    projection.searchable_text,
                ),
            )
            projection_id = connection.last_insert_rowid()
            connection.execute(
                "INSERT INTO memory_entry_search_fts (rowid, searchable_text) VALUES (?, ?)",
                (projection_id, projection.searchable_text),
            )
            if projection.embedding is not None:
                connection.execute(
                    "INSERT INTO memory_entry_search_vector (rowid, embedding) VALUES (?, ?)",
                    (projection_id, _pack_vector(projection.embedding)),
                )
                connection.execute(
                    """
                    INSERT INTO memory_entry_vector_metadata
                        (projection_id, entry_version_id, entry_content_hash, embedding_content_hash)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        projection_id,
                        version.entry_version_id,
                        version.entry_content_hash,
                        projection.embedding_content_hash,
                    ),
                )

    def _load_entry_version(self, memory_artifact_id: str, entry_version_id: str) -> MemoryEntryVersion:
        row = (
            self
            ._require_connection()
            .execute(
                _SELECT_ENTRY_SQL,
                (memory_artifact_id, entry_version_id),
            )
            .fetchone()
        )
        if row is None:
            raise ArtifactNotFoundError(ArtifactRef(memory_artifact_id, 0))
        return decode_entry_version(
            memory_artifact_id=row[0],
            entry_id=row[1],
            entry_version_id=row[2],
            version=row[3],
            previous_version_id=row[4],
            kind=row[5],
            text=row[6],
            source_refs=row[7],
            artifact_refs=row[8],
            entry_content_hash=row[9],
            created_in_revision=row[10],
            codec=self._evidence_codec,
        )

    def _current_or_none(self, artifact_id: str) -> Memory | None:
        row = (
            self
            ._require_connection()
            .execute(
                "SELECT revision FROM artifact_heads WHERE artifact_id = ?",
                (artifact_id,),
            )
            .fetchone()
        )
        if row is None:
            return None
        return self._get_sync(ArtifactRef(artifact_id, int(row[0])))

    def _require_connection(self) -> apsw.Connection:
        if self._closed:
            raise _SQLiteBackendStateError("closed")
        if self._connection is None:
            raise _SQLiteBackendStateError("not-initialized")
        return self._connection

    def _require_vector_profile(self) -> EmbeddingProfile:
        self._require_connection()
        if self._embedding_profile is None:
            raise CapabilityNotSupportedError("vector")
        return self._embedding_profile

    @staticmethod
    def _json_array(value: str) -> tuple[object, ...]:
        return _json_array(value)

    @staticmethod
    def _stored_int(value: object) -> int:
        return _stored_int(value)

    @staticmethod
    def _stored_text(value: object) -> str:
        return str(value)

    @staticmethod
    def _database_error(code: str, detail: object | None = None) -> MemoryBackendConfigurationError:
        return _SQLiteBackendStateError(code, detail)


def _version_tuple(value: str) -> tuple[int, int, int]:
    try:
        parts = tuple(int(part) for part in value.split(".")[:3])
    except ValueError:
        return (0, 0, 0)
    if len(parts) != 3:
        return (0, 0, 0)
    return parts


def _json_array(value: str) -> tuple[object, ...]:
    decoded = json.loads(value)
    if not isinstance(decoded, list):
        raise _SQLiteBackendStateError("commit", "stored references are not arrays")
    return tuple(decoded)


def _stored_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise _SQLiteBackendStateError("commit", "stored integer column has an invalid type")
    return value


def _pack_vector(vector: Sequence[float]) -> bytes:
    return struct.pack(f"={len(vector)}f", *vector)


def _unpack_vector(value: object, dimension: int) -> tuple[float, ...]:
    if not isinstance(value, bytes | bytearray | memoryview):
        raise _SQLiteBackendStateError("commit", "stored vector has an invalid type")
    packed = bytes(value)
    expected = struct.calcsize(f"={dimension}f")
    if len(packed) != expected:
        raise _SQLiteBackendStateError("commit", "stored vector has the wrong dimension")
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


def _fetch_int(connection: apsw.Connection, statement: str) -> int:
    row = connection.execute(statement).fetchone()
    if row is None:
        raise _SQLiteBackendStateError("commit", "integer query returned no row")
    return _stored_int(row[0])
