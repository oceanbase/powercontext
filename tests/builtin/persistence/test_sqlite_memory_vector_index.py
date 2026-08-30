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

from powercontext.builtin.artifacts.memory import CapabilityNotSupportedError, EmbeddingProfile
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.persistence.sqlite.memory_index import (
    _INSERT_VECTOR_SQL,
    SQLITE_MEMORY_VECTOR_TABLES,
    SQLiteMemoryVectorIndex,
    _pack_vector,
)


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
