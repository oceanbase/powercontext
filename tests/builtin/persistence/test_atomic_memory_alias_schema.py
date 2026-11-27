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

"""Backend DDL compilation is a schema check, not real OceanBase acceptance."""

import asyncio
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

from sqlalchemy.dialects import mysql, sqlite
from sqlalchemy.ext.asyncio import AsyncConnection
from sqlalchemy.schema import CreateTable

from powercontext.builtin.persistence.oceanbase.atomic_memory_index import OceanBaseAtomicMemoryIndex
from powercontext.builtin.persistence.sqlite.atomic_memory_index import SQLiteAtomicMemoryIndex
from tests.e2e.test_atomic_memory_related_embedding import PROFILE


def test_alias_ddl_uses_backend_identity_text_and_vector_types():
    for index, dialect, embedding_type, text_type in (
        (SQLiteAtomicMemoryIndex(PROFILE), sqlite.dialect(), "BLOB", "TEXT"),
        (OceanBaseAtomicMemoryIndex(PROFILE), mysql.dialect(), "VECTOR(3)", "MEDIUMTEXT"),
    ):
        ddl = str(CreateTable(index.alias_table).compile(dialect=dialect))
        assert "PRIMARY KEY (scope_id, alias_artifact_id)" in ddl
        assert f"embedding {embedding_type}" in ddl
        assert f"searchable_text {text_type}" in ddl
        assert "embedding IS NULL AND profile_fingerprint IS NULL AND embedding_input_hash IS NULL" in ddl
        assert index.alias_table.c.scope_id.type.compile(dialect=dialect) == index.table.c.scope_id.type.compile(
            dialect=dialect
        )


def test_oceanbase_reconfiguration_updates_current_and_alias_vector_columns():
    async def scenario():
        index = OceanBaseAtomicMemoryIndex(PROFILE)
        statements = []

        async def execute(statement):
            statements.append(statement)

        connection = SimpleNamespace(
            dialect=SimpleNamespace(name="mysql"), scalar=AsyncMock(return_value=False), exec_driver_sql=execute
        )
        index.initialize = AsyncMock()
        await index.reconfigure_vector_column(cast(AsyncConnection, connection))
        for name in ("pc_atomic_memory_current", "pc_atomic_memory_alias"):
            assert any(f"ALTER TABLE {name} MODIFY COLUMN embedding VECTOR(3) NULL" in sql for sql in statements)
            assert any(f"UPDATE {name} SET embedding = NULL" in sql for sql in statements)  # noqa: S608

    asyncio.run(scenario())
