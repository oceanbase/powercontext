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
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from pymysql.err import OperationalError as MySQLOperationalError
from sqlalchemy.dialects import mysql
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.builtin.persistence.atomic_memory_index import AtomicMemoryIndexError
from powercontext.builtin.persistence.oceanbase import atomic_memory_index, experience_index


@pytest.fixture(params=["experience", "atomic-memory"])
def fts_startup(request, monkeypatch):
    # Schema preparation is independent of the DDL-conflict contract exercised here.
    if request.param == "experience":
        index = experience_index.OceanBaseExperienceFTSIndex()
        monkeypatch.setattr(experience_index, "ensure_artifact_head_searchable_text", AsyncMock())
        monkeypatch.setattr(experience_index, "lexical_projection_is_current", AsyncMock(return_value=True))
    else:
        index = atomic_memory_index.OceanBaseAtomicMemoryIndex()
        monkeypatch.setattr(atomic_memory_index, "create_tables", AsyncMock())
        monkeypatch.setattr(index, "require_current_schema", AsyncMock())
    return index


def _connection():
    connection = AsyncMock(spec=AsyncConnection)
    connection.dialect = mysql.dialect()
    # Keep capability probing active while supplying an ordinary live-pool identity.
    connection.run_sync.return_value = MagicMock()
    connection.exec_driver_sql.return_value = MagicMock()
    return connection


def _assert_failure(error, raised):
    # Atomic Memory retains its public capability error wrapper and original cause.
    if isinstance(raised, AtomicMemoryIndexError):
        assert raised.__cause__ is error
    else:
        assert raised is error


@pytest.mark.parametrize("created_by_peer", [False, True])
def test_fulltext_initialization_recovers_without_explicit_transaction_control(fts_startup, created_by_peer):
    async def scenario():
        connection = _connection()
        created = set()
        conflicted = False

        async def exists(_statement, parameters):
            return int(parameters["index_name"] in created)

        async def execute(statement):
            nonlocal conflicted
            if not statement.lstrip().startswith("CREATE FULLTEXT INDEX"):
                return MagicMock()
            index_name = statement.split()[3]
            assert index_name not in created
            if not conflicted:
                conflicted = True
                if created_by_peer:
                    created.add(index_name)
                raise OperationalError(statement, None, MySQLOperationalError(1205, "DDL lock conflict"))
            created.add(index_name)
            return MagicMock()

        connection.scalar.side_effect = exists
        connection.exec_driver_sql.side_effect = execute
        await fts_startup.initialize(cast(AsyncConnection, connection))
        assert conflicted and created
        connection.commit.assert_not_awaited()
        connection.rollback.assert_not_awaited()

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", [1142, 1213, "cancelled", "persistent-lock"])
def test_fulltext_initialization_preserves_failures_and_bounds_lock_recovery(fts_startup, failure):
    async def scenario():
        connection = _connection()
        connection.scalar.return_value = 0
        error = (
            asyncio.CancelledError()
            if failure == "cancelled"
            else OperationalError(
                "CREATE FULLTEXT INDEX",
                None,
                MySQLOperationalError(1205 if failure == "persistent-lock" else failure, "DDL failed"),
            )
        )
        connection.exec_driver_sql.side_effect = error if failure == "persistent-lock" else [error, MagicMock()]
        with pytest.raises((type(error), AtomicMemoryIndexError)) as caught:
            await asyncio.wait_for(fts_startup.initialize(cast(AsyncConnection, connection)), timeout=30)
        _assert_failure(error, caught.value)
        connection.commit.assert_not_awaited()
        connection.rollback.assert_not_awaited()

    asyncio.run(scenario())


def test_fulltext_initialization_checks_capability_after_peer_created_index(fts_startup):
    async def scenario():
        connection = _connection()
        created = False
        capability_error = OperationalError("FULLTEXT capability probe", None, MySQLOperationalError(1142, "denied"))

        async def exists(_statement, _parameters):
            return int(created)

        async def execute(statement):
            nonlocal created
            if statement.lstrip().startswith("CREATE FULLTEXT INDEX"):
                assert not created
                created = True
                raise OperationalError(statement, None, MySQLOperationalError(1205, "DDL lock conflict"))
            raise capability_error

        connection.scalar.side_effect = exists
        connection.exec_driver_sql.side_effect = execute
        connection.execute.side_effect = capability_error
        with pytest.raises((OperationalError, AtomicMemoryIndexError)) as caught:
            await fts_startup.initialize(cast(AsyncConnection, connection))
        _assert_failure(capability_error, caught.value)
        assert created
        connection.commit.assert_not_awaited()
        connection.rollback.assert_not_awaited()

    asyncio.run(scenario())


def test_experience_initialization_refreshes_projection_after_peer_created_index(monkeypatch):
    monkeypatch.setattr(experience_index, "ensure_artifact_head_searchable_text", AsyncMock())
    monkeypatch.setattr(experience_index, "lexical_projection_is_current", AsyncMock(return_value=False))
    index = experience_index.OceanBaseExperienceFTSIndex()
    projection_error = OperationalError("projection refresh", None, MySQLOperationalError(1142, "denied"))
    monkeypatch.setattr(index, "rebuild", AsyncMock(side_effect=projection_error))

    async def scenario():
        connection = _connection()
        connection.scalar.side_effect = [0, 1]
        connection.exec_driver_sql.side_effect = OperationalError(
            "CREATE FULLTEXT INDEX", None, MySQLOperationalError(1205, "DDL lock conflict")
        )
        with pytest.raises(OperationalError) as caught:
            await index.initialize(cast(AsyncConnection, connection))
        assert caught.value is projection_error
        connection.commit.assert_not_awaited()
        connection.rollback.assert_not_awaited()

    asyncio.run(scenario())
