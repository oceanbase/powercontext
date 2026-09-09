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

"""Topic Memory active-projection index contracts."""

from __future__ import annotations

import hashlib
from typing import Protocol

from sqlalchemy import Table, and_, or_, select, true
from sqlalchemy.ext.asyncio import AsyncConnection
from sqlalchemy.sql import ColumnElement

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.memory import EmbeddingProfile
from powercontext.builtin.artifacts.topic_memory import (
    TopicMemory,
    TopicMemoryCapabilities,
    TopicMemoryProjection,
    TopicMemorySearchChannels,
    TopicMemorySearchRequest,
    TopicMemoryStorageInvariantError,
)
from powercontext.builtin.persistence.tables import TOPIC_MEMORY_ACTIVE_CHUNKS_TABLE, TOPIC_MEMORY_ACTIVE_TOPICS_TABLE


class TopicMemoryIndex(Protocol):
    """Backend projection participating in the publication transaction."""

    capabilities: TopicMemoryCapabilities
    tables: tuple[Table, ...]

    async def initialize(self, connection: AsyncConnection, /) -> None: ...

    async def validate_current(self, connection: AsyncConnection, /) -> None: ...

    async def replace(
        self,
        connection: AsyncConnection,
        scope_id: str,
        topic_ref: ArtifactRef,
        projection: TopicMemoryProjection,
        /,
    ) -> None: ...

    async def search(
        self,
        connection: AsyncConnection,
        scope_id: str,
        request: TopicMemorySearchRequest,
        /,
    ) -> TopicMemorySearchChannels: ...

    async def vector_complete(
        self,
        connection: AsyncConnection,
        scope_id: str,
        topic_ref: ArtifactRef,
        /,
    ) -> bool: ...


class NoTopicMemoryIndex:
    """Support exact audit reads without claiming searchable publication."""

    capabilities = TopicMemoryCapabilities(fts=False)
    tables: tuple[Table, ...] = ()

    async def initialize(self, _connection: AsyncConnection, /) -> None:
        pass

    async def validate_current(self, _connection: AsyncConnection, /) -> None:
        pass

    async def replace(
        self,
        _connection: AsyncConnection,
        _scope_id: str,
        _topic_ref: ArtifactRef,
        _projection: TopicMemoryProjection,
        /,
    ) -> None:
        pass

    async def search(
        self,
        _connection: AsyncConnection,
        _scope_id: str,
        _request: TopicMemorySearchRequest,
        /,
    ) -> TopicMemorySearchChannels:
        return TopicMemorySearchChannels()

    async def vector_complete(
        self,
        _connection: AsyncConnection,
        _scope_id: str,
        _topic_ref: ArtifactRef,
        /,
    ) -> bool:
        return False


class CompositeTopicMemoryIndex:
    """Compose one FTS adapter and an optional matching vector adapter."""

    def __init__(self, *indexes: TopicMemoryIndex) -> None:
        self.indexes = indexes
        fts_indexes = tuple(index for index in indexes if index.capabilities.fts)
        vector_indexes = tuple(index for index in indexes if index.capabilities.vector)
        if len(fts_indexes) != 1 or len(vector_indexes) > 1:
            raise ValueError("Topic Memory requires exactly one FTS index and at most one vector index")  # noqa: TRY003
        profile = vector_indexes[0].capabilities.embedding_profile if vector_indexes else None
        self.capabilities = TopicMemoryCapabilities(
            fts=True,
            vector=bool(vector_indexes),
            hybrid=bool(vector_indexes),
            embedding_profile=profile,
        )
        self.tables = tuple(table for index in indexes for table in index.tables)

    async def initialize(self, connection: AsyncConnection, /) -> None:
        for index in self.indexes:
            await index.initialize(connection)

    async def validate_current(self, connection: AsyncConnection, /) -> None:
        for index in self.indexes:
            await index.validate_current(connection)

    async def replace(
        self,
        connection: AsyncConnection,
        scope_id: str,
        topic_ref: ArtifactRef,
        projection: TopicMemoryProjection,
        /,
    ) -> None:
        for index in self.indexes:
            await index.replace(connection, scope_id, topic_ref, projection)

    async def search(
        self,
        connection: AsyncConnection,
        scope_id: str,
        request: TopicMemorySearchRequest,
        /,
    ) -> TopicMemorySearchChannels:
        channels = TopicMemorySearchChannels()
        for index in self.indexes:
            result = await index.search(connection, scope_id, request)
            channels = TopicMemorySearchChannels(
                topic_fts=channels.topic_fts + result.topic_fts,
                topic_vector=channels.topic_vector + result.topic_vector,
                detail_fts=channels.detail_fts + result.detail_fts,
                detail_vector=channels.detail_vector + result.detail_vector,
            )
        return channels

    async def vector_complete(
        self,
        connection: AsyncConnection,
        scope_id: str,
        topic_ref: ArtifactRef,
        /,
    ) -> bool:
        vector_indexes = tuple(index for index in self.indexes if index.capabilities.vector)
        if not vector_indexes:
            return False
        for index in vector_indexes:
            if not await index.vector_complete(connection, scope_id, topic_ref):
                return False
        return True


async def validate_current_topic_vectors(
    connection: AsyncConnection,
    topic_vectors: Table,
    chunk_vectors: Table,
    fingerprint: str,
    *,
    topic_present: ColumnElement[bool] | None = None,
    chunk_present: ColumnElement[bool] | None = None,
) -> None:
    """Check current vector projections in one statement snapshot, including RC.

    Never carry an active Revision into a later query: publication removes its
    old projection atomically. Both directions of ordinal membership matter;
    equal counts alone would accept a missing ordinal replaced by an extra one.
    """

    active = TOPIC_MEMORY_ACTIVE_TOPICS_TABLE.alias("active")
    expected = TOPIC_MEMORY_ACTIVE_CHUNKS_TABLE.alias("expected")
    expected_for_active = and_(
        expected.c.scope_id == active.c.scope_id,
        expected.c.family == active.c.family,
        expected.c.artifact_id == active.c.artifact_id,
        expected.c.revision == active.c.revision,
    )
    expected_for_vector = and_(
        expected.c.scope_id == chunk_vectors.c.scope_id,
        expected.c.artifact_id == chunk_vectors.c.artifact_id,
        expected.c.revision == chunk_vectors.c.revision,
        expected.c.chunk_ordinal == chunk_vectors.c.chunk_ordinal,
    )
    topic_complete = (
        select(topic_vectors.c.artifact_id)
        .where(
            topic_vectors.c.scope_id == active.c.scope_id,
            topic_vectors.c.artifact_id == active.c.artifact_id,
            topic_vectors.c.revision == active.c.revision,
            topic_vectors.c.profile_fingerprint == fingerprint,
            true() if topic_present is None else topic_present,
        )
        .correlate(active)
        .exists()
    )
    chunk_complete = (
        select(chunk_vectors.c.chunk_ordinal)
        .where(
            expected_for_vector,
            chunk_vectors.c.profile_fingerprint == fingerprint,
            true() if chunk_present is None else chunk_present,
        )
        .correlate(expected)
        .exists()
    )
    has_chunks = select(expected.c.chunk_ordinal).where(expected_for_active).correlate(active).exists()
    missing_chunk = (
        select(expected.c.chunk_ordinal).where(expected_for_active, ~chunk_complete).correlate(active).exists()
    )
    extra_chunk = (
        select(chunk_vectors.c.chunk_ordinal)
        .where(
            chunk_vectors.c.scope_id == active.c.scope_id,
            chunk_vectors.c.artifact_id == active.c.artifact_id,
            chunk_vectors.c.revision == active.c.revision,
            ~select(expected.c.chunk_ordinal).where(expected_for_vector).correlate(chunk_vectors).exists(),
        )
        .correlate(active)
        .exists()
    )
    invalid = (
        await connection.execute(
            select(active.c.scope_id, active.c.artifact_id, active.c.revision)
            .where(or_(~topic_complete, ~has_chunks, missing_chunk, extra_chunk))
            .limit(1)
        )
    ).one_or_none()
    if invalid is not None:
        ref = ArtifactRef(
            family=TopicMemory.family, artifact_id=str(invalid.artifact_id), revision=int(invalid.revision)
        )
        raise TopicMemoryStorageInvariantError("incomplete-vector", (str(invalid.scope_id), ref))


def topic_memory_embedding_profile_fingerprint(profile: EmbeddingProfile, /) -> str:
    """Return the backend-independent identity of one immutable retrieval profile."""

    payload = (
        f"{profile.profile_id}\0{profile.model}\0{profile.dimension}\0{profile.distance}\0{profile.normalization}"
    ).encode()
    return hashlib.sha256(payload).hexdigest()


__all__ = [
    "CompositeTopicMemoryIndex",
    "NoTopicMemoryIndex",
    "TopicMemoryIndex",
    "topic_memory_embedding_profile_fingerprint",
]
