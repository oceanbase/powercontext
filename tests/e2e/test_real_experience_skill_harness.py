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
from pathlib import Path

from sqlalchemy import text

from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from tests.e2e.real_experience_skill.harness import _purge_existing_harness_scopes


def test_preflight_cleanup_accepts_a_fresh_database(tmp_path: Path) -> None:
    database = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'fresh.db'}")

    cleanup = asyncio.run(_purge_existing_harness_scopes(database))

    assert cleanup == {
        "scope_count": 0,
        "rows_before": {},
        "rows_after": {},
        "remaining_row_count": 0,
        "remaining_harness_scope_count": 0,
    }


def test_preflight_cleanup_uses_only_existing_scope_tables(tmp_path: Path) -> None:
    database = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'partial.db'}")

    async def scenario() -> tuple[dict[str, object], int]:
        async with SQLiteProfile.open(database, tables=()) as profile, profile.database.transaction() as connection:
            await connection.exec_driver_sql("CREATE TABLE pc_sources (scope_id TEXT NOT NULL)")
            await connection.execute(
                text("INSERT INTO pc_sources (scope_id) VALUES (:scope_id)"),
                [
                    {"scope_id": "configured-real-memory:stale"},
                    {"scope_id": "project:keep"},
                ],
            )
        cleanup = await _purge_existing_harness_scopes(database)
        async with SQLiteProfile.open(database, tables=()) as profile, profile.database.transaction() as connection:
            remaining = int(await connection.scalar(text("SELECT COUNT(*) FROM pc_sources")) or 0)
        return cleanup, remaining

    cleanup, remaining = asyncio.run(scenario())

    assert cleanup["scope_count"] == 1
    assert cleanup["rows_before"] == {"pc_sources": 1}
    assert cleanup["rows_after"] == {}
    assert cleanup["remaining_row_count"] == 0
    assert cleanup["remaining_harness_scope_count"] == 0
    assert remaining == 1


def test_preflight_cleanup_discovers_registered_scope_and_removes_access_rows(tmp_path: Path) -> None:
    database = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'registered.db'}")

    async def scenario() -> tuple[dict[str, object], dict[str, int]]:
        async with SQLiteProfile.open(database, tables=()) as profile, profile.database.transaction() as connection:
            await connection.exec_driver_sql("CREATE TABLE pc_scopes (scope_id TEXT NOT NULL)")
            await connection.exec_driver_sql(
                "CREATE TABLE pc_scope_creation_requests (idempotency_key TEXT NOT NULL, scope_id TEXT NOT NULL)"
            )
            await connection.exec_driver_sql(
                "CREATE TABLE pc_access_relationships (binding_id TEXT NOT NULL, scope_id TEXT NOT NULL)"
            )
            await connection.exec_driver_sql("CREATE TABLE pc_access_owners (scope_id TEXT NOT NULL)")
            await connection.execute(
                text("INSERT INTO pc_scopes (scope_id) VALUES (:scope_id)"),
                [{"scope_id": "scope:generated"}, {"scope_id": "scope:keep"}],
            )
            await connection.execute(
                text(
                    "INSERT INTO pc_scope_creation_requests (idempotency_key, scope_id) "
                    "VALUES (:idempotency_key, :scope_id)"
                ),
                [
                    {
                        "idempotency_key": "configured-real-memory:registered",
                        "scope_id": "scope:generated",
                    },
                    {"idempotency_key": "keep", "scope_id": "scope:keep"},
                ],
            )
            await connection.execute(
                text("INSERT INTO pc_access_relationships (binding_id, scope_id) VALUES (:binding_id, :scope_id)"),
                [
                    {"binding_id": "binding:generated", "scope_id": "scope:generated"},
                    {"binding_id": "binding:keep", "scope_id": "scope:keep"},
                ],
            )
            await connection.execute(
                text("INSERT INTO pc_access_owners (scope_id) VALUES (:scope_id)"),
                [{"scope_id": "scope:generated"}, {"scope_id": "scope:keep"}],
            )
        cleanup = await _purge_existing_harness_scopes(database)
        async with SQLiteProfile.open(database, tables=()) as profile, profile.database.transaction() as connection:
            remaining = {
                table_name: int(
                    await connection.scalar(text(f"SELECT COUNT(*) FROM {table_name}")) or 0  # noqa: S608
                )
                for table_name in (
                    "pc_scopes",
                    "pc_scope_creation_requests",
                    "pc_access_relationships",
                    "pc_access_owners",
                )
            }
        return cleanup, remaining

    cleanup, remaining = asyncio.run(scenario())

    assert cleanup["scope_count"] == 1
    assert cleanup["rows_before"] == {
        "pc_access_relationships": 1,
        "pc_scope_creation_requests": 1,
        "pc_scopes": 1,
        "pc_access_owners": 1,
    }
    assert cleanup["rows_after"] == {}
    assert cleanup["remaining_row_count"] == 0
    assert cleanup["remaining_harness_scope_count"] == 0
    assert remaining == {
        "pc_scopes": 1,
        "pc_scope_creation_requests": 1,
        "pc_access_relationships": 1,
        "pc_access_owners": 1,
    }
