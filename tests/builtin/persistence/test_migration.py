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
from contextlib import asynccontextmanager
from typing import Any, cast

import pytest
from sqlalchemy import inspect

from powercontext.builtin.persistence import migration as migration_module
from powercontext.builtin.persistence.database import AsyncDatabase
from powercontext.builtin.persistence.migration import (
    CURRENT_SCHEMA_REVISION,
    SchemaCompatibilityError,
    SchemaNotCurrentError,
    migrate_database,
    require_current_schema,
)
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.persistence.tables import (
    MEMORY_TABLES,
    SHARED_TABLES,
    STATISTICS_TABLES,
    WORK_ITEMS_TABLE,
)


def test_migration_commits_coordination_ddl_before_lease_dml(monkeypatch: pytest.MonkeyPatch) -> None:
    """Protect OceanBase/MySQL from DDL invalidating the lease transaction."""

    async def scenario() -> None:
        connections: list[object] = []
        lease = object()

        class _Database:
            @asynccontextmanager
            async def transaction(self):
                connection = object()
                connections.append(connection)
                yield connection

        async def create_tables(connection: object, tables: object) -> None:
            assert connection is connections[0]
            assert tables == (migration_module.SCHEDULER_LEASES_TABLE,)

        class _Repository:
            async def acquire_lease(self, connection: object, **_: Any) -> object:
                assert connection is connections[1]
                return lease

        monkeypatch.setattr(migration_module, "create_tables", create_tables)
        monkeypatch.setattr(migration_module, "CoordinationRepository", _Repository)

        acquired = await migration_module._acquire_migration_lease(cast(AsyncDatabase, _Database()))

        assert acquired is lease
        assert len(connections) == 2

    asyncio.run(scenario())


def test_clean_schema_migrates_to_head_idempotently(tmp_path) -> None:
    async def scenario() -> None:
        config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'clean.db'}")
        async with SQLiteProfile.open(config, tables=(), create_schema=False) as profile:
            assert await migrate_database(profile.database) == CURRENT_SCHEMA_REVISION
            assert await migrate_database(profile.database) == CURRENT_SCHEMA_REVISION
            assert await require_current_schema(profile.database) == CURRENT_SCHEMA_REVISION
            async with profile.database.transaction() as connection:
                table_names = await connection.run_sync(lambda value: set(inspect(value).get_table_names()))
            assert WORK_ITEMS_TABLE.name in table_names

    asyncio.run(scenario())


def test_complete_unversioned_baseline_is_validated_stamped_and_expanded(tmp_path) -> None:
    async def scenario() -> None:
        config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'legacy.db'}")
        baseline = SHARED_TABLES + MEMORY_TABLES + STATISTICS_TABLES
        async with SQLiteProfile.open(config, tables=baseline) as profile:
            assert await migrate_database(profile.database) == CURRENT_SCHEMA_REVISION
            await require_current_schema(profile.database)

    asyncio.run(scenario())


def test_partial_unversioned_schema_is_rejected_without_blind_stamping(tmp_path) -> None:
    async def scenario() -> None:
        config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'partial.db'}")
        async with SQLiteProfile.open(config, tables=(SHARED_TABLES[0],)) as profile:
            with pytest.raises(SchemaCompatibilityError, match="missing baseline tables"):
                await migrate_database(profile.database)
            with pytest.raises(SchemaNotCurrentError):
                await require_current_schema(profile.database)

    asyncio.run(scenario())


def test_current_revision_with_missing_physical_table_is_rejected(tmp_path) -> None:
    async def scenario() -> None:
        config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'damaged.db'}")
        async with SQLiteProfile.open(config, tables=(), create_schema=False) as profile:
            await migrate_database(profile.database)
            async with profile.database.transaction() as connection:
                await connection.run_sync(WORK_ITEMS_TABLE.drop)

            with pytest.raises(SchemaCompatibilityError, match="current revision is missing tables"):
                await require_current_schema(profile.database)

    asyncio.run(scenario())
