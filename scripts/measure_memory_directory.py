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

"""Measure bounded Memory directory work without model or network calls."""

from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, TypeVar

from sqlalchemy import delete, func, select

from powercontext.builtin.artifacts.memory import MemoryDirectoryQuery, MemoryEntryInput
from powercontext.builtin.persistence import memory as persistence_memory
from powercontext.builtin.persistence.memory_query_migration import (
    apply_memory_query_migration,
    verify_memory_query_migration,
)
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.persistence.tables import (
    BUILTIN_TABLES,
    MEMORY_ENTRY_DIRECTORY_TABLE,
    MEMORY_ENTRY_HEADS_TABLE,
    MEMORY_QUERY_INDEX_SCHEMA_TABLE,
    MEMORY_TAG_GENERATIONS_TABLE,
)
from powercontext.builtin.runtime import BuiltinConfig, open_builtin_contexts

DEFAULT_SIZES = (200, 1_000, 5_000)
PAGE_LIMIT = 100
T = TypeVar("T")


class MeasurementError(RuntimeError):
    """The measured behavior no longer satisfies the RFC's resource contract."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise MeasurementError(message)


def _required(value: T | None, message: str) -> T:
    if value is None:
        raise MeasurementError(message)
    return value


async def _count(connection, table) -> int:
    return int(await connection.scalar(select(func.count()).select_from(table)) or 0)


async def _measure(entry_count: int) -> dict[str, Any]:
    with TemporaryDirectory(prefix=f"powercontext-memory-directory-{entry_count}-") as directory:
        config = SQLiteConfig(url=f"sqlite+aiosqlite:///{Path(directory) / 'scale.db'}")
        return await _measure_database(entry_count, config)


async def _measure_database(entry_count: int, config: SQLiteConfig) -> dict[str, Any]:
    runtime_config = BuiltinConfig(database=config)
    async with open_builtin_contexts(runtime_config) as contexts:
        service = (await contexts.get("project")).artifacts.memory
        memory = _required(
            await service.remember(
                memory=None,
                entries=tuple(
                    MemoryEntryInput(kind="fact", text=f"Memory directory scale entry {index}")
                    for index in range(entry_count)
                ),
                mode="append",
            ),
            "scale fixture did not create a Memory",
        )
        query = MemoryDirectoryQuery(limit=PAGE_LIMIT)
        page = await service.query_directory(memory.artifact_id, query)
        _require(page.memory_ref == memory.as_ref(), "directory page did not pin the written revision")
        _require(len(page.items) == PAGE_LIMIT, "directory page did not fill the requested limit")
        _require(page.next_cursor is not None, "directory page omitted continuation for remaining entries")

        statement = persistence_memory._directory_statement(
            "project",
            memory.artifact_id,
            query,
            memory.revision,
            "",
        )
        async with contexts.database.transaction() as connection:
            materialized = (await connection.execute(statement)).mappings().all()
            compiled = statement.compile(
                dialect=connection.dialect,
                compile_kwargs={"literal_binds": True},
            )
            plan = (await connection.exec_driver_sql(f"EXPLAIN QUERY PLAN {compiled}")).all()
            directory_before = await _count(connection, MEMORY_ENTRY_DIRECTORY_TABLE)
            heads_before = await _count(connection, MEMORY_ENTRY_HEADS_TABLE)

        _require(len(materialized) == PAGE_LIMIT + 1, "directory query did not materialize exactly one lookahead row")
        target = (await service.entries(memory))[0]
        revised = _required(
            await service.remember(
                memory=memory,
                entries=(
                    MemoryEntryInput(
                        entry=target,
                        kind=target.kind,
                        text=f"{target.text} revised",
                    ),
                ),
                mode="append",
            ),
            "single-entry revision produced no Memory revision",
        )
        async with contexts.database.transaction() as connection:
            directory_after = await _count(connection, MEMORY_ENTRY_DIRECTORY_TABLE)
            heads_after = await _count(connection, MEMORY_ENTRY_HEADS_TABLE)
            closed_rows = int(
                await connection.scalar(
                    select(func.count())
                    .select_from(MEMORY_ENTRY_DIRECTORY_TABLE)
                    .where(MEMORY_ENTRY_DIRECTORY_TABLE.c.valid_to_revision == revised.revision)
                )
                or 0
            )

        _require(directory_after - directory_before == 1, "single-entry revision added more than one directory row")
        _require(heads_before == heads_after == entry_count, "single-entry revision changed the head-row cardinality")
        _require(closed_rows == 1, "single-entry revision did not close exactly one directory row")

    # Rebuild the derived state with one revision per transaction-sized batch.
    async with (
        SQLiteProfile.open(config, tables=BUILTIN_TABLES) as profile,
        profile.database.transaction() as connection,
    ):
        await connection.execute(delete(MEMORY_ENTRY_DIRECTORY_TABLE))
        await connection.execute(delete(MEMORY_TAG_GENERATIONS_TABLE))
        await connection.execute(delete(MEMORY_QUERY_INDEX_SCHEMA_TABLE))

    migration_processed: list[int] = []
    migration_directory_rows: list[int] = []
    async with SQLiteProfile.open(config, tables=BUILTIN_TABLES) as profile:
        while True:
            async with profile.database.transaction() as connection:
                progress = await apply_memory_query_migration(
                    connection,
                    migration_id=f"scale-{entry_count}",
                    batch_size=1,
                )
                if progress.processed:
                    migration_processed.append(progress.processed)
                    migration_directory_rows.append(await _count(connection, MEMORY_ENTRY_DIRECTORY_TABLE))
            if progress.phase == "verify":
                break
            _require(not progress.complete, "migration unexpectedly completed without verification")
        async with profile.database.transaction() as connection:
            verification = await verify_memory_query_migration(connection)

    _require(migration_processed == [1, 1], "batch_size=1 did not process one revision per batch")
    _require(
        migration_directory_rows == [entry_count, entry_count + 1],
        "migration did not rebuild the initial manifest followed by one changed entry",
    )
    _require(verification.ready, "rebuilt directory did not verify")
    _require(verification.checked_revisions == 2, "verification did not inspect both Memory revisions")

    return {
        "entries": entry_count,
        "page_limit": PAGE_LIMIT,
        "returned_rows": len(page.items),
        "materialized_rows": len(materialized),
        "encoded_item_bytes": sum(len(item.model_dump_json().encode("utf-8")) for item in page.items),
        "encoded_page_bytes": len(page.model_dump_json().encode("utf-8")),
        "sqlite_query_plan": [str(row[3]) for row in plan],
        "write_delta": {
            "directory_rows_added": directory_after - directory_before,
            "directory_rows_closed": closed_rows,
            "head_row_delta": heads_after - heads_before,
        },
        "migration": {
            "batch_size_revisions": 1,
            "processed_revisions_per_batch": migration_processed,
            "directory_rows_after_each_batch": migration_directory_rows,
            "verified_revisions": verification.checked_revisions,
        },
    }


async def _run(sizes: tuple[int, ...]) -> dict[str, Any]:
    return {
        "environment": {"backend": "SQLite", "sqlite_version": sqlite3.sqlite_version},
        "measurements": [await _measure(size) for size in sizes],
        "limits": {
            "database_rows_examined": ("not reported: SQLite EXPLAIN QUERY PLAN does not provide an examined-row count")
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sizes", nargs="*", type=int, default=DEFAULT_SIZES)
    arguments = parser.parse_args()
    sizes = tuple(arguments.sizes)
    if not sizes or any(size < PAGE_LIMIT + 1 for size in sizes):
        parser.error(f"every size must be at least {PAGE_LIMIT + 1}")
    print(json.dumps(asyncio.run(_run(sizes)), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
