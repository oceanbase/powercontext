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
import struct
from pathlib import Path

import pytest
from sqlalchemy import inspect, select, text, update

from powercontext.builtin.artifacts.memory import EmbeddingProfile
from powercontext.builtin.artifacts.topic_memory import (
    TopicMemoryCapabilityError,
    TopicMemoryContent,
    TopicMemoryDraft,
    TopicMemoryStorageInvariantError,
    prepare_topic_memory_projection,
)
from powercontext.builtin.inference import EmbeddingResult
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.persistence.sqlite.topic_memory_index import (
    SQLiteTopicMemoryFTSIndex,
    SQLiteTopicMemoryVectorIndex,
)
from powercontext.builtin.persistence.sqlite.topic_memory_reindex import SQLiteTopicMemoryReindexBackend
from powercontext.builtin.persistence.tables import (
    ARTIFACTS_TABLE,
    BUILTIN_TABLES,
    TOPIC_MEMORY_ACTIVE_CHUNKS_TABLE,
    TOPIC_MEMORY_ACTIVE_TOPICS_TABLE,
    TOPIC_MEMORY_RETRIEVAL_SHAPE_TABLE,
)
from powercontext.builtin.persistence.topic_memory import TopicMemoryRepository
from powercontext.builtin.persistence.topic_memory_index import (
    CompositeTopicMemoryIndex,
    topic_memory_embedding_profile_fingerprint,
)
from powercontext.builtin.persistence.topic_memory_reindex import (
    pending_sources,
    reindex_topic_memory,
)


def _profile(version: str, dimension: int) -> EmbeddingProfile:
    return EmbeddingProfile(
        profile_id=f"topic-reindex-{version}",
        model="test",
        dimension=dimension,
        distance="l2",
        normalization="unit",
    )


def _index(profile: EmbeddingProfile) -> CompositeTopicMemoryIndex:
    return CompositeTopicMemoryIndex(SQLiteTopicMemoryFTSIndex(), SQLiteTopicMemoryVectorIndex(profile))


class _Embedding:
    def __init__(self, profile: EmbeddingProfile, *, fail_on_call: int | None = None) -> None:
        self.profile = profile
        self.fail_on_call = fail_on_call
        self.calls: list[tuple[str, ...]] = []

    async def embed(self, texts: tuple[str, ...], /) -> EmbeddingResult:
        self.calls.append(texts)
        if self.fail_on_call == len(self.calls):
            raise RuntimeError("injected embedding interruption")  # noqa: TRY003
        vector = (1.0, *(0.0,) * (self.profile.dimension - 1))
        return EmbeddingResult(vectors=(vector,) * len(texts))


async def _publish(
    profile: SQLiteProfile,
    embedding_profile: EmbeddingProfile,
    count: int = 2,
    *,
    vectorized: bool = True,
) -> None:
    index = _index(embedding_profile) if vectorized else SQLiteTopicMemoryFTSIndex()
    repository = TopicMemoryRepository(index=index)
    async with profile.database.transaction() as connection:
        await repository.initialize(connection)
        for number in range(count):
            content = TopicMemoryContent(
                title=f"Topic {number}",
                summary=f"Summary {number} retains its exact text.",
                detail=f"Detail {number} has enough evidence for one or more active chunks.",
            )
            base = prepare_topic_memory_projection(content)
            vector = (1.0, *(0.0,) * (embedding_profile.dimension - 1))
            projection = (
                base.model_copy(
                    update={
                        "topic_embedding": vector,
                        "chunk_embeddings": (vector,) * len(base.chunks),
                        "embedding_profile": embedding_profile,
                    }
                )
                if vectorized
                else base
            )
            await repository.publish_create(
                connection,
                "scope-a",
                f"topic-{number}",
                TopicMemoryDraft(content=content),
                projection,
            )


async def _authority(connection):
    return {
        name: (await connection.execute(select(table).order_by(*table.primary_key.columns))).all()
        for name, table in (
            ("artifacts", ARTIFACTS_TABLE),
            ("topics", TOPIC_MEMORY_ACTIVE_TOPICS_TABLE),
            ("chunks", TOPIC_MEMORY_ACTIVE_CHUNKS_TABLE),
        )
    }


async def _active_fingerprint(connection) -> str:
    return str(
        await connection.scalar(
            select(TOPIC_MEMORY_RETRIEVAL_SHAPE_TABLE.c.profile_fingerprint).where(
                TOPIC_MEMORY_RETRIEVAL_SHAPE_TABLE.c.singleton == 1
            )
        )
    )


def test_sqlite_reindex_dry_run_resume_idempotence_and_search(tmp_path: Path) -> None:
    async def scenario() -> None:
        old_profile, new_profile = _profile("old", 2), _profile("new", 3)
        config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'reindex.db'}")
        old_index = _index(old_profile)
        async with SQLiteProfile.open(
            config, tables=BUILTIN_TABLES + old_index.tables, load_vector_extension=True
        ) as profile:
            await _publish(profile, old_profile)
            async with profile.database.transaction() as connection:
                before = await _authority(connection)
                old_fingerprint = await _active_fingerprint(connection)
            assert old_fingerprint == topic_memory_embedding_profile_fingerprint(old_profile)

            backend = SQLiteTopicMemoryReindexBackend(new_profile)
            async with profile.database.transaction() as connection:
                dry_run = await reindex_topic_memory(
                    profile.database,
                    backend,
                    embedding_model=None,
                    maintenance_confirmed=False,
                    dry_run=True,
                )
                names = set(await connection.run_sync(lambda sync: inspect(sync).get_table_names()))
            assert dry_run.ready and not dry_run.activated
            assert dry_run.counts["topics_total"] == 2
            assert dry_run.counts["topics_remaining"] == 2
            assert not any(name.startswith("pc_topic_memory_reindex") for name in names)

            interrupted = _Embedding(new_profile, fail_on_call=2)
            progress = []
            with pytest.raises(RuntimeError, match="injected embedding interruption"):
                await reindex_topic_memory(
                    profile.database,
                    backend,
                    embedding_model=interrupted,
                    maintenance_confirmed=True,
                    batch_size=1,
                    progress=progress.append,
                )
            assert len(interrupted.calls) == 2
            assert len(progress) == 1
            assert progress[0]["topics_processed"] == 1
            async with profile.database.transaction() as connection:
                assert await _active_fingerprint(connection) == old_fingerprint
                await backend.validate_active(connection, old_fingerprint)
                assert await _authority(connection) == before

            resumed = _Embedding(new_profile)
            report = await reindex_topic_memory(
                profile.database,
                backend,
                embedding_model=resumed,
                maintenance_confirmed=True,
                batch_size=1,
            )
            assert report.ready and report.activated
            assert report.counts["topics_processed"] == report.counts["topics_total"] == 2
            assert report.counts["chunks_remaining"] == 0
            # The first committed Topic and all of its chunks are not embedded again.
            assert len(resumed.calls) == 1
            async with profile.database.transaction() as connection:
                assert await _active_fingerprint(connection) == backend.fingerprint
                assert await _authority(connection) == before
                names = set(await connection.run_sync(lambda sync: inspect(sync).get_table_names()))
                assert not any(name.startswith("pc_topic_memory_reindex") for name in names)
                await TopicMemoryRepository(index=_index(new_profile)).initialize(connection, validate_only=True)
                result = await TopicMemoryRepository(index=_index(new_profile)).search(
                    connection,
                    "scope-a",
                    "summary",
                    limit=10,
                    mode="vector",
                    query_vector=(1.0, 0.0, 0.0),
                    embedding_profile=new_profile,
                )
                assert {hit.artifact_ref.artifact_id for hit in result.hits} == {"topic-0", "topic-1"}

            no_work = _Embedding(new_profile, fail_on_call=1)
            repeated = await reindex_topic_memory(
                profile.database,
                backend,
                embedding_model=no_work,
                maintenance_confirmed=True,
            )
            assert repeated.ready and repeated.activated and repeated.embedded_vectors == 0
            assert no_work.calls == []

    asyncio.run(scenario())


@pytest.mark.parametrize("interruption", ["empty-shape", "missing-shape", "missing-native-vectors"])
def test_sqlite_reindex_resumes_partial_staging_ddl(tmp_path: Path, interruption: str) -> None:
    async def scenario() -> None:
        old_profile, new_profile = _profile("old", 2), _profile("new", 3)
        config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'partial-ddl.db'}")
        old_index = _index(old_profile)
        async with SQLiteProfile.open(
            config, tables=BUILTIN_TABLES + old_index.tables, load_vector_extension=True
        ) as profile:
            await _publish(profile, old_profile, count=1)
            backend = SQLiteTopicMemoryReindexBackend(new_profile)
            async with profile.database.transaction() as connection:
                await backend.prepare(connection, update_storage_profile=False)
                sources = await pending_sources(connection, backend, after=None, batch_size=1)
                expected_embeddings = sum(len(source.embedding_inputs) for source in sources)
                sentinel = (0.0, 0.0, 1.0)
                await backend.write_staging(connection, sources, (sentinel,) * expected_embeddings)
                if interruption == "empty-shape":
                    await connection.exec_driver_sql("DELETE FROM pc_topic_memory_reindex_shape")
                elif interruption == "missing-shape":
                    await connection.exec_driver_sql("DROP TABLE pc_topic_memory_reindex_shape")
                else:
                    await connection.exec_driver_sql("DROP TABLE pc_topic_memory_reindex_topic_vec")
                    await connection.exec_driver_sql("DROP TABLE pc_topic_memory_reindex_chunk_vec")
                before = await _authority(connection)
                old_fingerprint = await _active_fingerprint(connection)

            dry_run = await reindex_topic_memory(
                profile.database,
                backend,
                embedding_model=None,
                maintenance_confirmed=False,
                dry_run=True,
            )
            assert dry_run.ready and dry_run.counts["topics_remaining"] == 1

            model = _Embedding(new_profile)
            report = await reindex_topic_memory(
                profile.database,
                backend,
                embedding_model=model,
                maintenance_confirmed=True,
            )

            assert report.ready and report.activated
            assert report.embedded_vectors == expected_embeddings
            assert len(model.calls) == 1
            async with profile.database.transaction() as connection:
                assert await _active_fingerprint(connection) == backend.fingerprint
                assert await _authority(connection) == before
                await backend.validate_active(connection, backend.fingerprint)
                assert old_fingerprint != backend.fingerprint
                active_embedding = await connection.scalar(text("SELECT embedding FROM pc_topic_memory_topic_vec"))
                assert active_embedding == struct.pack("<3f", 1.0, 0.0, 0.0)

    asyncio.run(scenario())


def test_sqlite_reindex_adds_vectors_to_an_fts_only_generation(tmp_path: Path) -> None:
    async def scenario() -> None:
        old_profile, new_profile = _profile("old", 2), _profile("new", 3)
        fts_index = SQLiteTopicMemoryFTSIndex()
        config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'fts-only.db'}")
        async with SQLiteProfile.open(
            config, tables=BUILTIN_TABLES + fts_index.tables, load_vector_extension=True
        ) as profile:
            await _publish(profile, old_profile, count=2, vectorized=False)
            async with profile.database.transaction() as connection:
                before = await _authority(connection)
                shape = (
                    await connection.execute(
                        select(
                            TOPIC_MEMORY_RETRIEVAL_SHAPE_TABLE.c.shape,
                            TOPIC_MEMORY_RETRIEVAL_SHAPE_TABLE.c.profile_fingerprint,
                        )
                    )
                ).one()
                assert tuple(shape) == ("fts", None)

            backend = SQLiteTopicMemoryReindexBackend(new_profile)
            dry_run = await reindex_topic_memory(
                profile.database,
                backend,
                embedding_model=None,
                maintenance_confirmed=False,
                dry_run=True,
            )
            assert dry_run.ready and dry_run.counts["topics_remaining"] == 2

            report = await reindex_topic_memory(
                profile.database,
                backend,
                embedding_model=_Embedding(new_profile),
                maintenance_confirmed=True,
                batch_size=1,
            )

            assert report.ready and report.activated
            async with profile.database.transaction() as connection:
                assert await _active_fingerprint(connection) == backend.fingerprint
                assert await _authority(connection) == before
                await backend.validate_active(connection, backend.fingerprint)
                result = await TopicMemoryRepository(index=_index(new_profile)).search(
                    connection,
                    "scope-a",
                    "summary",
                    limit=10,
                    mode="vector",
                    query_vector=(1.0, 0.0, 0.0),
                    embedding_profile=new_profile,
                )
                assert {hit.artifact_ref.artifact_id for hit in result.hits} == {"topic-0", "topic-1"}

    asyncio.run(scenario())


def test_sqlite_activation_failure_rolls_back_to_the_old_generation(tmp_path: Path) -> None:
    class FailingBackend(SQLiteTopicMemoryReindexBackend):
        async def activate(self, connection, *, update_storage_profile: bool) -> None:
            await super().activate(connection, update_storage_profile=update_storage_profile)
            raise RuntimeError("injected activation failure")  # noqa: TRY003

    async def scenario() -> None:
        old_profile, new_profile = _profile("old", 2), _profile("new", 4)
        config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'rollback.db'}")
        old_index = _index(old_profile)
        async with SQLiteProfile.open(
            config, tables=BUILTIN_TABLES + old_index.tables, load_vector_extension=True
        ) as profile:
            await _publish(profile, old_profile, count=1)
            async with profile.database.transaction() as connection:
                before = await _authority(connection)
                old_fingerprint = await _active_fingerprint(connection)
            failing = FailingBackend(new_profile)
            model = _Embedding(new_profile)
            with pytest.raises(RuntimeError, match="injected activation failure"):
                await reindex_topic_memory(
                    profile.database,
                    failing,
                    embedding_model=model,
                    maintenance_confirmed=True,
                )
            async with profile.database.transaction() as connection:
                assert await _active_fingerprint(connection) == old_fingerprint
                await failing.validate_active(connection, old_fingerprint)
                assert await _authority(connection) == before
                old_topic_bytes = await connection.scalar(
                    text("SELECT length(embedding) FROM pc_topic_memory_topic_vec")
                )
                assert old_topic_bytes == old_profile.dimension * 4

            # Staging survived the rollback, so recovery needs no provider at all.
            report = await reindex_topic_memory(
                profile.database,
                SQLiteTopicMemoryReindexBackend(new_profile),
                embedding_model=None,
                maintenance_confirmed=True,
            )
            assert report.ready and report.activated
            async with profile.database.transaction() as connection:
                assert await _authority(connection) == before
                new_topic_bytes = await connection.scalar(
                    text("SELECT length(embedding) FROM pc_topic_memory_topic_vec")
                )
                assert new_topic_bytes == new_profile.dimension * 4

    asyncio.run(scenario())


def test_sqlite_reindex_rechecks_source_after_provider_io(tmp_path: Path) -> None:
    async def scenario() -> None:
        old_profile, new_profile = _profile("old", 2), _profile("new", 3)
        config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'changed.db'}")
        old_index = _index(old_profile)
        async with SQLiteProfile.open(
            config, tables=BUILTIN_TABLES + old_index.tables, load_vector_extension=True
        ) as profile:
            await _publish(profile, old_profile, count=1)
            async with profile.database.transaction() as connection:
                before = await _authority(connection)
                old_fingerprint = await _active_fingerprint(connection)

            class ChangingEmbedding(_Embedding):
                async def embed(self, texts: tuple[str, ...], /) -> EmbeddingResult:
                    result = await super().embed(texts)
                    async with profile.database.transaction() as connection:
                        await connection.execute(
                            update(TOPIC_MEMORY_ACTIVE_TOPICS_TABLE)
                            .where(TOPIC_MEMORY_ACTIVE_TOPICS_TABLE.c.artifact_id == "topic-0")
                            .values(title="changed while the provider was running")
                        )
                    return result

            with pytest.raises(TopicMemoryStorageInvariantError, match="reindex-source-changed"):
                await reindex_topic_memory(
                    profile.database,
                    SQLiteTopicMemoryReindexBackend(new_profile),
                    embedding_model=ChangingEmbedding(new_profile),
                    maintenance_confirmed=True,
                )
            async with profile.database.transaction() as connection:
                assert await _active_fingerprint(connection) == old_fingerprint
                current = await _authority(connection)
                assert current["artifacts"] == before["artifacts"]
                assert current["chunks"] == before["chunks"]
                # Only the deliberately simulated concurrent writer changed a source row.
                assert current["topics"] != before["topics"]

    asyncio.run(scenario())


def test_sqlite_reindex_provider_failure_preserves_active_vectors(tmp_path: Path) -> None:
    class MalformedEmbedding(_Embedding):
        async def embed(self, texts: tuple[str, ...], /) -> EmbeddingResult:
            self.calls.append(texts)
            return EmbeddingResult(vectors=((1.0, 0.0),) * len(texts))

    async def scenario() -> None:
        old_profile, new_profile = _profile("old", 2), _profile("new", 3)
        config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'provider.db'}")
        old_index = _index(old_profile)
        async with SQLiteProfile.open(
            config, tables=BUILTIN_TABLES + old_index.tables, load_vector_extension=True
        ) as profile:
            await _publish(profile, old_profile, count=1)
            async with profile.database.transaction() as connection:
                before = await _authority(connection)
                old_fingerprint = await _active_fingerprint(connection)
            backend = SQLiteTopicMemoryReindexBackend(new_profile)
            with pytest.raises(TopicMemoryCapabilityError, match="embedding-values"):
                await reindex_topic_memory(
                    profile.database,
                    backend,
                    embedding_model=MalformedEmbedding(new_profile),
                    maintenance_confirmed=True,
                )
            async with profile.database.transaction() as connection:
                assert await _active_fingerprint(connection) == old_fingerprint
                await backend.validate_active(connection, old_fingerprint)
                assert await _authority(connection) == before

    asyncio.run(scenario())


def test_recovery_validation_failure_prevents_previous_generation_cleanup(tmp_path: Path) -> None:
    async def scenario() -> None:
        old_profile, new_profile = _profile("old", 2), _profile("new", 3)
        config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'recovery-validation.db'}")
        old_index = _index(old_profile)
        async with SQLiteProfile.open(
            config, tables=BUILTIN_TABLES + old_index.tables, load_vector_extension=True
        ) as profile:
            await _publish(profile, old_profile, count=1)
            backend = SQLiteTopicMemoryReindexBackend(new_profile)
            activated = await reindex_topic_memory(
                profile.database,
                backend,
                embedding_model=_Embedding(new_profile),
                maintenance_confirmed=True,
            )
            assert activated.activated
            async with profile.database.transaction() as connection:
                # Simulate the durable marker left by a nontransactional backend rename.
                await connection.exec_driver_sql(
                    "CREATE TABLE pc_topic_memory_reindex_previous_topics (marker INTEGER)"
                )

            class FailingRecoveryBackend(SQLiteTopicMemoryReindexBackend):
                def __init__(self) -> None:
                    super().__init__(new_profile)
                    self.validations = 0
                    self.cleanups = 0

                async def validate_active(self, connection, fingerprint: str | None, /) -> None:
                    self.validations += 1
                    if self.validations == 2:
                        raise RuntimeError("injected recovery validation failure")  # noqa: TRY003
                    await super().validate_active(connection, fingerprint)

                async def cleanup(self, connection) -> None:
                    self.cleanups += 1
                    await super().cleanup(connection)

            recovering = FailingRecoveryBackend()
            with pytest.raises(RuntimeError, match="injected recovery validation failure"):
                await reindex_topic_memory(
                    profile.database,
                    recovering,
                    embedding_model=_Embedding(new_profile),
                    maintenance_confirmed=True,
                )
            assert recovering.validations == 2
            assert recovering.cleanups == 0
            async with profile.database.transaction() as connection:
                names = set(await connection.run_sync(lambda sync: inspect(sync).get_table_names()))
                assert "pc_topic_memory_reindex_previous_topics" in names

    asyncio.run(scenario())
