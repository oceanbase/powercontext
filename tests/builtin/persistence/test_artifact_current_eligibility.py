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
from datetime import UTC, datetime

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.builtin.artifacts.skill import Skill, SkillContent, SkillDraft
from powercontext.builtin.artifacts.topic_memory import (
    TopicMemory,
    TopicMemoryContent,
    TopicMemoryDraft,
    prepare_topic_memory_projection,
)
from powercontext.builtin.persistence.artifact_readers import TopicMemoryArtifactListReader
from powercontext.builtin.persistence.artifacts import ArtifactRepository
from powercontext.builtin.persistence.family_management import FamilyManagementWriterRegistry
from powercontext.builtin.persistence.records import RelationalRecordService
from powercontext.builtin.persistence.sources import SourceRepository
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.persistence.sqlite.topic_memory_index import SQLiteTopicMemoryFTSIndex
from powercontext.builtin.persistence.tables import (
    ARTIFACT_HEADS_TABLE,
    BUILTIN_TABLES,
    TOPIC_MEMORY_REVISION_PUBLICATIONS_TABLE,
)
from powercontext.builtin.persistence.topic_memory import TopicMemoryRepository
from powercontext.builtin.persistence.topic_memory_index import CompositeTopicMemoryIndex
from powercontext.builtin.runtime.relational import RelationalContexts
from powercontext.builtin.sources import CONTENT_SOURCE_ADAPTER

_ARTIFACT_IDS = (
    "a-frozen",
    "b-retired",
    "c-deprecated",
    "d-active",
    "e-frozen",
    "f-retired",
    "g-active",
    "h-frozen",
)


def _skill_draft(label: str) -> SkillDraft:
    return SkillDraft(
        content=SkillContent(
            name=label,
            description=f"Instructions for {label}.",
            instructions="Read the current evidence before acting.",
            validation=("Check the reported result.",),
        )
    )


async def _set_lifecycle_fixture(connection: AsyncConnection, family: str) -> None:
    for artifact_id in _ARTIFACT_IDS:
        if artifact_id.endswith("frozen"):
            values = {"lifecycle_state": "deprecated", "merged_into_id": "d-active"}
        elif artifact_id.endswith("retired"):
            values = {"lifecycle_state": "retired"}
        elif artifact_id.endswith("deprecated"):
            values = {"lifecycle_state": "deprecated", "replacement_artifact_id": "d-active"}
        else:
            continue
        await connection.execute(
            update(ARTIFACT_HEADS_TABLE)
            .where(
                ARTIFACT_HEADS_TABLE.c.scope_id == "scope",
                ARTIFACT_HEADS_TABLE.c.family == family,
                ARTIFACT_HEADS_TABLE.c.artifact_id == artifact_id,
            )
            .values(**values)
        )


def _records(profile: SQLiteProfile, artifacts: ArtifactRepository, sources: SourceRepository, **kwargs):
    return RelationalRecordService(
        profile.database,
        sources,
        artifacts,
        FamilyManagementWriterRegistry(()),
        cursor_secret=b"current-eligibility-test-secret",
        **kwargs,
    )


def test_common_current_list_filters_ineligible_heads_before_pagination() -> None:
    async def scenario() -> None:
        sources = SourceRepository((CONTENT_SOURCE_ADAPTER,))
        artifacts = ArtifactRepository((Skill,), sources=sources)
        async with SQLiteProfile.open(SQLiteConfig(), tables=BUILTIN_TABLES) as profile:
            records = _records(profile, artifacts, sources)
            async with profile.database.transaction() as connection:
                for artifact_id in _ARTIFACT_IDS:
                    await artifacts.create(connection, "scope", artifact_id, _skill_draft(artifact_id))
                await _set_lifecycle_fixture(connection, Skill.family)

            first = await records.query_artifacts("scope", Skill.family, limit=2, cursor=None)
            assert [item.artifact_id for item in first.items] == ["c-deprecated", "d-active"]
            assert first.next_cursor is not None
            second = await records.query_artifacts("scope", Skill.family, limit=2, cursor=first.next_cursor)
            assert [item.artifact_id for item in second.items] == ["g-active"]
            assert second.next_cursor is None

    asyncio.run(scenario())


def test_frozen_and_retired_identities_retain_latest_history_and_permission_catalog_reads() -> None:
    async def scenario() -> None:
        sources = SourceRepository((CONTENT_SOURCE_ADAPTER,))
        artifacts = ArtifactRepository((Skill,), sources=sources)
        async with SQLiteProfile.open(SQLiteConfig(), tables=BUILTIN_TABLES) as profile:
            records = _records(profile, artifacts, sources)
            async with profile.database.transaction() as connection:
                for artifact_id in _ARTIFACT_IDS:
                    original = await artifacts.create(connection, "scope", artifact_id, _skill_draft(artifact_id))
                    if artifact_id in {"a-frozen", "b-retired"}:
                        await artifacts.revise(connection, "scope", original, _skill_draft(f"updated-{artifact_id}"))
                await _set_lifecycle_fixture(connection, Skill.family)

            for artifact_id in ("a-frozen", "b-retired"):
                latest = await records.get_artifact("scope", Skill.family, artifact_id)
                exact = await records.get_artifact_revision("scope", Skill.family, artifact_id, 1)
                history = await records.list_artifact_revisions(
                    "scope", Skill.family, artifact_id, limit=1, cursor=None
                )
                assert latest.revision == 2
                assert exact.revision == 1
                assert [item.revision for item in history.items] == [2]
                assert history.next_cursor is not None
                older = await records.list_artifact_revisions(
                    "scope", Skill.family, artifact_id, limit=1, cursor=history.next_cursor
                )
                assert [item.revision for item in older.items] == [1]
                assert older.next_cursor is None

            catalog = await records.logical_artifacts("scope")
            assert {item.artifact_id for item in catalog} == set(_ARTIFACT_IDS)

    asyncio.run(scenario())


def test_topic_current_pages_and_has_more_filter_ineligible_heads() -> None:
    async def scenario() -> None:
        sources = SourceRepository((CONTENT_SOURCE_ADAPTER,))
        artifacts = ArtifactRepository((TopicMemory,), sources=sources)
        index = CompositeTopicMemoryIndex(SQLiteTopicMemoryFTSIndex())
        repository = TopicMemoryRepository(artifacts=artifacts, index=index)
        async with SQLiteProfile.open(SQLiteConfig(), tables=BUILTIN_TABLES + index.tables) as profile:
            records = _records(
                profile,
                artifacts,
                sources,
                topic_memory_list_reader=TopicMemoryArtifactListReader(
                    database=profile.database, artifacts=artifacts, topics=repository
                ),
            )
            async with profile.database.transaction() as connection:
                await repository.initialize(connection)
                for artifact_id in _ARTIFACT_IDS:
                    content = TopicMemoryContent(
                        title=artifact_id,
                        summary=f"Current evidence for {artifact_id}.",
                        detail="The exact evidence remains available for historical review.",
                    )
                    await repository.publish_create(
                        connection,
                        "scope",
                        artifact_id,
                        TopicMemoryDraft(content=content),
                        prepare_topic_memory_projection(content),
                    )
                await connection.execute(
                    update(TOPIC_MEMORY_REVISION_PUBLICATIONS_TABLE).values(
                        published_at=datetime(2026, 9, 5, 3, 4, 5, tzinfo=UTC)
                    )
                )
                await _set_lifecycle_fixture(connection, TopicMemory.family)

            first = await records.query_artifacts("scope", TopicMemory.family, limit=2, cursor=None)
            assert [item.artifact_id for item in first.items] == ["c-deprecated", "d-active"]
            assert first.next_cursor is not None
            second = await records.query_artifacts("scope", TopicMemory.family, limit=1, cursor=first.next_cursor)
            assert [item.artifact_id for item in second.items] == ["g-active"]
            assert second.next_cursor is None
            for artifact_id in ("a-frozen", "b-retired"):
                assert (await records.get_artifact("scope", TopicMemory.family, artifact_id)).revision == 1
                assert (await records.get_artifact_revision("scope", TopicMemory.family, artifact_id, 1)).revision == 1

    asyncio.run(scenario())


def test_skill_library_includes_ordinary_deprecated_heads_without_frozen_inputs() -> None:
    async def scenario() -> None:
        async with SQLiteProfile.open(SQLiteConfig(), tables=BUILTIN_TABLES) as profile:
            contexts = RelationalContexts(database=profile.database)
            async with profile.database.transaction() as connection:
                for artifact_id in _ARTIFACT_IDS:
                    await contexts.repositories.artifacts.create(
                        connection, "scope", artifact_id, _skill_draft(artifact_id)
                    )
                await _set_lifecycle_fixture(connection, Skill.family)

            included = await contexts.list_skills("scope", True, 2)
            assert [skill.artifact_id for skill, _ in included] == ["c-deprecated", "d-active"]
            assert included[0][1].replacement_artifact_id == "d-active"
            active = await contexts.list_skills("scope", False, 2)
            assert [skill.artifact_id for skill, _ in active] == ["d-active", "g-active"]

    asyncio.run(scenario())
