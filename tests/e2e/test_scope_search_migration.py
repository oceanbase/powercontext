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

"""Resume committed MySQL DDL from interrupted Scope search upgrades."""

import asyncio

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from powercontext.builtin.persistence.oceanbase import OceanBaseConfig
from powercontext.builtin.persistence.scope_search_schema import ensure_scope_search_schema
from tests.e2e.atomic_memory_migration_backend import migration_profile


@pytest.mark.parametrize("backfilled", [False, True])
def test_interrupted_scope_search_upgrade_recovers_constraints(migration_backend, backfilled: bool) -> None:
    if not isinstance(migration_backend.config, OceanBaseConfig):
        pytest.skip("MySQL DDL recovery requires the live OceanBase backend")

    async def scenario() -> None:
        title = "ＰowerContext"  # noqa: RUF001 - migration must preserve fullwidth characters
        async with (
            migration_profile(migration_backend.config) as profile,
            profile.database.transaction() as connection,
        ):
            await connection.exec_driver_sql(
                "CREATE TABLE pc_scopes (scope_id VARCHAR(256) PRIMARY KEY, title VARCHAR(256) NOT NULL, "
                "summary MEDIUMTEXT NOT NULL, scope_id_search MEDIUMTEXT NULL, title_search MEDIUMTEXT NOT NULL)"
            )
            await connection.exec_driver_sql(
                "CREATE TABLE pc_scope_external_references (scope_id VARCHAR(256) NOT NULL, ordinal INT NOT NULL, "
                "value MEDIUMTEXT NOT NULL, value_search MEDIUMTEXT NULL, PRIMARY KEY (scope_id, ordinal))"
            )
            await connection.exec_driver_sql(
                "CREATE TABLE pc_scope_bindings (integration VARCHAR(128) NOT NULL, kind VARCHAR(64) NOT NULL, "
                "external_id VARCHAR(256) NOT NULL, PRIMARY KEY (integration, kind, external_id))"
            )
            await connection.execute(
                text("INSERT INTO pc_scopes VALUES (:scope, :title, :summary, :search, :title)"),
                {"scope": "scp_ABC", "title": title, "summary": "部署地址", "search": None},
            )
            await connection.execute(
                text("INSERT INTO pc_scope_external_references VALUES (:scope, 0, :value, NULL)"),
                {"scope": "scp_ABC", "value": "GitHub/OceanBase"},
            )
            await connection.exec_driver_sql("INSERT INTO pc_scope_bindings VALUES ('codex', 'workspace', 'ONE')")
            if backfilled:
                await connection.exec_driver_sql("UPDATE pc_scopes SET scope_id_search = scope_id")
                await connection.exec_driver_sql("UPDATE pc_scope_external_references SET value_search = value")

        # A new connection sees nullable columns committed by a previous startup,
        # alongside a completed column and columns that have not yet been added.
        for _ in range(2):
            async with migration_profile(migration_backend.config) as profile:
                async with profile.database.transaction() as connection:
                    await ensure_scope_search_schema(connection)
                async with profile.database.transaction() as connection:
                    scope = (
                        await connection.exec_driver_sql(
                            "SELECT scope_id, title, summary, scope_id_search, title_search, summary_search FROM pc_scopes"
                        )
                    ).one()
                    assert tuple(scope) == (
                        "scp_ABC",
                        title,
                        "部署地址",
                        "scp_ABC",
                        title,
                        "部署地址",
                    )
                    reference = (
                        await connection.exec_driver_sql("SELECT value, value_search FROM pc_scope_external_references")
                    ).one()
                    assert tuple(reference) == ("GitHub/OceanBase", "GitHub/OceanBase")
                    binding = (
                        await connection.exec_driver_sql(
                            "SELECT external_id, external_id_search FROM pc_scope_bindings"
                        )
                    ).one()
                    assert tuple(binding) == ("ONE", "ONE")
                    columns = (
                        await connection.exec_driver_sql(
                            "SELECT table_name, column_name, is_nullable FROM information_schema.columns "
                            "WHERE table_schema = DATABASE() AND "
                            "((table_name = 'pc_scopes' AND column_name IN "
                            "('scope_id_search', 'title_search', 'summary_search')) OR "
                            "(table_name = 'pc_scope_external_references' AND column_name = 'value_search') OR "
                            "(table_name = 'pc_scope_bindings' AND column_name = 'external_id_search'))"
                        )
                    ).all()
                    assert len(columns) == 5
                    assert all(column.is_nullable == "NO" for column in columns)
                with pytest.raises(IntegrityError):
                    async with profile.database.transaction() as connection:
                        await connection.exec_driver_sql("UPDATE pc_scopes SET scope_id_search = NULL")

    asyncio.run(scenario())
