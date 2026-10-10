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

from powercontext.artifacts import ArtifactLineage
from powercontext.builtin.artifacts.atomic_memory import AtomicMemoryContent
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.persistence.tables import (
    MEMORY_ENTRY_HEADS_TABLE,
    MEMORY_ENTRY_VERSIONS_TABLE,
)
from powercontext.builtin.records import ArtifactWrite
from powercontext.builtin.runtime import BuiltinConfig, open_builtin_contexts
from powercontext.builtin.runtime.atomic_memory_rebuild import rebuild_atomic_memory_projection
from powercontext.builtin.sources import ContentCapture, ContentSource
from powercontext.sources import SourceRef

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

    assert "scope_id VARCHAR(256) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL" in versions
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
            application = contexts.atomic_memory
            memory = application.for_scope("project")
            async with contexts.database.transaction() as connection:
                plan = await application.service.inspect_change(
                    connection,
                    "project",
                    "decision",
                    AtomicMemoryContent(kind="decision", text="Use one atomic composition boundary."),
                    None,
                    lineage=ArtifactLineage(sources=(SourceRef(source_type="content", source_id=source.name),)),
                )
            prepared = await application.service.prepare_change(plan)
            async with contexts.database.transaction() as connection:
                first = (await application.service.commit(connection, prepared, None)).primary
            second = await contexts.records.replace_artifact(
                "project",
                "atomic-memory",
                first.ref.artifact_id,
                '"revision:1"',
                ArtifactWrite(
                    content={"kind": "decision", "text": "Use one atomic composition boundary for providers."}
                ),
            )
            constraint = await contexts.records.create_artifact(
                "project",
                "atomic-memory",
                ArtifactWrite(content={"kind": "constraint", "text": "Do not split the provider transaction."}),
            )
            assert second.revision == 2
            history = await contexts.records.list_artifact_revisions(
                "project",
                "atomic-memory",
                first.ref.artifact_id,
                limit=10,
                cursor=None,
            )
            assert tuple(item.revision for item in history.items) == (2, 1)
            assert (await memory.get(first.ref.artifact_id, revision=1)).artifact == first.artifact
            assert first.artifact.lineage.sources == (SourceRef(source_type="content", source_id=source.name),)
            assert {item.artifact.content.text for item in (await memory.list()).items} == {
                second.content["text"],
                "Do not split the provider transaction.",
            }
            assert constraint.artifact_id != first.ref.artifact_id
            result = await memory.search("atomic composition", mode="text")
            assert result.mode == "text"
            assert tuple(hit.text for hit in result.hits) == (second.content["text"],)
            assert result.hits[0].hit.artifact_ref == (await memory.get(first.ref.artifact_id)).ref
            unrelated = await memory.search("Should we use blue icons in the mobile navigation bar?", mode="text")
            assert unrelated.hits == ()

    asyncio.run(scenario())


def test_sqlite_memory_backend_rebuilds_head_and_fts_projections() -> None:
    async def scenario() -> None:
        async with open_builtin_contexts(BuiltinConfig(database=SQLiteConfig())) as contexts:
            await contexts.get("project")
            created = await contexts.records.create_artifact(
                "project",
                "atomic-memory",
                ArtifactWrite(
                    content={"kind": "decision", "text": "Rebuild search projections from authoritative revisions."}
                ),
            )
            memory = contexts.atomic_memory.for_scope("project")
            authoritative = await memory.get(created.artifact_id)
            async with contexts.database.transaction() as connection:
                await connection.execute(contexts.atomic_memory.index.table.delete())
            assert (await memory.search("authoritative revisions", mode="text")).hits == ()
            report = await rebuild_atomic_memory_projection(
                contexts.database,
                contexts.atomic_memory.index,
                maintenance_confirmed=True,
            )
            assert report.ready
            assert await memory.get(created.artifact_id) == authoritative
            rebuilt = await memory.search("authoritative revisions", mode="text")
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
