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

"""Bounded recall and complete enumeration keep eligibility and readiness contracts."""

import asyncio
from dataclasses import replace

import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.atomic_memory import AtomicMemoryContent
from powercontext.builtin.artifacts.memory import EmbeddingProfile
from powercontext.builtin.artifacts.search import AdmissionFloor
from powercontext.builtin.inference import EmbeddingResult
from powercontext.builtin.persistence.atomic_memory_index import (
    AtomicMemoryIndexError,
    AtomicMemoryIndexFilter,
    AtomicMemoryProjection,
    AtomicMemoryProjectionPublisher,
    AtomicMemoryRelatedRequest,
    AtomicMemorySearchRequest,
)
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.persistence.tables import ARTIFACT_HEADS_TABLE, ARTIFACTS_TABLE, SHARED_METADATA
from powercontext.builtin.tags import TagFilter
from tests.e2e.atomic_memory_migration_backend import MigrationBackend, migration_index, migration_profile


class Embedding:
    profile = EmbeddingProfile(profile_id="indexed-recall", model="fixture", dimension=3, normalization="unit")

    async def embed(self, texts, /):
        return EmbeddingResult(vectors=tuple((1.0, 0.0, 0.0) for _ in texts))


async def _initialize_related_authority(connection: AsyncConnection) -> None:
    await connection.run_sync(
        lambda sync: SHARED_METADATA.create_all(sync, tables=(ARTIFACTS_TABLE, ARTIFACT_HEADS_TABLE))
    )


@pytest.mark.parametrize("size", [3, 80])
def test_sqlite_lexical_scope_filter_preserves_global_raw_scores_and_complete_results(size: int) -> None:
    from powercontext.builtin.persistence.sqlite import SQLiteConfig
    from powercontext.builtin.persistence.sqlite.atomic_memory_index import SQLiteAtomicMemoryIndex

    async def scenario() -> None:
        index = SQLiteAtomicMemoryIndex()
        preparer = AtomicMemoryProjectionPublisher(index)
        scope = "项目a"
        async with migration_profile(SQLiteConfig()) as profile, profile.database.transaction() as connection:
            await index.initialize(connection)
            await _initialize_related_authority(connection)
            for target in (scope, scope + "b", scope.upper()):
                for number in range(size):
                    prepared = await preparer.prepare(
                        AtomicMemoryContent(
                            kind="fact" if number % 2 else "preference",
                            text=f"alpha beta {target} evidence {number}",
                        )
                    )
                    await index.replace_many(
                        connection,
                        target,
                        (
                            AtomicMemoryProjection(
                                artifact_ref=ArtifactRef(
                                    family="atomic-memory", artifact_id=f"memory-{number:02}", revision=1
                                ),
                                state_version=0,
                                prepared=prepared,
                                tag_keys=("keep",),
                            ),
                        ),
                    )
            all_matches, _ = await index.enumerate_related_with_heads(
                connection, scope, AtomicMemoryRelatedRequest(query="alpha beta", filters=AtomicMemoryIndexFilter())
            )
            assert len(all_matches) == size
            assert {hit.text for hit in all_matches} == {
                f"alpha beta {scope} evidence {number}" for number in range(size)
            }
            expected = [hit for hit in all_matches if hit.kind == "fact"]
            filters = AtomicMemoryIndexFilter(kind="fact", tag_filter=TagFilter(tags=("keep",), match="all"))
            request = AtomicMemorySearchRequest(query="alpha beta", filters=filters, mode="fts", limit=2)
            hits = (await index.search(connection, scope, request)).fts
            complete, _ = await index.enumerate_related_with_heads(
                connection, scope, AtomicMemoryRelatedRequest(query="alpha beta", filters=filters)
            )
            assert [(hit.artifact_ref, hit.channel_scores) for hit in hits] == [
                (hit.artifact_ref, hit.channel_scores) for hit in expected[:2]
            ]
            assert [(hit.artifact_ref, hit.channel_scores) for hit in complete] == [
                (hit.artifact_ref, hit.channel_scores) for hit in expected
            ]

    asyncio.run(scenario())


def test_vector_search_filters_and_complete_threshold_enumeration(migration_backend: MigrationBackend) -> None:
    async def scenario():
        model = Embedding()
        index = migration_index(migration_backend.config, model.profile)
        preparer = AtomicMemoryProjectionPublisher(index, embedding_model=model)
        async with migration_profile(migration_backend.config, load_vector_extension=True) as profile:
            async with profile.database.transaction() as connection:
                await index.initialize(connection)
                await _initialize_related_authority(connection)
                for n in range(40):
                    prepared = await preparer.prepare(
                        AtomicMemoryContent(kind="fact" if n % 2 else "preference", text=f"Semantic fact {n}.")
                    )
                    await index.replace_many(
                        connection,
                        "scope",
                        (
                            AtomicMemoryProjection(
                                artifact_ref=ArtifactRef(
                                    family="atomic-memory", artifact_id=f"memory-{39 - n:02}", revision=1
                                ),
                                state_version=0,
                                prepared=prepared,
                                tag_keys=("keep" if n % 3 else "other",),
                            ),
                        ),
                    )
                await index.replace_many(
                    connection,
                    "foreign",
                    (
                        AtomicMemoryProjection(
                            artifact_ref=ArtifactRef(family="atomic-memory", artifact_id="foreign", revision=1),
                            state_version=0,
                            prepared=prepared,
                            tag_keys=("keep",),
                        ),
                    ),
                )
            filters = AtomicMemoryIndexFilter(kind="fact", tag_filter=TagFilter(tags=("keep",), match="all"))
            expected = {f"memory-{39 - n:02}" for n in range(40) if n % 2 and n % 3}
            request = AtomicMemorySearchRequest(
                query="semantic",
                filters=filters,
                mode="vector",
                limit=5,
                query_vector=(1.0, 0.0, 0.0),
                embedding_profile=model.profile,
            )
            related = AtomicMemoryRelatedRequest(
                query="semantic",
                filters=filters,
                mode="vector",
                query_vector=(1.0, 0.0, 0.0),
                embedding_profile=model.profile,
                max_distance=0.1,
            )
            async with profile.database.transaction() as connection:
                if isinstance(migration_backend.config, SQLiteConfig):
                    # Supported SQLite builds need not enable optional math functions.
                    driver = (await connection.get_raw_connection()).driver_connection
                    assert driver is not None
                    await driver.create_function("power", 2, None)
                hits = (await index.search(connection, "scope", request)).vector
                assert len(hits) == 5
                assert {h.artifact_ref.artifact_id for h in hits} <= expected
                if isinstance(migration_backend.config, SQLiteConfig):
                    assert [hit.artifact_ref.artifact_id for hit in hits] == sorted(expected)[:5]
                assert not await index.probe_recoverable(
                    connection, "scope", request, AdmissionFloor(min_semantic_similarity=-1)
                )
                all_hits, _ = await index.enumerate_related_with_heads(connection, "scope", related)
                assert {h.artifact_ref.artifact_id for h in all_hits} == expected
                expanded = (await index.search(connection, "scope", replace(request, limit=20000))).vector
                assert {h.artifact_ref.artifact_id for h in expanded} == expected

    asyncio.run(scenario())


@pytest.mark.parametrize("corruption", ["missing", "older-profile", "newer-profile", "older-format", "newer-format"])
def test_bounded_search_skips_unusable_vectors_while_complete_operations_reject_them(
    migration_backend: MigrationBackend, corruption
):
    async def scenario():
        model = Embedding()
        index = migration_index(migration_backend.config, model.profile)
        preparer = AtomicMemoryProjectionPublisher(index, embedding_model=model)
        async with migration_profile(migration_backend.config, load_vector_extension=True) as profile:
            async with profile.database.transaction() as connection:
                await index.initialize(connection)
                await _initialize_related_authority(connection)
                prepared = await preparer.prepare(AtomicMemoryContent(kind="fact", text="Semantic fact."))
                projections = tuple(
                    AtomicMemoryProjection(
                        artifact_ref=ArtifactRef(family="atomic-memory", artifact_id=identity, revision=1),
                        state_version=0,
                        prepared=prepared,
                        tag_keys=("keep",),
                    )
                    for identity in ("broken", "healthy")
                )
                await index.replace_many(connection, "scope", projections)
                values = (
                    {"embedding": None, "profile_fingerprint": None, "embedding_input_hash": None}
                    if corruption == "missing"
                    else {
                        "profile_fingerprint" if "profile" in corruption else "projection_format": "0"
                        if "older" in corruption
                        else "z"
                    }
                )
                await connection.execute(
                    update(index.table).where(index.table.c.artifact_id == "broken").values(**values)
                )
            request = AtomicMemorySearchRequest(
                query="semantic",
                filters=AtomicMemoryIndexFilter(),
                mode="vector",
                limit=5,
                query_vector=(1.0, 0.0, 0.0),
                embedding_profile=model.profile,
            )
            related = AtomicMemoryRelatedRequest(
                query=request.query,
                filters=request.filters,
                mode="vector",
                query_vector=request.query_vector,
                embedding_profile=model.profile,
                max_distance=0.1,
            )
            async with profile.database.transaction() as connection:
                for limit in (5, 20000):
                    hits = (await index.search(connection, "scope", replace(request, limit=limit))).vector
                    assert [hit.artifact_ref.artifact_id for hit in hits] == ["healthy"]
                for operation in (
                    index.enumerate_related_with_heads(connection, "scope", related),
                    index.probe_recoverable(connection, "scope", request, AdmissionFloor(min_semantic_similarity=-1)),
                ):
                    with pytest.raises(AtomicMemoryIndexError, match="incomplete"):
                        await operation
                filtered, _ = await index.enumerate_related_with_heads(
                    connection, "scope", replace(related, filters=AtomicMemoryIndexFilter(kind="other"))
                )
                assert not filtered
                foreign, _ = await index.enumerate_related_with_heads(connection, "foreign", related)
                assert not foreign
                savepoint = await connection.begin_nested()
                await index.replace_many(connection, "scope", projections[:1])
                complete, _ = await index.enumerate_related_with_heads(connection, "scope", related)
                assert len(complete) == 2
                await savepoint.rollback()
                with pytest.raises(AtomicMemoryIndexError, match="incomplete"):
                    await index.enumerate_related_with_heads(connection, "scope", related)

    asyncio.run(scenario())


def test_sqlite_reopen_tag_updates_rollback_and_disabling_embeddings_preserve_search_results(tmp_path):
    from powercontext.builtin.persistence.sqlite import SQLiteConfig
    from powercontext.builtin.persistence.sqlite.atomic_memory_index import SQLiteAtomicMemoryIndex

    async def scenario():
        config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'vectors.db'}")
        model = Embedding()
        index = SQLiteAtomicMemoryIndex(model.profile)
        preparer = AtomicMemoryProjectionPublisher(index, embedding_model=model)
        request = AtomicMemorySearchRequest(
            query="semantic",
            filters=AtomicMemoryIndexFilter(),
            mode="vector",
            limit=5,
            query_vector=(1.0, 0.0, 0.0),
            embedding_profile=model.profile,
        )
        async with migration_profile(config, load_vector_extension=True) as profile:
            async with profile.database.transaction() as connection:
                await index.initialize(connection)
                prepared = await preparer.prepare(AtomicMemoryContent(kind="fact", text="Semantic fact."))
                for number in reversed(range(13)):
                    await index.replace_many(
                        connection,
                        "scope",
                        (
                            AtomicMemoryProjection(
                                artifact_ref=ArtifactRef(
                                    family="atomic-memory", artifact_id=f"m{number:02}", revision=1
                                ),
                                state_version=0,
                                prepared=prepared,
                                tag_keys=(),
                            ),
                        ),
                    )
                before = (await index.search(connection, "scope", request)).vector
                assert [hit.artifact_ref.artifact_id for hit in before] == [f"m{n:02}" for n in range(5)]
            async with profile.database.connection() as connection:
                await connection.exec_driver_sql("VACUUM")
        async with (
            migration_profile(config, load_vector_extension=True) as profile,
            profile.database.transaction() as connection,
        ):
            await index.initialize(connection)
            assert (await index.search(connection, "scope", request)).vector == before
            await index.refresh_tags(connection, "scope", "m00", ("keep",))
            filtered = replace(request, filters=AtomicMemoryIndexFilter(tag_filter=TagFilter(tags=("keep",))))
            assert [
                hit.artifact_ref.artifact_id for hit in (await index.search(connection, "scope", filtered)).vector
            ] == ["m00"]
            savepoint = await connection.begin_nested()
            changed = await preparer.prepare(AtomicMemoryContent(kind="preference", text="Changed fact."))
            await index.replace_many(
                connection,
                "scope",
                (
                    AtomicMemoryProjection(
                        artifact_ref=ArtifactRef(family="atomic-memory", artifact_id="m00", revision=2),
                        state_version=1,
                        prepared=changed,
                        tag_keys=("keep",),
                    ),
                ),
            )
            (updated,) = (await index.search(connection, "scope", filtered)).vector
            assert (updated.artifact_ref.revision, updated.kind, updated.text) == (2, "preference", "Changed fact.")
            await savepoint.rollback()
            assert (await index.search(connection, "scope", request)).vector == before
        async with migration_profile(config) as profile, profile.database.transaction() as connection:
            lexical = SQLiteAtomicMemoryIndex()
            await lexical.initialize(connection)
            await lexical.delete(connection, "scope", "m00")
        async with (
            migration_profile(config, load_vector_extension=True) as profile,
            profile.database.transaction() as connection,
        ):
            await index.initialize(connection)
            after = (await index.search(connection, "scope", request)).vector
            assert [hit.artifact_ref.artifact_id for hit in after] == [f"m{n:02}" for n in range(1, 6)]

    asyncio.run(scenario())
