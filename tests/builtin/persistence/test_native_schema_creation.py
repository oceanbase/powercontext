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
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import Column, Index, Integer, MetaData, Table
from sqlalchemy.dialects.mysql import dialect
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.builtin.artifacts.memory import EmbeddingProfile
from powercontext.builtin.persistence.oceanbase.atomic_memory_index import OceanBaseAtomicMemoryIndex
from powercontext.builtin.persistence.oceanbase.topic_memory_index import OceanBaseTopicMemoryVectorIndex
from powercontext.builtin.persistence.schema import create_tables


@pytest.mark.parametrize("family", ["atomic-memory", "topic-memory"])
def test_relational_schema_creation_preserves_native_vector_index_ownership(family):
    async def scenario():
        profile = EmbeddingProfile(
            profile_id="startup-test", model="test", dimension=3, distance="l2", normalization="unit"
        )
        index = (
            OceanBaseAtomicMemoryIndex(profile)
            if family == "atomic-memory"
            else OceanBaseTopicMemoryVectorIndex(profile)
        )
        connection = AsyncMock(spec=AsyncConnection)
        connection.dialect = dialect()
        await create_tables(connection, index.tables)
        statements = [str(call.args[0].compile(dialect=dialect())) for call in connection.execute.await_args_list]
        assert all("VECTOR(3)" in sql for sql in statements)
        assert all("CREATE TABLE IF NOT EXISTS" in sql for sql in statements)
        # A plain INDEX over a VECTOR column loses the native type and HNSW
        # parameters. Only the vector initializer may emit that specialized DDL.
        assert not any("CREATE INDEX" in sql for sql in statements)

    asyncio.run(scenario())


def test_relational_schema_creation_uses_idempotent_regular_index_ddl():
    async def scenario():
        table = Table("startup_probe", MetaData(), Column("id", Integer, primary_key=True), Column("value", Integer))
        Index("ix_startup_probe_value", table.c.value)
        connection = AsyncMock(spec=AsyncConnection)
        connection.dialect = dialect()
        await create_tables(connection, (table,))
        statements = [str(call.args[0].compile(dialect=dialect())) for call in connection.execute.await_args_list]
        assert any("CREATE TABLE IF NOT EXISTS startup_probe" in sql for sql in statements)
        assert any("CREATE INDEX IF NOT EXISTS ix_startup_probe_value" in sql for sql in statements)
        assert not any("DROP" in sql or "DELETE" in sql for sql in statements)

    asyncio.run(scenario())
