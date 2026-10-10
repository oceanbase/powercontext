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

"""Read-only Atomic Memory readiness checks over public rows and schema metadata.

No archived payload or legacy entry is loaded here. Full historical validation
belongs to the explicitly invoked offline migration.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.builtin.persistence.errors import PersistenceError

LEGACY_CITATION_COLUMN = "memory_citations"
_LEGACY_CITATION_TABLES = ("pc_artifacts", "pc_artifact_candidate_versions")
LEGACY_ENTRY_FOREIGN_KEYS = {
    "pc_memory_entry_versions": ("scope_id", "family", "memory_artifact_id", "created_in_revision"),
    "pc_memory_entry_heads": ("scope_id", "family", "memory_artifact_id", "head_revision"),
}


class AtomicMemoryMigrationError(PersistenceError):
    def __init__(self, errors: tuple[str, ...]) -> None:
        self.errors = errors
        super().__init__("Atomic Memory offline migration is not ready: " + "; ".join(errors))


async def legacy_citation_columns(connection: AsyncConnection, tables: set[str]) -> list[str]:
    """Return public tables that still declare the retired citation column."""

    def read(value: Any) -> list[str]:
        inspector = inspect(value)
        return [
            table
            for table in _LEGACY_CITATION_TABLES
            if table in tables
            and any(column["name"] == LEGACY_CITATION_COLUMN for column in inspector.get_columns(table))
        ]

    return await connection.run_sync(read)


async def legacy_entry_foreign_key_names(connection: AsyncConnection, table: str) -> list[str | None]:
    columns = LEGACY_ENTRY_FOREIGN_KEYS[table]

    def read(value: Any) -> list[str | None]:
        return [
            item.get("name")
            for item in inspect(value).get_foreign_keys(table)
            if item["referred_table"] == "pc_artifacts" and tuple(item["constrained_columns"]) == columns
        ]

    return await connection.run_sync(read)


async def legacy_foreign_keys(connection: AsyncConnection, tables: set[str]) -> list[str]:
    return [
        table
        for table in LEGACY_ENTRY_FOREIGN_KEYS
        if table in tables and await legacy_entry_foreign_key_names(connection, table)
    ]


async def atomic_memory_readiness_issues(
    connection: AsyncConnection, tables: set[str] | None = None, *, collections_removed: bool = True
) -> list[str]:
    """Check public migration completion, allowing collections only during offline pre-removal acceptance."""

    if tables is None:
        tables = set(await connection.run_sync(lambda value: inspect(value).get_table_names()))
    issues: list[str] = []
    if (
        collections_removed
        and "pc_artifacts" in tables
        and await connection.scalar(text("SELECT COUNT(*) FROM pc_artifacts WHERE family = 'memory'"))
    ):
        issues.append("legacy Memory collections remain in public Artifact tables; run apply")
    referrers = "" if collections_removed else " AND family <> 'memory'"
    if "pc_artifact_lineage_artifacts" in tables and await connection.scalar(
        text(
            "SELECT COUNT(*) FROM pc_artifact_lineage_artifacts WHERE upstream_family = 'memory'" + referrers  # noqa: S608
        )
    ):
        issues.append("public lineage still references legacy Memory collections; run apply")
    foreign_keys = await legacy_foreign_keys(connection, tables)
    if foreign_keys:
        issues.append("legacy entry tables still reference public Artifacts: " + ", ".join(foreign_keys))
    citation_tables = await legacy_citation_columns(connection, tables)
    if collections_removed and citation_tables:
        issues.append(
            "public tables still declare legacy memory_citations: " + ", ".join(citation_tables) + "; run apply"
        )
    return issues


async def assert_atomic_memory_migration_ready(connection: AsyncConnection) -> None:
    """Reject public migration residuals without reading frozen historical records."""

    errors = await atomic_memory_readiness_issues(connection)
    if errors:
        raise AtomicMemoryMigrationError(tuple(errors))
