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

import pytest
from sqlalchemy import insert

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.memory import CapabilityNotSupportedError, EmbeddingProfile
from powercontext.builtin.artifacts.memory.protocols import MemorySearchRequest
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.persistence.sqlite.memory_index import (
    _INSERT_VECTOR_SQL,
    SQLITE_MEMORY_VECTOR_ENTRIES_TABLE,
    SQLITE_MEMORY_VECTOR_TABLES,
    SQLiteMemoryVectorIndex,
    _pack_vector,
)
from powercontext.builtin.persistence.tables import MEMORY_ENTRY_VERSIONS_TABLE

# sqlite-vec rejects a KNN query whose k is above this.
_SQLITE_VEC_KNN_LIMIT = 4096


def _profile(dimension: int) -> EmbeddingProfile:
    return EmbeddingProfile(
        profile_id="test-v1",
        model="test",
        dimension=dimension,
        distance="l2",
        normalization="unit",
    )


def test_vector_index_probe_clears_a_leftover_probe_row(tmp_path) -> None:
    async def scenario() -> None:
        async with (
            SQLiteProfile.open(
                SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'memory.db'}"),
                tables=SQLITE_MEMORY_VECTOR_TABLES,
                load_vector_extension=True,
            ) as profile,
            profile.database.transaction() as connection,
        ):
            await connection.exec_driver_sql("CREATE VIRTUAL TABLE pc_memory_entry_vec USING vec0(embedding float[3])")
            await connection.execute(
                _INSERT_VECTOR_SQL,
                {"vector_id": -1, "embedding": _pack_vector((0.0, 0.0, 0.0))},
            )
            await SQLiteMemoryVectorIndex(_profile(3)).initialize(connection)
            leftover = (
                await connection.exec_driver_sql("SELECT count(*) FROM pc_memory_entry_vec WHERE rowid = -1")
            ).scalar()
            assert int(leftover) == 0

    asyncio.run(scenario())


def test_vector_index_initialize_drops_embeddings_without_metadata(tmp_path) -> None:
    async def scenario() -> None:
        async with (
            SQLiteProfile.open(
                SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'memory.db'}"),
                tables=SQLITE_MEMORY_VECTOR_TABLES,
                load_vector_extension=True,
            ) as profile,
            profile.database.transaction() as connection,
        ):
            await connection.exec_driver_sql("CREATE VIRTUAL TABLE pc_memory_entry_vec USING vec0(embedding float[3])")
            await connection.execute(
                _INSERT_VECTOR_SQL,
                {"vector_id": 7, "embedding": _pack_vector((0.0, 0.0, 1.0))},
            )
            await SQLiteMemoryVectorIndex(_profile(3)).initialize(connection)
            remaining = (await connection.exec_driver_sql("SELECT count(*) FROM pc_memory_entry_vec")).scalar()
            assert int(remaining) == 0

    asyncio.run(scenario())


def test_vector_index_probe_reports_a_table_dimension_mismatch(tmp_path) -> None:
    async def scenario() -> None:
        async with (
            SQLiteProfile.open(
                SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'memory.db'}"),
                tables=SQLITE_MEMORY_VECTOR_TABLES,
                load_vector_extension=True,
            ) as profile,
            profile.database.transaction() as connection,
        ):
            await connection.exec_driver_sql("CREATE VIRTUAL TABLE pc_memory_entry_vec USING vec0(embedding float[4])")
            index = SQLiteMemoryVectorIndex(_profile(3))
            with pytest.raises(CapabilityNotSupportedError, match=r"dimension") as exc_info:
                await index.initialize(connection)
            message = str(exc_info.value)
            assert "4" in message
            assert "3" in message
            assert "capability is not supported: vector" in message
            assert isinstance(exc_info.value.__cause__, Exception)

    asyncio.run(scenario())


def test_vector_index_probe_surfaces_the_underlying_cause(tmp_path) -> None:
    async def scenario() -> None:
        async with (
            SQLiteProfile.open(
                SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'memory.db'}"),
                tables=SQLITE_MEMORY_VECTOR_TABLES,
                load_vector_extension=True,
            ) as profile,
            profile.database.transaction() as connection,
        ):
            await connection.exec_driver_sql(
                "CREATE TABLE pc_memory_entry_vec (rowid INTEGER PRIMARY KEY, embedding BLOB)"
            )
            index = SQLiteMemoryVectorIndex(_profile(3))
            with pytest.raises(CapabilityNotSupportedError, match=r"sqlite-vec probe failed") as exc_info:
                await index.initialize(connection)
            cause = exc_info.value.__cause__
            assert cause is not None
            assert str(cause) not in ("", "None")
            assert "sqlite-vec probe failed:" in str(exc_info.value)

    asyncio.run(scenario())


def test_vector_index_probe_reports_the_provider_limit_for_a_fresh_oversized_dimension(tmp_path) -> None:
    async def scenario() -> None:
        async with (
            SQLiteProfile.open(
                SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'memory.db'}"),
                tables=SQLITE_MEMORY_VECTOR_TABLES,
                load_vector_extension=True,
            ) as profile,
            profile.database.transaction() as connection,
        ):
            index = SQLiteMemoryVectorIndex(_profile(65536))
            with pytest.raises(CapabilityNotSupportedError, match=r"sqlite-vec probe failed") as exc_info:
                await index.initialize(connection)
            message = str(exc_info.value)
            assert "migrate" not in message
            assert "8192" in message
            assert "65536" in message

    asyncio.run(scenario())


def _vector_entry(vector_id: int, scope_id: str, entry_id: str) -> dict[str, object]:
    return {
        "vector_id": vector_id,
        "scope_id": scope_id,
        "memory_artifact_id": "memory",
        "head_revision": 1,
        "entry_id": entry_id,
        "entry_version_id": f"{entry_id}-v1",
        "entry_content_hash": "entry-hash",
        "embedding_content_hash": "embedding-hash",
    }


def test_vector_search_is_not_capped_by_embeddings_in_other_scopes(tmp_path, monkeypatch) -> None:
    """Embeddings of every scope share one vec0 table, so the scope being
    searched must still be served once the whole table outgrows sqlite-vec's
    KNN limit, and its nearest entries must not be crowded out by others."""

    async def scenario() -> None:
        async with (
            SQLiteProfile.open(
                # Only the projection tables are created, so their parents are absent.
                SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'memory.db'}", foreign_keys=False),
                tables=(*SQLITE_MEMORY_VECTOR_TABLES, MEMORY_ENTRY_VERSIONS_TABLE),
                load_vector_extension=True,
            ) as profile,
            profile.database.transaction() as connection,
        ):
            index = SQLiteMemoryVectorIndex(_profile(2))
            await index.initialize(connection)

            # Another scope holds the whole KNN budget, and sits closer to the query.
            others = [_vector_entry(i, "other", f"other-{i}") for i in range(1, _SQLITE_VEC_KNN_LIMIT + 1)]
            mine = [
                _vector_entry(_SQLITE_VEC_KNN_LIMIT + 1, "project", "near"),
                _vector_entry(_SQLITE_VEC_KNN_LIMIT + 2, "project", "far"),
            ]
            await connection.execute(insert(SQLITE_MEMORY_VECTOR_ENTRIES_TABLE), [*others, *mine])
            await connection.execute(
                _INSERT_VECTOR_SQL,
                [{"vector_id": row["vector_id"], "embedding": _pack_vector((1.0, 0.0))} for row in others]
                + [
                    {"vector_id": mine[0]["vector_id"], "embedding": _pack_vector((0.6, 0.8))},
                    {"vector_id": mine[1]["vector_id"], "embedding": _pack_vector((0.0, 1.0))},
                ],
            )
            await connection.execute(
                insert(MEMORY_ENTRY_VERSIONS_TABLE),
                [
                    {
                        "scope_id": "project",
                        "family": "memory",
                        "memory_artifact_id": "memory",
                        "entry_id": row["entry_id"],
                        "entry_version_id": row["entry_version_id"],
                        "version": 1,
                        "kind": "fact",
                        "text": f"{row['entry_id']} entry",
                        "source_refs": b"[]",
                        "artifact_refs": b"[]",
                        "entry_content_hash": "entry-hash",
                        "created_in_revision": 1,
                    }
                    for row in mine
                ],
            )

            # The head/version bookkeeping behind completeness is not part of this fixture.
            async def complete(*_: object) -> bool:
                return True

            monkeypatch.setattr(index, "vector_complete", complete)
            memory = ArtifactRef(family="memory", artifact_id="memory", revision=1)
            channels = await index.search(
                connection,
                "project",
                MemorySearchRequest(
                    query="q",
                    analyzed_query="q",
                    memories=(memory,),
                    candidate_limit=10,
                    mode="vector",
                    query_vector=(1.0, 0.0),
                    embedding_profile=_profile(2),
                ),
            )
            assert [hit.entry_id for hit in channels.vector] == ["near", "far"]

    asyncio.run(scenario())
