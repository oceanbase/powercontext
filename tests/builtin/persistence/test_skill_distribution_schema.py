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

from powercontext.builtin.persistence.skill_distribution_schema import ensure_skill_distribution_schema
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.persistence.tables import BUILTIN_TABLES


def test_sqlite_startup_upgrades_legacy_skill_publications_idempotently(tmp_path) -> None:
    database = tmp_path / "legacy-skill-publications.db"
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            CREATE TABLE pc_agent_skill_targets (
                scope_id VARCHAR(256) NOT NULL,
                target_id VARCHAR(64) NOT NULL,
                agent_kind VARCHAR(32) NOT NULL,
                installation_scope VARCHAR(16) NOT NULL,
                delivery_mode VARCHAR(16) NOT NULL,
                installation_id VARCHAR(128),
                state VARCHAR(16) NOT NULL,
                enrollment_token_digest VARCHAR(64),
                enrollment_expires_at DATETIME,
                credential_subject VARCHAR(128),
                credential_verifier VARCHAR(64),
                receiver_version VARCHAR(64),
                environment_fingerprint VARCHAR(64),
                last_seen_at DATETIME,
                generation BIGINT NOT NULL,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL,
                PRIMARY KEY (scope_id, target_id)
            )
            """
        )
        connection.execute(
            """
            INSERT INTO pc_agent_skill_targets VALUES (
                'project:one', 'codex-legacy', 'codex', 'project', 'agent_pull', NULL,
                'pending', 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
                '2026-08-24 10:10:00', NULL, NULL, NULL, NULL, NULL, 0,
                '2026-08-24 10:00:00', '2026-08-24 10:00:00'
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE pc_skill_publications (
                scope_id VARCHAR(256) NOT NULL,
                target_id VARCHAR(64) NOT NULL,
                artifact_id VARCHAR(128) NOT NULL,
                desired_revision INTEGER NOT NULL,
                desired_tree_digest VARCHAR(64) NOT NULL,
                observed_revision INTEGER,
                observed_tree_digest VARCHAR(64),
                destination TEXT NOT NULL,
                state VARCHAR(32) NOT NULL,
                selected_runtime_variant VARCHAR(128),
                environment_fingerprint VARCHAR(64),
                generation BIGINT NOT NULL,
                updated_at DATETIME NOT NULL,
                PRIMARY KEY (scope_id, target_id, artifact_id)
            )
            """
        )
        connection.execute(
            """
            INSERT INTO pc_skill_publications VALUES (
                'project:one', 'codex-local', 'release-check', 3,
                'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
                3, 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
                '/workspace/.agents/skills/release-check', 'current', NULL, NULL, 7,
                '2026-08-24 10:00:00'
            )
            """
        )

    async def exercise() -> None:
        config = SQLiteConfig(url=f"sqlite+aiosqlite:///{database}")
        for _ in range(2):
            async with (
                SQLiteProfile.open(config, tables=BUILTIN_TABLES) as profile,
                profile.database.transaction() as connection,
            ):
                await ensure_skill_distribution_schema(connection)
                columns = tuple(
                    (await connection.exec_driver_sql("PRAGMA table_info('pc_skill_publications')")).mappings()
                )
                assert {column["name"] for column in columns} >= {
                    "desired_state",
                    "observed_generation",
                    "last_error_code",
                    "observed_at",
                }
                destination = next(column for column in columns if column["name"] == "destination")
                assert destination["notnull"] == 0

                row = (
                    (
                        await connection.exec_driver_sql(
                            "SELECT desired_state, observed_generation, observed_at FROM pc_skill_publications"
                        )
                    )
                    .mappings()
                    .one()
                )
                assert row["desired_state"] == "published"
                assert row["observed_generation"] == 7
                assert row["observed_at"] is not None

                target_columns = {
                    column["name"]
                    for column in (
                        await connection.exec_driver_sql("PRAGMA table_info('pc_agent_skill_targets')")
                    ).mappings()
                }
                assert target_columns >= {"display_name", "machine_hostname", "workspace_name"}
                target = (
                    (
                        await connection.exec_driver_sql(
                            "SELECT target_id, display_name, machine_hostname, workspace_name "
                            "FROM pc_agent_skill_targets"
                        )
                    )
                    .mappings()
                    .one()
                )
                assert target == {
                    "target_id": "codex-legacy",
                    "display_name": "codex-legacy",
                    "machine_hostname": None,
                    "workspace_name": None,
                }

    asyncio.run(exercise())


def test_oceanbase_migration_adds_remote_columns_and_replaces_checks() -> None:
    query_result = SimpleNamespace(mappings=lambda: [])
    connection = SimpleNamespace(
        dialect=SimpleNamespace(name="mysql"),
        execute=AsyncMock(return_value=query_result),
        scalar=AsyncMock(return_value=None),
        exec_driver_sql=AsyncMock(),
    )

    asyncio.run(ensure_skill_distribution_schema(cast(AsyncConnection, connection)))

    statements = [call.args[0] for call in connection.exec_driver_sql.await_args_list]
    assert "ALTER TABLE pc_skill_publications ADD COLUMN desired_state VARCHAR(16) " in statements[0]
    assert any("MODIFY COLUMN destination MEDIUMTEXT NULL" in statement for statement in statements)
    assert any("delivery_failed" in statement for statement in statements)
    assert any("observed_generation IS NULL OR observed_generation >= 0" in statement for statement in statements)
    assert any("ADD COLUMN display_name" in statement for statement in statements)
    assert any("SET display_name = target_id" in statement for statement in statements)
    assert any("ADD COLUMN machine_hostname" in statement for statement in statements)
    assert any("ADD COLUMN workspace_name" in statement for statement in statements)
