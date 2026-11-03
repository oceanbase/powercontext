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

"""Related candidate caps bound each channel without weakening completeness checks."""

from __future__ import annotations

import asyncio
import math
import re
from dataclasses import replace
from typing import Any, Literal, cast

import pytest
from sqlalchemy import insert
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
    AtomicMemoryRelatedRequest,
)
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.persistence.sqlite.atomic_memory_index import SQLiteAtomicMemoryIndex
from powercontext.builtin.persistence.tables import ARTIFACT_HEADS_TABLE, ARTIFACTS_TABLE, SHARED_METADATA
from powercontext.builtin.tags import TagFilter
from tests.e2e.atomic_memory_migration_backend import migration_profile

PROFILE = EmbeddingProfile(profile_id="candidate-cap", model="fixture", dimension=3, normalization="unit")
SCOPE = "scope"


class _Embedding:
    profile = PROFILE

    async def embed(self, texts, /):
        vectors = []
        for text in texts:
            number = int(re.findall(r"\d+", text)[-1])
            angle = number * 0.05
            vectors.append((math.cos(angle), math.sin(angle), 0.0))
        return EmbeddingResult(vectors=tuple(vectors))


class _WideEmbedding:
    """Dense fixture: every seeded row stays inside the default admission floor."""

    profile = PROFILE

    async def embed(self, texts, /):
        vectors = []
        for text in texts:
            number = int(re.findall(r"\d+", text)[-1])
            angle = number * 0.002
            vectors.append((math.cos(angle), math.sin(angle), 0.0))
        return EmbeddingResult(vectors=tuple(vectors))


def _request(mode: Literal["fts", "vector", "hybrid"], cap: int | None) -> AtomicMemoryRelatedRequest:
    vector = mode != "fts"
    return AtomicMemoryRelatedRequest(
        query="semantic memory",
        filters=AtomicMemoryIndexFilter(),
        mode=mode,
        query_vector=(1.0, 0.0, 0.0) if vector else None,
        embedding_profile=PROFILE if vector else None,
        max_distance=1.0 if vector else None,
        max_candidates=cap,
    )


async def _initialize_authority(connection: AsyncConnection) -> None:
    await connection.run_sync(
        lambda sync: SHARED_METADATA.create_all(sync, tables=(ARTIFACTS_TABLE, ARTIFACT_HEADS_TABLE))
    )


async def _seed(
    index: SQLiteAtomicMemoryIndex, preparer: AtomicMemoryProjectionPublisher, connection, count: int
) -> None:
    for number in range(count):
        artifact_id = f"m{number:02}"
        prepared = await preparer.prepare(AtomicMemoryContent(kind="fact", text=f"semantic memory {number}."))
        await index.replace_many(
            connection,
            SCOPE,
            (
                AtomicMemoryProjection(
                    artifact_ref=ArtifactRef(family="atomic-memory", artifact_id=artifact_id, revision=1),
                    state_version=0,
                    prepared=prepared,
                    tag_keys=(),
                ),
            ),
        )
        await _insert_head(connection, artifact_id)


async def _insert_head(connection: AsyncConnection, artifact_id: str, scope_id: str = SCOPE) -> None:
    await connection.execute(
        insert(ARTIFACTS_TABLE).values(
            scope_id=scope_id,
            family="atomic-memory",
            artifact_id=artifact_id,
            revision=1,
            content=f"authority-{artifact_id}".encode(),
        )
    )
    await connection.execute(
        insert(ARTIFACT_HEADS_TABLE).values(
            scope_id=scope_id, family="atomic-memory", artifact_id=artifact_id, revision=1
        )
    )


class _RecordedResult:
    def __init__(self, rows: tuple[Any, ...]) -> None:
        self._rows = rows

    def mappings(self) -> tuple[Any, ...]:
        return self._rows


class _RecordingConnection:
    """Delegate to the real connection while recording statements, parameters and returned rows."""

    def __init__(self, connection: AsyncConnection) -> None:
        self._connection = connection
        self.statements: list[str] = []
        self.parameters: list[dict[str, object] | None] = []
        self.row_counts: list[int] = []

    def __getattr__(self, name: str) -> Any:
        return getattr(self._connection, name)

    async def execute(self, statement: Any, parameters: Any = None) -> _RecordedResult:
        result = await self._connection.execute(statement, parameters)
        rows = tuple(result.mappings())
        self.statements.append(str(statement))
        self.parameters.append(dict(parameters) if isinstance(parameters, dict) else None)
        self.row_counts.append(len(rows))
        return _RecordedResult(rows)


def test_sqlite_related_candidate_cap_bounds_each_mode_and_keeps_completeness(tmp_path) -> None:
    async def scenario() -> None:
        config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'candidates.db'}")
        model = _Embedding()
        index = SQLiteAtomicMemoryIndex(model.profile)
        preparer = AtomicMemoryProjectionPublisher(index, embedding_model=model)
        async with migration_profile(config, load_vector_extension=True) as profile:
            async with profile.database.transaction() as connection:
                await index.initialize(connection)
                await _initialize_authority(connection)
                await _seed(index, preparer, connection, 12)
            for mode in ("fts", "vector", "hybrid"):
                complete_request = _request(mode, None)
                capped_request = replace(complete_request, max_candidates=3)
                async with profile.database.transaction() as connection:
                    complete, complete_heads = await index.enumerate_related_with_heads(
                        connection, SCOPE, complete_request
                    )
                    capped, capped_heads = await index.enumerate_related_with_heads(connection, SCOPE, capped_request)
                    assert len(complete) == 12
                    assert len(capped) == 3
                    assert capped == complete[:3]
                    assert len(complete_heads) == 12
                    assert set(capped_heads) == {hit.artifact_ref.artifact_id for hit in capped}
                    assert all(
                        capped_heads[artifact_id]["content"] == f"authority-{artifact_id}".encode()
                        for artifact_id in capped_heads
                    )
                    dropped = {hit.artifact_ref.artifact_id for hit in complete[3:]}
                    assert dropped and dropped.isdisjoint(capped_heads)
                    for invalid in (0, -1, cast(int, True)):
                        with pytest.raises(AtomicMemoryIndexError, match="positive"):
                            await index.enumerate_related_with_heads(
                                connection, SCOPE, replace(capped_request, max_candidates=invalid)
                            )
            async with profile.database.transaction() as connection:
                await connection.exec_driver_sql(
                    "UPDATE pc_atomic_memory_current SET embedding = NULL, profile_fingerprint = NULL, "
                    "embedding_input_hash = NULL WHERE scope_id = ? AND artifact_id = ?",
                    (SCOPE, "m11"),
                )
                with pytest.raises(AtomicMemoryIndexError, match="incomplete"):
                    await index.enumerate_related_with_heads(connection, SCOPE, _request("vector", 3))

    asyncio.run(scenario())


def test_sqlite_related_candidate_cap_applies_scope_and_content_filters_before_the_limit(tmp_path) -> None:
    async def scenario() -> None:
        config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'filtered.db'}")
        model = _Embedding()
        index = SQLiteAtomicMemoryIndex(PROFILE)
        preparer = AtomicMemoryProjectionPublisher(index, embedding_model=model)
        async with migration_profile(config, load_vector_extension=True) as profile:
            async with profile.database.transaction() as connection:
                await index.initialize(connection)
                await _initialize_authority(connection)
                for number in range(6):
                    artifact_id = f"m{number:02}"
                    prepared = await preparer.prepare(
                        AtomicMemoryContent(
                            kind="fact" if number % 2 == 0 else "preference",
                            text=f"semantic memory {number}.",
                        )
                    )
                    await index.replace_many(
                        connection,
                        SCOPE,
                        (
                            AtomicMemoryProjection(
                                artifact_ref=ArtifactRef(family="atomic-memory", artifact_id=artifact_id, revision=1),
                                state_version=0,
                                prepared=prepared,
                                tag_keys=("keep",) if number % 2 == 0 else ("other",),
                            ),
                        ),
                    )
                    await _insert_head(connection, artifact_id)
                for number in range(2):
                    artifact_id = f"f{number:02}"
                    prepared = await preparer.prepare(
                        AtomicMemoryContent(kind="fact", text=f"semantic memory {number}.")
                    )
                    await index.replace_many(
                        connection,
                        "foreign",
                        (
                            AtomicMemoryProjection(
                                artifact_ref=ArtifactRef(family="atomic-memory", artifact_id=artifact_id, revision=1),
                                state_version=0,
                                prepared=prepared,
                                tag_keys=("keep",),
                            ),
                        ),
                    )
                    await _insert_head(connection, artifact_id, "foreign")
            request = AtomicMemoryRelatedRequest(
                query="semantic memory",
                filters=AtomicMemoryIndexFilter(kind="fact", tag_filter=TagFilter(tags=("keep",), match="all")),
                mode="fts",
            )
            async with profile.database.transaction() as connection:
                complete, _ = await index.enumerate_related_with_heads(connection, SCOPE, request)
                capped, _ = await index.enumerate_related_with_heads(
                    connection, SCOPE, replace(request, max_candidates=2)
                )
                expected = {"m00", "m02", "m04"}
                assert {hit.artifact_ref.artifact_id for hit in complete} == expected
                assert capped == complete[:2]
                assert {hit.artifact_ref.artifact_id for hit in capped} <= expected

    asyncio.run(scenario())


def test_sqlite_hybrid_overfetch_keeps_candidates_outside_the_per_channel_cap(tmp_path) -> None:
    async def scenario() -> None:
        config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'overfetch.db'}")
        model = _Embedding()
        index = SQLiteAtomicMemoryIndex(PROFILE)
        preparer = AtomicMemoryProjectionPublisher(index, embedding_model=model)
        # FTS ranks a1 > x > z > b1; vector ranks b1 < x, while a1 and z exceed the L2 threshold.
        texts = {
            "a1": "alpha alpha alpha m9",
            "z": "alpha alpha m9",
            "x": "alpha alpha m2",
            "b1": "alpha m1",
        }
        async with migration_profile(config, load_vector_extension=True) as profile:
            async with profile.database.transaction() as connection:
                await index.initialize(connection)
                await _initialize_authority(connection)
                for artifact_id, text in texts.items():
                    prepared = await preparer.prepare(AtomicMemoryContent(kind="fact", text=text))
                    await index.replace_many(
                        connection,
                        SCOPE,
                        (
                            AtomicMemoryProjection(
                                artifact_ref=ArtifactRef(family="atomic-memory", artifact_id=artifact_id, revision=1),
                                state_version=0,
                                prepared=prepared,
                                tag_keys=(),
                            ),
                        ),
                    )
                    await _insert_head(connection, artifact_id)
            request = AtomicMemoryRelatedRequest(
                query="alpha",
                filters=AtomicMemoryIndexFilter(),
                mode="hybrid",
                query_vector=(1.0, 0.0, 0.0),
                embedding_profile=PROFILE,
                max_distance=0.2,
            )
            async with profile.database.transaction() as connection:
                complete, _ = await index.enumerate_related_with_heads(connection, SCOPE, request)
                capped, capped_heads = await index.enumerate_related_with_heads(
                    connection, SCOPE, replace(request, max_candidates=1)
                )
                # x is second in both channels, so a per-channel cap of one drops it before fusion.
                assert [hit.artifact_ref.artifact_id for hit in capped] == [
                    hit.artifact_ref.artifact_id for hit in complete[:1]
                ]
                assert capped[0].artifact_ref.artifact_id == "x"
                assert set(capped_heads) == {"x"}

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("mode", "max_rows"),
    [
        ("fts", 5),
        ("vector", 6),
        ("hybrid", 130),
    ],
)
def test_sqlite_related_candidate_cap_limits_rows_inside_sql(
    tmp_path, mode: Literal["fts", "vector", "hybrid"], max_rows: int
) -> None:
    async def scenario() -> None:
        config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / f'{mode}.db'}")
        model = _WideEmbedding()
        index = SQLiteAtomicMemoryIndex(PROFILE)
        preparer = AtomicMemoryProjectionPublisher(index, embedding_model=model)
        async with migration_profile(config, load_vector_extension=True) as profile:
            async with profile.database.transaction() as connection:
                await index.initialize(connection)
                await _initialize_authority(connection)
                await _seed(index, preparer, connection, 100)
            async with profile.database.transaction() as connection:
                recorder = _RecordingConnection(connection)
                hits, _ = await index.enumerate_related_with_heads(
                    cast(AsyncConnection, recorder), SCOPE, _request(mode, 5)
                )
                assert len(hits) == 5
                assert recorder.statements and recorder.row_counts
                assert all("LIMIT :result_limit" in statement for statement in recorder.statements)
                assert max(recorder.row_counts) <= max_rows
                limits = {
                    parameters["result_limit"]
                    for parameters in recorder.parameters
                    if parameters is not None and "result_limit" in parameters
                }
                assert limits == {65 if mode == "hybrid" else 5}
                complete, _ = await index.enumerate_related_with_heads(connection, SCOPE, _request(mode, None))
                assert len(complete) == 100
                assert hits == complete[:5]

    asyncio.run(scenario())
