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

import asyncio
import sqlite3
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.builtin.persistence.scope_search_schema import ensure_scope_search_schema
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.persistence.tables import BUILTIN_TABLES


def test_sqlite_scope_search_migration_backfills_legacy_rows_idempotently(tmp_path) -> None:
    database = tmp_path / "legacy-scopes.db"
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            CREATE TABLE pc_scopes (
                scope_id VARCHAR(256) PRIMARY KEY,
                title VARCHAR(256) NOT NULL,
                summary TEXT NOT NULL,
                parent_scope_id VARCHAR(256),
                version INTEGER NOT NULL,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE pc_scope_external_references (
                scope_id VARCHAR(256) NOT NULL,
                ordinal INTEGER NOT NULL,
                kind VARCHAR(128) NOT NULL,
                value TEXT NOT NULL,
                value_digest VARCHAR(64) NOT NULL,
                PRIMARY KEY (scope_id, ordinal)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE pc_scope_bindings (
                integration VARCHAR(128) NOT NULL,
                kind VARCHAR(64) NOT NULL,
                external_id VARCHAR(256) NOT NULL,
                scope_id VARCHAR(256) NOT NULL,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (integration, kind, external_id)
            )
            """
        )
        connection.execute(
            "INSERT INTO pc_scopes (scope_id, title, summary, version) VALUES (?, ?, ?, ?)",
            ("scp_ABC", "\N{FULLWIDTH LATIN CAPITAL LETTER P}owerContext", "Design", 1),
        )
        connection.execute(
            "INSERT INTO pc_scope_external_references "
            "(scope_id, ordinal, kind, value, value_digest) "
            "VALUES ('scp_ABC', 0, 'repository', 'GitHub/OceanBase', 'digest')"
        )
        connection.execute(
            "INSERT INTO pc_scope_bindings (integration, kind, external_id, scope_id) "
            "VALUES ('codex', 'workspace', 'Workspace-ONE', 'scp_ABC')"
        )

    async def exercise() -> None:
        config = SQLiteConfig(url=f"sqlite+aiosqlite:///{database}")
        async with SQLiteProfile.open(config, tables=BUILTIN_TABLES) as profile:
            for _ in range(2):
                async with profile.database.transaction() as connection:
                    await ensure_scope_search_schema(connection)

            async with profile.database.transaction() as connection:
                scope = (
                    (
                        await connection.exec_driver_sql(
                            "SELECT scope_id_search, title_search, summary_search FROM pc_scopes"
                        )
                    )
                    .mappings()
                    .one()
                )
                reference = (
                    (await connection.exec_driver_sql("SELECT value_search FROM pc_scope_external_references"))
                    .mappings()
                    .one()
                )
                binding = (
                    (await connection.exec_driver_sql("SELECT external_id_search FROM pc_scope_bindings"))
                    .mappings()
                    .one()
                )

        assert scope == {
            "scope_id_search": "scp_ABC",
            "title_search": "\N{FULLWIDTH LATIN CAPITAL LETTER P}owerContext",
            "summary_search": "Design",
        }
        assert reference["value_search"] == "GitHub/OceanBase"
        assert binding["external_id_search"] == "Workspace-ONE"

    asyncio.run(exercise())


def test_oceanbase_scope_search_migration_adds_portable_projection_columns() -> None:
    query_result = SimpleNamespace(mappings=lambda: [])
    connection = SimpleNamespace(
        dialect=SimpleNamespace(name="mysql"),
        execute=AsyncMock(return_value=query_result),
        exec_driver_sql=AsyncMock(),
    )

    asyncio.run(ensure_scope_search_schema(cast(AsyncConnection, connection)))

    statements = [call.args[0] for call in connection.exec_driver_sql.await_args_list]
    assert "ALTER TABLE pc_scopes ADD COLUMN scope_id_search MEDIUMTEXT NULL" in statements
    assert "ALTER TABLE pc_scope_external_references ADD COLUMN value_search MEDIUMTEXT NULL" in statements
    assert "ALTER TABLE pc_scope_bindings ADD COLUMN external_id_search MEDIUMTEXT NULL" in statements
    assert "ALTER TABLE pc_scopes MODIFY COLUMN scope_id_search MEDIUMTEXT NOT NULL" in statements
