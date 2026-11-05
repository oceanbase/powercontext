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

"""Related recall walks one rank snapshot so corpus changes cannot truncate pages."""

from __future__ import annotations

import asyncio
import math
import re
from dataclasses import replace
from typing import Any

import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.atomic_memory import AtomicMemoryContent
from powercontext.builtin.artifacts.memory import EmbeddingProfile
from powercontext.builtin.inference import EmbeddingResult
from powercontext.builtin.persistence.atomic_memory_index import (
    AtomicMemoryIndexError,
    AtomicMemoryIndexFilter,
    AtomicMemoryProjection,
    AtomicMemoryProjectionPublisher,
    AtomicMemoryRelatedRank,
    AtomicMemoryRelatedRequest,
    combine_atomic_memory_related_ranks,
)
from powercontext.builtin.persistence.tables import ARTIFACT_HEADS_TABLE, ARTIFACTS_TABLE, SHARED_METADATA
from tests.e2e.atomic_memory_migration_backend import MigrationBackend, migration_index, migration_profile

PROFILE = EmbeddingProfile(profile_id="related-pagination", model="fixture", dimension=3, normalization="unit")


class _OrderedEmbedding:
    profile = PROFILE

    async def embed(self, texts, /):
        vectors = []
        for text in texts:
            angle = int(re.findall(r"\d+", text)[-1]) * 0.01
            vectors.append((math.cos(angle), math.sin(angle), 0.0))
        return EmbeddingResult(vectors=tuple(vectors))


async def _initialize_related_authority(connection: AsyncConnection) -> None:
    await connection.run_sync(
        lambda sync: SHARED_METADATA.create_all(sync, tables=(ARTIFACTS_TABLE, ARTIFACT_HEADS_TABLE))
    )


async def _seed_memories(connection, index, scope, count, *, model=None) -> None:
    publisher = AtomicMemoryProjectionPublisher(index, embedding_model=model)
    projections = []
    for number in range(1, count + 1):
        prepared = await publisher.prepare(AtomicMemoryContent(kind="fact", text=f"rollout strategy {number}"))
        projections.append(
            AtomicMemoryProjection(
                artifact_ref=ArtifactRef(family="atomic-memory", artifact_id=f"memory-{number:03}", revision=1),
                state_version=0,
                prepared=prepared,
                tag_keys=(),
            )
        )
    await index.replace_many(connection, scope, projections)


async def _drain_pages(index, connection, scope, request) -> tuple[tuple[str, ...], ...]:
    pages: list[tuple[str, ...]] = []
    cursor = None
    while True:
        page = await index.related_page(connection, scope, request, cursor)
        if page is None:
            return tuple(pages)
        pages.append(tuple(hit.artifact_ref.artifact_id for hit in page.hits))
        cursor = page.cursor
        if cursor is None:
            return tuple(pages)


class _RecordingConnection:
    """Capture every statement the page loop executes on a real backend."""

    def __init__(self, connection: AsyncConnection) -> None:
        self.connection = connection
        self.executed: list[tuple[str, dict[str, Any]]] = []

    async def execute(self, statement, parameters=None):
        self.executed.append((str(statement), dict(parameters or {})))
        return await self.connection.execute(statement, parameters)

    async def scalar(self, statement, parameters=None):
        self.executed.append((str(statement), dict(parameters or {})))
        return await self.connection.scalar(statement, parameters)


@pytest.mark.parametrize("page_size", [7, 10, 100, 101, 102])
def test_related_pages_cover_every_qualified_memory(migration_backend: MigrationBackend, page_size: int) -> None:
    async def scenario() -> None:
        index = migration_index(migration_backend.config)
        async with migration_profile(migration_backend.config) as profile:
            async with profile.database.transaction() as connection:
                await index.initialize(connection)
                await _initialize_related_authority(connection)
                await _seed_memories(connection, index, "scope", 101)
            request = AtomicMemoryRelatedRequest(
                query="rollout strategy", filters=AtomicMemoryIndexFilter(), mode="fts"
            )
            async with profile.database.transaction() as connection:
                complete = await _drain_pages(index, connection, "scope", replace(request, page_size=200))
                paged = await _drain_pages(index, connection, "scope", replace(request, page_size=page_size))
        assert tuple(page for pages in paged for page in pages) == tuple(page for pages in complete for page in pages)
        assert len({page for pages in paged for page in pages}) == 101

    asyncio.run(scenario())


@pytest.mark.parametrize("page_size", [100, 101, 102])
def test_related_page_boundaries_and_tie_breaks(migration_backend: MigrationBackend, page_size: int) -> None:
    async def scenario() -> None:
        index = migration_index(migration_backend.config)
        async with migration_profile(migration_backend.config) as profile:
            async with profile.database.transaction() as connection:
                await index.initialize(connection)
                await _initialize_related_authority(connection)
                await _seed_memories(connection, index, "scope", 101)
            request = AtomicMemoryRelatedRequest(
                query="rollout strategy", filters=AtomicMemoryIndexFilter(), mode="fts", page_size=page_size
            )
            async with profile.database.transaction() as connection:
                pages = await _drain_pages(index, connection, "scope", request)
        full_pages, remainder = divmod(101, page_size)
        expected = (page_size,) * full_pages + ((remainder,) if remainder else ())
        assert tuple(len(page) for page in pages) == expected
        assert len({identity for page in pages for identity in page}) == 101

    asyncio.run(scenario())


@pytest.mark.parametrize("page_size", [3, 7, 100])
def test_hybrid_pages_match_full_rrf_fusion(migration_backend: MigrationBackend, page_size: int) -> None:
    async def scenario() -> None:
        model = _OrderedEmbedding()
        index = migration_index(migration_backend.config, model.profile)
        async with migration_profile(migration_backend.config, load_vector_extension=True) as profile:
            async with profile.database.transaction() as connection:
                await index.initialize(connection)
                await _initialize_related_authority(connection)
                await _seed_memories(connection, index, "scope", 40, model=model)
            request = AtomicMemoryRelatedRequest(
                query="rollout strategy 0",
                filters=AtomicMemoryIndexFilter(),
                mode="hybrid",
                query_vector=(1.0, 0.0, 0.0),
                embedding_profile=model.profile,
                max_distance=1.0,
            )
            async with profile.database.transaction() as connection:
                complete = await _drain_pages(index, connection, "scope", replace(request, page_size=200))
                paged = await _drain_pages(index, connection, "scope", replace(request, page_size=page_size))
        assert tuple(page for pages in paged for page in pages) == tuple(page for pages in complete for page in pages)
        assert len({page for pages in paged for page in pages}) == 40

    asyncio.run(scenario())


def test_hybrid_fusion_keeps_duplicate_channel_ranks_outside_the_former_window() -> None:
    def ranks(identities: tuple[str, ...]) -> tuple[AtomicMemoryRelatedRank, ...]:
        return tuple(
            AtomicMemoryRelatedRank(
                artifact_ref=ArtifactRef(family="atomic-memory", artifact_id=identity, revision=1),
                state_version=0,
                kind="fact",
                score=0.0,
            )
            for identity in identities
        )

    # The older per-channel window stopped after 61 rows, so a dual-channel rank 62
    # identity disappeared while a single-channel rank 1 identity survived.
    fts = ranks(("b", *(f"x{number:02}" for number in range(60)), "a"))
    vector = ranks((*(f"y{number:02}" for number in range(61)), "a"))
    fused = combine_atomic_memory_related_ranks(fts, vector)

    assert tuple(rank.artifact_ref.artifact_id for rank in fused[:2]) == ("a", "b")
    assert fused[0].score == fused[1].score


def test_vector_readiness_covers_rows_beyond_the_first_page(migration_backend: MigrationBackend) -> None:
    async def scenario() -> None:
        model = _OrderedEmbedding()
        index = migration_index(migration_backend.config, model.profile)
        async with migration_profile(migration_backend.config, load_vector_extension=True) as profile:
            async with profile.database.transaction() as connection:
                await index.initialize(connection)
                await _initialize_related_authority(connection)
                await _seed_memories(connection, index, "scope", 20, model=model)
                await connection.execute(
                    update(index.table)
                    .where(index.table.c.artifact_id == "memory-020")
                    .values(embedding=None, embedding_input_hash=None, profile_fingerprint=None)
                )
            request = AtomicMemoryRelatedRequest(
                query="rollout strategy 0",
                filters=AtomicMemoryIndexFilter(),
                mode="vector",
                query_vector=(1.0, 0.0, 0.0),
                embedding_profile=model.profile,
                max_distance=1.0,
                page_size=10,
            )
            async with profile.database.transaction() as connection:
                with pytest.raises(AtomicMemoryIndexError, match="incomplete"):
                    await index.related_page(connection, "scope", request)

    asyncio.run(scenario())


def test_related_page_bodies_are_fetched_in_page_sized_batches(migration_backend: MigrationBackend) -> None:
    async def scenario() -> None:
        index = migration_index(migration_backend.config)
        async with migration_profile(migration_backend.config) as profile:
            async with profile.database.transaction() as connection:
                await index.initialize(connection)
                await _initialize_related_authority(connection)
                await _seed_memories(connection, index, "scope", 5)
            request = AtomicMemoryRelatedRequest(
                query="rollout strategy", filters=AtomicMemoryIndexFilter(), mode="fts", page_size=2
            )
            async with profile.database.transaction() as connection:
                recording = _RecordingConnection(connection)
                pages = await _drain_pages(index, recording, "scope", request)
            body_calls = tuple(
                (sql, parameters) for sql, parameters in recording.executed if " AS projected_text" in sql
            )
            rank_statements = tuple(sql for sql, _ in recording.executed if " AS projected_text" not in sql)
        assert tuple(len(page) for page in pages) == (2, 2, 1)
        assert body_calls and all(len(parameters["body_ids"]) <= 2 for _, parameters in body_calls)
        body_ids = [identity for _, parameters in body_calls for identity in parameters["body_ids"]]
        assert len(body_ids) == 5 and set(body_ids) == {f"memory-{number:03}" for number in range(1, 6)}
        assert all("authority_content" not in statement for statement in rank_statements)
        assert all(" AS projected_text" not in statement for statement in rank_statements)

    asyncio.run(scenario())


def test_fts_pages_survive_corpus_changes_in_other_scopes(migration_backend: MigrationBackend) -> None:
    """Other Scopes changing BM25 statistics must not hide later pages of this Scope."""

    async def scenario() -> None:
        index = migration_index(migration_backend.config)
        async with migration_profile(migration_backend.config) as profile:
            async with profile.database.transaction() as connection:
                await index.initialize(connection)
                await _initialize_related_authority(connection)
                await _seed_memories(connection, index, "scope", 3)
            request = AtomicMemoryRelatedRequest(
                query="rollout strategy", filters=AtomicMemoryIndexFilter(), mode="fts", page_size=1
            )
            async with profile.database.transaction() as connection:
                first = await index.related_page(connection, "scope", request)
                assert first is not None
                assert tuple(hit.artifact_ref.artifact_id for hit in first.hits) == ("memory-001",)
                await _seed_memories(connection, index, "other-scope", 100)
                pages: list[tuple[str, ...]] = []
                cursor = first.cursor
                while cursor is not None:
                    page = await index.related_page(connection, "scope", request, cursor)
                    if page is None:
                        break
                    pages.append(tuple(hit.artifact_ref.artifact_id for hit in page.hits))
                    cursor = page.cursor
        identities = tuple(identity for page in pages for identity in page)
        assert identities == ("memory-002", "memory-003")

    asyncio.run(scenario())
