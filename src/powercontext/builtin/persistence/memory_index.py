"""Memory search-index contracts and composition."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any, Protocol

from sqlalchemy import Table
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.memory import (
    CapabilityNotSupportedError,
    EmbeddingProfile,
    MemoryCapabilities,
    MemoryChannelHit,
    MemoryProjection,
    MemorySearchChannels,
    MemorySearchRequest,
)


def memory_channel_hits(
    rows: Iterable[Mapping[Any, Any]],
    memories: tuple[ArtifactRef, ...],
    /,
) -> tuple[MemoryChannelHit, ...]:
    requested = {(memory.artifact_id, memory.revision) for memory in memories}
    return tuple(
        MemoryChannelHit(
            memory_ref=ArtifactRef(
                family="memory",
                artifact_id=str(row["memory_artifact_id"]),
                revision=int(row["head_revision"]),
            ),
            entry_id=str(row["entry_id"]),
            entry_version_id=str(row["entry_version_id"]),
            text=str(row["text"]),
        )
        for row in rows
        if (str(row["memory_artifact_id"]), int(row["head_revision"])) in requested
    )


class MemoryIndex(Protocol):
    """Optional query projection updated in the authoritative write transaction."""

    capabilities: MemoryCapabilities
    tables: tuple[Table, ...]

    async def initialize(self, connection: AsyncConnection, /) -> None: ...

    async def replace(
        self,
        connection: AsyncConnection,
        scope_id: str,
        memory_ref: ArtifactRef,
        projections: tuple[MemoryProjection, ...],
        /,
    ) -> None: ...

    async def search(
        self,
        connection: AsyncConnection,
        scope_id: str,
        request: MemorySearchRequest,
        /,
    ) -> MemorySearchChannels: ...

    async def vector_complete(
        self,
        connection: AsyncConnection,
        scope_id: str,
        memories: tuple[ArtifactRef, ...],
        profile: EmbeddingProfile,
        /,
    ) -> bool: ...

    async def hydrate(
        self,
        connection: AsyncConnection,
        scope_id: str,
        projections: tuple[MemoryProjection, ...],
        /,
    ) -> tuple[MemoryProjection, ...]: ...


class NoMemoryIndex:
    """Truthfully expose an authoritative store without search projections."""

    capabilities = MemoryCapabilities(fts=False)
    tables: tuple[Table, ...] = ()

    async def initialize(self, _connection: AsyncConnection, /) -> None:
        pass

    async def replace(
        self,
        _connection: AsyncConnection,
        _scope_id: str,
        _memory_ref: ArtifactRef,
        _projections: tuple[MemoryProjection, ...],
        /,
    ) -> None:
        pass

    async def search(
        self,
        _connection: AsyncConnection,
        _scope_id: str,
        _request: MemorySearchRequest,
        /,
    ) -> MemorySearchChannels:
        raise CapabilityNotSupportedError("fts")

    async def vector_complete(
        self,
        _connection: AsyncConnection,
        _scope_id: str,
        _memories: tuple[ArtifactRef, ...],
        _profile: EmbeddingProfile,
        /,
    ) -> bool:
        return False

    async def hydrate(
        self,
        _connection: AsyncConnection,
        _scope_id: str,
        projections: tuple[MemoryProjection, ...],
        /,
    ) -> tuple[MemoryProjection, ...]:
        return projections


class CompositeMemoryIndex:
    """Combine independent search indexes without leaking them into repositories."""

    def __init__(self, *indexes: MemoryIndex) -> None:
        self.indexes = indexes
        fts = any(index.capabilities.fts for index in indexes)
        vector_indexes = tuple(index for index in indexes if index.capabilities.vector)
        profile = vector_indexes[0].capabilities.embedding_profile if vector_indexes else None
        self.capabilities = MemoryCapabilities(
            fts=fts,
            vector=bool(vector_indexes),
            hybrid=fts and bool(vector_indexes),
            embedding_profile=profile,
        )
        self.tables = tuple(table for index in indexes for table in index.tables)

    async def initialize(self, connection: AsyncConnection, /) -> None:
        for index in self.indexes:
            await index.initialize(connection)

    async def replace(
        self,
        connection: AsyncConnection,
        scope_id: str,
        memory_ref: ArtifactRef,
        projections: tuple[MemoryProjection, ...],
        /,
    ) -> None:
        for index in self.indexes:
            await index.replace(connection, scope_id, memory_ref, projections)

    async def search(
        self,
        connection: AsyncConnection,
        scope_id: str,
        request: MemorySearchRequest,
        /,
    ) -> MemorySearchChannels:
        fts: tuple[MemoryChannelHit, ...] = ()
        vector: tuple[MemoryChannelHit, ...] = ()
        for index in self.indexes:
            channels = await index.search(connection, scope_id, request)
            fts += channels.fts
            vector += channels.vector
        return MemorySearchChannels(fts=fts, vector=vector)

    async def vector_complete(
        self,
        connection: AsyncConnection,
        scope_id: str,
        memories: tuple[ArtifactRef, ...],
        profile: EmbeddingProfile,
        /,
    ) -> bool:
        vector_indexes = tuple(index for index in self.indexes if index.capabilities.vector)
        if not vector_indexes:
            return False
        for index in vector_indexes:
            if not await index.vector_complete(connection, scope_id, memories, profile):
                return False
        return True

    async def hydrate(
        self,
        connection: AsyncConnection,
        scope_id: str,
        projections: tuple[MemoryProjection, ...],
        /,
    ) -> tuple[MemoryProjection, ...]:
        for index in self.indexes:
            projections = await index.hydrate(connection, scope_id, projections)
        return projections
