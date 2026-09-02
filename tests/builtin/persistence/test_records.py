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
import hashlib
import sqlite3
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
import rfc8785
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.builtin.persistence.records import RelationalRecordService
from powercontext.builtin.persistence.schema import ensure_record_columns
from powercontext.builtin.persistence.sources import SourceRepository
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.persistence.statistics import StatisticsRepository
from powercontext.builtin.persistence.tables import (
    ARTIFACT_HEADS_TABLE,
    ARTIFACTS_TABLE,
    SHARED_TABLES,
    SOURCES_TABLE,
)
from powercontext.builtin.records import (
    ArtifactRevisionPreconditionError,
    ArtifactWrite,
    BaseOperationNotSupportedError,
    BaseValueConflictError,
    BaseValueNotFoundError,
    CursorExpiredError,
    InvalidBaseAccessRequestError,
    InvalidCursorError,
)
from powercontext.builtin.sources import CONTENT_SOURCE_ADAPTER
from powercontext.sources import SourceRef


def test_source_records_support_create_get_list_search_and_bound_cursors() -> None:
    async def scenario() -> None:
        async with SQLiteProfile.open(SQLiteConfig(), tables=SHARED_TABLES) as profile:
            generated_ids = iter(("source-1", "source-2"))
            records = RelationalRecordService(
                profile.database,
                SourceRepository((CONTENT_SOURCE_ADAPTER,)),
                id_factory=lambda _kind: next(generated_ids),
                cursor_secret=b"source-record-cursor-test",
            )
            first = await records.create_source(
                "scope-a",
                "content",
                "Keep the OpenAPI contract authoritative.",
                {"channel": "test"},
            )
            repeated = await records.capture_source(
                "scope-a",
                "content",
                "source-1",
                "Keep the OpenAPI contract authoritative.",
                {"channel": "test"},
            )
            second = await records.create_source(
                "scope-a",
                "content",
                "Preserve immutable artifact revisions.",
                {},
            )

            assert repeated == first
            assert first.position == 1
            assert second.position == 2
            assert first.content_digest == f"sha256:{hashlib.sha256(rfc8785.dumps(first.content)).hexdigest()}"
            assert (await records.get_source("scope-a", "content", "source-1")) == first

            first_page = await records.query_sources(
                "scope-a",
                "content",
                query=None,
                mode=None,
                limit=1,
                cursor=None,
            )
            assert [item.source_id for item in first_page.items] == ["source-1"]
            assert "content" not in type(first_page.items[0]).model_fields
            assert first_page.next_cursor is not None
            second_page = await records.query_sources(
                "scope-a",
                "content",
                query=None,
                mode=None,
                limit=1,
                cursor=first_page.next_cursor,
            )
            assert [item.source_id for item in second_page.items] == ["source-2"]

            assert first_page.next_cursor is not None
            payload, signature = first_page.next_cursor.split(".")
            tampered_signature = ("A" if signature[0] != "A" else "B") + signature[1:]
            with pytest.raises(InvalidCursorError):
                await records.query_sources(
                    "scope-a",
                    "content",
                    query=None,
                    mode=None,
                    limit=1,
                    cursor=f"{payload}.{tampered_signature}",
                )

            other_caller = RelationalRecordService(
                profile.database,
                SourceRepository((CONTENT_SOURCE_ADAPTER,)),
                cursor_secret=b"other-caller-cursor-test",
            )
            with pytest.raises(InvalidCursorError):
                await other_caller.query_sources(
                    "scope-a",
                    "content",
                    query=None,
                    mode=None,
                    limit=1,
                    cursor=first_page.next_cursor,
                )

            found = await records.query_sources(
                "scope-a",
                "content",
                query="OpenAPI authoritative",
                mode="auto",
                limit=10,
                cursor=None,
            )
            assert [item.source_id for item in found.items] == ["source-1"]
            assert found.mode == "keyword"
            assert found.items[0].score == 1.0

            with pytest.raises(BaseValueConflictError):
                await records.capture_source("scope-a", "content", "source-1", "different", {})
            with pytest.raises(InvalidBaseAccessRequestError):
                await records.query_sources(
                    "scope-a",
                    "content",
                    query="OpenAPI",
                    mode="auto",
                    limit=1,
                    cursor=first_page.next_cursor,
                )
            with pytest.raises(InvalidBaseAccessRequestError):
                await records.query_sources(
                    "scope-a",
                    "content",
                    query=None,
                    mode="auto",
                    limit=10,
                    cursor=None,
                )
            with pytest.raises(InvalidBaseAccessRequestError):
                await records.query_sources(
                    "scope-a",
                    "content",
                    query=None,
                    mode=None,
                    limit=101,
                    cursor=None,
                )
            with pytest.raises(BaseOperationNotSupportedError):
                await records.get_source("scope-a", "git", "source-1")

    asyncio.run(scenario())


def test_record_cursor_expires_after_its_bounded_lifetime() -> None:
    async def scenario() -> None:
        current_time = [datetime(2026, 9, 3, tzinfo=UTC)]
        async with SQLiteProfile.open(SQLiteConfig(), tables=SHARED_TABLES) as profile:
            generated_ids = iter(("source-1", "source-2"))
            records = RelationalRecordService(
                profile.database,
                SourceRepository((CONTENT_SOURCE_ADAPTER,)),
                clock=lambda: current_time[0],
                id_factory=lambda _kind: next(generated_ids),
                cursor_secret=b"expiring-cursor-test",
                cursor_ttl_seconds=60,
            )
            await records.create_source("scope-a", "content", "first", {})
            await records.create_source("scope-a", "content", "second", {})
            page = await records.query_sources(
                "scope-a",
                "content",
                query=None,
                mode=None,
                limit=1,
                cursor=None,
            )
            assert page.next_cursor is not None

            current_time[0] += timedelta(seconds=60)
            with pytest.raises(CursorExpiredError):
                await records.query_sources(
                    "scope-a",
                    "content",
                    query=None,
                    mode=None,
                    limit=1,
                    cursor=page.next_cursor,
                )

    asyncio.run(scenario())


def test_record_tables_replace_sidecars_and_upgrade_legacy_sqlite_columns(tmp_path) -> None:
    table_names = {table.name for table in SHARED_TABLES}
    assert {
        "pc_source_records",
        "pc_artifact_revision_records",
        "pc_artifact_tombstones",
    }.isdisjoint(table_names)

    database = tmp_path / "legacy-records.db"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE pc_sources ("
            "scope_id VARCHAR(256) NOT NULL, source_type VARCHAR(128) NOT NULL, "
            "source_id VARCHAR(256) NOT NULL, payload BLOB NOT NULL, journal_position BIGINT NOT NULL, "
            "PRIMARY KEY (scope_id, source_type, source_id))"
        )
        connection.execute(
            "CREATE TABLE pc_artifacts ("
            "scope_id VARCHAR(256) NOT NULL, family VARCHAR(128) NOT NULL, artifact_id VARCHAR(128) NOT NULL, "
            "revision INTEGER NOT NULL, content BLOB NOT NULL, "
            "PRIMARY KEY (scope_id, family, artifact_id, revision))"
        )
        connection.execute(
            "CREATE TABLE pc_artifact_heads ("
            "scope_id VARCHAR(256) NOT NULL, family VARCHAR(128) NOT NULL, artifact_id VARCHAR(128) NOT NULL, "
            "revision INTEGER NOT NULL, searchable_text TEXT NULL, PRIMARY KEY (scope_id, family, artifact_id))"
        )

    async def scenario() -> None:
        config = SQLiteConfig(url=f"sqlite+aiosqlite:///{database}")
        selected = (SOURCES_TABLE, ARTIFACTS_TABLE, ARTIFACT_HEADS_TABLE)
        for _ in range(2):
            async with (
                SQLiteProfile.open(config, tables=selected) as profile,
                profile.database.transaction() as connection,
            ):
                source_columns = {
                    str(row["name"])
                    for row in (await connection.exec_driver_sql("PRAGMA table_info('pc_sources')")).mappings()
                }
                artifact_columns = {
                    str(row["name"])
                    for row in (await connection.exec_driver_sql("PRAGMA table_info('pc_artifacts')")).mappings()
                }
                head_columns = {
                    str(row["name"])
                    for row in (await connection.exec_driver_sql("PRAGMA table_info('pc_artifact_heads')")).mappings()
                }
                assert "created_at" in source_columns
                assert "created_at" in artifact_columns
                assert "deleted_at" in head_columns

    asyncio.run(scenario())


def test_record_column_migration_uses_mysql_compatible_nullable_timestamps() -> None:
    result = MagicMock()
    result.scalars.return_value = ()
    connection = SimpleNamespace(
        dialect=SimpleNamespace(name="mysql"),
        execute=AsyncMock(return_value=result),
        exec_driver_sql=AsyncMock(),
    )

    asyncio.run(
        ensure_record_columns(
            cast(AsyncConnection, connection),
            {"pc_sources", "pc_artifacts", "pc_artifact_heads"},
        )
    )

    assert [call.args[0] for call in connection.exec_driver_sql.await_args_list] == [
        "ALTER TABLE pc_sources ADD COLUMN created_at DATETIME NULL",
        "ALTER TABLE pc_artifacts ADD COLUMN created_at DATETIME NULL",
        "ALTER TABLE pc_artifact_heads ADD COLUMN deleted_at DATETIME NULL",
    ]


def test_artifact_records_preserve_revisions_and_delete_only_the_head() -> None:
    async def scenario() -> None:
        async with SQLiteProfile.open(SQLiteConfig(), tables=SHARED_TABLES) as profile:
            records = RelationalRecordService(
                profile.database,
                SourceRepository((CONTENT_SOURCE_ADAPTER,)),
                id_factory=lambda kind: "source-1" if kind == "source" else "guide-1",
            )
            source = await records.create_source(
                "scope-a",
                "content",
                "The reviewed API design.",
                {},
            )
            first = await records.create_artifact(
                "scope-a",
                "document",
                ArtifactWrite(
                    content={"body": "Use a complete replacement."},
                    source_refs=(SourceRef(source_type=source.source_type, source_id=source.source_id),),
                ),
            )

            assert first.artifact_ref.revision == 1
            assert first.source_refs == (SourceRef(source_type="content", source_id="source-1"),)
            assert (await records.get_artifact("scope-a", "document", "guide-1")) == first
            listed = await records.query_artifacts(
                "scope-a",
                "document",
                query=None,
                mode=None,
                limit=10,
                cursor=None,
            )
            assert [item.artifact_ref for item in listed.items] == [first.artifact_ref]
            assert "content" not in type(listed.items[0]).model_fields
            found = await records.query_artifacts(
                "scope-a",
                "document",
                query="complete replacement",
                mode="auto",
                limit=10,
                cursor=None,
            )
            assert [item.artifact_ref.artifact_id for item in found.items] == ["guide-1"]

            second = await records.replace_artifact(
                "scope-a",
                "document",
                "guide-1",
                1,
                ArtifactWrite(
                    content={"body": "Use If-Match for replacement."},
                    source_refs=(SourceRef(source_type=source.source_type, source_id=source.source_id),),
                    artifact_refs=(first.artifact_ref,),
                ),
            )
            assert second.artifact_ref.revision == 2
            assert (await records.get_artifact_revision("scope-a", "document", "guide-1", 1)) == first
            with pytest.raises(ArtifactRevisionPreconditionError):
                await records.replace_artifact(
                    "scope-a",
                    "document",
                    "guide-1",
                    1,
                    ArtifactWrite(content={"body": "stale"}),
                )

            scopes = await records.list_scopes(limit=10, cursor=None)
            assert [(item.scope_id, item.source_count, item.artifact_count) for item in scopes.items] == [
                ("scope-a", 1, 1)
            ]
            assert scopes.items[0].source_types == ("content",)
            assert scopes.items[0].artifact_families == ("document",)

            deleted = await records.delete_artifact("scope-a", "document", "guide-1", 2)
            assert deleted.artifact_ref == second.artifact_ref
            assert await records.delete_artifact("scope-a", "document", "guide-1", 2) == deleted
            with pytest.raises(BaseValueNotFoundError):
                await records.get_artifact("scope-a", "document", "guide-1")
            assert (await records.get_artifact_revision("scope-a", "document", "guide-1", 2)) == second
            assert (
                await records.query_artifacts("scope-a", "document", query=None, mode=None, limit=10, cursor=None)
            ).items == ()
            async with profile.database.transaction() as connection:
                head = (
                    await connection.execute(select(ARTIFACT_HEADS_TABLE.c.revision, ARTIFACT_HEADS_TABLE.c.deleted_at))
                ).one()
                assert head.revision == 2
                assert head.deleted_at is not None
                assert (await StatisticsRepository().inventory(connection, "scope-a")).artifacts == ()

            with pytest.raises(BaseValueConflictError):
                await records.create_artifact(
                    "scope-a",
                    "document",
                    ArtifactWrite(content={"body": "reused identity"}),
                )
            for protected_family in ("experience", "handoff", "memory", "skill"):
                with pytest.raises(BaseOperationNotSupportedError):
                    await records.create_artifact(
                        "scope-a",
                        protected_family,
                        ArtifactWrite(content={"body": "protected"}),
                    )

    asyncio.run(scenario())
