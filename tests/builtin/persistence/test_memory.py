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
from sqlalchemy import BigInteger, DateTime, Integer, String, select
from sqlalchemy.dialects import mysql
from sqlalchemy.schema import CreateTable, ForeignKeyConstraint, PrimaryKeyConstraint, UniqueConstraint

from powercontext.builtin.artifacts.memory import MemoryDirectoryQuery, MemoryEntryInput
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.persistence.tables import (
    MEMORY_ENTRY_DIRECTORY_TABLE,
    MEMORY_ENTRY_HEADS_TABLE,
    MEMORY_ENTRY_VERSIONS_TABLE,
    MEMORY_QUERY_INDEX_SCHEMA_TABLE,
    MEMORY_TAG_GENERATIONS_TABLE,
)
from powercontext.builtin.records import InvalidCursorError
from powercontext.builtin.runtime import BuiltinConfig, open_builtin_contexts
from powercontext.builtin.sources import ContentCapture, ContentSource
from powercontext.builtin.tags import MemoryEntryTagTarget, TagFilter

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
    directory = str(CreateTable(MEMORY_ENTRY_DIRECTORY_TABLE).compile(dialect=dialect))
    heads = str(CreateTable(MEMORY_ENTRY_HEADS_TABLE).compile(dialect=dialect))
    generations = str(CreateTable(MEMORY_TAG_GENERATIONS_TABLE).compile(dialect=dialect))
    migration = str(CreateTable(MEMORY_QUERY_INDEX_SCHEMA_TABLE).compile(dialect=dialect))

    assert "scope_id VARCHAR(256) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL" in versions
    assert "text MEDIUMTEXT NOT NULL" in versions
    assert "source_refs MEDIUMBLOB NOT NULL" in versions
    assert "artifact_refs MEDIUMBLOB NOT NULL" in versions
    assert "valid_from_revision INTEGER NOT NULL" in directory
    assert "valid_to_revision INTEGER" in directory
    assert "searchable_text MEDIUMTEXT NOT NULL" in heads
    assert "generation BIGINT NOT NULL" in generations
    assert "phase VARCHAR(16)" in migration

    budgets = [
        _key_budget(constraint)
        for table in (
            MEMORY_ENTRY_VERSIONS_TABLE,
            MEMORY_ENTRY_DIRECTORY_TABLE,
            MEMORY_ENTRY_HEADS_TABLE,
            MEMORY_TAG_GENERATIONS_TABLE,
            MEMORY_QUERY_INDEX_SCHEMA_TABLE,
        )
        for constraint in table.constraints
        if isinstance(constraint, PrimaryKeyConstraint | UniqueConstraint | ForeignKeyConstraint)
    ]
    assert budgets
    assert max(budgets) == 2560
    assert all(budget < _INNODB_MAX_INDEX_BYTES for budget in budgets)


def test_sqlite_memory_directory_records_only_changed_revision_intervals() -> None:
    async def scenario() -> None:
        async with open_builtin_contexts(BuiltinConfig(database=SQLiteConfig())) as contexts:
            context = await contexts.get("project")
            service = context.artifacts.memory
            first = await service.remember(
                memory=None,
                entries=(
                    MemoryEntryInput(kind="decision", text="Keep the stable entry unchanged."),
                    MemoryEntryInput(kind="constraint", text="Revise and then retire this entry."),
                ),
                mode="append",
            )
            assert first is not None
            stable, changing = await service.entries(first)
            second = await service.remember(
                memory=first,
                entries=(
                    MemoryEntryInput(
                        entry=changing,
                        kind=changing.kind,
                        text="This entry now has a second immutable version.",
                    ),
                ),
                mode="append",
            )
            assert second is not None
            revised = next(entry for entry in await service.entries(second) if entry.entry_id == changing.entry_id)
            third = await service.forget(second, entries=(revised,), reason="No longer current.")

            async with contexts.database.transaction() as connection:
                rows = (
                    (
                        await connection.execute(
                            select(MEMORY_ENTRY_DIRECTORY_TABLE).order_by(
                                MEMORY_ENTRY_DIRECTORY_TABLE.c.entry_id,
                                MEMORY_ENTRY_DIRECTORY_TABLE.c.valid_from_revision,
                            )
                        )
                    )
                    .mappings()
                    .all()
                )
                generation = await connection.scalar(select(MEMORY_TAG_GENERATIONS_TABLE.c.generation))

            by_entry = {
                entry_id: [row for row in rows if row["entry_id"] == entry_id]
                for entry_id in {stable.entry_id, changing.entry_id}
            }
            assert [
                (row["entry_version_id"], row["state"], row["valid_from_revision"], row["valid_to_revision"])
                for row in by_entry[stable.entry_id]
            ] == [(stable.entry_version_id, "active", 1, None)]
            assert [
                (row["entry_version_id"], row["state"], row["valid_from_revision"], row["valid_to_revision"])
                for row in by_entry[changing.entry_id]
            ] == [
                (changing.entry_version_id, "active", 1, 2),
                (revised.entry_version_id, "active", 2, 3),
                (revised.entry_version_id, "inactive", 3, None),
            ]
            assert third.revision == 3
            assert generation == 0

    asyncio.run(scenario())


def test_sqlite_memory_directory_query_pins_revision_and_invalidates_changed_tags(monkeypatch) -> None:
    async def scenario() -> None:
        async with open_builtin_contexts(BuiltinConfig(database=SQLiteConfig())) as contexts:
            context = await contexts.get("project")
            service = context.artifacts.memory
            first = await service.remember(
                memory=None,
                entries=tuple(MemoryEntryInput(kind="fact", text=f"Directory body {index}") for index in range(3)),
                mode="append",
            )
            assert first is not None
            original_ids = sorted(item.entry_id for item in await service.entries(first))

            import powercontext.builtin.persistence.memory as persistence_memory

            decoded = 0
            original_decode = persistence_memory._decode_entry

            def counting_decode(row):
                nonlocal decoded
                decoded += 1
                return original_decode(row)

            monkeypatch.setattr(persistence_memory, "_decode_entry", counting_decode)
            page_one = await service.query_directory(
                first.artifact_id,
                MemoryDirectoryQuery(limit=1),
            )
            assert page_one.memory_ref == first.as_ref()
            assert [item.citation.entry_id for item in page_one.items] == original_ids[:1]
            assert set(page_one.items[0].model_dump()) == {"citation", "version", "kind", "state"}
            assert page_one.next_cursor is not None
            assert decoded == 0

            second = await service.remember(
                memory=first,
                entries=(MemoryEntryInput(kind="fact", text="Visible only to a new traversal."),),
                mode="append",
            )
            assert second is not None
            remaining = []
            cursor = page_one.next_cursor
            while cursor is not None:
                page = await service.query_directory(
                    first.artifact_id,
                    MemoryDirectoryQuery(limit=1, cursor=cursor),
                )
                assert page.memory_ref == first.as_ref()
                remaining.extend(item.citation.entry_id for item in page.items)
                cursor = page.next_cursor
            assert remaining == original_ids[1:]
            assert decoded == 0
            with pytest.raises(InvalidCursorError):
                await service.query_directory(
                    first.artifact_id,
                    MemoryDirectoryQuery(limit=2, cursor=page_one.next_cursor),
                )

            tagged_ids = sorted(original_ids[:2])
            for entry_id in tagged_ids:
                target = MemoryEntryTagTarget(artifact_id=first.artifact_id, entry_id=entry_id)
                empty = await contexts.records.get_tags("project", target)
                await contexts.records.replace_tags(
                    "project",
                    target,
                    ("paged",),
                    expected_etag=empty.etag,
                )
            filtered = await service.query_directory(
                first.artifact_id,
                MemoryDirectoryQuery(tag_filter=TagFilter(tags=("PAGED",)), limit=1),
            )
            assert [item.citation.entry_id for item in filtered.items] == tagged_ids[:1]
            assert filtered.next_cursor is not None

            unchanged_target = MemoryEntryTagTarget(artifact_id=first.artifact_id, entry_id=tagged_ids[0])
            unchanged = await contexts.records.get_tags("project", unchanged_target)
            await contexts.records.replace_tags(
                "project",
                unchanged_target,
                unchanged.tags,
                expected_etag=unchanged.etag,
            )
            continued = await service.query_directory(
                first.artifact_id,
                MemoryDirectoryQuery(
                    tag_filter=TagFilter(tags=("paged",)),
                    limit=1,
                    cursor=filtered.next_cursor,
                ),
            )
            assert [item.citation.entry_id for item in continued.items] == tagged_ids[1:]

            restarted = await service.query_directory(
                first.artifact_id,
                MemoryDirectoryQuery(tag_filter=TagFilter(tags=("paged",)), limit=1),
            )
            changed_target = MemoryEntryTagTarget(artifact_id=first.artifact_id, entry_id=tagged_ids[1])
            changed = await contexts.records.get_tags("project", changed_target)
            await contexts.records.replace_tags("project", changed_target, (), expected_etag=changed.etag)
            with pytest.raises(InvalidCursorError, match="tag_state_changed"):
                await service.query_directory(
                    first.artifact_id,
                    MemoryDirectoryQuery(
                        tag_filter=TagFilter(tags=("paged",)),
                        limit=1,
                        cursor=restarted.next_cursor,
                    ),
                )

    asyncio.run(scenario())


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
