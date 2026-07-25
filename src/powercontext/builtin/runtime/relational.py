"""Scope-bound built-in contexts over one SQLAlchemy async database."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any, cast
from uuid import uuid4

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.artifacts import Artifact
from powercontext.builtin.artifacts.memory import (
    CandidatePipeline,
    Memory,
    MemoryService,
    MemoryWritePlan,
)
from powercontext.builtin.components import (
    BuiltinArtifacts,
    BuiltinSources,
    MemoryFlushResult,
    SourceWindowApplication,
)
from powercontext.builtin.inference import EmbeddingModel
from powercontext.builtin.persistence.artifacts import ArtifactRepository
from powercontext.builtin.persistence.cursors import SourceCursorRepository
from powercontext.builtin.persistence.database import AsyncDatabase
from powercontext.builtin.persistence.errors import RepositoryNotFoundError, StoredPayloadConflictError
from powercontext.builtin.persistence.memory import (
    MemoryIndexStrategy,
    NoMemoryIndex,
    RelationalMemoryBackend,
)
from powercontext.builtin.persistence.sources import SourceRepository
from powercontext.builtin.persistence.tables import SOURCE_JOURNAL_HEADS_TABLE
from powercontext.builtin.sources import CONTENT_SOURCE_ADAPTER, SourceCursor, validate_scope_id
from powercontext.builtin.triggers import (
    SOURCE_WINDOW_TRIGGER_NAME,
    ProcessSourceWindow,
    SourceHighWatermark,
    SourceWindowTrigger,
)
from powercontext.context import PowerContext
from powercontext.errors import ArtifactNotFoundError, SourceConflictError, SourceNotFoundError
from powercontext.sources import (
    Source,
    SourceAdapter,
    SourceCatalog,
    SourceRef,
)

IdFactory = Callable[[str], str]
_SOURCE_ADAPTERS: tuple[SourceAdapter[Any, Any, Any], ...] = (CONTENT_SOURCE_ADAPTER,)


class _Repositories(BaseModel):
    """Repositories shared by every scoped context."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    sources: SourceRepository
    artifacts: ArtifactRepository
    cursors: SourceCursorRepository


class RelationalContexts:
    """Compose typed, scope-bound contexts without owning the database lifecycle."""

    def __init__(
        self,
        *,
        database: AsyncDatabase,
        index: MemoryIndexStrategy | None = None,
        candidate_pipeline: CandidatePipeline | None = None,
        embedding_model: EmbeddingModel | None = None,
        id_factory: IdFactory | None = None,
        memory_artifact_id: str = "memory",
    ) -> None:
        self.database = database
        self.index = NoMemoryIndex() if index is None else index
        self.repositories = _Repositories(
            sources=SourceRepository(_SOURCE_ADAPTERS),
            artifacts=ArtifactRepository((Memory,)),
            cursors=SourceCursorRepository(),
        )
        self._candidate_pipeline = candidate_pipeline
        self.memory_extraction = candidate_pipeline is not None
        self._embedding_model = embedding_model
        self._id_factory = _scoped_id_factory(memory_artifact_id, id_factory)
        self._memory_artifact_id = memory_artifact_id
        self._contexts: dict[
            str,
            PowerContext[BuiltinSources, BuiltinArtifacts, SourceWindowApplication],
        ] = {}
        self._source_locks: dict[str, asyncio.Lock] = {}
        self._activation_locks: dict[str, asyncio.Lock] = {}

    async def scope_ids(self) -> tuple[str, ...]:
        """Return scopes with a Source journal, in deterministic order."""

        async with self.database.transaction() as connection:
            values = (
                await connection.execute(
                    select(SOURCE_JOURNAL_HEADS_TABLE.c.scope_id).order_by(SOURCE_JOURNAL_HEADS_TABLE.c.scope_id)
                )
            ).scalars()
            return tuple(str(value) for value in values)

    async def get(
        self,
        scope_id: str,
        /,
    ) -> PowerContext[BuiltinSources, BuiltinArtifacts, SourceWindowApplication]:
        scope = validate_scope_id(scope_id)
        existing = self._contexts.get(scope)
        if existing is not None:
            return existing

        sources_backend = _RelationalSources(
            database=self.database,
            scope_id=scope,
            adapters=_SOURCE_ADAPTERS,
            repository=self.repositories.sources,
            write_lock=self._source_locks.setdefault(scope, asyncio.Lock()),
        )
        source_catalog = SourceCatalog(
            backend=sources_backend,
            adapters=_SOURCE_ADAPTERS,
        )
        memory = self._memory_service(
            scope,
            source_resolver=source_catalog,
            artifact_resolver=_RelationalArtifactResolver(
                database=self.database,
                scope_id=scope,
                repository=self.repositories.artifacts,
            ),
        )
        source_windows = _RelationalSourceWindows(
            database=self.database,
            scope_id=scope,
            repositories=self.repositories,
            index=self.index,
            candidate_pipeline=self._candidate_pipeline,
            embedding_model=self._embedding_model,
            id_factory=self._id_factory,
            memory_artifact_id=self._memory_artifact_id,
            lock=self._activation_locks.setdefault(scope, asyncio.Lock()),
        )
        context: PowerContext[BuiltinSources, BuiltinArtifacts, SourceWindowApplication] = PowerContext(
            sources=BuiltinSources(
                catalog=source_catalog,
                store=sources_backend,
                journal=sources_backend,
            ),
            artifacts=BuiltinArtifacts(
                memory=memory,
                memory_artifact_id=self._memory_artifact_id,
            ),
            triggers=source_windows,
        )
        return self._contexts.setdefault(scope, context)

    def _memory_service(
        self,
        scope_id: str,
        *,
        source_resolver: SourceCatalog,
        artifact_resolver: _RelationalArtifactResolver,
        connection: AsyncConnection | None = None,
    ) -> MemoryService:
        backend = RelationalMemoryBackend(
            database=self.database,
            scope_id=scope_id,
            artifacts=self.repositories.artifacts,
            index=self.index,
            connection=connection,
        )
        return MemoryService(
            backend=backend,
            candidate_pipeline=self._candidate_pipeline,
            embedding_model=self._embedding_model,
            source_resolver=source_resolver,
            artifact_resolver=artifact_resolver,
            id_factory=self._id_factory,
        )


class _RelationalSources:
    def __init__(
        self,
        *,
        database: AsyncDatabase,
        scope_id: str,
        adapters: tuple[SourceAdapter[Any, Any, Any], ...],
        repository: SourceRepository,
        write_lock: asyncio.Lock,
        connection: AsyncConnection | None = None,
    ) -> None:
        self._database = database
        self._scope_id = scope_id
        self._source_names = {adapter.source_class: adapter.name for adapter in adapters}
        self._repository = repository
        self._write_lock = write_lock
        self._bound_connection = connection

    async def add(self, source: Source, /) -> Source:
        async with self._write_lock:
            try:
                if self._bound_connection is not None:
                    return (await self._repository.add(self._bound_connection, self._scope_id, source)).value
                async with self._database.transaction() as connection:
                    return (await self._repository.add(connection, self._scope_id, source)).value
            except StoredPayloadConflictError as error:
                raise SourceConflictError("identity", error.identity) from None

    async def get(self, source: Source, /) -> Source:
        ref = self._as_ref(source)
        try:
            if self._bound_connection is not None:
                return (await self._repository.get(self._bound_connection, self._scope_id, ref)).value
            async with self._database.transaction() as connection:
                return (await self._repository.get(connection, self._scope_id, ref)).value
        except RepositoryNotFoundError:
            raise SourceNotFoundError(source) from None

    async def list(self) -> tuple[Source, ...]:
        if self._bound_connection is not None:
            rows = await self._repository.list(self._bound_connection, self._scope_id)
        else:
            async with self._database.transaction() as connection:
                rows = await self._repository.list(connection, self._scope_id)
        return tuple(row.value for row in rows)

    async def position(self, source: Source, /) -> int:
        ref = self._as_ref(source)
        try:
            if self._bound_connection is not None:
                return (await self._repository.get(self._bound_connection, self._scope_id, ref)).journal_position
            async with self._database.transaction() as connection:
                return (await self._repository.get(connection, self._scope_id, ref)).journal_position
        except RepositoryNotFoundError:
            raise SourceNotFoundError(source) from None

    def _as_ref(self, source: Source) -> SourceRef:
        return SourceRef(source_type=self._source_names[type(source)], source_id=source.name)


class _RelationalArtifactResolver:
    def __init__(
        self,
        *,
        database: AsyncDatabase,
        scope_id: str,
        repository: ArtifactRepository,
        connection: AsyncConnection | None = None,
    ) -> None:
        self._database = database
        self._scope_id = scope_id
        self._repository = repository
        self._connection = connection

    async def get(self, artifact: Artifact[object], /) -> Artifact[object]:
        try:
            if self._connection is not None:
                return cast(
                    Artifact[object],
                    await self._repository.get(self._connection, self._scope_id, artifact.as_ref()),
                )
            async with self._database.transaction() as connection:
                return cast(
                    Artifact[object],
                    await self._repository.get(connection, self._scope_id, artifact.as_ref()),
                )
        except RepositoryNotFoundError:
            raise ArtifactNotFoundError(artifact) from None


class _RelationalSourceWindows:
    def __init__(
        self,
        *,
        database: AsyncDatabase,
        scope_id: str,
        repositories: _Repositories,
        index: MemoryIndexStrategy,
        candidate_pipeline: CandidatePipeline | None,
        embedding_model: EmbeddingModel | None,
        id_factory: IdFactory | None,
        memory_artifact_id: str,
        lock: asyncio.Lock,
    ) -> None:
        self._database = database
        self._scope_id = scope_id
        self._repositories = repositories
        self._index = index
        self._candidate_pipeline = candidate_pipeline
        self._embedding_model = embedding_model
        self._id_factory = id_factory
        self._memory_artifact_id = memory_artifact_id
        self._lock = lock
        self._trigger = SourceWindowTrigger()

    async def cursor(self) -> SourceCursor:
        async with self._database.transaction() as connection:
            state = await self._repositories.cursors.load(
                connection,
                self._scope_id,
                SOURCE_WINDOW_TRIGGER_NAME,
            )
        return self._trigger.initial_state() if state is None else state.cursor

    async def flush(self, *, limit: int) -> MemoryFlushResult:
        async with self._lock:
            async with self._database.transaction() as connection:
                state_row = await self._repositories.cursors.load(
                    connection,
                    self._scope_id,
                    SOURCE_WINDOW_TRIGGER_NAME,
                )
                state = self._trigger.initial_state() if state_row is None else state_row.cursor
                high_watermark = await self._repositories.sources.journal_position(
                    connection,
                    self._scope_id,
                )
                signal = SourceHighWatermark(sequence=high_watermark, limit=limit)
                transition = self._trigger.activate(signal, state)
                sources = () if not transition.actions else await self._sources(connection, transition.actions[0])
            if not transition.actions:
                return MemoryFlushResult(
                    previous_cursor=state.sequence,
                    high_watermark=high_watermark,
                    current_cursor=state.sequence,
                    source_count=0,
                    memory_ref=None,
                )

            action = transition.actions[0]
            prepared = await self._prepare_memory(sources)
            async with self._database.transaction() as connection:
                updated = await self._memory_service(connection).apply(prepared)
                await self._repositories.cursors.save(
                    connection,
                    self._scope_id,
                    SOURCE_WINDOW_TRIGGER_NAME,
                    transition.state,
                    expected_generation=None if state_row is None else state_row.generation,
                )
            return MemoryFlushResult(
                previous_cursor=action.after,
                high_watermark=high_watermark,
                current_cursor=action.through,
                source_count=len(sources),
                memory_ref=None if updated is None else updated.as_ref(),
            )

    async def _sources(
        self,
        connection: AsyncConnection,
        action: ProcessSourceWindow,
    ) -> tuple[Source, ...]:
        rows = await self._repositories.sources.list(
            connection,
            self._scope_id,
            after=action.after,
        )
        return tuple(row.value for row in rows if row.journal_position <= action.through)

    async def _prepare_memory(self, sources: tuple[Source, ...]) -> MemoryWritePlan:
        service = self._memory_service()
        try:
            current = await service.head(self._memory_artifact_id)
        except ArtifactNotFoundError:
            current = None
        return await service.plan_remember(memory=current, sources=sources, mode="extract")

    def _memory_service(self, connection: AsyncConnection | None = None) -> MemoryService:
        source_backend = _RelationalSources(
            database=self._database,
            scope_id=self._scope_id,
            adapters=_SOURCE_ADAPTERS,
            repository=self._repositories.sources,
            write_lock=self._lock,
            connection=connection,
        )
        source_catalog = SourceCatalog(
            backend=source_backend,
            adapters=_SOURCE_ADAPTERS,
        )
        artifact_resolver = _RelationalArtifactResolver(
            database=self._database,
            scope_id=self._scope_id,
            repository=self._repositories.artifacts,
            connection=connection,
        )
        backend = RelationalMemoryBackend(
            database=self._database,
            scope_id=self._scope_id,
            artifacts=self._repositories.artifacts,
            index=self._index,
            connection=connection,
        )
        service = MemoryService(
            backend=backend,
            candidate_pipeline=self._candidate_pipeline,
            embedding_model=self._embedding_model,
            source_resolver=source_catalog,
            artifact_resolver=artifact_resolver,
            id_factory=self._id_factory,
        )
        return service


def _scoped_id_factory(memory_artifact_id: str, delegate: IdFactory | None) -> IdFactory:
    def new_id(kind: str) -> str:
        if kind == "memory":
            return memory_artifact_id
        if delegate is not None:
            return delegate(kind)
        prefixes = {"entry": "mem_ent", "version": "mem_ver"}
        return f"{prefixes[kind]}_{uuid4().hex}"

    return new_id
