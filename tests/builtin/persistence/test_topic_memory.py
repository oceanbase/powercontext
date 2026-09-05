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

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.memory import EmbeddingProfile
from powercontext.builtin.artifacts.topic_memory import (
    TopicMemory,
    TopicMemoryBrowseCursor,
    TopicMemoryCapabilityError,
    TopicMemoryChunk,
    TopicMemoryContent,
    TopicMemoryDraft,
    TopicMemoryProjection,
    TopicMemoryProjectionError,
    TopicMemorySearchChannels,
    TopicMemorySearchRequest,
    TopicMemoryStorageInvariantError,
    prepare_topic_memory_projection,
)
from powercontext.builtin.persistence.artifacts import ArtifactRepository
from powercontext.builtin.persistence.sources import SourceRepository
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.persistence.sqlite.topic_memory_index import (
    SQLITE_TOPIC_MEMORY_VECTOR_CHUNKS_TABLE,
    SQLiteTopicMemoryFTSIndex,
    SQLiteTopicMemoryVectorIndex,
)
from powercontext.builtin.persistence.tables import (
    ARTIFACTS_TABLE,
    BUILTIN_TABLES,
    TOPIC_MEMORY_ACTIVE_CHUNKS_TABLE,
    TOPIC_MEMORY_RETRIEVAL_SHAPE_TABLE,
    TOPIC_MEMORY_REVISION_PUBLICATIONS_TABLE,
)
from powercontext.builtin.persistence.topic_memory import TopicMemoryRepository
from powercontext.builtin.persistence.topic_memory_index import CompositeTopicMemoryIndex, TopicMemoryIndex
from powercontext.sources import SourceMaterialization, SourceRef
from tests.builtin.persistence.contract import SOURCE_ADAPTERS, NoteSource


class _SwitchableIndex:
    def __init__(self, delegate: TopicMemoryIndex) -> None:
        self.delegate = delegate
        self.capabilities = delegate.capabilities
        self.tables = delegate.tables
        self.fail_replace = False

    async def initialize(self, connection: AsyncConnection, /) -> None:
        await self.delegate.initialize(connection)

    async def replace(
        self,
        connection: AsyncConnection,
        scope_id: str,
        topic_ref: ArtifactRef,
        projection: TopicMemoryProjection,
        /,
    ) -> None:
        await self.delegate.replace(connection, scope_id, topic_ref, projection)
        if self.fail_replace:
            raise RuntimeError("injected projection failure")  # noqa: TRY003

    async def search(
        self,
        connection: AsyncConnection,
        scope_id: str,
        request: TopicMemorySearchRequest,
        /,
    ) -> TopicMemorySearchChannels:
        return await self.delegate.search(connection, scope_id, request)

    async def vector_complete(
        self,
        connection: AsyncConnection,
        scope_id: str,
        topic_ref: ArtifactRef,
        /,
    ) -> bool:
        return await self.delegate.vector_complete(connection, scope_id, topic_ref)


def _content(label: str, detail_term: str) -> TopicMemoryContent:
    return TopicMemoryContent(
        title=f"{label} recovery",
        summary=f"{label} leader state is durable.",
        detail=f"# {label}\n\nThe searchable detail contains {detail_term} evidence.",
    )


def _draft(
    content: TopicMemoryContent,
    *,
    sources: tuple[SourceRef, ...] = (),
    artifacts: tuple[ArtifactRef, ...] = (),
) -> TopicMemoryDraft:
    return TopicMemoryDraft(content=content, sources=sources, artifacts=artifacts)


def _fts_index() -> CompositeTopicMemoryIndex:
    return CompositeTopicMemoryIndex(SQLiteTopicMemoryFTSIndex())


def test_publication_retains_exact_revisions_and_searches_only_the_current_scope_head() -> None:
    async def scenario() -> None:
        index = _fts_index()
        repository = TopicMemoryRepository(index=index)
        sources = SourceRepository(SOURCE_ADAPTERS)
        async with SQLiteProfile.open(SQLiteConfig(), tables=BUILTIN_TABLES + index.tables) as profile:
            async with profile.database.transaction() as connection:
                await repository.initialize(connection)
                source = await sources.add(
                    connection,
                    "scope-a",
                    NoteSource(
                        name="note-1",
                        materialization=SourceMaterialization.CAPTURED,
                        body="Topic evidence",
                    ),
                )

            first_content = _content("Legacy", "saffron")
            first_draft = _draft(first_content, sources=(source.ref,))
            async with profile.database.transaction() as connection:
                first = await repository.publish_create(
                    connection,
                    "scope-a",
                    "topic-1",
                    first_draft,
                    prepare_topic_memory_projection(first_content),
                )

            second_content = _content("Current", "zircon")
            second_draft = _draft(
                second_content,
                sources=(source.ref,),
                artifacts=(first.topic.as_ref(),),
            )
            async with profile.database.transaction() as connection:
                second = await repository.publish_revision(
                    connection,
                    "scope-a",
                    first.topic,
                    second_draft,
                    prepare_topic_memory_projection(second_content),
                )

            other_content = _content("Other", "zircon")
            async with profile.database.transaction() as connection:
                await repository.publish_create(
                    connection,
                    "scope-b",
                    "topic-other",
                    _draft(other_content),
                    prepare_topic_memory_projection(other_content),
                )

            async with profile.database.transaction() as connection:
                old = await repository.get_exact(connection, "scope-a", first.topic.as_ref())
                current = await repository.get_exact(connection, "scope-a", second.topic.as_ref())
                browse = await repository.browse_current(connection, "scope-a", limit=10)
                old_search = await repository.search(connection, "scope-a", "saffron", limit=10)
                current_search = await repository.search(connection, "scope-a", "zircon", limit=10)
                publications = int(
                    await connection.scalar(
                        select(func.count())
                        .select_from(TOPIC_MEMORY_REVISION_PUBLICATIONS_TABLE)
                        .where(TOPIC_MEMORY_REVISION_PUBLICATIONS_TABLE.c.scope_id == "scope-a")
                    )
                    or 0
                )

            assert old.topic == first.topic
            assert old.published_at.tzinfo == UTC
            assert not old.is_current
            assert old.current_artifact == second.topic.as_ref()
            assert current.is_current
            assert current.topic.lineage.sources == (source.ref,)
            assert current.topic.lineage.artifacts == (first.topic.as_ref(),)
            assert tuple(item.artifact_ref for item in browse) == (second.topic.as_ref(),)
            assert browse[0].source_count == 1
            assert old_search.hits == ()
            assert tuple(hit.artifact_ref for hit in current_search.hits) == (second.topic.as_ref(),)
            assert "detail_fts" in current_search.hits[0].matched_by
            assert publications == 2

    asyncio.run(scenario())


def test_current_browse_uses_stable_exclusive_keyset_order() -> None:
    async def scenario() -> None:
        index = _fts_index()
        repository = TopicMemoryRepository(index=index)
        async with SQLiteProfile.open(SQLiteConfig(), tables=BUILTIN_TABLES + index.tables) as profile:
            async with profile.database.transaction() as connection:
                await repository.initialize(connection)
                for artifact_id in ("topic-c", "topic-a", "topic-b"):
                    content = _content(artifact_id, artifact_id)
                    await repository.publish_create(
                        connection,
                        "scope-a",
                        artifact_id,
                        _draft(content),
                        prepare_topic_memory_projection(content),
                    )
                frozen_time = datetime(2026, 9, 5, 3, 4, 5, tzinfo=UTC)
                await connection.execute(
                    update(TOPIC_MEMORY_REVISION_PUBLICATIONS_TABLE).values(published_at=frozen_time)
                )

            async with profile.database.transaction() as connection:
                first_page = await repository.browse_current(connection, "scope-a", limit=2)
                boundary = first_page[-1]
                second_page = await repository.browse_current(
                    connection,
                    "scope-a",
                    limit=2,
                    after=TopicMemoryBrowseCursor(
                        published_at=boundary.published_at,
                        artifact_id=boundary.artifact_ref.artifact_id,
                        revision=boundary.artifact_ref.revision,
                    ),
                )

            assert [item.artifact_ref.artifact_id for item in first_page] == ["topic-a", "topic-b"]
            assert [item.artifact_ref.artifact_id for item in second_page] == ["topic-c"]

    asyncio.run(scenario())


def test_failed_projection_replace_rolls_back_revision_head_publication_and_fts_switch() -> None:
    async def scenario() -> None:
        index = _SwitchableIndex(_fts_index())
        repository = TopicMemoryRepository(index=index)
        async with SQLiteProfile.open(SQLiteConfig(), tables=BUILTIN_TABLES + index.tables) as profile:
            original_content = _content("Original", "amber")
            async with profile.database.transaction() as connection:
                await repository.initialize(connection)
                original = await repository.publish_create(
                    connection,
                    "scope-a",
                    "topic-1",
                    _draft(original_content),
                    prepare_topic_memory_projection(original_content),
                )

            index.fail_replace = True
            revised_content = _content("Revised", "cobalt")
            with pytest.raises(RuntimeError, match="injected projection failure"):
                async with profile.database.transaction() as connection:
                    await repository.publish_revision(
                        connection,
                        "scope-a",
                        original.topic,
                        _draft(revised_content),
                        prepare_topic_memory_projection(revised_content),
                    )
            index.fail_replace = False

            async with profile.database.transaction() as connection:
                latest = await repository.get_exact(connection, "scope-a", original.topic.as_ref())
                amber = await repository.search(connection, "scope-a", "amber", limit=10)
                cobalt = await repository.search(connection, "scope-a", "cobalt", limit=10)
                revision_count = int(
                    await connection.scalar(
                        select(func.count())
                        .select_from(ARTIFACTS_TABLE)
                        .where(
                            ARTIFACTS_TABLE.c.scope_id == "scope-a",
                            ARTIFACTS_TABLE.c.family == TopicMemory.family,
                            ARTIFACTS_TABLE.c.artifact_id == "topic-1",
                        )
                    )
                    or 0
                )
                publication_count = int(
                    await connection.scalar(
                        select(func.count())
                        .select_from(TOPIC_MEMORY_REVISION_PUBLICATIONS_TABLE)
                        .where(
                            TOPIC_MEMORY_REVISION_PUBLICATIONS_TABLE.c.scope_id == "scope-a",
                            TOPIC_MEMORY_REVISION_PUBLICATIONS_TABLE.c.artifact_id == "topic-1",
                        )
                    )
                    or 0
                )

            assert latest.is_current
            assert latest.current_artifact.revision == 1
            assert amber.hits[0].artifact_ref.revision == 1
            assert cobalt.hits == ()
            assert revision_count == publication_count == 1

    asyncio.run(scenario())


def test_detail_fts_limits_distinct_topics_after_selecting_each_best_chunk() -> None:
    async def scenario() -> None:
        index = _fts_index()
        repository = TopicMemoryRepository(index=index)
        async with SQLiteProfile.open(SQLiteConfig(), tables=BUILTIN_TABLES + index.tables) as profile:
            heavy = TopicMemoryContent(
                title="Heavy recovery",
                summary="Many repeated detail matches.",
                detail="needle " * 13_000,
            )
            light = TopicMemoryContent(
                title="Light recovery",
                summary="One relevant detail match.",
                detail=f"{'unrelated ' * 120}needle",
            )
            assert len(prepare_topic_memory_projection(heavy).chunks) > 50
            async with profile.database.transaction() as connection:
                await repository.initialize(connection)
                for artifact_id, content in (("topic-heavy", heavy), ("topic-light", light)):
                    await repository.publish_create(
                        connection,
                        "scope-a",
                        artifact_id,
                        _draft(content),
                        prepare_topic_memory_projection(content),
                    )

            async with profile.database.transaction() as connection:
                result = await repository.search(connection, "scope-a", "needle", limit=2)

            assert {hit.artifact_ref.artifact_id for hit in result.hits} == {"topic-heavy", "topic-light"}
            assert all(hit.matched_by == ("detail_fts",) for hit in result.hits)

    asyncio.run(scenario())


def test_publish_rejects_noncanonical_chunks_and_lexical_text_before_writes() -> None:
    async def scenario() -> None:
        index = _fts_index()
        repository = TopicMemoryRepository(index=index)
        content = TopicMemoryContent(
            title="Canonical projection",
            summary="Publication validates rebuildable fields.",
            detail="A" * 2_000,
        )
        canonical = prepare_topic_memory_projection(content)
        malformed = (
            TopicMemoryProjection(
                content=content,
                chunks=canonical.chunks[:-1],
                topic_searchable_text=canonical.topic_searchable_text,
            ),
            TopicMemoryProjection(
                content=content,
                chunks=(
                    TopicMemoryChunk(ordinal=0, start_offset=0, end_offset=900, text=content.detail[:900]),
                    TopicMemoryChunk(ordinal=1, start_offset=1_100, end_offset=2_000, text=content.detail[1_100:]),
                ),
                topic_searchable_text=canonical.topic_searchable_text,
            ),
            TopicMemoryProjection(
                content=content,
                chunks=(
                    TopicMemoryChunk(ordinal=0, start_offset=0, end_offset=1_200, text=content.detail[:1_200]),
                    TopicMemoryChunk(ordinal=1, start_offset=1_000, end_offset=2_000, text=content.detail[1_000:]),
                ),
                topic_searchable_text=canonical.topic_searchable_text,
            ),
            canonical.model_copy(update={"topic_searchable_text": f"{canonical.topic_searchable_text} forged"}),
        )
        expected_codes = ("chunks", "chunks", "chunks", "lexical")
        async with SQLiteProfile.open(SQLiteConfig(), tables=BUILTIN_TABLES + index.tables) as profile:
            async with profile.database.transaction() as connection:
                await repository.initialize(connection)
                for position, (projection, code) in enumerate(zip(malformed, expected_codes, strict=True)):
                    with pytest.raises(TopicMemoryProjectionError, match=r"canonical|Analyzer") as caught:
                        await repository.publish_create(
                            connection,
                            "scope-a",
                            f"topic-{position}",
                            _draft(content),
                            projection,
                        )
                    assert caught.value.code == code
                artifact_count = int(await connection.scalar(select(func.count()).select_from(ARTIFACTS_TABLE)) or 0)
                publication_count = int(
                    await connection.scalar(select(func.count()).select_from(TOPIC_MEMORY_REVISION_PUBLICATIONS_TABLE))
                    or 0
                )

            assert artifact_count == publication_count == 0

    asyncio.run(scenario())


def test_retrieval_shape_is_persistent_and_rejects_bidirectional_downgrades(tmp_path: Path) -> None:
    async def scenario() -> None:
        embedding_profile = EmbeddingProfile(
            profile_id="topic-test-v1",
            model="test",
            dimension=2,
            distance="l2",
            normalization="unit",
        )
        hybrid_index = CompositeTopicMemoryIndex(
            SQLiteTopicMemoryFTSIndex(),
            SQLiteTopicMemoryVectorIndex(embedding_profile),
        )
        fts_index = _fts_index()

        hybrid_config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'hybrid.db'}")
        async with SQLiteProfile.open(
            hybrid_config,
            tables=BUILTIN_TABLES + hybrid_index.tables,
            load_vector_extension=True,
        ) as profile:
            repository = TopicMemoryRepository(index=hybrid_index)
            content = _content("Hybrid", "vector")
            base = prepare_topic_memory_projection(content)
            projection = base.model_copy(
                update={
                    "topic_embedding": (1.0, 0.0),
                    "chunk_embeddings": tuple((1.0, 0.0) for _chunk in base.chunks),
                    "embedding_profile": embedding_profile,
                }
            )
            async with profile.database.transaction() as connection:
                await repository.initialize(connection)
                await repository.publish_create(connection, "scope-a", "topic-1", _draft(content), projection)

        async with SQLiteProfile.open(hybrid_config, tables=BUILTIN_TABLES + fts_index.tables) as profile:
            with pytest.raises(TopicMemoryCapabilityError, match="retrieval-shape"):
                async with profile.database.transaction() as connection:
                    await TopicMemoryRepository(index=fts_index).initialize(connection)
            async with profile.database.transaction() as connection:
                assert int(await connection.scalar(select(func.count()).select_from(ARTIFACTS_TABLE)) or 0) == 1

        changed_profile = embedding_profile.model_copy(update={"profile_id": "topic-test-v2"})
        changed_index = CompositeTopicMemoryIndex(
            SQLiteTopicMemoryFTSIndex(),
            SQLiteTopicMemoryVectorIndex(changed_profile),
        )
        async with SQLiteProfile.open(
            hybrid_config,
            tables=BUILTIN_TABLES + changed_index.tables,
            load_vector_extension=True,
        ) as profile:
            with pytest.raises(TopicMemoryCapabilityError, match="retrieval-shape"):
                async with profile.database.transaction() as connection:
                    await TopicMemoryRepository(index=changed_index).initialize(connection)

        fts_config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'fts.db'}")
        async with (
            SQLiteProfile.open(fts_config, tables=BUILTIN_TABLES + fts_index.tables) as profile,
            profile.database.transaction() as connection,
        ):
            await TopicMemoryRepository(index=fts_index).initialize(connection)
            shape = await connection.scalar(select(TOPIC_MEMORY_RETRIEVAL_SHAPE_TABLE.c.shape))
            assert shape == "fts"

        async with SQLiteProfile.open(
            fts_config,
            tables=BUILTIN_TABLES + hybrid_index.tables,
            load_vector_extension=True,
        ) as profile:
            with pytest.raises(TopicMemoryCapabilityError, match="retrieval-shape"):
                async with profile.database.transaction() as connection:
                    await TopicMemoryRepository(index=hybrid_index).initialize(connection)

        async with (
            SQLiteProfile.open(fts_config, tables=BUILTIN_TABLES + fts_index.tables) as profile,
            profile.database.transaction() as connection,
        ):
            await TopicMemoryRepository(index=fts_index).initialize(connection)

    asyncio.run(scenario())


def test_startup_rejects_a_topic_revision_without_publication_metadata() -> None:
    async def scenario() -> None:
        index = _fts_index()
        repository = TopicMemoryRepository(index=index)
        artifacts = ArtifactRepository((TopicMemory,))
        async with SQLiteProfile.open(SQLiteConfig(), tables=BUILTIN_TABLES + index.tables) as profile:
            content = _content("Legacy", "unpublished")
            async with profile.database.transaction() as connection:
                await artifacts.create(connection, "scope-a", "topic-1", _draft(content))

            with pytest.raises(TopicMemoryStorageInvariantError, match="missing-publication"):
                async with profile.database.transaction() as connection:
                    await repository.initialize(connection)

    asyncio.run(scenario())


def test_startup_rejects_a_chunk_that_is_not_part_of_the_current_head() -> None:
    async def scenario() -> None:
        index = _fts_index()
        repository = TopicMemoryRepository(index=index)
        async with SQLiteProfile.open(SQLiteConfig(), tables=BUILTIN_TABLES + index.tables) as profile:
            first_content = _content("First", "amber")
            second_content = _content("Second", "cobalt")
            async with profile.database.transaction() as connection:
                await repository.initialize(connection)
                first = await repository.publish_create(
                    connection,
                    "scope-a",
                    "topic-1",
                    _draft(first_content),
                    prepare_topic_memory_projection(first_content),
                )
                await repository.publish_revision(
                    connection,
                    "scope-a",
                    first.topic,
                    _draft(second_content),
                    prepare_topic_memory_projection(second_content),
                )
                await connection.execute(
                    insert(TOPIC_MEMORY_ACTIVE_CHUNKS_TABLE).values(
                        scope_id="scope-a",
                        family=TopicMemory.family,
                        artifact_id="topic-1",
                        revision=1,
                        chunk_ordinal=99,
                        start_offset=0,
                        end_offset=5,
                        chunk_text="stale",
                        searchable_text="stale",
                        policy_version="markdown-v1",
                    )
                )

            with pytest.raises(TopicMemoryStorageInvariantError, match="active-chunk-not-head"):
                async with profile.database.transaction() as connection:
                    await repository.initialize(connection)

    asyncio.run(scenario())


def test_sqlite_vector_and_hybrid_require_complete_active_embeddings() -> None:
    async def scenario() -> None:
        embedding_profile = EmbeddingProfile(
            profile_id="topic-test-v1",
            model="test",
            dimension=2,
            distance="l2",
            normalization="unit",
        )
        index = CompositeTopicMemoryIndex(
            SQLiteTopicMemoryFTSIndex(),
            SQLiteTopicMemoryVectorIndex(embedding_profile),
        )
        repository = TopicMemoryRepository(index=index)
        async with SQLiteProfile.open(
            SQLiteConfig(),
            tables=BUILTIN_TABLES + index.tables,
            load_vector_extension=True,
        ) as profile:
            async with profile.database.transaction() as connection:
                await repository.initialize(connection)
                incomplete_content = _content("Missing", "vector")
                with pytest.raises(TopicMemoryProjectionError, match="requires the topic and every detail chunk"):
                    await repository.publish_create(
                        connection,
                        "scope-a",
                        "topic-missing",
                        _draft(incomplete_content),
                        prepare_topic_memory_projection(incomplete_content),
                    )
                assert (
                    int(
                        await connection.scalar(
                            select(func.count())
                            .select_from(ARTIFACTS_TABLE)
                            .where(ARTIFACTS_TABLE.c.artifact_id == "topic-missing")
                        )
                        or 0
                    )
                    == 0
                )
                for artifact_id, label, vector in (
                    ("topic-alpha", "Alpha", (1.0, 0.0)),
                    ("topic-beta", "Beta", (0.0, 1.0)),
                ):
                    content = _content(label, label.casefold())
                    base = prepare_topic_memory_projection(content)
                    projection = TopicMemoryProjection(
                        content=content,
                        chunks=base.chunks,
                        topic_searchable_text=base.topic_searchable_text,
                        topic_embedding=vector,
                        chunk_embeddings=tuple(vector for _chunk in base.chunks),
                        embedding_profile=embedding_profile,
                    )
                    await repository.publish_create(
                        connection,
                        "scope-a",
                        artifact_id,
                        _draft(content),
                        projection,
                    )

            async with profile.database.transaction() as connection:
                vector_result = await repository.search(
                    connection,
                    "scope-a",
                    "semantic alpha",
                    limit=10,
                    mode="vector",
                    query_vector=(1.0, 0.0),
                    embedding_profile=embedding_profile,
                )
                hybrid_result = await repository.search(
                    connection,
                    "scope-a",
                    "alpha",
                    limit=10,
                    mode="hybrid",
                    query_vector=(1.0, 0.0),
                    embedding_profile=embedding_profile,
                )
                vector_without_lexical_terms = await repository.search(
                    connection,
                    "scope-a",
                    "!!!",
                    limit=10,
                    mode="vector",
                    query_vector=(1.0, 0.0),
                    embedding_profile=embedding_profile,
                )

            assert vector_result.mode == "vector"
            assert vector_result.hits[0].artifact_ref.artifact_id == "topic-alpha"
            assert set(vector_result.hits[0].matched_by) == {"topic_vector", "detail_vector"}
            assert hybrid_result.mode == "hybrid"
            assert set(hybrid_result.hits[0].matched_by) == {
                "topic_fts",
                "topic_vector",
                "detail_fts",
                "detail_vector",
            }
            assert vector_without_lexical_terms.hits[0].artifact_ref.artifact_id == "topic-alpha"

            async with profile.database.transaction() as connection:
                await connection.execute(
                    update(SQLITE_TOPIC_MEMORY_VECTOR_CHUNKS_TABLE)
                    .where(SQLITE_TOPIC_MEMORY_VECTOR_CHUNKS_TABLE.c.artifact_id == "topic-alpha")
                    .values(chunk_ordinal=99)
                )
            with pytest.raises(TopicMemoryStorageInvariantError, match="incomplete-vector"):
                async with profile.database.transaction() as connection:
                    await repository.initialize(connection)
            async with profile.database.transaction() as connection:
                await connection.execute(
                    update(SQLITE_TOPIC_MEMORY_VECTOR_CHUNKS_TABLE)
                    .where(SQLITE_TOPIC_MEMORY_VECTOR_CHUNKS_TABLE.c.artifact_id == "topic-alpha")
                    .values(chunk_ordinal=0)
                )
                await connection.execute(
                    delete(SQLITE_TOPIC_MEMORY_VECTOR_CHUNKS_TABLE).where(
                        SQLITE_TOPIC_MEMORY_VECTOR_CHUNKS_TABLE.c.artifact_id == "topic-alpha"
                    )
                )
            with pytest.raises(TopicMemoryStorageInvariantError, match="incomplete-vector"):
                async with profile.database.transaction() as connection:
                    await repository.initialize(connection)

    asyncio.run(scenario())


def test_detail_vector_limits_distinct_topics_after_selecting_each_best_chunk() -> None:
    async def scenario() -> None:
        embedding_profile = EmbeddingProfile(
            profile_id="topic-test-v1",
            model="test",
            dimension=2,
            distance="l2",
            normalization="unit",
        )
        index = CompositeTopicMemoryIndex(
            SQLiteTopicMemoryFTSIndex(),
            SQLiteTopicMemoryVectorIndex(embedding_profile),
        )
        repository = TopicMemoryRepository(index=index)
        heavy = TopicMemoryContent(
            title="Heavy vector recovery",
            summary="Many equally close detail chunks.",
            detail="evidence " * 12_000,
        )
        light = _content("Light vector", "evidence")
        async with SQLiteProfile.open(
            SQLiteConfig(),
            tables=BUILTIN_TABLES + index.tables,
            load_vector_extension=True,
        ) as profile:
            async with profile.database.transaction() as connection:
                await repository.initialize(connection)
                for artifact_id, content in (("topic-heavy", heavy), ("topic-light", light)):
                    base = prepare_topic_memory_projection(content)
                    assert artifact_id != "topic-heavy" or len(base.chunks) > 50
                    projection = base.model_copy(
                        update={
                            "topic_embedding": (0.0, 1.0),
                            "chunk_embeddings": tuple((1.0, 0.0) for _chunk in base.chunks),
                            "embedding_profile": embedding_profile,
                        }
                    )
                    await repository.publish_create(
                        connection,
                        "scope-a",
                        artifact_id,
                        _draft(content),
                        projection,
                    )

            async with profile.database.transaction() as connection:
                result = await repository.search(
                    connection,
                    "scope-a",
                    "semantic evidence",
                    limit=2,
                    mode="vector",
                    query_vector=(1.0, 0.0),
                    embedding_profile=embedding_profile,
                )

            assert {hit.artifact_ref.artifact_id for hit in result.hits} == {"topic-heavy", "topic-light"}
            assert all(hit.matched_by == ("detail_vector",) for hit in result.hits)

    asyncio.run(scenario())


def test_sqlite_vector_probe_rejects_a_detail_channel_dimension_mismatch() -> None:
    async def scenario() -> None:
        embedding_profile = EmbeddingProfile(
            profile_id="topic-test-v1",
            model="test",
            dimension=3,
            distance="l2",
            normalization="unit",
        )
        index = SQLiteTopicMemoryVectorIndex(embedding_profile)
        async with (
            SQLiteProfile.open(
                SQLiteConfig(),
                tables=index.tables,
                load_vector_extension=True,
            ) as profile,
            profile.database.transaction() as connection,
        ):
            await connection.exec_driver_sql(
                "CREATE VIRTUAL TABLE pc_topic_memory_topic_vec USING vec0(embedding float[3])"
            )
            await connection.exec_driver_sql(
                "CREATE VIRTUAL TABLE pc_topic_memory_chunk_vec USING vec0(embedding float[4])"
            )
            with pytest.raises(TopicMemoryCapabilityError, match="sqlite-vec probe failed"):
                await index.initialize(connection)

    asyncio.run(scenario())
