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

from typing import Protocol

from sqlalchemy import Table
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.topic_memory import (
    TopicMemoryCapabilities,
    TopicMemoryProjection,
    TopicMemorySearchChannels,
    TopicMemorySearchRequest,
)


class TopicMemoryIndex(Protocol):
    """Backend projection participating in the publication transaction."""

    capabilities: TopicMemoryCapabilities
    tables: tuple[Table, ...]

    async def initialize(self, connection: AsyncConnection, /) -> None: ...

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


__all__ = ["CompositeTopicMemoryIndex", "NoTopicMemoryIndex", "TopicMemoryIndex"]
