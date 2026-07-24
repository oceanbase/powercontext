"""SQLite implementation of backend-neutral Runtime storage contracts."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from uuid import uuid4

import apsw

from powercontext._sqlite import sqlite_write_lock
from powercontext.memory import EmbeddingProfile, MemoryCapabilities
from powercontext.memory.backends.sqlite import SQLiteMemoryBackend
from powercontext.memory.canonical import validate_identifier
from powercontext.runtime.protocols import (
    MemoryBindingStore,
    RuntimeScopeStorage,
    RuntimeStorage,
)
from powercontext.sources.backends.sqlite import (
    SQLiteScopedSourceBackend,
    SQLiteSourceBackend,
    SQLiteSourceEvidenceCodec,
)
from powercontext.sources.journal import validate_scope_id

_BINDING_SCHEMA = """
CREATE TABLE IF NOT EXISTS runtime_memory_bindings (
    scope_id TEXT NOT NULL PRIMARY KEY,
    memory_artifact_id TEXT NOT NULL UNIQUE
)
"""


class _SQLiteMemoryBindingStateError(RuntimeError):
    def __init__(self, code: str) -> None:
        messages = {
            "not-initialized": "SQLite Memory binding store is not initialized",
            "missing-binding": "SQLite Memory binding store failed to resolve a scope",
        }
        super().__init__(messages[code])


class SQLiteMemoryBindingStore(MemoryBindingStore):
    """Persist one globally unique Memory Artifact identity per scope."""

    def __init__(self, database: str | Path) -> None:
        self._database = str(database)
        self._connection: apsw.Connection | None = None
        self._lock = RLock()
        self._write_lock = sqlite_write_lock(database)

    async def initialize(self) -> None:
        await asyncio.to_thread(self._initialize_sync)

    async def close(self) -> None:
        await asyncio.to_thread(self._close_sync)

    async def memory_artifact_id(self, scope_id: str, /) -> str:
        normalized_scope = validate_scope_id(scope_id)
        return await asyncio.to_thread(self._memory_artifact_id_sync, normalized_scope)

    def _initialize_sync(self) -> None:
        with self._lock:
            if self._connection is not None:
                return
            with self._write_lock:
                connection = apsw.Connection(self._database)
                connection.set_busy_timeout(30_000)
                if self._database != ":memory:":
                    connection.execute("PRAGMA journal_mode = WAL")
                connection.execute(_BINDING_SCHEMA)
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
            raise _SQLiteMemoryBindingStateError("not-initialized")
        return connection

    def _memory_artifact_id_sync(self, scope_id: str) -> str:
        candidate = f"memory-{uuid4()}"
        with self._lock:
            connection = self._connection_sync()
            with self._write_lock, connection:
                connection.execute(
                    """
                        INSERT INTO runtime_memory_bindings (scope_id, memory_artifact_id)
                        VALUES (?, ?)
                        ON CONFLICT (scope_id) DO NOTHING
                        """,
                    (scope_id, candidate),
                )
                row = connection.execute(
                    """
                        SELECT memory_artifact_id
                        FROM runtime_memory_bindings
                        WHERE scope_id = ?
                        """,
                    (scope_id,),
                ).fetchone()
        if row is None:
            raise _SQLiteMemoryBindingStateError("missing-binding")
        return validate_identifier(str(row[0]))


@dataclass(slots=True)
class SQLiteRuntimeScopeStorage(RuntimeScopeStorage):
    """Own SQLite resources for one Runtime scope."""

    memory_artifact_id: str
    sources: SQLiteScopedSourceBackend
    memory: SQLiteMemoryBackend
    evidence_codec: SQLiteSourceEvidenceCodec

    async def close(self) -> None:
        await self.memory.close()


class SQLiteRuntimeStorage(RuntimeStorage):
    """Assemble Source, binding, and Memory storage for the SQLite profile."""

    def __init__(
        self,
        database: str | Path,
        *,
        embedding_profile: EmbeddingProfile | None = None,
        vec1_extension: str | Path | None = None,
    ) -> None:
        self._database = str(database)
        self._embedding_profile = embedding_profile
        self._vec1_extension = vec1_extension
        self._sources = SQLiteSourceBackend(database)
        self._bindings = SQLiteMemoryBindingStore(database)
        self._memory_probe = SQLiteMemoryBackend(
            database,
            embedding_profile=embedding_profile,
            vec1_extension=vec1_extension,
        )
        self._memory_capabilities: MemoryCapabilities | None = None

    async def initialize(self) -> None:
        try:
            try:
                await self._sources.initialize()
                await self._bindings.initialize()
                await self._memory_probe.initialize()
                self._memory_capabilities = await self._memory_probe.capabilities()
            finally:
                await self._memory_probe.close()
        except BaseException:
            await self.close()
            raise

    async def close(self) -> None:
        await self._bindings.close()
        await self._sources.close()

    async def memory_capabilities(self) -> MemoryCapabilities:
        capabilities = self._memory_capabilities
        if capabilities is None:
            raise _SQLiteMemoryBindingStateError("not-initialized")
        return capabilities

    async def open_scope(self, scope_id: str, /) -> SQLiteRuntimeScopeStorage:
        normalized_scope = validate_scope_id(scope_id)
        memory_artifact_id = await self._bindings.memory_artifact_id(normalized_scope)
        sources = self._sources.for_scope(normalized_scope)
        evidence_codec = SQLiteSourceEvidenceCodec(self._sources, normalized_scope)
        memory = SQLiteMemoryBackend(
            self._database,
            evidence_codec=evidence_codec,
            embedding_profile=self._embedding_profile,
            vec1_extension=self._vec1_extension,
        )
        try:
            await memory.initialize()
        except BaseException:
            await memory.close()
            raise
        return SQLiteRuntimeScopeStorage(
            memory_artifact_id=memory_artifact_id,
            sources=sources,
            memory=memory,
            evidence_codec=evidence_codec,
        )

    async def pending_scopes(self, trigger_name: str, /) -> tuple[str, ...]:
        return await self._sources.pending_scopes(trigger_name)
