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
from sqlalchemy import insert
from sqlalchemy.exc import IntegrityError

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.topic_memory import TopicMemory, TopicMemoryContent, TopicMemoryDraft
from powercontext.builtin.persistence.artifacts import ArtifactRepository
from powercontext.builtin.persistence.tables import (
    ARTIFACT_HEADS_TABLE,
)
from powercontext.builtin.runtime.relational import RelationalContexts
from powercontext.errors import InvalidSourceReferenceError, RevisionConflictError
from powercontext.sources import SourceDefinitionRegistry, SourceMaterialization, SourceRef
from tests.builtin.persistence.contract import (
    SOURCE_ADAPTERS,
    Handoff,
    HandoffContent,
    HandoffDraft,
    NoteSource,
    Report,
    ReportContent,
    ReportDraft,
    repository_profile,
)


def test_two_artifact_families_share_revisions_and_ordered_direct_lineage() -> None:
    async def scenario() -> None:
        async with repository_profile() as (profile, repositories):
            first_source = NoteSource(
                name="note-1",
                materialization=SourceMaterialization.CAPTURED,
                body="first",
            )
            second_source = NoteSource(
                name="note-2",
                materialization=SourceMaterialization.CAPTURED,
                body="second",
            )
            async with profile.database.transaction() as connection:
                first = await repositories.sources.add(connection, "scope-a", first_source)
                second = await repositories.sources.add(connection, "scope-a", second_source)
                report = await repositories.artifacts.create(
                    connection,
                    "scope-a",
                    "report-1",
                    ReportDraft(content=ReportContent(status="green")),
                )
                handoff = await repositories.artifacts.create(
                    connection,
                    "scope-a",
                    "handoff-1",
                    HandoffDraft(
                        content=HandoffContent(summary="Ready"),
                        sources=(second.ref, first.ref),
                        artifacts=(report.as_ref(),),
                    ),
                )
                revised = await repositories.artifacts.revise(
                    connection,
                    "scope-a",
                    handoff,
                    HandoffDraft(
                        content=HandoffContent(summary="Ready for review"),
                        sources=(first.ref, second.ref),
                        artifacts=(report.as_ref(),),
                    ),
                )

                assert type(report) is Report
                assert type(revised) is Handoff
                assert revised.as_ref() == ArtifactRef(family="handoff", artifact_id="handoff-1", revision=2)
                assert revised.lineage.sources == (first.ref, second.ref)
                assert revised.lineage.artifacts == (report.as_ref(),)
                assert await repositories.artifacts.revisions(
                    connection,
                    "scope-a",
                    "handoff",
                    "handoff-1",
                ) == (handoff, revised)

    asyncio.run(scenario())


def test_bulk_create_preserves_existing_artifact_lineage() -> None:
    async def scenario() -> None:
        async with repository_profile() as (profile, repositories), profile.database.transaction() as connection:
            source = await repositories.sources.add(
                connection,
                "scope-a",
                NoteSource(name="note-bulk", materialization=SourceMaterialization.CAPTURED, body="Evidence"),
            )
            report = await repositories.artifacts.create(
                connection,
                "scope-a",
                "report-existing",
                ReportDraft(content=ReportContent(status="green")),
            )
            items = []
            for index in range(2):
                artifact_id = f"handoff-bulk-{index}"
                draft = HandoffDraft(
                    content=HandoffContent(summary=f"Ready {index}"),
                    sources=(source.ref,),
                    artifacts=(report.as_ref(),),
                )
                items.append((artifact_id, draft, None))
            created = await repositories.artifacts.create_many(connection, "scope-a", items)
            loaded = await repositories.artifacts.get_many(
                connection, "scope-a", tuple(artifact.as_ref() for artifact in created)
            )
            assert all(artifact.lineage.sources == (source.ref,) for artifact in loaded)
            assert all(artifact.lineage.artifacts == (report.as_ref(),) for artifact in loaded)

    asyncio.run(scenario())


def test_source_citation_positions_survive_separate_transactions() -> None:
    async def scenario() -> None:
        async with repository_profile() as (profile, repositories):
            async with profile.database.transaction() as connection:
                stored = await repositories.sources.add(
                    connection,
                    "scope-a",
                    NoteSource(name="positioned", materialization=SourceMaterialization.CAPTURED, body="证据"),
                )
                refs = tuple(
                    SourceRef.model_validate({
                        **stored.ref.model_dump(),
                        "locator": locator,
                    })
                    for locator in (
                        {"kind": "sequence", "sequence": stored.journal_position},
                        {"kind": "line_range", "start_line": 40, "end_line": 55, "heading_path": "设计"},
                        {"kind": "line_range", "start_line": 60, "end_line": 65},
                    )
                )
                await repositories.artifacts.create(
                    connection,
                    "scope-a",
                    "positioned-report",
                    ReportDraft(content=ReportContent(status="green"), sources=(*refs, stored.ref)),
                )
            async with profile.database.transaction() as connection:
                loaded = await repositories.artifacts.revisions(connection, "scope-a", "report", "positioned-report")
                assert loaded[0].lineage.sources == (*refs, stored.ref)
                assert loaded[0].lineage.sources[-1].model_dump() == stored.ref.model_dump()

    asyncio.run(scenario())


def test_source_locator_cannot_redirect_to_another_journal_entry() -> None:
    async def scenario() -> None:
        async with repository_profile() as (profile, repositories), profile.database.transaction() as connection:
            stored = await repositories.sources.add(
                connection,
                "scope-a",
                NoteSource(name="first", materialization=SourceMaterialization.CAPTURED, body="first fact"),
            )
            other = await repositories.sources.add(
                connection,
                "scope-a",
                NoteSource(name="second", materialization=SourceMaterialization.CAPTURED, body="unrelated fact"),
            )
            wrong = SourceRef.model_validate({
                **stored.ref.model_dump(),
                "locator": {"kind": "sequence", "sequence": other.journal_position},
            })
            with pytest.raises(InvalidSourceReferenceError, match=r"locator\.sequence"):
                await ArtifactRepository((Report,), sources=repositories.sources).create(
                    connection,
                    "scope-a",
                    "wrong-position",
                    ReportDraft(content=ReportContent(status="green"), sources=(wrong,)),
                )
            assert await repositories.artifacts.revisions(connection, "scope-a", "report", "wrong-position") == ()

    asyncio.run(scenario())


def test_relational_contexts_persist_topic_memory_revisions_and_exact_lineage() -> None:
    async def scenario() -> None:
        async with repository_profile() as (profile, repositories):
            contexts = RelationalContexts(
                database=profile.database,
                source_registry=SourceDefinitionRegistry.from_adapters(SOURCE_ADAPTERS),
            )
            source = NoteSource(
                name="note-1",
                materialization=SourceMaterialization.CAPTURED,
                body="Supervisor recovery evidence",
            )
            async with profile.database.transaction() as connection:
                stored_source = await repositories.sources.add(connection, "scope-a", source)
                original = await contexts.repositories.artifacts.create(
                    connection,
                    "scope-a",
                    "topic-1",
                    TopicMemoryDraft(
                        content=TopicMemoryContent(
                            title="Supervisor recovery",
                            summary="The new leader resumes persisted pending work.",
                            detail="The lease generation fences stale workers.",
                        ),
                        sources=(stored_source.ref,),
                    ),
                )
                revised = await contexts.repositories.artifacts.revise(
                    connection,
                    "scope-a",
                    original,
                    TopicMemoryDraft(
                        content=TopicMemoryContent(
                            title="Supervisor recovery",
                            summary="The new leader restores each binding's deadline.",
                            detail="Persisted completion time prevents an early automatic wave.",
                        ),
                        sources=(stored_source.ref,),
                        artifacts=(original.as_ref(),),
                    ),
                )

                assert type(original) is TopicMemory
                assert type(revised) is TopicMemory
                assert revised.revision == 2
                assert revised.lineage.sources == (stored_source.ref,)
                assert revised.lineage.artifacts == (original.as_ref(),)
                assert await contexts.repositories.artifacts.revisions(
                    connection,
                    "scope-a",
                    "topic-memory",
                    "topic-1",
                ) == (original, revised)

    asyncio.run(scenario())


def test_stale_artifact_revision_fails_optimistic_head_cas() -> None:
    async def scenario() -> None:
        async with (
            repository_profile() as (profile, repositories),
            profile.database.transaction() as connection,
        ):
            original = await repositories.artifacts.create(
                connection,
                "scope-a",
                "handoff-1",
                HandoffDraft(content=HandoffContent(summary="v1")),
            )
            current = await repositories.artifacts.revise(
                connection,
                "scope-a",
                original,
                HandoffDraft(content=HandoffContent(summary="v2")),
            )
            with pytest.raises(RevisionConflictError) as error:
                await repositories.artifacts.revise(
                    connection,
                    "scope-a",
                    original,
                    HandoffDraft(content=HandoffContent(summary="stale")),
                )

            assert error.value.artifact == original
            assert error.value.current == current

    asyncio.run(scenario())


def test_artifact_lineage_and_head_foreign_keys_are_enforced() -> None:
    async def scenario() -> None:
        async with repository_profile() as (profile, repositories):
            with pytest.raises(IntegrityError):
                async with profile.database.transaction() as connection:
                    await repositories.artifacts.create(
                        connection,
                        "scope-a",
                        "handoff-1",
                        HandoffDraft(
                            content=HandoffContent(summary="invalid"),
                            sources=(SourceRef(source_type="note", source_id="missing"),),
                        ),
                    )

            with pytest.raises(IntegrityError):
                async with profile.database.transaction() as connection:
                    await repositories.artifacts.create(
                        connection,
                        "scope-a",
                        "handoff-2",
                        HandoffDraft(
                            content=HandoffContent(summary="invalid"),
                            artifacts=(ArtifactRef(family="report", artifact_id="missing", revision=1),),
                        ),
                    )

            with pytest.raises(IntegrityError):
                async with profile.database.transaction() as connection:
                    await connection.execute(
                        insert(ARTIFACT_HEADS_TABLE).values(
                            scope_id="scope-a",
                            family="handoff",
                            artifact_id="missing",
                            revision=1,
                        )
                    )

    asyncio.run(scenario())
