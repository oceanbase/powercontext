from __future__ import annotations

import asyncio
import sqlite3
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

from sqlalchemy import select, text
from sqlalchemy.dialects import mysql
from sqlalchemy.ext.asyncio import AsyncConnection
from sqlalchemy.schema import CreateTable

from powercontext.builtin.artifacts.experience import Experience, ExperienceContent
from powercontext.builtin.artifacts.skill import Skill, SkillContent
from powercontext.builtin.persistence.experience_index import ensure_artifact_head_searchable_text
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.persistence.tables import ARTIFACT_HEADS_TABLE, BUILTIN_TABLES
from powercontext.builtin.runtime import BuiltinConfig, open_builtin_contexts
from powercontext.builtin.sources import ContentCapture


def _experience(keyword: str, lesson: str) -> ExperienceContent:
    return ExperienceContent(
        situation=f"A generated client contains the stale marker {keyword}.",
        action="Regenerate the client and inspect the resulting diff.",
        outcome="The checked-in client agrees with the public contract.",
        lesson=lesson,
    )


def _skill() -> SkillContent:
    return SkillContent(
        name="generated-client-check",
        description="Use after changing the public HTTP contract.",
        instructions="Regenerate the client and inspect the diff.",
        validation=("make contract-test passes",),
    )


def test_artifact_head_search_projection_schema_is_mysql_compilable() -> None:
    statement = str(CreateTable(ARTIFACT_HEADS_TABLE).compile(dialect=mysql.dialect()))

    assert "searchable_text MEDIUMTEXT" in statement
    assert "FOREIGN KEY(scope_id, family, artifact_id, revision)" in statement
    assert "pc_experience_heads" not in {table.name for table in BUILTIN_TABLES}


def test_sqlite_startup_upgrades_legacy_artifact_heads_without_searchable_text(tmp_path) -> None:
    database = tmp_path / "legacy.db"
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            CREATE TABLE pc_artifact_heads (
                scope_id VARCHAR(256) NOT NULL,
                family VARCHAR(128) NOT NULL,
                artifact_id VARCHAR(128) NOT NULL,
                revision INTEGER NOT NULL,
                PRIMARY KEY (scope_id, family, artifact_id)
            )
            """
        )

    async def scenario() -> None:
        config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{database}"))
        for _ in range(2):
            async with (
                open_builtin_contexts(config) as contexts,
                contexts.database.transaction() as connection,
            ):
                columns = tuple((await connection.exec_driver_sql("PRAGMA table_info('pc_artifact_heads')")).mappings())
                assert tuple(column["name"] for column in columns).count("searchable_text") == 1

    asyncio.run(scenario())


def test_oceanbase_startup_upgrades_legacy_artifact_heads_with_mediumtext() -> None:
    connection = SimpleNamespace(
        dialect=SimpleNamespace(name="mysql"),
        scalar=AsyncMock(return_value=0),
        exec_driver_sql=AsyncMock(),
    )

    asyncio.run(ensure_artifact_head_searchable_text(cast(AsyncConnection, connection)))

    connection.exec_driver_sql.assert_awaited_once_with(
        "ALTER TABLE pc_artifact_heads ADD COLUMN searchable_text MEDIUMTEXT NULL"
    )


def test_sqlite_experience_fts_tracks_only_approved_current_heads_and_rebuilds() -> None:
    async def scenario() -> None:
        async with open_builtin_contexts(BuiltinConfig(database=SQLiteConfig())) as contexts:
            context = await contexts.get("project")
            async with contexts.database.transaction() as connection:
                experience_tables = set(
                    (
                        await connection.execute(
                            text(
                                "SELECT name FROM sqlite_master "
                                "WHERE name IN ('pc_experience_heads', 'pc_experience_fts_index', 'pc_experience_fts')"
                            )
                        )
                    ).scalars()
                )
            assert experience_tables == {"pc_experience_fts"}

            first_source, _ = await context.sources.capture(
                ContentCapture(source_id="task-1", content="The first client repair passed.")
            )
            first_source_ref = context.sources.catalog.as_ref(first_source)
            review = contexts.review("project")
            candidate = await review.propose_experience(
                _experience("hamsterlegacy", "Run client generation before contract validation."),
                sources=(first_source_ref,),
                artifacts=(),
                target=None,
                reason=None,
            )

            assert await contexts.search_experience("project", "hamsterlegacy", 8) == ()

            approved = await review.approve(candidate.candidate_id, candidate.version)
            assert approved.result_artifact is not None
            first_hits = await contexts.search_experience("project", "hamsterlegacy", 8)
            assert tuple(hit.artifact_ref for hit in first_hits) == (approved.result_artifact,)
            assert await contexts.search_experience("other-project", "hamsterlegacy", 8) == ()
            assert await contexts.search_experience("project", "situation outcome", 8) == ()

            second_source, _ = await context.sources.capture(
                ContentCapture(source_id="task-2", content="The corrected client repair passed.")
            )
            replacement = await review.propose_experience(
                _experience("falconcurrent", "Inspect generated changes before contract validation."),
                sources=(context.sources.catalog.as_ref(second_source),),
                artifacts=(approved.result_artifact,),
                target=approved.result_artifact,
                reason="The newer task evidence supersedes the stale marker.",
            )
            replaced = await review.approve(replacement.candidate_id, replacement.version)
            assert replaced.result_artifact is not None
            assert replaced.result_artifact.revision == 2
            assert await contexts.search_experience("project", "hamsterlegacy", 8) == ()
            current_hits = await contexts.search_experience("project", "falconcurrent", 8)
            assert tuple(hit.artifact_ref for hit in current_hits) == (replaced.result_artifact,)

            skill_candidate = await review.propose_skill(
                _skill(),
                sources=(context.sources.catalog.as_ref(second_source),),
                artifacts=(),
                target=None,
                reason=None,
            )
            skill_approval = await review.approve(skill_candidate.candidate_id, skill_candidate.version)
            assert skill_approval.result_artifact is not None

            async with contexts.database.transaction() as connection:
                experience_searchable_text = await connection.scalar(
                    select(ARTIFACT_HEADS_TABLE.c.searchable_text).where(
                        ARTIFACT_HEADS_TABLE.c.scope_id == "project",
                        ARTIFACT_HEADS_TABLE.c.family == Experience.family,
                    )
                )
                skill_searchable_text = await connection.scalar(
                    select(ARTIFACT_HEADS_TABLE.c.searchable_text).where(
                        ARTIFACT_HEADS_TABLE.c.scope_id == "project",
                        ARTIFACT_HEADS_TABLE.c.family == Skill.family,
                    )
                )
                assert experience_searchable_text is not None
                assert "falconcurrent" in experience_searchable_text
                assert skill_searchable_text is None

                await connection.execute(
                    ARTIFACT_HEADS_TABLE
                    .update()
                    .where(ARTIFACT_HEADS_TABLE.c.family == Experience.family)
                    .values(searchable_text=None)
                )
                await connection.exec_driver_sql("DELETE FROM pc_experience_fts")
            assert await contexts.search_experience("project", "falconcurrent", 8) == ()

            async with contexts.database.transaction() as connection:
                await contexts.experience_index.initialize(connection)
            rebuilt_hits = await contexts.search_experience("project", "falconcurrent", 8)
            assert tuple(hit.artifact_ref for hit in rebuilt_hits) == (replaced.result_artifact,)

    asyncio.run(scenario())
