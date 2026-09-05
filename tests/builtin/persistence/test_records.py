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
from collections import defaultdict
from pathlib import Path

import pytest
from pydantic import JsonValue
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.experience import Experience
from powercontext.builtin.artifacts.handoff import Handoff
from powercontext.builtin.artifacts.memory import Memory, MemoryContent
from powercontext.builtin.artifacts.skill import Skill
from powercontext.builtin.persistence.artifacts import ArtifactRepository
from powercontext.builtin.persistence.experience_index import ExperienceIndex, NoExperienceIndex
from powercontext.builtin.persistence.family_management import (
    ExperienceManagementWriter,
    FamilyManagementWriterRegistry,
    HandoffManagementWriter,
    MemoryManagementWriter,
    SkillManagementWriter,
)
from powercontext.builtin.persistence.memory_index import NoMemoryIndex
from powercontext.builtin.persistence.records import RelationalRecordService
from powercontext.builtin.persistence.skill_packages import SkillPackageRepository
from powercontext.builtin.persistence.sources import SourceRepository
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.persistence.sqlite.experience_index import SQLiteExperienceFTSIndex
from powercontext.builtin.persistence.tables import (
    ARTIFACT_HEADS_TABLE,
    ARTIFACT_LINEAGE_SOURCES_TABLE,
    ARTIFACTS_TABLE,
    BUILTIN_TABLES,
    MEMORY_ENTRY_HEADS_TABLE,
    MEMORY_ENTRY_VERSIONS_TABLE,
    SKILL_PACKAGES_TABLE,
    SOURCES_TABLE,
)
from powercontext.builtin.records import (
    ArtifactRevisionPreconditionError,
    ArtifactWrite,
    InvalidBaseAccessRequestError,
    InvalidCursorError,
)
from powercontext.builtin.source_eligibility import SourceNotEligibleError
from powercontext.builtin.sources import CONTENT_SOURCE_ADAPTER, ContentSource
from powercontext.builtin.tags import ArtifactTagTarget, MemoryEntryTagTarget, TagFilter, TagPreconditionError, TagQuery


class _FailingExperienceIndex(NoExperienceIndex):
    async def replace(
        self,
        _connection: AsyncConnection,
        _scope_id: str,
        _experience: Experience,
        /,
    ) -> None:
        raise RuntimeError("projection update failed")  # noqa: TRY003


def _memory_content() -> dict[str, JsonValue]:
    return {"entries": [{"kind": "preference", "text": "用户偏好使用中文回答"}]}


def test_empty_tag_set_has_one_concurrent_winner_across_connections(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with SQLiteProfile.open(
            SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'tag-race.db'}"), tables=BUILTIN_TABLES
        ) as profile:
            first, _, _ = _services(profile)
            second, _, _ = _services(profile)
            created = await first.create_artifact("scope", "memory", ArtifactWrite(content=_memory_content()))
            target = ArtifactTagTarget(family="memory", artifact_id=created.artifact_id)
            empty = await first.get_tags("scope", target)
            outcomes = await asyncio.gather(
                first.replace_tags("scope", target, ("one",), expected_etag=empty.etag),
                second.replace_tags("scope", target, ("two",), expected_etag=empty.etag),
                return_exceptions=True,
            )
            assert sum(isinstance(outcome, TagPreconditionError) for outcome in outcomes) == 1
            assert (await first.get_tags("scope", target)).tags in {("one",), ("two",)}

    asyncio.run(scenario())


def test_tag_cursor_is_bound_to_filter_scope_and_caller() -> None:
    async def scenario() -> None:
        async with SQLiteProfile.open(SQLiteConfig(), tables=BUILTIN_TABLES) as profile:
            records, _, _ = _services(profile)
            for _ in range(2):
                created = await records.create_artifact("scope", "memory", ArtifactWrite(content=_memory_content()))
                target = ArtifactTagTarget(family="memory", artifact_id=created.artifact_id)
                empty = await records.get_tags("scope", target)
                await records.replace_tags("scope", target, ("shared",), expected_etag=empty.etag)
            page = await records.query_tags("scope", TagQuery(tags=("shared",), limit=1), caller="a")
            assert page.next_cursor
            for scope, label, caller in (
                ("other", "shared", "a"),
                ("scope", "different", "a"),
                ("scope", "shared", "b"),
            ):
                with pytest.raises(InvalidCursorError):
                    await records.query_tags(scope, TagQuery(tags=(label,), cursor=page.next_cursor), caller=caller)

    asyncio.run(scenario())


def test_in_memory_sqlite_supports_concurrent_tag_reads() -> None:
    async def scenario() -> None:
        async with SQLiteProfile.open(SQLiteConfig(), tables=BUILTIN_TABLES) as profile:
            records, _, _ = _services(profile)
            created = await records.create_artifact("scope", "memory", ArtifactWrite(content=_memory_content()))
            target = ArtifactTagTarget(family="memory", artifact_id=created.artifact_id)
            values = await asyncio.gather(*(records.get_tags("scope", target) for _ in range(3)))
            assert len({value.etag for value in values}) == 1

    asyncio.run(scenario())


def test_artifact_and_entry_tags_preserve_content_and_query_independently() -> None:
    async def scenario() -> None:
        async with SQLiteProfile.open(SQLiteConfig(), tables=BUILTIN_TABLES) as profile:
            records, _, _ = _services(profile)
            created = await records.create_artifact("scope-a", "memory", ArtifactWrite(content=_memory_content()))
            before = await records.get_artifact("scope-a", "memory", created.artifact_id)
            artifact = ArtifactTagTarget(family="memory", artifact_id=created.artifact_id)
            entry_id = MemoryContent.model_validate(before.content).manifest.entries[0].entry_id
            entry = MemoryEntryTagTarget(artifact_id=created.artifact_id, entry_id=entry_id)
            empty = await records.get_tags("scope-a", artifact)
            tagged = await records.replace_tags("scope-a", artifact, ("Project", "中文"), expected_etag=empty.etag)
            entry_empty = await records.get_tags("scope-a", entry)
            assert entry_empty.tags == ()
            assert entry_empty.etag != empty.etag
            await records.replace_tags("scope-a", entry, ("project",), expected_etag=entry_empty.etag)
            assert await records.get_artifact("scope-a", "memory", created.artifact_id) == before
            page = await records.query_tags("scope-a", TagQuery(tags=("PROJECT",), limit=1))
            assert len(page.items) == 1 and page.next_cursor
            second = await records.query_tags("scope-a", TagQuery(tags=("project",), limit=1, cursor=page.next_cursor))
            assert {page.items[0].target.type, second.items[0].target.type} == {"artifact", "memory_entry"}
            assert second.next_cursor is None
            assert (await records.query_tags("other-scope", TagQuery(tags=("project",)))).items == ()
            all_tags = await records.query_tags("scope-a", TagQuery(tags=("project", "中文")))
            assert [item.target for item in all_tags.items] == [artifact]
            assert (
                len((await records.query_tags("scope-a", TagQuery(tags=("project", "中文"), match="any"))).items) == 2
            )
            assert (
                await records.query_artifacts(
                    "scope-a", "memory", limit=1, cursor=None, tag_filter=TagFilter(tags=("missing",))
                )
            ).items == ()
            assert (
                len(
                    (
                        await records.query_artifacts(
                            "scope-a", "memory", limit=1, cursor=None, tag_filter=TagFilter(tags=("project",))
                        )
                    ).items
                )
                == 1
            )
            with pytest.raises(TagPreconditionError):
                await records.replace_tags("scope-a", artifact, ("lost update",), expected_etag=empty.etag)
            await records.replace_tags("scope-a", artifact, (), expected_etag=tagged.etag)
            assert (await records.get_tags("scope-a", entry)).tags == ("project",)

    asyncio.run(scenario())


def _handoff_content(objective: str = "Transfer the API test result.") -> dict[str, JsonValue]:
    return {
        "schema": "powercontext.handoff.v1",
        "objective": objective,
        "state": [
            {
                "text": "The Source and Artifact API passed live HTTP tests.",
                "citations": [
                    {
                        "kind": "source",
                        "source_ref": {"source_type": "content", "source_id": "source-evidence"},
                    }
                ],
            }
        ],
        "disposition": "complete",
        "next_action": None,
        "omissions": [],
    }


def _services(
    profile: SQLiteProfile,
    *,
    experience_index: ExperienceIndex | None = None,
) -> tuple[RelationalRecordService, ArtifactRepository, SourceRepository]:
    counters: defaultdict[str, int] = defaultdict(int)

    def new_id(kind: str) -> str:
        counters[kind] += 1
        prefixes = {"source": "src", "memory": "mem", "experience": "exp", "skill": "skill"}
        return f"{prefixes.get(kind, kind)}-{counters[kind]}"

    sources = SourceRepository((CONTENT_SOURCE_ADAPTER,))
    artifacts = ArtifactRepository((Handoff, Memory, Experience, Skill), sources=sources)
    memory_index = NoMemoryIndex()
    selected_experience_index = NoExperienceIndex() if experience_index is None else experience_index
    packages = SkillPackageRepository()
    writers = FamilyManagementWriterRegistry((
        MemoryManagementWriter(
            database=profile.database,
            artifacts=artifacts,
            index=memory_index,
            embedding_model=None,
            id_factory=new_id,
        ),
        ExperienceManagementWriter(artifacts, selected_experience_index),
        SkillManagementWriter(artifacts, selected_experience_index, packages),
        HandoffManagementWriter(
            database=profile.database,
            artifacts=artifacts,
            sources=sources,
            memory_index=memory_index,
            id_factory=new_id,
            memory_artifact_id="memory",
            handoff_artifact_id="handoff",
        ),
    ))
    records = RelationalRecordService(
        profile.database,
        sources,
        artifacts,
        writers,
        id_factory=new_id,
        cursor_secret=b"record-test-secret",
    )
    return records, artifacts, sources


def test_source_create_persists_json_without_public_internal_fields() -> None:
    async def scenario() -> None:
        async with SQLiteProfile.open(SQLiteConfig(), tables=BUILTIN_TABLES) as profile:
            records, _, _ = _services(profile)
            created = await records.create_source("scope-a", "content", {"fact": True})
            loaded = await records.get_source("scope-a", "content", "src-1")
            null_source = await records.create_source("scope-a", "content", None)

            assert loaded == created
            assert created.content == {"fact": True}
            assert null_source.content is None
            assert (await records.get_source("scope-a", "content", "src-2")).content is None
            assert set(created.model_dump()) == {
                "scope_id",
                "source_type",
                "source_id",
                "content",
                "position",
                "content_digest",
            }
            with pytest.raises(InvalidBaseAccessRequestError):
                await records.create_source("scope-a", "private", "not public")

    asyncio.run(scenario())


def test_artifact_create_is_atomic_and_binds_its_system_source() -> None:
    async def scenario() -> None:
        async with SQLiteProfile.open(SQLiteConfig(), tables=BUILTIN_TABLES) as profile:
            records, artifacts, sources = _services(profile)
            created = await records.create_artifact("scope-a", "memory", ArtifactWrite(content=_memory_content()))

            assert (created.family, created.artifact_id, created.revision) == ("memory", "mem-1", 1)
            assert created.artifacts == ()
            assert len(created.sources) == 1
            assert created.sources[0].source_id == "src-1"

            loaded_source = await records.get_source("scope-a", "content", "src-1")
            assert loaded_source.content == _memory_content()
            async with profile.database.transaction() as connection:
                stored = await sources.get(connection, "scope-a", created.sources[0])
                assert isinstance(stored.value, ContentSource)
                assert stored.value.internal is not None
                assert stored.value.internal.target.model_dump() == {
                    "scope_id": "scope-a",
                    "family": "memory",
                    "artifact_id": "mem-1",
                    "revision": 1,
                }
                lineage = (await connection.execute(select(ARTIFACT_LINEAGE_SOURCES_TABLE))).mappings().one()
                assert lineage["ordinal"] == 0
                entry = (await connection.execute(select(MEMORY_ENTRY_VERSIONS_TABLE))).mappings().one()
                assert (entry["kind"], entry["text"]) == ("preference", "用户偏好使用中文回答")
                projection = (await connection.execute(select(MEMORY_ENTRY_HEADS_TABLE))).mappings().one()
                assert projection["searchable_text"]

            async with profile.database.transaction() as connection:
                assert await connection.scalar(select(func.count()).select_from(SOURCES_TABLE)) == 1
                assert await connection.scalar(select(func.count()).select_from(ARTIFACTS_TABLE)) == 1
                assert await connection.scalar(select(func.count()).select_from(ARTIFACT_HEADS_TABLE)) == 1
                stored_memory = await records.get_artifact("scope-a", "memory", created.artifact_id)
                foreign = artifacts.draft("memory", stored_memory.content, sources=created.sources)
                with pytest.raises(SourceNotEligibleError):
                    await artifacts.create(connection, "scope-a", "mem-foreign", foreign)

    asyncio.run(scenario())


def test_artifact_get_list_replace_use_family_models_and_opaque_etags() -> None:
    async def scenario() -> None:
        async with SQLiteProfile.open(SQLiteConfig(), tables=BUILTIN_TABLES) as profile:
            records, _, sources = _services(profile)
            created = await records.create_artifact("scope-a", "memory", ArtifactWrite(content=_memory_content()))
            head = await records.get_artifact("scope-a", "memory", created.artifact_id)
            page = await records.query_artifacts("scope-a", "memory", limit=10, cursor=None)

            assert head.revision == 1
            assert head.sources == created.sources
            assert [item.artifact_id for item in page.items] == [created.artifact_id]
            assert "content" not in page.items[0].model_dump()
            replaced = await records.replace_artifact(
                "scope-a",
                "memory",
                created.artifact_id,
                '"revision:1"',
                ArtifactWrite(content={"entries": [{"kind": "working_note", "text": "继续验证 API"}]}),
            )
            assert replaced.revision == 2
            assert replaced.sources[0].source_id == "src-2"
            original = await records.get_artifact_revision("scope-a", "memory", created.artifact_id, 1)
            assert original.sources == created.sources
            async with profile.database.transaction() as connection:
                replacement_source = await sources.get(connection, "scope-a", replaced.sources[0])
                versions = (
                    (
                        await connection.execute(
                            select(MEMORY_ENTRY_VERSIONS_TABLE).order_by(MEMORY_ENTRY_VERSIONS_TABLE.c.entry_version_id)
                        )
                    )
                    .mappings()
                    .all()
                )
                projections = (await connection.execute(select(MEMORY_ENTRY_HEADS_TABLE))).mappings().all()
            assert isinstance(replacement_source.value, ContentSource)
            assert replacement_source.value.internal is not None
            assert replacement_source.value.internal.operation == "artifact_replace"
            assert replacement_source.value.internal.target.revision == 2
            assert [(row["kind"], row["text"]) for row in versions] == [
                ("preference", "用户偏好使用中文回答"),
                ("working_note", "继续验证 API"),
            ]
            assert len(projections) == 2

            with pytest.raises(ArtifactRevisionPreconditionError):
                await records.replace_artifact(
                    "scope-a",
                    "memory",
                    created.artifact_id,
                    '"opaque-stale"',
                    ArtifactWrite(content=_memory_content()),
                )
            with pytest.raises(InvalidBaseAccessRequestError):
                await records.create_artifact("scope-a", "document", ArtifactWrite(content={}))
            with pytest.raises(InvalidBaseAccessRequestError):
                await records.create_artifact("scope-a", "memory", ArtifactWrite(content={"invalid": True}))

    asyncio.run(scenario())


def test_artifact_create_and_replace_validate_handoff_as_json() -> None:
    async def scenario() -> None:
        async with SQLiteProfile.open(SQLiteConfig(), tables=BUILTIN_TABLES) as profile:
            records, _, _ = _services(profile)
            evidence = await records.create_source("scope-a", "content", "verified API test")
            created = await records.create_artifact(
                "scope-a",
                "handoff",
                ArtifactWrite(
                    content=_handoff_content().copy()
                    | {
                        "state": [
                            {
                                "text": "verified",
                                "citations": [
                                    {
                                        "kind": "source",
                                        "source_ref": evidence.model_dump(include={"source_type", "source_id"}),
                                    }
                                ],
                            }
                        ]
                    }
                ),
            )

            loaded = await records.get_artifact("scope-a", "handoff", created.artifact_id)
            assert loaded.artifact_id == "handoff"
            assert loaded.sources[0] == created.sources[0]
            assert loaded.sources[1].source_id == evidence.source_id

            replacement = loaded.content | {"objective": "Transfer the verified API test result."}
            replaced = await records.replace_artifact(
                "scope-a",
                "handoff",
                created.artifact_id,
                '"revision:1"',
                ArtifactWrite(content=replacement),
            )
            assert replaced.revision == 2
            assert replaced.sources[0].source_id != created.sources[0].source_id
            assert replaced.sources[1].source_id == evidence.source_id
            assert replaced.content == replacement

    asyncio.run(scenario())


def test_artifact_list_batches_revision_and_lineage_reads() -> None:
    async def scenario() -> None:
        async with SQLiteProfile.open(SQLiteConfig(), tables=BUILTIN_TABLES) as profile:
            records, _, _ = _services(profile)
            for _ in range(3):
                await records.create_artifact("scope-a", "memory", ArtifactWrite(content=_memory_content()))

            statements: list[str] = []

            def record_statement(*args: object) -> None:
                statements.append(str(args[2]))

            event.listen(profile.database.engine.sync_engine, "before_cursor_execute", record_statement)
            try:
                page = await records.query_artifacts("scope-a", "memory", limit=10, cursor=None)
            finally:
                event.remove(profile.database.engine.sync_engine, "before_cursor_execute", record_statement)

            assert len(page.items) == 3
            assert len([statement for statement in statements if statement.lstrip().upper().startswith("SELECT")]) == 4

    asyncio.run(scenario())


def test_experience_and_skill_writers_update_owned_search_projections() -> None:
    async def scenario() -> None:
        async with SQLiteProfile.open(SQLiteConfig(), tables=BUILTIN_TABLES) as profile:
            index = SQLiteExperienceFTSIndex()
            async with profile.database.transaction() as connection:
                await index.initialize(connection)
            records, _, _ = _services(profile, experience_index=index)

            experience = await records.create_artifact(
                "scope-a",
                "experience",
                ArtifactWrite(
                    content={
                        "situation": "发布前发现兼容性问题",
                        "action": "增加跨版本测试",
                        "outcome": "避免线上回归",
                        "lesson": "公共接口变更需要覆盖兼容性测试",
                    }
                ),
            )
            skill = await records.create_artifact(
                "scope-a",
                "skill",
                ArtifactWrite(
                    content={
                        "name": "compatibility-check",
                        "description": "Check compatibility before release",
                        "instructions": "Run cross-version compatibility tests.",
                        "validation": ["Compatibility tests pass"],
                    }
                ),
            )
            replaced_experience = await records.replace_artifact(
                "scope-a",
                "experience",
                experience.artifact_id,
                '"revision:1"',
                ArtifactWrite(
                    content={
                        "situation": "A rollback was required",
                        "action": "Rebuild the compatibility matrix",
                        "outcome": "The release became safe",
                        "lesson": "Keep compatibility evidence current",
                    }
                ),
            )
            replaced_skill = await records.replace_artifact(
                "scope-a",
                "skill",
                skill.artifact_id,
                '"revision:1"',
                ArtifactWrite(
                    content={
                        "name": "compatibility-check",
                        "description": "Check compatibility before every release",
                        "instructions": "Run the complete compatibility matrix.",
                        "validation": ["Compatibility matrix passes"],
                    }
                ),
            )

            async with profile.database.transaction() as connection:
                experience_hits = await index.search(connection, "scope-a", "rollback", 10)
                skill_hits = await index.search_skills(connection, "scope-a", "matrix", 10)
                package_count = await connection.scalar(select(func.count()).select_from(SKILL_PACKAGES_TABLE))
            assert [hit.artifact_ref for hit in experience_hits] == [
                ArtifactRef(
                    family="experience", artifact_id=experience.artifact_id, revision=replaced_experience.revision
                )
            ]
            assert [hit.artifact_ref for hit in skill_hits] == [
                ArtifactRef(family="skill", artifact_id=skill.artifact_id, revision=replaced_skill.revision)
            ]
            assert package_count == 2

    asyncio.run(scenario())


def test_family_projection_failure_rolls_back_source_and_artifact() -> None:
    async def scenario() -> None:
        async with SQLiteProfile.open(SQLiteConfig(), tables=BUILTIN_TABLES) as profile:
            records, _, _ = _services(profile, experience_index=_FailingExperienceIndex())

            with pytest.raises(RuntimeError, match="projection update failed"):
                await records.create_artifact(
                    "scope-a",
                    "experience",
                    ArtifactWrite(
                        content={
                            "situation": "A projection cannot be updated",
                            "action": "Abort the management write",
                            "outcome": "No partial state remains",
                            "lesson": "Derived state belongs to the transaction",
                        }
                    ),
                )

            async with profile.database.transaction() as connection:
                assert await connection.scalar(select(func.count()).select_from(SOURCES_TABLE)) == 0
                assert await connection.scalar(select(func.count()).select_from(ARTIFACTS_TABLE)) == 0
                assert await connection.scalar(select(func.count()).select_from(ARTIFACT_HEADS_TABLE)) == 0

    asyncio.run(scenario())


def test_base_access_reuses_existing_tables_without_lifecycle_columns() -> None:
    assert "created_at" not in SOURCES_TABLE.c
    assert "created_at" not in ARTIFACTS_TABLE.c
    assert "deleted_at" not in ARTIFACT_HEADS_TABLE.c
    assert ArtifactRef(family="memory", artifact_id="memory-1", revision=1).revision == 1
