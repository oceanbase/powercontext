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

from sqlalchemy import BigInteger, DateTime, Integer, String
from sqlalchemy.dialects import mysql
from sqlalchemy.schema import CreateTable, ForeignKeyConstraint, PrimaryKeyConstraint, UniqueConstraint

from powercontext.builtin.artifacts.memory import MemoryEntryInput
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.persistence.tables import (
    MEMORY_ENTRY_HEADS_TABLE,
    MEMORY_ENTRY_VERSIONS_TABLE,
)
from powercontext.builtin.runtime import BuiltinConfig, open_builtin_contexts
from powercontext.builtin.sources import ContentCapture, ContentSource

_INNODB_MAX_INDEX_BYTES = 3072


class _UnbudgetedColumnTypeError(TypeError):
    def __init__(self, column_type: object) -> None:
        super().__init__(f"unbudgeted indexed column type: {column_type!r}")


def _key_budget(constraint: PrimaryKeyConstraint | UniqueConstraint | ForeignKeyConstraint) -> int:
    total = 0
    for column in constraint.columns:
        if isinstance(column.type, String):
            assert column.type.length is not None
            total += column.type.length * 4
        elif isinstance(column.type, BigInteger | Integer | DateTime):
            total += 8
        else:
            raise _UnbudgetedColumnTypeError(column.type)
    return total


def test_memory_schema_is_mysql_compilable_and_respects_key_and_payload_limits() -> None:
    dialect = mysql.dialect()
    versions = str(CreateTable(MEMORY_ENTRY_VERSIONS_TABLE).compile(dialect=dialect))
    heads = str(CreateTable(MEMORY_ENTRY_HEADS_TABLE).compile(dialect=dialect))

    assert "text MEDIUMTEXT NOT NULL" in versions
    assert "source_refs MEDIUMBLOB NOT NULL" in versions
    assert "artifact_refs MEDIUMBLOB NOT NULL" in versions
    assert "searchable_text MEDIUMTEXT NOT NULL" in heads

    budgets = [
        _key_budget(constraint)
        for table in (MEMORY_ENTRY_VERSIONS_TABLE, MEMORY_ENTRY_HEADS_TABLE)
        for constraint in table.constraints
        if isinstance(constraint, PrimaryKeyConstraint | UniqueConstraint | ForeignKeyConstraint)
    ]
    assert budgets
    assert max(budgets) == 2560
    assert all(budget < _INNODB_MAX_INDEX_BYTES for budget in budgets)


def test_sqlite_memory_backend_commits_authoritative_history_and_fts() -> None:
    async def scenario() -> None:
        async with open_builtin_contexts(BuiltinConfig(database=SQLiteConfig())) as contexts:
            context = await contexts.get("project")
            source, _ = await context.sources.capture(
                ContentCapture(
                    source_id="turn-1",
                    content="PowerContext owns the atomic composition boundary.",
                )
            )
            first = await context.artifacts.memory.remember(
                memory=None,
                sources=(source,),
                entries=(
                    MemoryEntryInput(
                        kind="decision",
                        text="Use one atomic composition boundary.",
                        sources=(source,),
                    ),
                ),
                mode="append",
            )
            assert first is not None
            second = await context.artifacts.memory.remember(
                memory=first,
                entries=(
                    MemoryEntryInput(
                        kind="constraint",
                        text="Do not split the provider transaction.",
                    ),
                ),
                mode="append",
            )
            assert second is not None
            assert second.artifact_id == "memory"
            assert second.revision == 2
            assert tuple(item.revision for item in await context.artifacts.memory.revisions(first)) == (1, 2)
            assert {item.text for item in await context.artifacts.memory.entries(second)} == {
                "Use one atomic composition boundary.",
                "Do not split the provider transaction.",
            }

            result = await context.artifacts.memory.search(
                "atomic composition",
                memories=(second,),
                mode="fts",
            )
            assert result.mode == "fts"
            assert tuple(hit.text for hit in result.hits) == ("Use one atomic composition boundary.",)
            assert result.hits[0].memory_ref == second.as_ref()

            unrelated = await context.artifacts.memory.search(
                "Should we use blue icons in the mobile navigation bar?",
                memories=(second,),
                mode="fts",
            )
            assert unrelated.hits == ()

    asyncio.run(scenario())


def test_sqlite_memory_backend_rebuilds_head_and_fts_projections() -> None:
    async def scenario() -> None:
        async with open_builtin_contexts(BuiltinConfig(database=SQLiteConfig())) as contexts:
            context = await contexts.get("project")
            memory = await context.artifacts.memory.remember(
                memory=None,
                entries=(
                    MemoryEntryInput(
                        kind="decision",
                        text="Rebuild search projections from authoritative revisions.",
                    ),
                ),
                mode="append",
            )
            assert memory is not None
            async with contexts.database.transaction() as connection:
                await connection.execute(MEMORY_ENTRY_HEADS_TABLE.delete())
                await connection.exec_driver_sql("DELETE FROM pc_memory_entry_fts")

            assert (
                await context.artifacts.memory.search(
                    "authoritative revisions",
                    memories=(memory,),
                    mode="fts",
                )
            ).hits == ()

            await context.artifacts.memory.rebuild_projections()

            rebuilt = await context.artifacts.memory.search(
                "authoritative revisions",
                memories=(memory,),
                mode="fts",
            )
            assert tuple(hit.text for hit in rebuilt.hits) == (
                "Rebuild search projections from authoritative revisions.",
            )

    asyncio.run(scenario())


def test_scope_bound_contexts_do_not_share_rows() -> None:
    async def scenario() -> None:
        async with open_builtin_contexts(BuiltinConfig(database=SQLiteConfig())) as contexts:
            left = await contexts.get("left")
            right = await contexts.get("right")

            await left.sources.capture(ContentCapture(source_id="same", content="left"))
            await right.sources.capture(ContentCapture(source_id="same", content="right"))

            left_sources = await left.sources.list()
            right_sources = await right.sources.list()
            assert all(isinstance(source, ContentSource) for source in (*left_sources, *right_sources))
            assert tuple(source.content for source in left_sources if isinstance(source, ContentSource)) == ("left",)
            assert tuple(source.content for source in right_sources if isinstance(source, ContentSource)) == ("right",)

    asyncio.run(scenario())
