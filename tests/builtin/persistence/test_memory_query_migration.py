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

import pytest
from sqlalchemy import delete, select, update

from powercontext.builtin.artifacts.memory import MemoryDirectoryQuery, MemoryEntryInput
from powercontext.builtin.persistence.memory_query_migration import (
    MemoryQueryIndexUnavailableError,
    apply_memory_query_migration,
    plan_memory_query_migration,
    verify_memory_query_migration,
)
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.persistence.tables import (
    BUILTIN_TABLES,
    MEMORY_ENTRY_DIRECTORY_TABLE,
    MEMORY_QUERY_INDEX_SCHEMA_TABLE,
    MEMORY_TAG_GENERATIONS_TABLE,
)
from powercontext.builtin.runtime import BuiltinConfig, open_builtin_contexts


def test_existing_memory_requires_resumable_verified_directory_backfill(tmp_path) -> None:
    async def scenario() -> None:
        config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'memory-query-migration.db'}")
        runtime_config = BuiltinConfig(database=config)

        async with open_builtin_contexts(runtime_config) as contexts:
            service = (await contexts.get("project")).artifacts.memory
            first = await service.remember(
                memory=None,
                entries=(
                    MemoryEntryInput(kind="fact", text="Keep this entry."),
                    MemoryEntryInput(kind="decision", text="Revise this entry before upgrading."),
                ),
                mode="append",
            )
            assert first is not None
            entries = await service.entries(first)
            changing = next(entry for entry in entries if entry.kind == "decision")
            second = await service.remember(
                memory=first,
                entries=(
                    MemoryEntryInput(
                        entry=changing,
                        kind=changing.kind,
                        text="This is the pre-upgrade revision.",
                    ),
                ),
                mode="append",
            )
            assert second is not None

        # Simulate a database created before the directory projection existed.
        async with (
            SQLiteProfile.open(config, tables=BUILTIN_TABLES) as profile,
            profile.database.transaction() as connection,
        ):
            await connection.execute(delete(MEMORY_ENTRY_DIRECTORY_TABLE))
            await connection.execute(delete(MEMORY_TAG_GENERATIONS_TABLE))
            await connection.execute(delete(MEMORY_QUERY_INDEX_SCHEMA_TABLE))

        async with open_builtin_contexts(runtime_config) as contexts:
            service = (await contexts.get("project")).artifacts.memory
            assert len(await service.entries(second)) == 2
            with pytest.raises(MemoryQueryIndexUnavailableError):
                await service.query_directory(second.artifact_id, MemoryDirectoryQuery())

            # Legacy writes remain available and maintain their delta even
            # while historical revisions still require backfill.
            stable = next(entry for entry in await service.entries(second) if entry.kind == "fact")
            third = await service.remember(
                memory=second,
                entries=(
                    MemoryEntryInput(
                        entry=stable,
                        kind=stable.kind,
                        text="This post-upgrade write already owns its directory delta.",
                    ),
                ),
                mode="append",
            )
            assert third is not None
            assert len(await service.entries(third)) == 2

        async with SQLiteProfile.open(config, tables=BUILTIN_TABLES) as profile:
            async with profile.database.transaction() as connection:
                plan = await plan_memory_query_migration(connection)
                assert plan.required
                assert plan.phase == "bootstrap"
                assert plan.memory_revision_count == 3
                assert plan.directory_row_count == 1

            async with profile.database.transaction() as connection:
                first_batch = await apply_memory_query_migration(
                    connection,
                    migration_id="test-1656",
                    batch_size=1,
                )
                assert first_batch.phase == "backfill"
                assert first_batch.processed == 1

            async with profile.database.transaction() as connection:
                with pytest.raises(MemoryQueryIndexUnavailableError, match="original ID"):
                    await apply_memory_query_migration(connection, migration_id="different", batch_size=1)

            while True:
                async with profile.database.transaction() as connection:
                    progress = await apply_memory_query_migration(
                        connection,
                        migration_id="test-1656",
                        batch_size=1,
                    )
                if progress.phase == "verify":
                    break

            async with profile.database.transaction() as connection:
                active_row = (
                    (
                        await connection.execute(
                            select(MEMORY_ENTRY_DIRECTORY_TABLE).where(MEMORY_ENTRY_DIRECTORY_TABLE.c.state == "active")
                        )
                    )
                    .mappings()
                    .first()
                )
                assert active_row is not None
                row_key = {
                    "scope_id": active_row["scope_id"],
                    "memory_artifact_id": active_row["memory_artifact_id"],
                    "entry_id": active_row["entry_id"],
                    "valid_from_revision": active_row["valid_from_revision"],
                }
                predicate = tuple(
                    getattr(MEMORY_ENTRY_DIRECTORY_TABLE.c, name) == value for name, value in row_key.items()
                )
                await connection.execute(
                    update(MEMORY_ENTRY_DIRECTORY_TABLE).where(*predicate).values(state="inactive")
                )
                failed = await verify_memory_query_migration(connection)
                assert not failed.ready
                assert failed.issues and failed.issues[0].startswith("directory mismatch")
                await connection.execute(update(MEMORY_ENTRY_DIRECTORY_TABLE).where(*predicate).values(state="active"))
                verified = await verify_memory_query_migration(connection)
                assert verified.ready
                assert verified.checked_revisions == 3

            async with profile.database.transaction() as connection:
                plan = await plan_memory_query_migration(connection)
                assert not plan.required
                assert plan.phase == "complete"
                assert await connection.scalar(select(MEMORY_TAG_GENERATIONS_TABLE.c.generation)) == 0

        async with open_builtin_contexts(runtime_config) as contexts:
            service = (await contexts.get("project")).artifacts.memory
            page = await service.query_directory(third.artifact_id, MemoryDirectoryQuery(limit=10))
            assert page.memory_ref == third.as_ref()
            assert {item.citation.entry_id for item in page.items} == {
                entry.entry_id for entry in await service.entries(third)
            }

    asyncio.run(scenario())
