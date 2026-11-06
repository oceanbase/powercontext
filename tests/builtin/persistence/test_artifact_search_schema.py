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

"""Artifact FTS initialization preserves the caller's transaction during DDL contention."""

import asyncio
from typing import cast
from unittest.mock import AsyncMock

import pytest
import sqlalchemy
from pymysql.err import OperationalError as MySQLOperationalError
from sqlalchemy.dialects import mysql
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.builtin.persistence import schema
from powercontext.builtin.persistence.tables import ARTIFACT_SEARCH_TABLES, ARTIFACT_SEARCH_TEXT_TABLE

_DDL = "CREATE FULLTEXT INDEX ix_pc_artifact_search_fts ON pc_artifact_search_text (searchable_text) WITH PARSER SPACE"


class _SearchSchema:
    dialect = mysql.dialect()

    def __init__(self, failures, *, concurrent_index=False):
        self.failures = list(failures)
        self.concurrent_index = concurrent_index
        self.index_exists = False
        self.statements = []

    def get_table_names(self):
        return [table.name for table in ARTIFACT_SEARCH_TABLES]

    def has_table(self, name, **_kwargs):
        return name in self.get_table_names()

    def get_columns(self, name):
        return [{"name": column.name} for table in ARTIFACT_SEARCH_TABLES if table.name == name for column in table.c]

    def get_indexes(self, _name):
        return [{"name": "ix_pc_artifact_search_fts"}] if self.index_exists else []

    def exec_driver_sql(self, statement):
        self.statements.append(statement)
        assert statement == _DDL
        if self.failures:
            if self.concurrent_index:
                self.index_exists = True
            raise self.failures.pop(0)
        self.index_exists = True


def _connection(monkeypatch, backend):
    monkeypatch.setattr(schema, "inspect", lambda _connection: backend)
    monkeypatch.setattr(sqlalchemy, "inspect", lambda _connection: backend)
    connection = AsyncMock(spec=AsyncConnection)
    connection.dialect = backend.dialect

    async def run_sync(callback):
        return callback(backend)

    connection.run_sync.side_effect = run_sync
    return connection


@pytest.mark.parametrize("concurrent_index", [False, True])
def test_artifact_fts_recovers_from_a_transient_ddl_lock_without_ending_the_transaction(monkeypatch, concurrent_index):
    async def scenario():
        error = OperationalError(_DDL, None, MySQLOperationalError(1205, "DDL lock conflict"))
        backend = _SearchSchema([error], concurrent_index=concurrent_index)
        connection = _connection(monkeypatch, backend)
        await schema.create_tables(cast(AsyncConnection, connection), (ARTIFACT_SEARCH_TEXT_TABLE,))
        assert backend.index_exists
        assert backend.statements == ([_DDL] if concurrent_index else [_DDL, _DDL])
        connection.commit.assert_not_awaited()
        connection.rollback.assert_not_awaited()

    asyncio.run(scenario())


def test_artifact_fts_bounds_persistent_lock_retries(monkeypatch):
    async def scenario():
        error = OperationalError(_DDL, None, MySQLOperationalError(1205, "DDL lock conflict"))
        backend = _SearchSchema([error] * 20)
        connection = _connection(monkeypatch, backend)
        with pytest.raises(OperationalError) as raised:
            async with asyncio.timeout(5):
                await schema.create_tables(cast(AsyncConnection, connection), (ARTIFACT_SEARCH_TEXT_TABLE,))
        assert raised.value is error
        assert 1 <= len(backend.statements) <= 6
        connection.commit.assert_not_awaited()
        connection.rollback.assert_not_awaited()

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", [1142, 1213, "cancelled", "other-statement"])
def test_artifact_fts_preserves_other_errors_and_cancellation(monkeypatch, failure):
    async def scenario():
        error = (
            asyncio.CancelledError()
            if failure == "cancelled"
            else OperationalError(
                "SHOW FULL COLUMNS FROM pc_artifact_search_text" if failure == "other-statement" else _DDL,
                None,
                MySQLOperationalError(1205 if failure == "other-statement" else failure, "DDL failed"),
            )
        )
        backend = _SearchSchema([error])
        connection = _connection(monkeypatch, backend)
        with pytest.raises(type(error)) as raised:
            await schema.create_tables(cast(AsyncConnection, connection), (ARTIFACT_SEARCH_TEXT_TABLE,))
        assert raised.value is error
        assert not backend.index_exists
        connection.commit.assert_not_awaited()
        connection.rollback.assert_not_awaited()

    asyncio.run(scenario())
