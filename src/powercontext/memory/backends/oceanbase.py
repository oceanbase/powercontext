"""Direct PyMySQL adapter for OceanBase 4.3.5 MySQL-mode Memory storage."""

# All interpolated SQL fragments in this module are identifiers validated by
# ``_table_names``. Runtime values always use PyMySQL bindings.
# ruff: noqa: S608

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, fields

import pymysql
from pymysql.connections import Connection

from powercontext.artifacts import ArtifactRef
from powercontext.errors import (
    ArtifactNotFoundError,
    CapabilityNotSupportedError,
    MemoryBackendConfigurationError,
    RevisionConflictError,
)
from powercontext.memory.backends._sql import (
    decode_entry_version,
    encode_entry_refs,
    encode_lineage,
    encode_memory_content,
)
from powercontext.memory.backends.base import DatabaseMemoryBackend
from powercontext.memory.canonical import (
    analyze_text,
    canonical_json,
    embedding_content_hash,
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
    EmbeddingProvider,
    MemoryCommit,
    MemoryEvidenceCodec,
    MemoryProjection,
    MemorySearchChannels,
    MemorySearchRequest,
)

_MINIMUM_OCEANBASE_VERSION = (4, 3, 5, 3)
_PREFIX = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,23}\Z")


class _OceanBaseBackendError(MemoryBackendConfigurationError):
    def __init__(self, code: str, detail: object | None = None) -> None:
        messages = {
            "version": f"OceanBase 4.3.5 BP3 (4.3.5.3) or newer is required, found {detail}",
            "version-format": f"unable to parse OceanBase version: {detail}",
            "mode": f"OceanBase Memory backend requires MySQL mode, found {detail}",
            "prefix": "OceanBase table prefix must be a safe identifier prefix of at most 24 characters",
            "dimension": "OceanBase embedding dimension must be an integer from 1 through 16000",
            "profile": "OceanBase Memory backend requires an L2 embedding profile",
            "not-initialized": "OceanBase Memory backend has not been initialized",
            "closed": "OceanBase Memory backend is closed",
            "schema": f"OceanBase Memory schema does not match configured profile: {detail}",
            "family": "stored Artifact row is not a Memory Artifact",
            "content-hash": "stored Memory content hash does not match its canonical content",
            "manifest": "Memory manifest is not canonical or contains duplicate identities",
            "entry-link": "stored Memory entry does not match its manifest anchor",
            "entry-hash": "stored Memory entry hash does not match its canonical content",
            "commit": f"invalid Memory commit: {detail}",
            "transaction": "OceanBase Memory transaction is already complete",
            "probe": f"OceanBase capability probe failed: {detail}",
            "projection-rebuild": f"OceanBase projection rebuild failed: {detail}",
        }
        super().__init__(messages[code])


@dataclass(frozen=True, slots=True)
class _TableNames:
    schema: str
    artifact_revisions: str
    artifact_heads: str
    entry_versions: str
    entry_heads: str
    ftx_heads: str
    vidx_heads: str
    idx_entry_hash: str
    idx_head_version: str
    fk_artifact_head: str
    fk_entry_previous: str
    fk_entry_revision: str
    fk_head_version: str
    probe: str
    probe_ftx: str
    probe_vidx: str

    @property
    def tables(self) -> tuple[str, ...]:
        return (
            self.schema,
            self.artifact_revisions,
            self.artifact_heads,
            self.entry_versions,
            self.entry_heads,
        )


@dataclass(frozen=True, slots=True)
class _ProjectionRebuildRow:
    memory_ref: ArtifactRef
    entry_version: MemoryEntryVersion
    searchable_text: str


def parse_oceanbase_version(value: str) -> tuple[int, int, int, int]:
    """Extract the four-part OceanBase server version from either banner form."""

    match = re.search(r"(?:OceanBase(?:_CE)?(?:-v|\s))([0-9]+(?:\.[0-9]+){3})", value, re.IGNORECASE)
    if match is None:
        raise _OceanBaseBackendError("version-format", value)
    parts = tuple(int(part) for part in match.group(1).split("."))
    return (parts[0], parts[1], parts[2], parts[3])


def validate_oceanbase_server(version: str, compatibility_mode: str) -> None:
    """Reject unsupported OceanBase releases and non-MySQL tenants."""

    if parse_oceanbase_version(version) < _MINIMUM_OCEANBASE_VERSION:
        raise _OceanBaseBackendError("version", version)
    if compatibility_mode.upper() != "MYSQL":
        raise _OceanBaseBackendError("mode", compatibility_mode)


def oceanbase_schema_statements(prefix: str, profile: EmbeddingProfile) -> tuple[str, ...]:
    """Return the fixed Memory DDL after validating every interpolated literal."""

    names = _table_names(prefix)
    _validate_profile(profile)
    dimension = profile.dimension
    return (
        f"""
        CREATE TABLE IF NOT EXISTS {names.schema} (
            singleton TINYINT NOT NULL,
            schema_version BIGINT NOT NULL,
            embedding_profile_json LONGTEXT CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
            PRIMARY KEY (singleton)
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {names.artifact_revisions} (
            artifact_id VARCHAR(128) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
            revision BIGINT NOT NULL,
            family VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
            content LONGTEXT CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
            content_hash CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
            lineage LONGTEXT CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
            PRIMARY KEY (artifact_id, revision)
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {names.artifact_heads} (
            artifact_id VARCHAR(128) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
            revision BIGINT NOT NULL,
            PRIMARY KEY (artifact_id),
            CONSTRAINT {names.fk_artifact_head}
                FOREIGN KEY (artifact_id, revision)
                REFERENCES {names.artifact_revisions} (artifact_id, revision)
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {names.entry_versions} (
            memory_artifact_id VARCHAR(128) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
            entry_id VARCHAR(128) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
            entry_version_id VARCHAR(128) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
            version BIGINT NOT NULL,
            previous_version_id VARCHAR(128) CHARACTER SET ascii COLLATE ascii_bin,
            kind VARCHAR(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
            text TEXT CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
            source_refs LONGTEXT CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
            artifact_refs LONGTEXT CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
            entry_content_hash CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
            created_in_revision BIGINT NOT NULL,
            PRIMARY KEY (entry_version_id),
            UNIQUE KEY uk_memory_entry_versions_number (memory_artifact_id, entry_id, version),
            UNIQUE KEY uk_memory_entry_versions_identity (memory_artifact_id, entry_id, entry_version_id),
            KEY {names.idx_entry_hash} (memory_artifact_id, entry_content_hash),
            CONSTRAINT {names.fk_entry_previous}
                FOREIGN KEY (previous_version_id) REFERENCES {names.entry_versions} (entry_version_id),
            CONSTRAINT {names.fk_entry_revision}
                FOREIGN KEY (memory_artifact_id, created_in_revision)
                REFERENCES {names.artifact_revisions} (artifact_id, revision)
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {names.entry_heads} (
            projection_id BIGINT NOT NULL AUTO_INCREMENT,
            memory_artifact_id VARCHAR(128) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
            head_revision BIGINT NOT NULL,
            entry_id VARCHAR(128) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
            entry_version_id VARCHAR(128) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
            entry_content_hash CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
            searchable_text TEXT CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
            embedding_content_hash CHAR(64) CHARACTER SET ascii COLLATE ascii_bin,
            embedding VECTOR({dimension}),
            PRIMARY KEY (projection_id),
            UNIQUE KEY uk_memory_entry_heads_entry (memory_artifact_id, entry_id),
            KEY {names.idx_head_version} (entry_version_id),
            FULLTEXT INDEX {names.ftx_heads} (searchable_text) WITH PARSER SPACE,
            VECTOR INDEX {names.vidx_heads} (embedding) WITH (distance=L2, type=hnsw),
            CONSTRAINT {names.fk_head_version}
                FOREIGN KEY (memory_artifact_id, entry_id, entry_version_id)
                REFERENCES {names.entry_versions} (memory_artifact_id, entry_id, entry_version_id)
        )
        """,
    )


class OceanBaseMemoryBackend(DatabaseMemoryBackend):
    """Persist Memory history and current FULLTEXT/HNSW projections in OceanBase."""

    def __init__(
        self,
        *,
        host: str,
        port: int,
        user: str,
        password: str,
        database: str,
        embedding_profile: EmbeddingProfile,
        table_prefix: str = "",
        evidence_codec: MemoryEvidenceCodec | None = None,
        connect_timeout: int = 10,
    ) -> None:
        super().__init__(evidence_codec=evidence_codec)
        _validate_profile(embedding_profile)
        self._host = host
        self._port = port
        self._user = user
        self._password = password
        self._database = database
        self._profile = embedding_profile
        self._names = _table_names(table_prefix)
        self._connect_timeout = connect_timeout
        self._connection: Connection | None = None

    @property
    def table_names(self) -> tuple[str, ...]:
        """Return the exact schema objects owned by this adapter."""

        return self._names.tables

    async def initialize(self) -> None:
        """Connect, probe the live tenant, and install the fixed schema."""

        async with self._lock:
            if self._closed:
                raise _OceanBaseBackendError("closed")
            if self._connection is not None:
                return
            await asyncio.to_thread(self._initialize_sync)

    async def close(self) -> None:
        """Close the serialized PyMySQL connection."""

        async with self._lock:
            connection = self._connection
            self._connection = None
            self._closed = True
            if connection is not None:
                await asyncio.to_thread(connection.close)

    async def drop_schema(self) -> None:
        """Drop only the exact adapter-owned tables, in dependency order."""

        async with self._lock:
            await asyncio.to_thread(self._drop_schema_sync)

    def _capabilities_sync(self) -> MemoryCapabilities:
        self._require_connection()
        return MemoryCapabilities(fts=True, vector=True, hybrid=True, embedding_profile=self._profile)

    async def rebuild_projections(self, provider: EmbeddingProvider | None = None, /) -> None:
        """Offline-rebuild FULLTEXT and optional vectors from authoritative heads."""

        async with self._lock:
            rows = await asyncio.to_thread(self._authoritative_projection_rows)
            vectors: tuple[tuple[float, ...] | None, ...] = tuple(None for _ in rows)
            if provider is not None:
                if provider.profile != self._profile:
                    raise CapabilityNotSupportedError("embedding-profile")
                provided = await provider.embed(tuple(row.entry_version.text for row in rows))
                if len(provided) != len(rows):
                    raise _OceanBaseBackendError("projection-rebuild", "provider returned the wrong vector count")
                vectors = tuple(validate_embedding(vector, dimension=self._profile.dimension) for vector in provided)
            await asyncio.to_thread(self._rebuild_projections_sync, rows, vectors)

    async def rebuild_vectors(self, provider: EmbeddingProvider, /) -> None:
        """Offline-rebuild all projections with complete fixed-profile vectors."""

        await self.rebuild_projections(provider)

    def _connect(self) -> Connection:
        return pymysql.connect(
            host=self._host,
            port=self._port,
            user=self._user,
            password=self._password,
            database=self._database,
            charset="utf8mb4",
            autocommit=True,
            connect_timeout=self._connect_timeout,
        )

    def _initialize_sync(self) -> None:
        connection = self._connect()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SET SESSION TRANSACTION ISOLATION LEVEL READ COMMITTED")
            self._probe_server(connection)
            self._probe_search_capabilities(connection)
            with connection.cursor() as cursor:
                for statement in oceanbase_schema_statements(self._prefix(), self._profile):
                    cursor.execute(statement)
                profile_json = _profile_json(self._profile)
                cursor.execute(
                    f"""
                    INSERT IGNORE INTO {self._names.schema}
                        (singleton, schema_version, embedding_profile_json)
                    VALUES (1, 1, %s)
                    """,
                    (profile_json,),
                )
                cursor.execute(
                    f"SELECT schema_version, embedding_profile_json FROM {self._names.schema} WHERE singleton = 1"
                )
                row = cursor.fetchone()
            self._validate_schema_row(row, profile_json)
        except BaseException:
            connection.close()
            raise
        self._connection = connection

    @staticmethod
    def _validate_schema_row(row: tuple[object, ...] | None, profile_json: str) -> None:
        if row != (1, profile_json):
            raise _OceanBaseBackendError("schema", row)

    def _probe_server(self, connection: Connection) -> None:
        with connection.cursor() as cursor:
            cursor.execute("SELECT VERSION()")
            version_row = cursor.fetchone()
            cursor.execute("SHOW VARIABLES LIKE 'ob_compatibility_mode'")
            mode_row = cursor.fetchone()
            cursor.execute("SHOW PARAMETERS LIKE 'ob_vector_memory_limit_percentage'")
            vector_parameters = cursor.fetchall()
        if version_row is None or mode_row is None:
            raise _OceanBaseBackendError("probe", "server identity query returned no rows")
        validate_oceanbase_server(str(version_row[0]), str(mode_row[1]))
        if not vector_parameters:
            raise CapabilityNotSupportedError("vector", "OceanBase vector memory parameter is unavailable")

    def _probe_search_capabilities(self, connection: Connection) -> None:
        names = self._names
        self._drop_probe(connection)
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT TOKENIZE(%s, 'space')", ("alpha beta",))
                token_row = cursor.fetchone()
                cursor.execute(
                    f"""
                    CREATE TABLE {names.probe} (
                        id BIGINT NOT NULL AUTO_INCREMENT,
                        searchable_text TEXT CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
                        embedding VECTOR({self._profile.dimension}),
                        PRIMARY KEY (id),
                        FULLTEXT INDEX {names.probe_ftx} (searchable_text) WITH PARSER SPACE,
                        VECTOR INDEX {names.probe_vidx} (embedding) WITH (distance=L2, type=hnsw)
                    )
                    """
                )
                cursor.execute(
                    f"INSERT INTO {names.probe} (searchable_text, embedding) VALUES (%s, %s)",
                    (
                        "alpha beta",
                        "[1,0,0]" if self._profile.dimension == 3 else _zero_vector(self._profile.dimension),
                    ),
                )
                cursor.execute(
                    f"""
                    SELECT id FROM {names.probe}
                    WHERE MATCH(searchable_text) AGAINST (%s)
                    """,
                    ("alpha",),
                )
                fts_row = cursor.fetchone()
                vector = "[1,0,0]" if self._profile.dimension == 3 else _zero_vector(self._profile.dimension)
                cursor.execute(
                    f"""
                    SELECT id FROM {names.probe}
                    ORDER BY L2_DISTANCE(embedding, %s) APPROXIMATE LIMIT 1
                    """,
                    (vector,),
                )
                vector_row = cursor.fetchone()
            if token_row is None or fts_row is None or vector_row is None:
                raise _OceanBaseBackendError("probe", "search probe returned no match")
        except pymysql.MySQLError as error:
            raise _OceanBaseBackendError("probe", error.args[0]) from error
        finally:
            self._drop_probe(connection)

    def _drop_probe(self, connection: Connection) -> None:
        with connection.cursor() as cursor:
            cursor.execute(f"DROP TABLE IF EXISTS {self._names.probe}")

    def _drop_schema_sync(self) -> None:
        connection = self._connection if self._connection is not None else self._connect()
        owns_connection = connection is not self._connection
        try:
            with connection.cursor() as cursor:
                cursor.execute(f"DROP TABLE IF EXISTS {self._names.entry_heads}")
                cursor.execute(f"DROP TABLE IF EXISTS {self._names.entry_versions}")
                cursor.execute(f"DROP TABLE IF EXISTS {self._names.artifact_heads}")
                cursor.execute(f"DROP TABLE IF EXISTS {self._names.artifact_revisions}")
                cursor.execute(f"DROP TABLE IF EXISTS {self._names.schema}")
                cursor.execute(f"DROP TABLE IF EXISTS {self._names.probe}")
        finally:
            if owns_connection:
                connection.close()

    def _get_sync(self, memory: ArtifactRef) -> Memory:
        row = self._fetchone(
            f"""
            SELECT artifact_id, revision, family, content, content_hash, lineage
            FROM {self._names.artifact_revisions}
            WHERE artifact_id = %s AND revision = %s
            """,
            (memory.artifact_id, memory.revision),
        )
        if row is None:
            raise ArtifactNotFoundError(memory)
        return self._decode_memory_row(row)

    def _projections_sync(self, memory: ArtifactRef) -> tuple[MemoryProjection, ...]:
        revision = self._get_sync(memory)
        rows = self._fetchall(
            f"""
            SELECT entry_version_id, searchable_text, embedding_content_hash, embedding
            FROM {self._names.entry_heads}
            WHERE memory_artifact_id = %s AND head_revision = %s
            ORDER BY entry_id
            """,
            (memory.artifact_id, memory.revision),
        )
        projections: list[MemoryProjection] = []
        for entry_version_id, searchable_text, embedding_hash, packed in rows:
            version = self._load_entry_version(memory.artifact_id, str(entry_version_id))
            embedding = None
            stored_hash = None if embedding_hash is None else str(embedding_hash)
            if packed is not None and stored_hash is not None:
                embedding = _parse_vector(packed, self._profile.dimension)
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
            raise _OceanBaseBackendError("commit", "active projections do not match the manifest")
        return tuple(projections)

    def _latest_sync(self, artifact_id: str) -> Memory:
        row = self._fetchone(
            f"""
            SELECT revisions.artifact_id, revisions.revision, revisions.family,
                   revisions.content, revisions.content_hash, revisions.lineage
            FROM {self._names.artifact_heads} AS heads
            JOIN {self._names.artifact_revisions} AS revisions
              ON revisions.artifact_id = heads.artifact_id AND revisions.revision = heads.revision
            WHERE heads.artifact_id = %s
            """,
            (artifact_id,),
        )
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
        rows = self._fetchall(
            f"""
            SELECT artifact_id, revision, family, content, content_hash, lineage
            FROM {self._names.artifact_revisions}
            WHERE artifact_id = %s AND revision > %s AND revision <= %s
            ORDER BY revision
            """,
            (target.artifact_id, lower, target.revision),
        )
        revisions = tuple(self._decode_memory_row(row) for row in rows)
        return tuple(
            MemoryRevisionChanges(memory_ref=revision.ref, changes=revision.content.changes) for revision in revisions
        )

    def _commit_sync(self, value: MemoryCommit) -> Memory:
        connection = self._require_connection()
        connection.begin()
        try:
            self._validate_commit(value)
            current = self._current_or_none(value.memory.artifact_id, for_update=True)
            self._assert_commit_base(value, current)
            memory = value.memory
            with connection.cursor() as cursor:
                cursor.execute(
                    f"""
                    INSERT INTO {self._names.artifact_revisions}
                        (artifact_id, revision, family, content, content_hash, lineage)
                    VALUES (%s, %s, 'memory', %s, %s, %s)
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
                    cursor.execute(
                        f"""
                        INSERT INTO {self._names.entry_versions}
                            (memory_artifact_id, entry_id, entry_version_id, version, previous_version_id,
                             kind, text, source_refs, artifact_refs, entry_content_hash,
                             created_in_revision)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
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
                    cursor.execute(
                        f"INSERT INTO {self._names.artifact_heads} (artifact_id, revision) VALUES (%s, %s)",
                        (memory.artifact_id, memory.revision),
                    )
                else:
                    cursor.execute(
                        f"""
                        UPDATE {self._names.artifact_heads}
                        SET revision = %s WHERE artifact_id = %s AND revision = %s
                        """,
                        (memory.revision, memory.artifact_id, value.base.revision),
                    )
                    self._assert_cas_updated(value, current, cursor.rowcount)
                cursor.execute(
                    f"DELETE FROM {self._names.entry_heads} WHERE memory_artifact_id = %s",
                    (memory.artifact_id,),
                )
                for projection in value.projections:
                    version = projection.entry_version
                    vector = None if projection.embedding is None else _vector_json(projection.embedding)
                    cursor.execute(
                        f"""
                        INSERT INTO {self._names.entry_heads}
                            (memory_artifact_id, head_revision, entry_id, entry_version_id,
                             entry_content_hash, searchable_text, embedding_content_hash, embedding)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            version.memory_artifact_id,
                            memory.revision,
                            version.entry_id,
                            version.entry_version_id,
                            version.entry_content_hash,
                            projection.searchable_text,
                            projection.embedding_content_hash,
                            vector,
                        ),
                    )
            connection.commit()
        except pymysql.IntegrityError as error:
            connection.rollback()
            self._raise_integrity_error(value, error)
        except BaseException:
            connection.rollback()
            raise
        return value.memory

    def _authoritative_projection_rows(self, *, for_update: bool = False) -> tuple[_ProjectionRebuildRow, ...]:
        suffix = " FOR UPDATE" if for_update else ""
        heads = self._fetchall(
            f"SELECT artifact_id, revision FROM {self._names.artifact_heads} ORDER BY artifact_id{suffix}"
        )
        rows: list[_ProjectionRebuildRow] = []
        for artifact_id, revision in heads:
            memory_ref = ArtifactRef(_stored_text(artifact_id), _stored_int(revision))
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
        connection.begin()
        try:
            self._assert_projection_rebuild_snapshot(rows)
            with connection.cursor() as cursor:
                cursor.execute(f"DELETE FROM {self._names.entry_heads}")
                for row, vector in zip(rows, vectors, strict=True):
                    version = row.entry_version
                    embedding_hash = None
                    vector_json = None
                    if vector is not None:
                        embedding_hash = _embedding_hash(self._profile, version.entry_content_hash)
                        vector_json = _vector_json(vector)
                    cursor.execute(
                        f"""
                        INSERT INTO {self._names.entry_heads}
                            (memory_artifact_id, head_revision, entry_id, entry_version_id,
                             entry_content_hash, searchable_text, embedding_content_hash, embedding)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            row.memory_ref.artifact_id,
                            row.memory_ref.revision,
                            version.entry_id,
                            version.entry_version_id,
                            version.entry_content_hash,
                            row.searchable_text,
                            embedding_hash,
                            vector_json,
                        ),
                    )
            connection.commit()
        except BaseException:
            connection.rollback()
            raise

    def _assert_projection_rebuild_snapshot(self, rows: tuple[_ProjectionRebuildRow, ...]) -> None:
        if self._authoritative_projection_rows(for_update=True) != rows:
            raise _OceanBaseBackendError(
                "projection-rebuild",
                "authoritative heads changed while embeddings were generated",
            )

    def _raise_integrity_error(self, value: MemoryCommit, error: pymysql.IntegrityError) -> None:
        current = self._current_or_none(value.memory.artifact_id)
        if current != value.base or self._revision_exists(value.memory.ref):
            raise RevisionConflictError(value.base if value.base is not None else value.memory, current) from error
        raise _OceanBaseBackendError("commit", f"integrity constraint {error.args[0]}") from error

    def _revision_exists(self, memory: ArtifactRef) -> bool:
        row = self._fetchone(
            f"""
            SELECT 1 FROM {self._names.artifact_revisions}
            WHERE artifact_id = %s AND revision = %s
            """,
            (memory.artifact_id, memory.revision),
        )
        return row is not None

    @staticmethod
    def _assert_cas_updated(value: MemoryCommit, current: Memory | None, changed_rows: int) -> None:
        if changed_rows != 1:
            raise RevisionConflictError(value.base, current)

    def _validate_projection_vector(
        self,
        embedding: tuple[float, ...] | None,
        stored_hash: str | None,
        version: MemoryEntryVersion,
    ) -> None:
        if embedding is None and stored_hash is None:
            return
        if embedding is None or stored_hash is None:
            raise _OceanBaseBackendError("commit", "vector and embedding hash must be written together")
        validate_embedding(embedding, dimension=self._profile.dimension)
        if stored_hash != _embedding_hash(self._profile, version.entry_content_hash):
            raise _OceanBaseBackendError("commit", "embedding hash is not bound to entry content")

    def _vector_complete_sync(
        self,
        memories: tuple[ArtifactRef, ...],
        profile: EmbeddingProfile,
        *,
        validate_heads: bool = True,
    ) -> bool:
        if profile != self._profile:
            return False
        if validate_heads:
            self._assert_current_refs(memories)
        for memory_ref in memories:
            memory = self._get_sync(memory_ref)
            active = {item.entry_id: item for item in memory.content.manifest.entries if item.state == "active"}
            rows = self._fetchall(
                f"""
                SELECT entry_id, entry_version_id, entry_content_hash, embedding_content_hash,
                       embedding IS NOT NULL
                FROM {self._names.entry_heads}
                WHERE memory_artifact_id = %s AND head_revision = %s
                """,
                (memory_ref.artifact_id, memory_ref.revision),
            )
            seen: set[str] = set()
            for entry_id, version_id, entry_hash, stored_hash, has_vector in rows:
                item = active.get(str(entry_id))
                if (
                    item is None
                    or item.entry_version_id != version_id
                    or item.entry_content_hash != entry_hash
                    or stored_hash != _embedding_hash(profile, str(entry_hash))
                    or not has_vector
                ):
                    return False
                seen.add(str(entry_id))
            if seen != set(active):
                return False
        return True

    def _search_sync(self, request: MemorySearchRequest) -> MemorySearchChannels:
        connection = self._require_connection()
        with connection.cursor() as cursor:
            cursor.execute("SET SESSION TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        try:
            connection.begin()
            try:
                self._assert_current_refs(request.memories)
                fts = self._fts_search_sync(request) if request.mode in {"fts", "hybrid"} else ()
                vector = self._vector_search_sync(request) if request.mode in {"vector", "hybrid"} else ()
                result = MemorySearchChannels(fts=fts, vector=vector)
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        finally:
            with connection.cursor() as cursor:
                cursor.execute("SET SESSION TRANSACTION ISOLATION LEVEL READ COMMITTED")
        return result

    def _fts_search_sync(self, request: MemorySearchRequest) -> tuple[MemoryChannelHit, ...]:
        analyzed = analyze_text(request.query)
        if not analyzed:
            return ()
        placeholders = ", ".join("%s" for _ in request.memories)
        rows = self._fetchall(
            f"""
            SELECT heads.memory_artifact_id, heads.head_revision, heads.entry_id,
                   heads.entry_version_id, heads.entry_content_hash, versions.text,
                   MATCH(heads.searchable_text) AGAINST (%s) AS score
            FROM {self._names.entry_heads} AS heads
            JOIN {self._names.entry_versions} AS versions
              ON versions.entry_version_id = heads.entry_version_id
            JOIN {self._names.artifact_heads} AS artifact_heads
              ON artifact_heads.artifact_id = heads.memory_artifact_id
             AND artifact_heads.revision = heads.head_revision
            WHERE MATCH(heads.searchable_text) AGAINST (%s)
              AND heads.memory_artifact_id IN ({placeholders})
            ORDER BY score DESC, heads.memory_artifact_id, heads.entry_id, heads.entry_version_id
            LIMIT %s
            """,
            (
                analyzed,
                analyzed,
                *(memory.artifact_id for memory in request.memories),
                request.candidate_limit,
            ),
        )
        return self._validated_channel_hits(rows, request.memories)

    def _vector_search_sync(self, request: MemorySearchRequest) -> tuple[MemoryChannelHit, ...]:
        if request.embedding_profile != self._profile or request.query_vector is None:
            raise CapabilityNotSupportedError("embedding-profile")
        vector = validate_embedding(request.query_vector, dimension=self._profile.dimension)
        if not self._vector_complete_sync(request.memories, self._profile, validate_heads=False):
            raise CapabilityNotSupportedError("vector")
        placeholders = ", ".join("%s" for _ in request.memories)
        vector_json = _vector_json(vector)
        rows = self._fetchall(
            f"""
            SELECT heads.memory_artifact_id, heads.head_revision, heads.entry_id,
                   heads.entry_version_id, heads.entry_content_hash, versions.text,
                   L2_DISTANCE(heads.embedding, %s) AS distance
            FROM {self._names.entry_heads} AS heads
            JOIN {self._names.entry_versions} AS versions
              ON versions.entry_version_id = heads.entry_version_id
            JOIN {self._names.artifact_heads} AS artifact_heads
              ON artifact_heads.artifact_id = heads.memory_artifact_id
             AND artifact_heads.revision = heads.head_revision
            WHERE heads.embedding IS NOT NULL
              AND heads.memory_artifact_id IN ({placeholders})
            ORDER BY L2_DISTANCE(heads.embedding, %s) APPROXIMATE LIMIT %s
            """,
            (
                vector_json,
                *(memory.artifact_id for memory in request.memories),
                vector_json,
                request.candidate_limit,
            ),
        )
        ordered = sorted(rows, key=lambda row: (float(row[6]), str(row[0]), str(row[2]), str(row[3])))
        return self._validated_channel_hits(ordered, request.memories)

    def _load_entry_version(self, memory_artifact_id: str, entry_version_id: str) -> MemoryEntryVersion:
        row = self._fetchone(
            f"""
            SELECT memory_artifact_id, entry_id, entry_version_id, version, previous_version_id,
                   kind, text, source_refs, artifact_refs, entry_content_hash,
                   created_in_revision
            FROM {self._names.entry_versions}
            WHERE memory_artifact_id = %s AND entry_version_id = %s
            """,
            (memory_artifact_id, entry_version_id),
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

    def _current_or_none(self, artifact_id: str, *, for_update: bool = False) -> Memory | None:
        suffix = " FOR UPDATE" if for_update else ""
        row = self._fetchone(
            f"SELECT revision FROM {self._names.artifact_heads} WHERE artifact_id = %s{suffix}",
            (artifact_id,),
        )
        if row is None:
            return None
        return self._get_sync(ArtifactRef(artifact_id, _stored_int(row[0])))

    def _fetchone(self, statement: str, bindings: Sequence[object] = ()) -> tuple[object, ...] | None:
        with self._require_connection().cursor() as cursor:
            cursor.execute(statement, bindings)
            row = cursor.fetchone()
        return row

    def _fetchall(self, statement: str, bindings: Sequence[object] = ()) -> tuple[tuple[object, ...], ...]:
        with self._require_connection().cursor() as cursor:
            cursor.execute(statement, bindings)
            rows = cursor.fetchall()
        return rows

    def _require_connection(self) -> Connection:
        if self._closed:
            raise _OceanBaseBackendError("closed")
        if self._connection is None:
            raise _OceanBaseBackendError("not-initialized")
        self._connection.ping(reconnect=False)
        return self._connection

    def _prefix(self) -> str:
        return self._names.schema.removesuffix("powercontext_schema")

    @staticmethod
    def _json_array(value: str) -> tuple[object, ...]:
        return _json_array(value)

    @staticmethod
    def _stored_int(value: object) -> int:
        return _stored_int(value)

    @staticmethod
    def _stored_text(value: object) -> str:
        return _stored_text(value)

    @staticmethod
    def _database_error(code: str, detail: object | None = None) -> MemoryBackendConfigurationError:
        return _OceanBaseBackendError(code, detail)


def _table_names(prefix: str) -> _TableNames:
    if prefix and _PREFIX.fullmatch(prefix) is None:
        raise _OceanBaseBackendError("prefix")
    names = _TableNames(
        schema=f"{prefix}powercontext_schema",
        artifact_revisions=f"{prefix}artifact_revisions",
        artifact_heads=f"{prefix}artifact_heads",
        entry_versions=f"{prefix}memory_entry_versions",
        entry_heads=f"{prefix}memory_entry_heads",
        ftx_heads=f"{prefix}ftx_memory_entry_heads_text",
        vidx_heads=f"{prefix}vidx_memory_entry_heads_embedding",
        idx_entry_hash=f"{prefix}idx_memory_entry_versions_hash",
        idx_head_version=f"{prefix}idx_memory_entry_heads_version",
        fk_artifact_head=f"{prefix}fk_artifact_heads_revision",
        fk_entry_previous=f"{prefix}fk_memory_entry_previous",
        fk_entry_revision=f"{prefix}fk_memory_entry_revision",
        fk_head_version=f"{prefix}fk_memory_head_version",
        probe=f"{prefix}probe_search",
        probe_ftx=f"{prefix}probe_ftx",
        probe_vidx=f"{prefix}probe_vidx",
    )
    if any(len(getattr(names, field.name)) > 64 for field in fields(names)):
        raise _OceanBaseBackendError("prefix")
    return names


def _validate_profile(profile: EmbeddingProfile) -> None:
    if (
        isinstance(profile.dimension, bool)
        or not isinstance(profile.dimension, int)
        or not 1 <= profile.dimension <= 16000
    ):
        raise _OceanBaseBackendError("dimension")
    if profile.distance != "l2":
        raise _OceanBaseBackendError("profile")


def _profile_json(profile: EmbeddingProfile) -> str:
    return canonical_json({
        "profile_id": profile.profile_id,
        "model": profile.model,
        "dimension": profile.dimension,
        "distance": profile.distance,
        "normalization": profile.normalization,
    }).decode("utf-8")


def _embedding_hash(profile: EmbeddingProfile, entry_hash: str) -> str:
    return embedding_content_hash(
        profile_id=profile.profile_id,
        model=profile.model,
        dimension=profile.dimension,
        distance=profile.distance,
        normalization=profile.normalization,
        entry_content_hash=entry_hash,
    )


def _vector_json(vector: Sequence[float]) -> str:
    return json.dumps(tuple(vector), ensure_ascii=True, allow_nan=False, separators=(",", ":"))


def _parse_vector(value: object, dimension: int) -> tuple[float, ...]:
    if isinstance(value, bytes | bytearray):
        decoded: object = json.loads(value.decode("utf-8"))
    elif isinstance(value, str):
        decoded = json.loads(value)
    elif isinstance(value, list | tuple):
        decoded = value
    else:
        raise _OceanBaseBackendError("commit", "stored vector has an invalid type")
    if not isinstance(decoded, list | tuple) or len(decoded) != dimension:
        raise _OceanBaseBackendError("commit", "stored vector has the wrong dimension")
    components: list[float] = []
    for item in decoded:
        if isinstance(item, bool) or not isinstance(item, int | float):
            raise _OceanBaseBackendError("commit", "stored vector has an invalid type")
        components.append(float(item))
    return validate_embedding(tuple(components), dimension=dimension)


def _zero_vector(dimension: int) -> str:
    return _vector_json((0.0,) * dimension)


def _json_array(value: str) -> tuple[object, ...]:
    decoded = json.loads(value)
    if not isinstance(decoded, list):
        raise _OceanBaseBackendError("commit", "stored references are not arrays")
    return tuple(decoded)


def _stored_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise _OceanBaseBackendError("commit", "stored integer column has an invalid type")
    return value


def _stored_text(value: object) -> str:
    if not isinstance(value, str):
        raise _OceanBaseBackendError("commit", "stored text column has an invalid type")
    return value
