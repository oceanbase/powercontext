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
from sqlalchemy import (
    Column,
    Index,
    Integer,
    MetaData,
    Table,
    create_engine,
    create_mock_engine,
    event,
    inspect,
    select,
)
from sqlalchemy.dialects.mysql import dialect
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

from powercontext.builtin.artifacts.memory import EmbeddingProfile
from powercontext.builtin.persistence import schema
from powercontext.builtin.persistence.oceanbase.atomic_memory_index import OceanBaseAtomicMemoryIndex
from powercontext.builtin.persistence.oceanbase.topic_memory_index import OceanBaseTopicMemoryVectorIndex


@pytest.mark.parametrize("family", ["atomic-memory", "topic-memory"])
def test_relational_schema_creation_preserves_native_vector_index_ownership(family, monkeypatch):
    async def scenario():
        profile = EmbeddingProfile(
            profile_id="startup-test", model="test", dimension=3, distance="l2", normalization="unit"
        )
        index = (
            OceanBaseAtomicMemoryIndex(profile)
            if family == "atomic-memory"
            else OceanBaseTopicMemoryVectorIndex(profile)
        )
        statements = []
        sync = create_mock_engine(
            "mysql://", lambda sql, *_a, **_k: statements.append(str(sql.compile(dialect=dialect())))
        )
        inspector = MagicMock()
        inspector.get_table_names.return_value = []
        inspector.has_table.return_value = False
        monkeypatch.setattr(schema, "inspect", lambda _connection: inspector)
        connection = AsyncMock(spec=AsyncConnection)
        connection.dialect = dialect()
        connection.run_sync.side_effect = lambda callback: callback(sync)
        await schema.create_tables(connection, index.tables)
        assert len(statements) == len(index.tables)
        assert all("VECTOR(3)" in sql for sql in statements)
        assert all("CREATE TABLE IF NOT EXISTS" in sql for sql in statements)
        # Native initializers own VECTOR DDL; table creation must not replace
        # it with an ordinary index or preempt the native race validation.
        assert not any("CREATE INDEX" in sql or "CREATE VECTOR INDEX" in sql for sql in statements)

    asyncio.run(scenario())


def test_relational_schema_creation_preserves_rows_and_regular_indexes():
    table = Table("startup_probe", MetaData(), Column("id", Integer, primary_key=True), Column("value", Integer))
    Index("ix_startup_probe_value", table.c.value)
    with create_engine("sqlite://").begin() as connection:
        schema._create_mysql_tables(connection, (table,))
        connection.execute(table.insert().values(id=1, value=42))

        @event.listens_for(connection, "before_cursor_execute")
        def forbid_ddl(_connection, _cursor, statement, _parameters, _context, _executemany):
            assert not statement.lstrip().upper().startswith(("CREATE", "ALTER", "DROP"))

        schema._create_mysql_tables(connection, (table,))
        assert connection.execute(select(table)).all() == [(1, 42)]
        assert [index["name"] for index in inspect(connection).get_indexes(table.name)] == ["ix_startup_probe_value"]


@pytest.mark.parametrize("selected_exists", [False, True])
def test_managed_schema_startup_never_creates_missing_tables_or_indexes(selected_exists):
    from powercontext.builtin.persistence.migrations.models import MigrationError

    async def scenario():
        table = Table("managed_probe", MetaData(), Column("id", Integer, primary_key=True))
        Index("ix_managed_probe_id", table.c.id)
        engine = create_async_engine("sqlite+aiosqlite://")
        try:
            async with engine.begin() as connection:
                await connection.exec_driver_sql("CREATE TABLE pc_schema_revision (revision TEXT)")
                if selected_exists:
                    await connection.exec_driver_sql("CREATE TABLE managed_probe (id INTEGER PRIMARY KEY)")
                    await connection.execute(table.insert().values(id=7))
                    await schema.create_tables(connection, (table,))
                    assert (await connection.execute(select(table))).all() == [(7,)]
                    indexes = await connection.run_sync(lambda sync: inspect(sync).get_indexes(table.name))
                    assert indexes == []
                else:
                    with pytest.raises(MigrationError) as raised:
                        await schema.create_tables(connection, (table,))
                    assert raised.value.code == "unsupported_configuration"
                    assert not await connection.run_sync(lambda sync: inspect(sync).has_table(table.name))
        finally:
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize("family", ["atomic-memory", "experience", "topic-memory"])
@pytest.mark.parametrize("provisioned", [False, True])
def test_fulltext_initialization_preserves_provisioned_and_missing_indexes(family, provisioned):
    from pymysql.err import OperationalError as MySQLOperationalError
    from sqlalchemy.exc import OperationalError

    from powercontext.builtin.persistence.oceanbase import experience_index
    from powercontext.builtin.persistence.oceanbase.topic_memory_index import OceanBaseTopicMemoryFTSIndex

    async def scenario():
        connection = AsyncMock(spec=AsyncConnection)
        connection.dialect = dialect()
        connection.run_sync.return_value = {"description", "searchable_text"}

        async def scalar(statement, *_args):
            sql = str(statement)
            if "information_schema.statistics" in sql:
                return int(provisioned)
            if "information_schema.columns" in sql:
                return 1
            if "pc_lexical_projection_versions" in sql:
                return experience_index.APPROVED_ARTIFACT_PROJECTION_VERSION
            raise AssertionError(sql)

        async def execute(statement):
            sql = statement.lstrip().upper()
            if sql.startswith("SELECT") or (not provisioned and sql.startswith("CREATE FULLTEXT INDEX IF NOT EXISTS")):
                return MagicMock()
            raise OperationalError(statement, {}, MySQLOperationalError(1142, "CREATE command denied"))

        connection.scalar.side_effect = scalar
        connection.exec_driver_sql.side_effect = execute
        indexes = {
            "atomic-memory": OceanBaseAtomicMemoryIndex(),
            "experience": experience_index.OceanBaseExperienceFTSIndex(),
            "topic-memory": OceanBaseTopicMemoryFTSIndex(),
        }
        await indexes[family].initialize(connection)
        probes = [str(c.args[0]) for c in connection.exec_driver_sql.await_args_list]
        probes += [str(c.args[0].compile(dialect=dialect())) for c in connection.execute.await_args_list]
        assert any("MATCH" in sql for sql in probes)
        assert any("CREATE FULLTEXT INDEX IF NOT EXISTS" in sql for sql in probes) == (not provisioned)

    asyncio.run(scenario())
