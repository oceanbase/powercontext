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

from powercontext.builtin.persistence.records import RelationalRecordService
from powercontext.builtin.persistence.sources import SourceRepository
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.persistence.tables import SHARED_TABLES
from powercontext.builtin.records import (
    ArtifactRevisionPreconditionError,
    ArtifactWrite,
    BaseOperationNotSupportedError,
    BaseValueConflictError,
    BaseValueNotFoundError,
    InvalidBaseAccessRequestError,
)
from powercontext.builtin.sources import CONTENT_SOURCE_ADAPTER
from powercontext.sources import SourceRef


def test_source_records_support_create_get_list_search_and_bound_cursors() -> None:
    async def scenario() -> None:
        async with SQLiteProfile.open(SQLiteConfig(), tables=SHARED_TABLES) as profile:
            records = RelationalRecordService(
                profile.database,
                SourceRepository((CONTENT_SOURCE_ADAPTER,)),
            )
            first = await records.create_source(
                "scope-a",
                "content",
                "source-1",
                "Keep the OpenAPI contract authoritative.",
                {"channel": "test"},
            )
            repeated = await records.create_source(
                "scope-a",
                "content",
                "source-1",
                "Keep the OpenAPI contract authoritative.",
                {"channel": "test"},
            )
            second = await records.create_source(
                "scope-a",
                "content",
                "source-2",
                "Preserve immutable artifact revisions.",
                {},
            )

            assert repeated == first
            assert first.position == 1
            assert second.position == 2
            assert (await records.get_source("scope-a", "content", "source-1")) == first

            first_page = await records.query_sources(
                "scope-a",
                "content",
                "list",
                query=None,
                mode=None,
                limit=1,
                cursor=None,
            )
            assert [item.source_ref.source_id for item in first_page.items] == ["source-1"]
            assert first_page.next_cursor is not None
            second_page = await records.query_sources(
                "scope-a",
                "content",
                "list",
                query=None,
                mode=None,
                limit=1,
                cursor=first_page.next_cursor,
            )
            assert [item.source_ref.source_id for item in second_page.items] == ["source-2"]

            found = await records.query_sources(
                "scope-a",
                "content",
                "search",
                query="OpenAPI authoritative",
                mode="auto",
                limit=10,
                cursor=None,
            )
            assert [item.source_ref.source_id for item in found.items] == ["source-1"]
            assert found.mode == "keyword"
            assert found.items[0].score == 1.0

            with pytest.raises(BaseValueConflictError):
                await records.create_source("scope-a", "content", "source-1", "different", {})
            with pytest.raises(InvalidBaseAccessRequestError):
                await records.query_sources(
                    "scope-a",
                    "content",
                    "search",
                    query="OpenAPI",
                    mode="auto",
                    limit=1,
                    cursor=first_page.next_cursor,
                )
            with pytest.raises(BaseOperationNotSupportedError):
                await records.get_source("scope-a", "git", "source-1")

    asyncio.run(scenario())


def test_artifact_records_preserve_revisions_and_delete_only_the_head() -> None:
    async def scenario() -> None:
        async with SQLiteProfile.open(SQLiteConfig(), tables=SHARED_TABLES) as profile:
            records = RelationalRecordService(
                profile.database,
                SourceRepository((CONTENT_SOURCE_ADAPTER,)),
            )
            source = await records.create_source(
                "scope-a",
                "content",
                "source-1",
                "The reviewed API design.",
                {},
            )
            first = await records.create_artifact(
                "scope-a",
                "document",
                "guide-1",
                ArtifactWrite(
                    schema_version=1,
                    metadata={"title": "API Guide"},
                    content={"body": "Use a complete replacement."},
                    source_refs=(source.source_ref,),
                ),
            )

            assert first.artifact_ref.revision == 1
            assert first.source_refs == (SourceRef(source_type="content", source_id="source-1"),)
            assert (await records.get_artifact("scope-a", "document", "guide-1")) == first
            assert (await records.list_artifacts("scope-a", "document", limit=10, cursor=None)).items == (first,)
            found = await records.search_artifacts(
                "scope-a",
                "document",
                "complete replacement",
                mode="auto",
                limit=10,
                cursor=None,
            )
            assert [hit.artifact.artifact_ref.artifact_id for hit in found.hits] == ["guide-1"]

            second = await records.replace_artifact(
                "scope-a",
                "document",
                "guide-1",
                1,
                ArtifactWrite(
                    schema_version=2,
                    metadata={"title": "API Guide"},
                    content={"body": "Use If-Match for replacement."},
                    source_refs=(source.source_ref,),
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
                    ArtifactWrite(schema_version=2, metadata={}, content={"body": "stale"}),
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
            assert (await records.list_artifacts("scope-a", "document", limit=10, cursor=None)).items == ()

            with pytest.raises(BaseValueConflictError):
                await records.create_artifact(
                    "scope-a",
                    "document",
                    "guide-1",
                    ArtifactWrite(schema_version=1, metadata={}, content={"body": "reused identity"}),
                )
            with pytest.raises(BaseOperationNotSupportedError):
                await records.create_artifact(
                    "scope-a",
                    "memory",
                    "memory-1",
                    ArtifactWrite(schema_version=1, metadata={}, content={"body": "protected"}),
                )

    asyncio.run(scenario())
