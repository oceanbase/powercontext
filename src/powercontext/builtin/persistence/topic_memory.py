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

"""Atomic Topic Memory publication and bounded current-head retrieval."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import and_, delete, func, insert, or_, select
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.memory import EmbeddingProfile
from powercontext.builtin.artifacts.memory.canonical import validate_embedding
from powercontext.builtin.artifacts.search import analyze_text
from powercontext.builtin.artifacts.topic_memory import (
    PublishedTopicMemory,
    TopicMemory,
    TopicMemoryBrowseCursor,
    TopicMemoryCapabilityError,
    TopicMemoryCurrentItem,
    TopicMemoryDraft,
    TopicMemoryProjection,
    TopicMemoryProjectionError,
    TopicMemorySearchMode,
    TopicMemorySearchRequest,
    TopicMemorySearchResult,
    TopicMemoryStorageInvariantError,
    TopicMemoryUsedSearchMode,
    fuse_topic_memory_rankings,
)
from powercontext.builtin.persistence.artifacts import ArtifactRepository
from powercontext.builtin.persistence.errors import InvalidRepositoryArgumentError
from powercontext.builtin.persistence.supervision import database_utc_now
from powercontext.builtin.persistence.tables import (
    ARTIFACT_HEADS_TABLE,
    ARTIFACTS_TABLE,
    TOPIC_MEMORY_ACTIVE_CHUNKS_TABLE,
    TOPIC_MEMORY_ACTIVE_TOPICS_TABLE,
    TOPIC_MEMORY_REVISION_PUBLICATIONS_TABLE,
)
from powercontext.builtin.persistence.topic_memory_index import NoTopicMemoryIndex, TopicMemoryIndex


class TopicMemoryRepository:
    """Store complete searchable Topic Revisions through one caller transaction."""

    def __init__(
        self,
        *,
        artifacts: ArtifactRepository | None = None,
        index: TopicMemoryIndex | None = None,
    ) -> None:
        self.artifacts = ArtifactRepository((TopicMemory,)) if artifacts is None else artifacts
        self.index = NoTopicMemoryIndex() if index is None else index

    async def initialize(self, connection: AsyncConnection, /) -> None:
        """Initialize indexes and reject incomplete historical Topic projections."""

        await self.index.initialize(connection)
        missing_publication = (
            await connection.execute(
                select(
                    ARTIFACTS_TABLE.c.scope_id,
                    ARTIFACTS_TABLE.c.artifact_id,
                    ARTIFACTS_TABLE.c.revision,
                )
                .outerjoin(
                    TOPIC_MEMORY_REVISION_PUBLICATIONS_TABLE,
                    (TOPIC_MEMORY_REVISION_PUBLICATIONS_TABLE.c.scope_id == ARTIFACTS_TABLE.c.scope_id)
                    & (TOPIC_MEMORY_REVISION_PUBLICATIONS_TABLE.c.family == ARTIFACTS_TABLE.c.family)
                    & (TOPIC_MEMORY_REVISION_PUBLICATIONS_TABLE.c.artifact_id == ARTIFACTS_TABLE.c.artifact_id)
                    & (TOPIC_MEMORY_REVISION_PUBLICATIONS_TABLE.c.revision == ARTIFACTS_TABLE.c.revision),
                )
                .where(
                    ARTIFACTS_TABLE.c.family == TopicMemory.family,
                    TOPIC_MEMORY_REVISION_PUBLICATIONS_TABLE.c.artifact_id.is_(None),
                )
                .limit(1)
            )
        ).one_or_none()
        if missing_publication is not None:
            raise TopicMemoryStorageInvariantError("missing-publication", tuple(missing_publication))

        orphan_active = (
            await connection.execute(
                select(
                    TOPIC_MEMORY_ACTIVE_TOPICS_TABLE.c.scope_id,
                    TOPIC_MEMORY_ACTIVE_TOPICS_TABLE.c.artifact_id,
                    TOPIC_MEMORY_ACTIVE_TOPICS_TABLE.c.revision,
                )
                .outerjoin(
                    ARTIFACT_HEADS_TABLE,
                    (ARTIFACT_HEADS_TABLE.c.scope_id == TOPIC_MEMORY_ACTIVE_TOPICS_TABLE.c.scope_id)
                    & (ARTIFACT_HEADS_TABLE.c.family == TOPIC_MEMORY_ACTIVE_TOPICS_TABLE.c.family)
                    & (ARTIFACT_HEADS_TABLE.c.artifact_id == TOPIC_MEMORY_ACTIVE_TOPICS_TABLE.c.artifact_id)
                    & (ARTIFACT_HEADS_TABLE.c.revision == TOPIC_MEMORY_ACTIVE_TOPICS_TABLE.c.revision),
                )
                .where(ARTIFACT_HEADS_TABLE.c.artifact_id.is_(None))
                .limit(1)
            )
        ).one_or_none()
        if orphan_active is not None:
            raise TopicMemoryStorageInvariantError("active-topic-not-head", tuple(orphan_active))

        orphan_chunk = (
            await connection.execute(
                select(
                    TOPIC_MEMORY_ACTIVE_CHUNKS_TABLE.c.scope_id,
                    TOPIC_MEMORY_ACTIVE_CHUNKS_TABLE.c.artifact_id,
                    TOPIC_MEMORY_ACTIVE_CHUNKS_TABLE.c.revision,
                    TOPIC_MEMORY_ACTIVE_CHUNKS_TABLE.c.chunk_ordinal,
                )
                .outerjoin(
                    TOPIC_MEMORY_ACTIVE_TOPICS_TABLE,
                    (TOPIC_MEMORY_ACTIVE_TOPICS_TABLE.c.scope_id == TOPIC_MEMORY_ACTIVE_CHUNKS_TABLE.c.scope_id)
                    & (TOPIC_MEMORY_ACTIVE_TOPICS_TABLE.c.family == TOPIC_MEMORY_ACTIVE_CHUNKS_TABLE.c.family)
                    & (TOPIC_MEMORY_ACTIVE_TOPICS_TABLE.c.artifact_id == TOPIC_MEMORY_ACTIVE_CHUNKS_TABLE.c.artifact_id)
                    & (TOPIC_MEMORY_ACTIVE_TOPICS_TABLE.c.revision == TOPIC_MEMORY_ACTIVE_CHUNKS_TABLE.c.revision),
                )
                .where(TOPIC_MEMORY_ACTIVE_TOPICS_TABLE.c.artifact_id.is_(None))
                .limit(1)
            )
        ).one_or_none()
        if orphan_chunk is not None:
            raise TopicMemoryStorageInvariantError("active-chunk-not-head", tuple(orphan_chunk))

        active_rows = (
            await connection.execute(
                select(
                    ARTIFACT_HEADS_TABLE.c.scope_id,
                    ARTIFACT_HEADS_TABLE.c.artifact_id,
                    ARTIFACT_HEADS_TABLE.c.revision,
                    TOPIC_MEMORY_ACTIVE_TOPICS_TABLE.c.revision.label("active_revision"),
                )
                .outerjoin(
                    TOPIC_MEMORY_ACTIVE_TOPICS_TABLE,
                    (TOPIC_MEMORY_ACTIVE_TOPICS_TABLE.c.scope_id == ARTIFACT_HEADS_TABLE.c.scope_id)
                    & (TOPIC_MEMORY_ACTIVE_TOPICS_TABLE.c.family == ARTIFACT_HEADS_TABLE.c.family)
                    & (TOPIC_MEMORY_ACTIVE_TOPICS_TABLE.c.artifact_id == ARTIFACT_HEADS_TABLE.c.artifact_id),
                )
                .where(ARTIFACT_HEADS_TABLE.c.family == TopicMemory.family)
            )
        ).mappings()
        for row in active_rows:
            ref = ArtifactRef(
                family=TopicMemory.family,
                artifact_id=str(row["artifact_id"]),
                revision=int(row["revision"]),
            )
            if row["active_revision"] is None or int(row["active_revision"]) != ref.revision:
                raise TopicMemoryStorageInvariantError("head-not-active", (str(row["scope_id"]), ref))
            chunk_count = int(
                await connection.scalar(
                    select(func.count())
                    .select_from(TOPIC_MEMORY_ACTIVE_CHUNKS_TABLE)
                    .where(
                        TOPIC_MEMORY_ACTIVE_CHUNKS_TABLE.c.scope_id == row["scope_id"],
                        TOPIC_MEMORY_ACTIVE_CHUNKS_TABLE.c.family == TopicMemory.family,
                        TOPIC_MEMORY_ACTIVE_CHUNKS_TABLE.c.artifact_id == ref.artifact_id,
                        TOPIC_MEMORY_ACTIVE_CHUNKS_TABLE.c.revision == ref.revision,
                    )
                )
                or 0
            )
            if chunk_count == 0:
                raise TopicMemoryStorageInvariantError("missing-active-chunks", (str(row["scope_id"]), ref))
            if self.index.capabilities.vector and not await self.index.vector_complete(
                connection, str(row["scope_id"]), ref
            ):
                raise TopicMemoryStorageInvariantError("incomplete-vector", (str(row["scope_id"]), ref))

    async def publish_create(
        self,
        connection: AsyncConnection,
        scope_id: str,
        artifact_id: str,
        draft: TopicMemoryDraft,
        projection: TopicMemoryProjection,
        /,
    ) -> PublishedTopicMemory:
        """Create Revision 1 and activate every configured channel atomically."""

        self._validate_projection(draft, projection)
        topic = await self.artifacts.create(connection, scope_id, artifact_id, draft)
        if not isinstance(topic, TopicMemory):
            raise TopicMemoryStorageInvariantError("artifact-type", topic.as_ref())
        published_at = await self._activate(connection, scope_id, topic, projection)
        return PublishedTopicMemory(
            topic=topic,
            published_at=_aware_utc(published_at),
            is_current=True,
            current_artifact=topic.as_ref(),
        )

    async def publish_revision(
        self,
        connection: AsyncConnection,
        scope_id: str,
        current: TopicMemory,
        draft: TopicMemoryDraft,
        projection: TopicMemoryProjection,
        /,
    ) -> PublishedTopicMemory:
        """CAS the current Head and atomically replace its complete active projection."""

        self._validate_projection(draft, projection)
        topic = await self.artifacts.revise(connection, scope_id, current, draft)
        if not isinstance(topic, TopicMemory):
            raise TopicMemoryStorageInvariantError("artifact-type", topic.as_ref())
        published_at = await self._activate(connection, scope_id, topic, projection)
        return PublishedTopicMemory(
            topic=topic,
            published_at=_aware_utc(published_at),
            is_current=True,
            current_artifact=topic.as_ref(),
        )

    async def get_exact(
        self,
        connection: AsyncConnection,
        scope_id: str,
        ref: ArtifactRef,
        /,
    ) -> PublishedTopicMemory:
        """Read one exact immutable Revision even after its active Head changes."""

        if ref.family != TopicMemory.family:
            raise InvalidRepositoryArgumentError("artifact_ref", "must reference topic-memory")
        topic = await self.artifacts.get(connection, scope_id, ref)
        if not isinstance(topic, TopicMemory):
            raise TopicMemoryStorageInvariantError("artifact-type", ref)
        published_at = await connection.scalar(
            select(TOPIC_MEMORY_REVISION_PUBLICATIONS_TABLE.c.published_at).where(
                TOPIC_MEMORY_REVISION_PUBLICATIONS_TABLE.c.scope_id == scope_id,
                TOPIC_MEMORY_REVISION_PUBLICATIONS_TABLE.c.family == TopicMemory.family,
                TOPIC_MEMORY_REVISION_PUBLICATIONS_TABLE.c.artifact_id == ref.artifact_id,
                TOPIC_MEMORY_REVISION_PUBLICATIONS_TABLE.c.revision == ref.revision,
            )
        )
        if not isinstance(published_at, datetime):
            raise TopicMemoryStorageInvariantError("missing-publication", (scope_id, ref))
        active_revision = await connection.scalar(
            select(TOPIC_MEMORY_ACTIVE_TOPICS_TABLE.c.revision).where(
                TOPIC_MEMORY_ACTIVE_TOPICS_TABLE.c.scope_id == scope_id,
                TOPIC_MEMORY_ACTIVE_TOPICS_TABLE.c.family == TopicMemory.family,
                TOPIC_MEMORY_ACTIVE_TOPICS_TABLE.c.artifact_id == ref.artifact_id,
            )
        )
        if active_revision is None:
            raise TopicMemoryStorageInvariantError("missing-active-head", (scope_id, ref.artifact_id))
        current = ArtifactRef(family=TopicMemory.family, artifact_id=ref.artifact_id, revision=int(active_revision))
        return PublishedTopicMemory(
            topic=topic,
            published_at=_aware_utc(published_at),
            is_current=current == ref,
            current_artifact=current,
        )

    async def browse_current(
        self,
        connection: AsyncConnection,
        scope_id: str,
        /,
        *,
        limit: int,
        after: TopicMemoryBrowseCursor | None = None,
    ) -> tuple[TopicMemoryCurrentItem, ...]:
        """Return an exclusive, bounded current-head page in stable publication order."""

        if not 1 <= limit <= 100:
            raise InvalidRepositoryArgumentError("limit", "must be between 1 and 100")
        statement = (
            select(
                TOPIC_MEMORY_ACTIVE_TOPICS_TABLE.c.artifact_id,
                TOPIC_MEMORY_ACTIVE_TOPICS_TABLE.c.revision,
                TOPIC_MEMORY_ACTIVE_TOPICS_TABLE.c.title,
                TOPIC_MEMORY_ACTIVE_TOPICS_TABLE.c.summary,
                TOPIC_MEMORY_ACTIVE_TOPICS_TABLE.c.source_count,
                TOPIC_MEMORY_REVISION_PUBLICATIONS_TABLE.c.published_at,
            )
            .join(
                TOPIC_MEMORY_REVISION_PUBLICATIONS_TABLE,
                (TOPIC_MEMORY_REVISION_PUBLICATIONS_TABLE.c.scope_id == TOPIC_MEMORY_ACTIVE_TOPICS_TABLE.c.scope_id)
                & (TOPIC_MEMORY_REVISION_PUBLICATIONS_TABLE.c.family == TOPIC_MEMORY_ACTIVE_TOPICS_TABLE.c.family)
                & (
                    TOPIC_MEMORY_REVISION_PUBLICATIONS_TABLE.c.artifact_id
                    == TOPIC_MEMORY_ACTIVE_TOPICS_TABLE.c.artifact_id
                )
                & (TOPIC_MEMORY_REVISION_PUBLICATIONS_TABLE.c.revision == TOPIC_MEMORY_ACTIVE_TOPICS_TABLE.c.revision),
            )
            .join(
                ARTIFACT_HEADS_TABLE,
                (ARTIFACT_HEADS_TABLE.c.scope_id == TOPIC_MEMORY_ACTIVE_TOPICS_TABLE.c.scope_id)
                & (ARTIFACT_HEADS_TABLE.c.family == TOPIC_MEMORY_ACTIVE_TOPICS_TABLE.c.family)
                & (ARTIFACT_HEADS_TABLE.c.artifact_id == TOPIC_MEMORY_ACTIVE_TOPICS_TABLE.c.artifact_id)
                & (ARTIFACT_HEADS_TABLE.c.revision == TOPIC_MEMORY_ACTIVE_TOPICS_TABLE.c.revision),
            )
            .where(
                TOPIC_MEMORY_ACTIVE_TOPICS_TABLE.c.scope_id == scope_id,
                TOPIC_MEMORY_ACTIVE_TOPICS_TABLE.c.family == TopicMemory.family,
            )
        )
        if after is not None:
            boundary = _stored_utc(after.published_at)
            statement = statement.where(
                or_(
                    TOPIC_MEMORY_REVISION_PUBLICATIONS_TABLE.c.published_at < boundary,
                    and_(
                        TOPIC_MEMORY_REVISION_PUBLICATIONS_TABLE.c.published_at == boundary,
                        TOPIC_MEMORY_ACTIVE_TOPICS_TABLE.c.artifact_id > after.artifact_id,
                    ),
                    and_(
                        TOPIC_MEMORY_REVISION_PUBLICATIONS_TABLE.c.published_at == boundary,
                        TOPIC_MEMORY_ACTIVE_TOPICS_TABLE.c.artifact_id == after.artifact_id,
                        TOPIC_MEMORY_ACTIVE_TOPICS_TABLE.c.revision < after.revision,
                    ),
                )
            )
        rows = (
            await connection.execute(
                statement.order_by(
                    TOPIC_MEMORY_REVISION_PUBLICATIONS_TABLE.c.published_at.desc(),
                    TOPIC_MEMORY_ACTIVE_TOPICS_TABLE.c.artifact_id,
                    TOPIC_MEMORY_ACTIVE_TOPICS_TABLE.c.revision.desc(),
                ).limit(limit)
            )
        ).mappings()
        return tuple(
            TopicMemoryCurrentItem(
                artifact_ref=ArtifactRef(
                    family=TopicMemory.family,
                    artifact_id=str(row["artifact_id"]),
                    revision=int(row["revision"]),
                ),
                title=str(row["title"]),
                summary=str(row["summary"]),
                published_at=_aware_utc(row["published_at"]),
                source_count=int(row["source_count"]),
            )
            for row in rows
        )

    async def search(
        self,
        connection: AsyncConnection,
        scope_id: str,
        query: str,
        /,
        *,
        limit: int,
        mode: TopicMemorySearchMode = "auto",
        query_vector: tuple[float, ...] | None = None,
        embedding_profile: EmbeddingProfile | None = None,
    ) -> TopicMemorySearchResult:
        """Search current complete projections and fuse two or four logical channels."""

        if not 1 <= limit <= 100:
            raise InvalidRepositoryArgumentError("limit", "must be between 1 and 100")
        if query != query.strip() or not query:
            raise InvalidRepositoryArgumentError("query", "must be a non-empty trimmed string")
        analyzed = analyze_text(query)
        used_mode = self._select_mode(mode, query_vector, embedding_profile)
        if not analyzed and used_mode == "fts":
            return TopicMemorySearchResult(mode=used_mode, hits=())
        request = TopicMemorySearchRequest(
            query=query,
            analyzed_query=analyzed,
            candidate_limit=min(100, max(limit * 4, limit)),
            mode=used_mode,
            query_vector=query_vector,
            embedding_profile=embedding_profile,
        )
        channels = await self.index.search(connection, scope_id, request)
        return TopicMemorySearchResult(
            mode=used_mode,
            hits=fuse_topic_memory_rankings(query, channels, limit),
        )

    async def _activate(
        self,
        connection: AsyncConnection,
        scope_id: str,
        topic: TopicMemory,
        projection: TopicMemoryProjection,
    ) -> datetime:
        published_at = await database_utc_now(connection)
        ref = topic.as_ref()
        await connection.execute(
            insert(TOPIC_MEMORY_REVISION_PUBLICATIONS_TABLE).values(
                scope_id=scope_id,
                family=TopicMemory.family,
                artifact_id=ref.artifact_id,
                revision=ref.revision,
                published_at=published_at,
            )
        )
        await self.index.replace(connection, scope_id, ref, projection)
        await connection.execute(
            delete(TOPIC_MEMORY_ACTIVE_CHUNKS_TABLE).where(
                TOPIC_MEMORY_ACTIVE_CHUNKS_TABLE.c.scope_id == scope_id,
                TOPIC_MEMORY_ACTIVE_CHUNKS_TABLE.c.artifact_id == ref.artifact_id,
            )
        )
        await connection.execute(
            delete(TOPIC_MEMORY_ACTIVE_TOPICS_TABLE).where(
                TOPIC_MEMORY_ACTIVE_TOPICS_TABLE.c.scope_id == scope_id,
                TOPIC_MEMORY_ACTIVE_TOPICS_TABLE.c.artifact_id == ref.artifact_id,
            )
        )
        await connection.execute(
            insert(TOPIC_MEMORY_ACTIVE_TOPICS_TABLE).values(
                scope_id=scope_id,
                family=TopicMemory.family,
                artifact_id=ref.artifact_id,
                revision=ref.revision,
                title=topic.content.title,
                summary=topic.content.summary,
                searchable_text=projection.topic_searchable_text,
                source_count=len(topic.lineage.sources),
            )
        )
        await connection.execute(
            insert(TOPIC_MEMORY_ACTIVE_CHUNKS_TABLE),
            [
                {
                    "scope_id": scope_id,
                    "family": TopicMemory.family,
                    "artifact_id": ref.artifact_id,
                    "revision": ref.revision,
                    "chunk_ordinal": chunk.ordinal,
                    "start_offset": chunk.start_offset,
                    "end_offset": chunk.end_offset,
                    "chunk_text": chunk.text,
                    "searchable_text": analyze_text(chunk.text),
                    "policy_version": chunk.policy_version,
                }
                for chunk in projection.chunks
            ],
        )
        return published_at

    def _validate_projection(self, draft: TopicMemoryDraft, projection: TopicMemoryProjection) -> None:
        if projection.content != draft.content:
            raise TopicMemoryProjectionError("content")
        capabilities = self.index.capabilities
        if not capabilities.fts:
            raise TopicMemoryProjectionError("fts")
        has_vectors = projection.topic_embedding is not None or bool(projection.chunk_embeddings)
        if not capabilities.vector:
            if has_vectors or projection.embedding_profile is not None:
                raise TopicMemoryProjectionError("vector-unconfigured")
            return
        if projection.topic_embedding is None or len(projection.chunk_embeddings) != len(projection.chunks):
            raise TopicMemoryProjectionError("vector-incomplete")
        if projection.embedding_profile != capabilities.embedding_profile:
            raise TopicMemoryProjectionError("embedding-profile")
        profile = capabilities.embedding_profile
        if profile is None:
            raise TopicMemoryProjectionError("embedding-profile")
        try:
            validate_embedding(projection.topic_embedding, dimension=profile.dimension)
            for embedding in projection.chunk_embeddings:
                validate_embedding(embedding, dimension=profile.dimension)
        except ValueError:
            raise TopicMemoryProjectionError("embedding-values") from None

    def _select_mode(
        self,
        mode: TopicMemorySearchMode,
        query_vector: object,
        embedding_profile: object,
    ) -> TopicMemoryUsedSearchMode:
        capabilities = self.index.capabilities
        selected = "hybrid" if mode == "auto" and capabilities.hybrid and query_vector is not None else mode
        if selected == "auto":
            selected = "fts"
        if selected in {"vector", "hybrid"}:
            if not capabilities.vector:
                raise TopicMemoryCapabilityError(selected)
            if query_vector is None or embedding_profile != capabilities.embedding_profile:
                raise TopicMemoryCapabilityError("embedding-profile")
        if selected not in {"fts", "vector", "hybrid"}:
            raise TopicMemoryCapabilityError(selected)
        return selected


def _aware_utc(value: object) -> datetime:
    if not isinstance(value, datetime):
        raise TopicMemoryStorageInvariantError("publication-time", value)
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _stored_utc(value: datetime) -> datetime:
    return value.astimezone(UTC).replace(tzinfo=None)


__all__ = ["TopicMemoryRepository"]
