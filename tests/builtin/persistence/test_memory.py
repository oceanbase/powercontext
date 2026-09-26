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

from sqlalchemy import BigInteger, DateTime, Integer, String, delete, event, select
from sqlalchemy.dialects import mysql
from sqlalchemy.schema import CreateTable, ForeignKeyConstraint, PrimaryKeyConstraint, UniqueConstraint

from powercontext.builtin.artifacts.memory import MemoryEntryInput
from powercontext.builtin.persistence.database import SELECTION_BATCH_SIZE
from powercontext.builtin.persistence.memory import RelationalMemoryBackend
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.persistence.tables import (
    MEMORY_ENTRY_EVIDENCE_TABLE,
    MEMORY_ENTRY_HEADS_TABLE,
    MEMORY_ENTRY_LIFECYCLE_PROJECTIONS_TABLE,
    MEMORY_ENTRY_VERSIONS_TABLE,
)
from powercontext.builtin.runtime import BuiltinConfig, open_builtin_contexts
from powercontext.builtin.runtime.relational import RelationalContexts
from powercontext.builtin.sources import ContentCapture, ContentSource

_INNODB_MAX_INDEX_BYTES = 3072
_EVIDENCE_BUDGET_ENTRIES = SELECTION_BATCH_SIZE


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
    evidence = str(CreateTable(MEMORY_ENTRY_EVIDENCE_TABLE).compile(dialect=dialect))
    heads = str(CreateTable(MEMORY_ENTRY_HEADS_TABLE).compile(dialect=dialect))
    lifecycle = str(CreateTable(MEMORY_ENTRY_LIFECYCLE_PROJECTIONS_TABLE).compile(dialect=dialect))

    assert "scope_id VARCHAR(256) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL" in versions
    assert "text MEDIUMTEXT NOT NULL" in versions
    assert "source_refs MEDIUMBLOB NOT NULL" in versions
    assert "artifact_refs MEDIUMBLOB NOT NULL" in versions
    assert "declaration_version VARCHAR(128) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL" in evidence
    assert "searchable_text MEDIUMTEXT NOT NULL" in heads
    assert "quality_policy VARCHAR(16) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL" in lifecycle

    budgets = [
        _key_budget(constraint)
        for table in (
            MEMORY_ENTRY_VERSIONS_TABLE,
            MEMORY_ENTRY_EVIDENCE_TABLE,
            MEMORY_ENTRY_HEADS_TABLE,
            MEMORY_ENTRY_LIFECYCLE_PROJECTIONS_TABLE,
        )
        for constraint in table.constraints
        if isinstance(constraint, PrimaryKeyConstraint | UniqueConstraint | ForeignKeyConstraint)
    ]
    assert budgets
    assert max(budgets) <= 2568
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


def test_memory_evidence_snapshots_and_neutral_lifecycle_projection_are_rebuildable() -> None:
    async def scenario() -> None:
        async with open_builtin_contexts(BuiltinConfig(database=SQLiteConfig())) as contexts:
            context = await contexts.get("project")
            source, _ = await context.sources.capture(
                ContentCapture(source_id="turn-1", content="A durable source declaration must be snapshotted.")
            )
            memory = await context.artifacts.memory.remember(
                memory=None,
                sources=(source,),
                entries=(
                    MemoryEntryInput(
                        kind="decision",
                        text="Keep provenance metadata outside the Memory body.",
                        sources=(source,),
                    ),
                ),
                mode="append",
            )
            assert memory is not None
            (entry,) = await context.artifacts.memory.entries(memory)
            assert entry.source_evidence[0].source.source_id == "turn-1"
            assert entry.source_evidence[0].declaration.authority == "untrusted"
            assert entry.source_evidence[0].declaration.verification == "unknown"

            backend = RelationalMemoryBackend(
                database=contexts.database,
                scope_id="project",
                artifacts=contexts.repositories.artifacts,
                index=contexts.index,
            )
            current_lifecycle = (await backend.lifecycle_projections(memory.as_ref()))[0]
            assert current_lifecycle.validity == "current"
            assert current_lifecycle.context_annotation.time_state == "unknown"
            assert current_lifecycle.context_annotation.quality_policy == "neutral"

            inactive = await context.artifacts.memory.forget(memory, entries=(entry,), reason="superseded elsewhere")
            assert inactive is not None
            lifecycle = await backend.lifecycle_projections(inactive.as_ref())
            assert lifecycle[0].validity == "inactive"
            assert lifecycle[0].source_evidence == entry.source_evidence

            async with contexts.database.transaction() as connection:
                await connection.execute(delete(MEMORY_ENTRY_LIFECYCLE_PROJECTIONS_TABLE))

            await context.artifacts.memory.rebuild_projections()

            rebuilt = await backend.lifecycle_projections(inactive.as_ref())
            assert rebuilt == lifecycle

    asyncio.run(scenario())


async def _lifecycle_rows(contexts: RelationalContexts) -> dict[str, tuple[int, str]]:
    async with contexts.database.transaction() as connection:
        rows = (
            await connection.execute(
                select(
                    MEMORY_ENTRY_LIFECYCLE_PROJECTIONS_TABLE.c.entry_id,
                    MEMORY_ENTRY_LIFECYCLE_PROJECTIONS_TABLE.c.head_revision,
                    MEMORY_ENTRY_LIFECYCLE_PROJECTIONS_TABLE.c.validity,
                )
            )
        ).all()
    return {str(entry_id): (int(revision), str(validity)) for entry_id, revision, validity in rows}


def test_lifecycle_projection_rows_are_rewritten_only_for_changed_entries() -> None:
    async def scenario() -> None:
        async with open_builtin_contexts(BuiltinConfig(database=SQLiteConfig())) as contexts:
            context = await contexts.get("project")
            source, _ = await context.sources.capture(
                ContentCapture(source_id="turn-1", content="Lifecycle rows must follow their own entry.")
            )
            opened = await context.artifacts.memory.remember(
                memory=None,
                sources=(source,),
                entries=(
                    MemoryEntryInput(kind="decision", text="Keep the first entry.", sources=(source,)),
                    MemoryEntryInput(kind="constraint", text="Keep the second entry.", sources=(source,)),
                ),
                mode="append",
            )
            assert opened is not None
            opened_rows = await _lifecycle_rows(contexts)
            assert len(opened_rows) == 2
            assert {revision for revision, _ in opened_rows.values()} == {opened.revision}

            appended = await context.artifacts.memory.remember(
                memory=opened,
                sources=(source,),
                entries=(MemoryEntryInput(kind="agent-note", text="Append exactly one entry.", sources=(source,)),),
                mode="append",
            )
            assert appended is not None
            assert appended.revision == opened.revision + 1

            backend = RelationalMemoryBackend(
                database=contexts.database,
                scope_id="project",
                artifacts=contexts.repositories.artifacts,
                index=contexts.index,
            )
            appended_rows = await _lifecycle_rows(contexts)

            # Appending one entry rewrites that entry alone: every row that was
            # already current keeps the revision that last wrote it.
            assert len(appended_rows) == 3
            assert {
                entry_id: value for entry_id, value in appended_rows.items() if entry_id in opened_rows
            } == opened_rows
            assert {validity for _, validity in appended_rows.values()} == {"current"}

            forgotten = (await context.artifacts.memory.entries(opened))[0]
            inactive = await context.artifacts.memory.forget(
                appended,
                entries=(forgotten,),
                reason="superseded elsewhere",
            )
            assert inactive is not None
            inactive_rows = await _lifecycle_rows(contexts)

            assert inactive_rows[forgotten.entry_id] == (inactive.revision, "inactive")
            assert {entry_id: value for entry_id, value in inactive_rows.items() if entry_id != forgotten.entry_id} == {
                entry_id: value for entry_id, value in appended_rows.items() if entry_id != forgotten.entry_id
            }

            incremental = await backend.lifecycle_projections(inactive.as_ref())
            assert {item.validity for item in incremental if item.entry_id == forgotten.entry_id} == {"inactive"}
            assert {item.validity for item in incremental if item.entry_id != forgotten.entry_id} == {"current"}

            async with contexts.database.transaction() as connection:
                await connection.execute(delete(MEMORY_ENTRY_LIFECYCLE_PROJECTIONS_TABLE))
            await context.artifacts.memory.rebuild_projections()

            assert await backend.lifecycle_projections(inactive.as_ref()) == incremental

    asyncio.run(scenario())


def test_manifest_evidence_lookups_stay_within_the_shared_bind_budget() -> None:
    """A manifest read must batch evidence lookups instead of binding every entry.

    The ceiling that matters depends on the SQLite build (32,766 on most system
    builds, 250,000 in CPython's bundled Windows library), so this pins the bind
    budget the backend controls rather than one build's variable limit.
    """

    async def scenario() -> None:
        async with open_builtin_contexts(BuiltinConfig(database=SQLiteConfig())) as contexts:
            context = await contexts.get("project")
            bound: list[int] = []

            def record_statement(
                _connection: object,
                _cursor: object,
                statement: str,
                parameters: object,
                *_rest: object,
            ) -> None:
                if not statement.lstrip().upper().startswith("SELECT"):
                    return
                if "pc_memory_entry_evidence" in statement:
                    assert isinstance(parameters, tuple | list)
                    bound.append(len(parameters))

            engine = contexts.database.engine.sync_engine
            event.listen(engine, "before_cursor_execute", record_statement)
            try:
                opened = await context.artifacts.memory.remember(
                    memory=None,
                    entries=tuple(
                        MemoryEntryInput(kind="agent-note", text=f"Bounded entry {index}.", sources=())
                        for index in range(_EVIDENCE_BUDGET_ENTRIES)
                    ),
                    mode="append",
                )
                assert opened is not None
                appended = await context.artifacts.memory.remember(
                    memory=opened,
                    entries=(MemoryEntryInput(kind="agent-note", text="Appended within budget.", sources=()),),
                    mode="append",
                )
                assert appended is not None
                assert len(await context.artifacts.memory.entries(appended)) == _EVIDENCE_BUDGET_ENTRIES + 1
            finally:
                event.remove(engine, "before_cursor_execute", record_statement)

            assert bound
            assert max(bound) <= SELECTION_BATCH_SIZE

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
