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
from collections.abc import Iterator

import pytest
from pydantic import JsonValue
from sqlalchemy import event, func, select

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.experience import Experience
from powercontext.builtin.artifacts.handoff import Handoff
from powercontext.builtin.artifacts.memory import Memory
from powercontext.builtin.artifacts.skill import Skill
from powercontext.builtin.persistence.artifacts import ArtifactRepository
from powercontext.builtin.persistence.records import RelationalRecordService
from powercontext.builtin.persistence.sources import SourceRepository
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.persistence.tables import (
    ARTIFACT_HEADS_TABLE,
    ARTIFACT_LINEAGE_SOURCES_TABLE,
    ARTIFACTS_TABLE,
    SHARED_TABLES,
    SOURCES_TABLE,
)
from powercontext.builtin.records import (
    ArtifactRevisionPreconditionError,
    ArtifactWrite,
    BaseValueConflictError,
    InvalidBaseAccessRequestError,
)
from powercontext.builtin.source_eligibility import SourceNotEligibleError
from powercontext.builtin.sources import CONTENT_SOURCE_ADAPTER, ContentSource


def _memory_content() -> dict[str, JsonValue]:
    return {
        "manifest": {"entries": [], "format": "flat-v1"},
        "changes": [],
        "schema": "powercontext.memory.v1",
    }


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
    ids: Iterator[str],
) -> tuple[RelationalRecordService, ArtifactRepository, SourceRepository]:
    sources = SourceRepository((CONTENT_SOURCE_ADAPTER,))
    artifacts = ArtifactRepository((Handoff, Memory, Experience, Skill), sources=sources)
    records = RelationalRecordService(
        profile.database,
        sources,
        artifacts,
        id_factory=lambda _kind: next(ids),
        cursor_secret=b"record-test-secret",
    )
    return records, artifacts, sources


def test_source_create_persists_json_without_public_internal_fields() -> None:
    async def scenario() -> None:
        async with SQLiteProfile.open(SQLiteConfig(), tables=SHARED_TABLES) as profile:
            records, _, _ = _services(profile, iter(("src-1", "src-2")))
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
        async with SQLiteProfile.open(SQLiteConfig(), tables=SHARED_TABLES) as profile:
            records, artifacts, sources = _services(profile, iter(("mem-1", "src-1", "mem-1", "src-2")))
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

            with pytest.raises(BaseValueConflictError):
                await records.create_artifact("scope-a", "memory", ArtifactWrite(content=_memory_content()))

            async with profile.database.transaction() as connection:
                assert await connection.scalar(select(func.count()).select_from(SOURCES_TABLE)) == 1
                assert await connection.scalar(select(func.count()).select_from(ARTIFACTS_TABLE)) == 1
                assert await connection.scalar(select(func.count()).select_from(ARTIFACT_HEADS_TABLE)) == 1
                foreign = artifacts.draft("memory", _memory_content(), sources=created.sources)
                with pytest.raises(SourceNotEligibleError):
                    await artifacts.create(connection, "scope-a", "mem-foreign", foreign)

    asyncio.run(scenario())


def test_artifact_get_list_replace_use_family_models_and_opaque_etags() -> None:
    async def scenario() -> None:
        async with SQLiteProfile.open(SQLiteConfig(), tables=SHARED_TABLES) as profile:
            records, _, sources = _services(profile, iter(("mem-1", "src-1", "src-2")))
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
                ArtifactWrite(content=_memory_content()),
            )
            assert replaced.revision == 2
            assert replaced.sources[0].source_id == "src-2"
            original = await records.get_artifact_revision("scope-a", "memory", created.artifact_id, 1)
            assert original.sources == created.sources
            async with profile.database.transaction() as connection:
                replacement_source = await sources.get(connection, "scope-a", replaced.sources[0])
            assert isinstance(replacement_source.value, ContentSource)
            assert replacement_source.value.internal is not None
            assert replacement_source.value.internal.operation == "artifact_replace"
            assert replacement_source.value.internal.target.revision == 2

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
        async with SQLiteProfile.open(SQLiteConfig(), tables=SHARED_TABLES) as profile:
            records, _, _ = _services(profile, iter(("handoff-1", "src-1", "src-2")))
            created = await records.create_artifact(
                "scope-a",
                "handoff",
                ArtifactWrite(content=_handoff_content()),
            )

            loaded = await records.get_artifact("scope-a", "handoff", created.artifact_id)
            assert loaded.content == _handoff_content()

            replacement = _handoff_content("Transfer the verified API test result.")
            replaced = await records.replace_artifact(
                "scope-a",
                "handoff",
                created.artifact_id,
                '"revision:1"',
                ArtifactWrite(content=replacement),
            )
            assert replaced.revision == 2
            assert replaced.content == replacement

    asyncio.run(scenario())


def test_artifact_list_batches_revision_and_lineage_reads() -> None:
    async def scenario() -> None:
        async with SQLiteProfile.open(SQLiteConfig(), tables=SHARED_TABLES) as profile:
            records, _, _ = _services(
                profile,
                iter(("mem-1", "src-1", "mem-2", "src-2", "mem-3", "src-3")),
            )
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


def test_base_access_reuses_existing_tables_without_lifecycle_columns() -> None:
    assert "created_at" not in SOURCES_TABLE.c
    assert "created_at" not in ARTIFACTS_TABLE.c
    assert "deleted_at" not in ARTIFACT_HEADS_TABLE.c
    assert ArtifactRef(family="memory", artifact_id="memory-1", revision=1).revision == 1
