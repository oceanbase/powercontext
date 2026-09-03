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

"""Startup migration for remote Skill desired/observed publication state."""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.builtin.persistence.tables import SKILL_PUBLICATIONS_TABLE

_REMOTE_COLUMNS = frozenset({"desired_state", "observed_generation", "last_error_code", "observed_at"})
_MYSQL_IDENTITY = "CHARACTER SET utf8mb4 COLLATE utf8mb4_bin"


async def ensure_skill_distribution_schema(connection: AsyncConnection, /) -> None:
    """Upgrade host-local publication rows for remote desired-state reconciliation."""

    if connection.dialect.name == "sqlite":
        await _ensure_sqlite_schema(connection)
        return
    if connection.dialect.name == "mysql":
        await _ensure_mysql_schema(connection)
        return
    raise ValueError(f"unsupported Skill distribution migration dialect: {connection.dialect.name}")  # noqa: TRY003


async def _ensure_sqlite_schema(connection: AsyncConnection) -> None:
    columns = tuple((await connection.exec_driver_sql("PRAGMA table_info('pc_skill_publications')")).mappings())
    by_name = {str(column["name"]): column for column in columns}
    if not (by_name.keys() >= _REMOTE_COLUMNS and int(by_name["destination"]["notnull"]) == 0):
        await connection.exec_driver_sql(
            "ALTER TABLE pc_skill_publications RENAME TO pc_skill_publications_remote_v1_legacy"
        )
        await connection.run_sync(lambda sync_connection: SKILL_PUBLICATIONS_TABLE.create(sync_connection))
        await connection.exec_driver_sql(
            """
            INSERT INTO pc_skill_publications (
                scope_id, target_id, artifact_id, desired_state, desired_revision, desired_tree_digest,
                observed_revision, observed_tree_digest, observed_generation, destination, state,
                selected_runtime_variant, environment_fingerprint, last_error_code, observed_at,
                generation, updated_at
            )
            SELECT
                scope_id,
                target_id,
                artifact_id,
                CASE WHEN state = 'unpublished' THEN 'unpublished' ELSE 'published' END,
                desired_revision,
                desired_tree_digest,
                observed_revision,
                observed_tree_digest,
                generation,
                destination,
                state,
                selected_runtime_variant,
                environment_fingerprint,
                NULL,
                updated_at,
                generation,
                updated_at
            FROM pc_skill_publications_remote_v1_legacy
            """
        )
        await connection.exec_driver_sql("DROP TABLE pc_skill_publications_remote_v1_legacy")

    target_columns = {
        str(column["name"])
        for column in (await connection.exec_driver_sql("PRAGMA table_info('pc_agent_skill_targets')")).mappings()
    }
    if "display_name" not in target_columns:
        await connection.exec_driver_sql("ALTER TABLE pc_agent_skill_targets ADD COLUMN display_name VARCHAR(128)")
        await connection.exec_driver_sql(
            "UPDATE pc_agent_skill_targets SET display_name = target_id WHERE display_name IS NULL"
        )
    if "machine_hostname" not in target_columns:
        await connection.exec_driver_sql("ALTER TABLE pc_agent_skill_targets ADD COLUMN machine_hostname VARCHAR(255)")
    if "workspace_name" not in target_columns:
        await connection.exec_driver_sql("ALTER TABLE pc_agent_skill_targets ADD COLUMN workspace_name VARCHAR(128)")


async def _ensure_mysql_schema(connection: AsyncConnection) -> None:
    columns = tuple(
        (
            await connection.execute(
                text(
                    "SELECT column_name, is_nullable FROM information_schema.columns "
                    "WHERE table_schema = DATABASE() AND table_name = :table_name"
                ),
                {"table_name": "pc_skill_publications"},
            )
        ).mappings()
    )
    by_name = {str(column["column_name"]): column for column in columns}

    if "desired_state" not in by_name:
        await connection.exec_driver_sql(
            f"ALTER TABLE pc_skill_publications ADD COLUMN desired_state VARCHAR(16) {_MYSQL_IDENTITY} NULL"
        )
        await connection.exec_driver_sql(
            "UPDATE pc_skill_publications SET desired_state = "
            "CASE WHEN state = 'unpublished' THEN 'unpublished' ELSE 'published' END"
        )
        await connection.exec_driver_sql(
            "ALTER TABLE pc_skill_publications MODIFY COLUMN desired_state "
            f"VARCHAR(16) {_MYSQL_IDENTITY} NOT NULL DEFAULT 'published'"
        )
    if "observed_generation" not in by_name:
        await connection.exec_driver_sql("ALTER TABLE pc_skill_publications ADD COLUMN observed_generation BIGINT NULL")
        await connection.exec_driver_sql("UPDATE pc_skill_publications SET observed_generation = generation")
    if "last_error_code" not in by_name:
        await connection.exec_driver_sql(
            f"ALTER TABLE pc_skill_publications ADD COLUMN last_error_code VARCHAR(128) {_MYSQL_IDENTITY} NULL"
        )
    if "observed_at" not in by_name:
        await connection.exec_driver_sql("ALTER TABLE pc_skill_publications ADD COLUMN observed_at DATETIME(6) NULL")
        await connection.exec_driver_sql("UPDATE pc_skill_publications SET observed_at = updated_at")
    if str(by_name.get("destination", {}).get("is_nullable", "NO")).upper() != "YES":
        await connection.exec_driver_sql("ALTER TABLE pc_skill_publications MODIFY COLUMN destination MEDIUMTEXT NULL")

    target_columns = tuple(
        (
            await connection.execute(
                text(
                    "SELECT column_name, is_nullable FROM information_schema.columns "
                    "WHERE table_schema = DATABASE() AND table_name = :table_name"
                ),
                {"table_name": "pc_agent_skill_targets"},
            )
        ).mappings()
    )
    target_by_name = {str(column["column_name"]): column for column in target_columns}
    if "display_name" not in target_by_name:
        await connection.exec_driver_sql(
            f"ALTER TABLE pc_agent_skill_targets ADD COLUMN display_name VARCHAR(128) {_MYSQL_IDENTITY} NULL"
        )
    if str(target_by_name.get("display_name", {}).get("is_nullable", "YES")).upper() == "YES":
        await connection.exec_driver_sql(
            "UPDATE pc_agent_skill_targets SET display_name = target_id WHERE display_name IS NULL"
        )
        await connection.exec_driver_sql(
            f"ALTER TABLE pc_agent_skill_targets MODIFY COLUMN display_name VARCHAR(128) {_MYSQL_IDENTITY} NOT NULL"
        )
    if "machine_hostname" not in target_by_name:
        await connection.exec_driver_sql(
            f"ALTER TABLE pc_agent_skill_targets ADD COLUMN machine_hostname VARCHAR(255) {_MYSQL_IDENTITY} NULL"
        )
    if "workspace_name" not in target_by_name:
        await connection.exec_driver_sql(
            f"ALTER TABLE pc_agent_skill_targets ADD COLUMN workspace_name VARCHAR(128) {_MYSQL_IDENTITY} NULL"
        )

    await _replace_mysql_check(
        connection,
        name="ck_pc_skill_publications_desired_state",
        expression="desired_state IN ('published', 'unpublished')",
        required_marker="unpublished",
    )
    await _replace_mysql_check(
        connection,
        name="ck_pc_skill_publications_state",
        expression=(
            "state IN ('unpublished', 'pending', 'current', 'update_available', "
            "'delivery_failed', 'conflict', 'drifted', 'incompatible')"
        ),
        required_marker="delivery_failed",
    )
    await _replace_mysql_check(
        connection,
        name="ck_pc_skill_publications_observed_generation_nonnegative",
        expression="observed_generation IS NULL OR observed_generation >= 0",
        required_marker="observed_generation",
    )


async def _replace_mysql_check(
    connection: AsyncConnection,
    *,
    name: str,
    expression: str,
    required_marker: str,
) -> None:
    clause = await connection.scalar(
        text(
            "SELECT check_clause FROM information_schema.check_constraints "
            "WHERE constraint_schema = DATABASE() AND constraint_name = :constraint_name"
        ),
        {"constraint_name": name},
    )
    if clause is not None and required_marker in str(clause):
        return
    if clause is not None:
        await connection.exec_driver_sql(f"ALTER TABLE pc_skill_publications DROP CHECK {name}")
    await connection.exec_driver_sql(f"ALTER TABLE pc_skill_publications ADD CONSTRAINT {name} CHECK ({expression})")


__all__ = ["ensure_skill_distribution_schema"]
