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

"""Initialize missing search tables in existing Artifact storage, preserving exact historical content."""

import asyncio
import sqlite3

from sqlalchemy import insert

from powercontext.artifacts import Artifact
from powercontext.builtin.persistence.artifact_indexes import ArtifactIndexBuilder
from powercontext.builtin.persistence.artifacts import ArtifactRepository, RepositoryArtifactDraft
from powercontext.builtin.persistence.custom_artifacts import StoredArtifactContent
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.persistence.tables import (
    ARTIFACT_FAMILY_SCHEMAS_TABLE,
    ARTIFACT_SEARCH_TABLES,
    BUILTIN_TABLES,
)
from tests.builtin.persistence.test_artifact_indexes import service


def test_existing_database_adds_search_tables_and_rebuilds_legacy_declarations(tmp_path):
    path = tmp_path / "existing.db"
    config = SQLiteConfig(url=f"sqlite+aiosqlite:///{path}")

    async def legacy():
        tables = tuple(table for table in BUILTIN_TABLES if table not in ARTIFACT_SEARCH_TABLES)
        async with SQLiteProfile.open(config, tables=tables) as profile:
            artifact_type = type("Legacy", (Artifact[StoredArtifactContent],), {"family": "reports"})
            repository = ArtifactRepository((artifact_type,))
            async with profile.database.transaction() as connection:
                await connection.execute(
                    insert(ARTIFACT_FAMILY_SCHEMAS_TABLE).values(
                        family="reports",
                        schema_version="1",
                        declaration={"family": "reports", "schema_version": "1", "search_fields": ["body"]},
                    )
                )
                first = await repository.create(
                    connection,
                    "scope",
                    "a",
                    RepositoryArtifactDraft(
                        family="reports",
                        content=StoredArtifactContent(schema_version="1", content={"body": "数据库 TIMEOUT"}),
                    ),
                )
                await repository.revise(
                    connection,
                    "scope",
                    first,
                    RepositoryArtifactDraft(
                        family="reports",
                        content=StoredArtifactContent(schema_version="1", content={"body": "连接恢复 RECOVERED"}),
                    ),
                )

    asyncio.run(legacy())
    with sqlite3.connect(path) as connection:
        before = connection.execute("SELECT * FROM pc_artifacts ORDER BY revision").fetchall()
        assert not connection.execute("SELECT 1 FROM sqlite_master WHERE name='pc_artifact_search_states'").fetchall()

    async def rebuilt():
        async with SQLiteProfile.open(config, tables=BUILTIN_TABLES) as profile:
            builder = ArtifactIndexBuilder(profile.database)
            await builder.refresh("scope", "reports")
            assert await builder.build("scope", "reports")
            search = service(profile.database, "reports")
            assert not (await search.search("scope", "reports", {"query": "TIMEOUT"})).matches
            result = await search.search("scope", "reports", {"query": "RECOVERED"})
            assert result.artifacts[0].revision == 2
            assert result.artifacts[0].content.model_dump() == {"body": "连接恢复 RECOVERED"}

    asyncio.run(rebuilt())
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT * FROM pc_artifacts ORDER BY revision").fetchall() == before
        assert not connection.execute("SELECT 1 FROM sqlite_master WHERE name='pc_artifact_search_schema'").fetchall()
        connection.execute("DROP TRIGGER pc_artifact_search_fts_update")
    # Missing full-text support is recreated; repeated initialization preserves exact content.
    asyncio.run(rebuilt())


def test_intermediate_search_schema_is_rejected_without_deleting_existing_rows(tmp_path):
    import pytest

    path = tmp_path / "incompatible.db"

    async def initialize_database():
        tables = tuple(table for table in BUILTIN_TABLES if table not in ARTIFACT_SEARCH_TABLES)
        async with SQLiteProfile.open(SQLiteConfig(url=f"sqlite+aiosqlite:///{path}"), tables=tables):
            pass

    asyncio.run(initialize_database())
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE pc_artifact_search_states (scope_id TEXT, family TEXT, release_id INTEGER, serving_manifest TEXT, desired_manifest TEXT, change_seq INTEGER, PRIMARY KEY(scope_id, family))"
        )
        connection.execute("INSERT INTO pc_artifact_search_states VALUES ('scope', 'reports', 3, '{}', '{}', 9)")
        before = connection.execute("SELECT * FROM pc_artifact_search_states").fetchall()

    async def open_old_database():
        with pytest.raises(ValueError, match=r"incompatible.*migration"):
            async with SQLiteProfile.open(SQLiteConfig(url=f"sqlite+aiosqlite:///{path}"), tables=BUILTIN_TABLES):
                pytest.fail("Old search layout must not be treated as upgraded")

    asyncio.run(open_old_database())
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT * FROM pc_artifact_search_states").fetchall() == before
