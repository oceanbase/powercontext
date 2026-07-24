"""APSW-backed Content Source catalog, journal, and Trigger cursor storage."""

from __future__ import annotations

import asyncio
from pathlib import Path
from threading import RLock

import apsw
from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError

from powercontext._sqlite import sqlite_write_lock
from powercontext.artifacts import ArtifactRef
from powercontext.errors import SourceConflictError, SourceNotFoundError
from powercontext.sources import (
    CONTENT_SOURCE_NAME,
    ContentSource,
    Source,
    SourceCatalogBackend,
    SourceMaterialization,
    SourceStore,
)
from powercontext.sources.journal import (
    SourceCursor,
    SourceJournal,
    SourceRecord,
    TriggerCursorStore,
    validate_scope_id,
)

_CONTENT_SOURCE = TypeAdapter(ContentSource)
_ARTIFACT_REF = TypeAdapter(ArtifactRef)
_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS runtime_sources (
        scope_id TEXT NOT NULL,
        sequence INTEGER NOT NULL CHECK (sequence > 0),
        adapter_name TEXT NOT NULL,
        source_name TEXT NOT NULL,
        payload TEXT NOT NULL,
        PRIMARY KEY (scope_id, sequence),
        UNIQUE (scope_id, adapter_name, source_name)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_runtime_sources_scope_sequence
    ON runtime_sources (scope_id, sequence)
    """,
    """
    CREATE TABLE IF NOT EXISTS runtime_trigger_cursors (
        scope_id TEXT NOT NULL,
        trigger_name TEXT NOT NULL,
        sequence INTEGER NOT NULL CHECK (sequence >= 0),
        PRIMARY KEY (scope_id, trigger_name)
    )
    """,
)


class _SQLiteSourceStateError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("SQLite Source backend is not initialized")


class _UnsupportedSQLiteSourceError(TypeError):
    def __init__(self, code: str, detail: str | None = None) -> None:
        messages = {
            "source": "SQLite Source backend only supports ContentSource values",
            "evidence": "SQLite Source evidence only supports ContentSource values",
            "source-ref": "Source reference must be an object",
            "artifact-ref": "Artifact reference must be an object",
            "artifact-fields": "Artifact reference must contain artifact_id and revision",
            "adapter": f"unsupported stored Source adapter name: {detail}",
            "payload": "stored Content Source payload is invalid",
        }
        super().__init__(messages[code])


class _SourceEvidenceRef(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    scope_id: str
    name: str
    source_name: str


class _ArtifactEvidenceRef(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    artifact_id: str
    revision: int


class _InvalidSQLiteSourceValueError(ValueError):
    def __init__(self, code: str) -> None:
        messages = {
            "window": "invalid Source journal window",
            "negative-cursor": "Source cursor must not be negative",
            "backward-cursor": "Source cursor must not move backwards",
        }
        super().__init__(messages[code])


class SQLiteSourceBackend:
    """Own Source runtime tables and produce isolated scoped catalog views."""

    def __init__(self, database: str | Path) -> None:
        self._database = str(database)
        self._connection: apsw.Connection | None = None
        self._lock = RLock()
        self._write_lock = sqlite_write_lock(database)

    async def initialize(self) -> None:
        await asyncio.to_thread(self._initialize_sync)

    async def close(self) -> None:
        await asyncio.to_thread(self._close_sync)

    def for_scope(self, scope_id: str) -> SQLiteScopedSourceBackend:
        return SQLiteScopedSourceBackend(self, validate_scope_id(scope_id))

    async def pending_scopes(self, trigger_name: str, /) -> tuple[str, ...]:
        return await asyncio.to_thread(self._pending_scopes_sync, trigger_name)

    def _initialize_sync(self) -> None:
        with self._lock:
            if self._connection is not None:
                return
            with self._write_lock:
                connection = apsw.Connection(self._database)
                connection.set_busy_timeout(30_000)
                connection.transaction_mode = "IMMEDIATE"
                connection.execute("PRAGMA foreign_keys = ON")
                if self._database != ":memory:":
                    connection.execute("PRAGMA journal_mode = WAL")
                for statement in _SCHEMA:
                    connection.execute(statement)
                self._connection = connection

    def _close_sync(self) -> None:
        with self._lock:
            if self._connection is None:
                return
            self._connection.close()
            self._connection = None

    def _connection_sync(self) -> apsw.Connection:
        connection = self._connection
        if connection is None:
            raise _SQLiteSourceStateError
        return connection

    def _add_sync(self, scope_id: str, source: Source) -> Source:
        if type(source) is not ContentSource:
            raise _UnsupportedSQLiteSourceError("source")
        payload = _encode_content_source(source)
        with self._lock:
            connection = self._connection_sync()
            with self._write_lock, connection:
                row = connection.execute(
                    """
                        SELECT payload
                        FROM runtime_sources
                        WHERE scope_id = ? AND adapter_name = ? AND source_name = ?
                        """,
                    (scope_id, CONTENT_SOURCE_NAME, source.name),
                ).fetchone()
                if row is not None:
                    stored = _decode_content_source(str(row[0]))
                    if stored == source:
                        return source
                    raise SourceConflictError("identity", (scope_id, CONTENT_SOURCE_NAME, source.name))
                sequence_row = connection.execute(
                    "SELECT COALESCE(MAX(sequence), 0) + 1 FROM runtime_sources WHERE scope_id = ?",
                    (scope_id,),
                ).fetchone()
                sequence = 1 if sequence_row is None else int(sequence_row[0])
                connection.execute(
                    """
                        INSERT INTO runtime_sources (
                            scope_id, sequence, adapter_name, source_name, payload
                        ) VALUES (?, ?, ?, ?, ?)
                        """,
                    (scope_id, sequence, CONTENT_SOURCE_NAME, source.name, payload),
                )
        return source

    def _get_sync(self, scope_id: str, source: Source) -> Source:
        if type(source) is not ContentSource:
            raise SourceNotFoundError(source)
        stored = self._find_sync(scope_id, CONTENT_SOURCE_NAME, source.name)
        if stored is None or stored != source:
            raise SourceNotFoundError(source)
        return stored

    def _list_sync(self, scope_id: str) -> tuple[Source, ...]:
        with self._lock:
            rows = (
                self
                ._connection_sync()
                .execute(
                    """
                SELECT adapter_name, payload
                FROM runtime_sources
                WHERE scope_id = ?
                ORDER BY sequence
                """,
                    (scope_id,),
                )
                .fetchall()
            )
        return tuple(_decode_source(str(row[0]), str(row[1])) for row in rows)

    def _position_sync(self, scope_id: str, source: Source) -> int:
        if type(source) is not ContentSource:
            raise SourceNotFoundError(source)
        with self._lock:
            row = (
                self
                ._connection_sync()
                .execute(
                    """
                SELECT sequence, payload
                FROM runtime_sources
                WHERE scope_id = ? AND adapter_name = ? AND source_name = ?
                """,
                    (scope_id, CONTENT_SOURCE_NAME, source.name),
                )
                .fetchone()
            )
        if row is None or _decode_content_source(str(row[1])) != source:
            raise SourceNotFoundError(source)
        return int(row[0])

    def _high_watermark_sync(self, scope_id: str) -> int:
        with self._lock:
            row = (
                self
                ._connection_sync()
                .execute(
                    "SELECT COALESCE(MAX(sequence), 0) FROM runtime_sources WHERE scope_id = ?",
                    (scope_id,),
                )
                .fetchone()
            )
        return 0 if row is None else int(row[0])

    def _list_between_sync(self, scope_id: str, after: int, through: int) -> tuple[SourceRecord, ...]:
        if after < 0 or through < after:
            raise _InvalidSQLiteSourceValueError("window")
        with self._lock:
            rows = (
                self
                ._connection_sync()
                .execute(
                    """
                SELECT sequence, adapter_name, payload
                FROM runtime_sources
                WHERE scope_id = ? AND sequence > ? AND sequence <= ?
                ORDER BY sequence
                """,
                    (scope_id, after, through),
                )
                .fetchall()
            )
        return tuple(
            SourceRecord(sequence=int(row[0]), source=_decode_source(str(row[1]), str(row[2]))) for row in rows
        )

    def _load_cursor_sync(self, scope_id: str, trigger_name: str) -> SourceCursor:
        with self._lock:
            row = (
                self
                ._connection_sync()
                .execute(
                    """
                SELECT sequence
                FROM runtime_trigger_cursors
                WHERE scope_id = ? AND trigger_name = ?
                """,
                    (scope_id, trigger_name),
                )
                .fetchone()
            )
        return SourceCursor() if row is None else SourceCursor(int(row[0]))

    def _save_cursor_sync(self, scope_id: str, trigger_name: str, cursor: SourceCursor) -> None:
        if cursor.sequence < 0:
            raise _InvalidSQLiteSourceValueError("negative-cursor")
        with self._lock:
            connection = self._connection_sync()
            with self._write_lock, connection:
                row = connection.execute(
                    """
                        INSERT INTO runtime_trigger_cursors (scope_id, trigger_name, sequence)
                        VALUES (?, ?, ?)
                        ON CONFLICT (scope_id, trigger_name)
                        DO UPDATE SET sequence = excluded.sequence
                        WHERE excluded.sequence >= runtime_trigger_cursors.sequence
                        RETURNING sequence
                        """,
                    (scope_id, trigger_name, cursor.sequence),
                ).fetchone()
                if row is None:
                    raise _InvalidSQLiteSourceValueError("backward-cursor")

    def _pending_scopes_sync(self, trigger_name: str) -> tuple[str, ...]:
        with self._lock:
            rows = (
                self
                ._connection_sync()
                .execute(
                    """
                SELECT sources.scope_id
                FROM (
                    SELECT scope_id, MAX(sequence) AS high_watermark
                    FROM runtime_sources
                    GROUP BY scope_id
                ) AS sources
                LEFT JOIN runtime_trigger_cursors AS cursors
                  ON cursors.scope_id = sources.scope_id
                 AND cursors.trigger_name = ?
                WHERE sources.high_watermark > COALESCE(cursors.sequence, 0)
                ORDER BY sources.scope_id
                """,
                    (trigger_name,),
                )
                .fetchall()
            )
        return tuple(str(row[0]) for row in rows)

    def _find_sync(self, scope_id: str, adapter_name: str, source_name: str) -> Source | None:
        with self._lock:
            row = (
                self
                ._connection_sync()
                .execute(
                    """
                SELECT payload
                FROM runtime_sources
                WHERE scope_id = ? AND adapter_name = ? AND source_name = ?
                """,
                    (scope_id, adapter_name, source_name),
                )
                .fetchone()
            )
        return None if row is None else _decode_source(adapter_name, str(row[0]))


class SQLiteScopedSourceBackend(
    SourceCatalogBackend,
    SourceStore[Source],
    SourceJournal,
    TriggerCursorStore,
):
    """A Source catalog, journal, and cursor store for one opaque scope."""

    def __init__(self, backend: SQLiteSourceBackend, scope_id: str) -> None:
        self._backend = backend
        self.scope_id = scope_id

    async def add(self, source: Source, /) -> Source:
        return await asyncio.to_thread(self._backend._add_sync, self.scope_id, source)

    async def get(self, source: Source, /) -> Source:
        return await asyncio.to_thread(self._backend._get_sync, self.scope_id, source)

    async def list(self) -> tuple[Source, ...]:
        return await asyncio.to_thread(self._backend._list_sync, self.scope_id)

    async def position(self, source: Source, /) -> int:
        return await asyncio.to_thread(self._backend._position_sync, self.scope_id, source)

    async def high_watermark(self) -> int:
        return await asyncio.to_thread(self._backend._high_watermark_sync, self.scope_id)

    async def list_between(self, after: int, through: int, /) -> tuple[SourceRecord, ...]:
        return await asyncio.to_thread(self._backend._list_between_sync, self.scope_id, after, through)

    async def load_cursor(self, trigger_name: str, /) -> SourceCursor:
        return await asyncio.to_thread(self._backend._load_cursor_sync, self.scope_id, trigger_name)

    async def save_cursor(self, trigger_name: str, cursor: SourceCursor, /) -> None:
        await asyncio.to_thread(self._backend._save_cursor_sync, self.scope_id, trigger_name, cursor)


class SQLiteSourceEvidenceCodec:
    """Encode exact Source and Artifact references for one scoped runtime."""

    def __init__(self, backend: SQLiteSourceBackend, scope_id: str) -> None:
        self._backend = backend
        self._scope_id = validate_scope_id(scope_id)

    def encode_source(self, source: Source, /) -> object:
        if type(source) is not ContentSource:
            raise _UnsupportedSQLiteSourceError("evidence")
        return _SourceEvidenceRef(
            scope_id=self._scope_id,
            name=CONTENT_SOURCE_NAME,
            source_name=source.name,
        ).model_dump(mode="json")

    def decode_source(self, value: object, /) -> Source:
        try:
            reference = _SourceEvidenceRef.model_validate(value, strict=True)
        except ValidationError as error:
            raise _UnsupportedSQLiteSourceError("source-ref") from error
        if reference.scope_id != self._scope_id or reference.name != CONTENT_SOURCE_NAME:
            raise SourceNotFoundError(value)
        source = self._backend._find_sync(self._scope_id, reference.name, reference.source_name)
        if source is None:
            raise SourceNotFoundError(value)
        return source

    def encode_artifact(self, artifact: ArtifactRef, /) -> object:
        return _ARTIFACT_REF.dump_python(artifact, mode="json")

    def decode_artifact(self, value: object, /) -> ArtifactRef:
        try:
            reference = _ArtifactEvidenceRef.model_validate(value)
        except ValidationError as error:
            code = "artifact-ref" if not isinstance(value, dict) else "artifact-fields"
            raise _UnsupportedSQLiteSourceError(code) from error
        return ArtifactRef(reference.artifact_id, reference.revision)


def _encode_content_source(source: ContentSource) -> str:
    return _CONTENT_SOURCE.dump_json(source).decode()


def _decode_source(adapter_name: str, payload: str) -> Source:
    if adapter_name != CONTENT_SOURCE_NAME:
        raise _UnsupportedSQLiteSourceError("adapter", adapter_name)
    return _decode_content_source(payload)


def _decode_content_source(payload: str) -> ContentSource:
    try:
        source = _CONTENT_SOURCE.validate_json(payload, strict=True)
    except ValidationError as error:
        raise _UnsupportedSQLiteSourceError("payload") from error
    if source.materialization is not SourceMaterialization.CAPTURED:
        raise _UnsupportedSQLiteSourceError("payload")
    return source
