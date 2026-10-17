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
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.dialects.mysql import dialect
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.builtin.artifacts.memory import EmbeddingProfile
from powercontext.builtin.persistence.oceanbase.atomic_memory_index import OceanBaseAtomicMemoryIndex
from powercontext.builtin.persistence.oceanbase.topic_memory_index import OceanBaseTopicMemoryVectorIndex


@pytest.mark.parametrize("family", ["atomic-memory", "topic-memory"])
def test_native_vector_initialization_is_idempotent_and_preserves_hnsw(family):
    async def scenario():
        profile = EmbeddingProfile(
            profile_id="startup-test", model="test", dimension=3, distance="l2", normalization="unit"
        )
        statements = []
        start = asyncio.Event()
        ready = 0

        async def initialize():
            nonlocal ready
            index = (
                OceanBaseAtomicMemoryIndex(profile)
                if family == "atomic-memory"
                else OceanBaseTopicMemoryVectorIndex(profile)
            )
            connection = AsyncMock(spec=AsyncConnection)
            connection.dialect = dialect()
            connection.scalar.return_value = "VECTOR(3)"
            connection.run_sync.return_value = set()
            connection.execute.return_value.all.return_value = []

            async def execute(sql):
                statements.append(sql)
                result = MagicMock()
                result.first.return_value = ("table", "CREATE TABLE `table` ()")
                return result

            connection.exec_driver_sql.side_effect = execute
            ready += 1
            if ready == 8:
                start.set()
            await start.wait()
            await index.initialize(connection)

        await asyncio.gather(*(initialize() for _ in range(8)))
        await initialize()
        vector_ddl = [sql for sql in statements if sql.startswith("CREATE VECTOR INDEX")]
        expected = (
            {
                "CREATE VECTOR INDEX IF NOT EXISTS ix_pc_atomic_memory_current_embedding "
                "ON pc_atomic_memory_current (embedding) WITH (distance=l2,type=hnsw)"
            }
            if family == "atomic-memory"
            else {
                "CREATE VECTOR INDEX IF NOT EXISTS ix_pc_topic_memory_vector_topics_embedding "
                "ON pc_topic_memory_vector_topics (embedding) WITH (distance=l2,type=hnsw)",
                "CREATE VECTOR INDEX IF NOT EXISTS ix_pc_topic_memory_vector_chunks_embedding "
                "ON pc_topic_memory_vector_chunks (embedding) WITH (distance=l2,type=hnsw)",
            }
        )
        assert set(vector_ddl) == expected
        assert all("IF NOT EXISTS" in sql for sql in vector_ddl)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "case", ["existing", "duplicate", "ordinary", "wrong-distance", "other-unsupported", "missing-name"]
)
def test_vector_initialization_only_accepts_matching_native_metadata(case):
    from pymysql.err import NotSupportedError as MySQLNotSupportedError
    from sqlalchemy.exc import NotSupportedError, SQLAlchemyError

    from powercontext.builtin.persistence.oceanbase.schema import create_vector_index

    async def scenario():
        profile = EmbeddingProfile(profile_id="test", model="test", dimension=3, distance="l2", normalization="unit")
        index = OceanBaseTopicMemoryVectorIndex(profile)._topic_index
        connection = AsyncMock(spec=AsyncConnection)
        connection.dialect = dialect()
        present = case in {"existing", "ordinary", "wrong-distance"}
        native = (
            "CREATE TABLE `pc_topic_memory_vector_topics` (\n"
            "  VECTOR KEY `ix_pc_topic_memory_vector_topics_embedding` (`embedding`) "
            "WITH (DISTANCE = L2, TYPE = HNSW, LIB=VSAG)\n)"
        )

        async def execute(sql):
            nonlocal present
            if sql.startswith("SELECT @@ob_query_timeout"):
                result = MagicMock()
                result.scalar_one.return_value = 1000
                return result
            if sql.startswith("SHOW CREATE"):
                definition = native if present else "CREATE TABLE `pc_topic_memory_vector_topics` ()"
                if case == "ordinary":
                    definition = native.replace("VECTOR KEY", "KEY")
                elif case == "wrong-distance":
                    definition = native.replace("L2", "COSINE")
                result = MagicMock()
                result.first.return_value = ("pc_topic_memory_vector_topics", definition)
                return result
            present = case != "missing-name"
            message = (
                "unrelated unsupported feature"
                if case == "other-unsupported"
                else "create vector index on column has vector index is not supported"
            )
            raise NotSupportedError(sql, {}, MySQLNotSupportedError(1235, message))

        connection.exec_driver_sql.side_effect = execute
        if case in {"existing", "duplicate"}:
            await create_vector_index(connection, index)
        else:
            with pytest.raises(SQLAlchemyError):
                await create_vector_index(connection, index)

    asyncio.run(scenario())
