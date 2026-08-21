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
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.persistence.artifacts import ArtifactRepository
from powercontext.builtin.persistence.tables import (
    ARTIFACT_HEADS_TABLE,
)
from powercontext.errors import RevisionConflictError
from powercontext.sources import SourceMaterialization, SourceRef
from tests.builtin.persistence.contract import (
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


def test_lifecycle_committed_after_the_head_read_conflicts(monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario() -> None:
        async with (
            repository_profile() as (profile, repositories),
            profile.database.transaction() as connection,
        ):
            winner = await repositories.artifacts.create(
                connection,
                "scope-a",
                "handoff-1",
                HandoffDraft(content=HandoffContent(summary="winner")),
            )
            committed_head = ArtifactRepository._find_head
            reads = 0

            async def stale_first_read(
                repository: ArtifactRepository,
                connection: AsyncConnection,
                scope_id: str,
                family: str,
                artifact_id: str,
            ) -> int | None:
                nonlocal reads
                reads += 1
                # The first read models the window before the winner committed.
                if reads == 1:
                    return None
                return await committed_head(repository, connection, scope_id, family, artifact_id)

            monkeypatch.setattr(ArtifactRepository, "_find_head", stale_first_read)
            draft = HandoffDraft(content=HandoffContent(summary="loser"))
            with pytest.raises(RevisionConflictError) as error:
                await repositories.artifacts.create(connection, "scope-a", "handoff-1", draft)

            assert error.value.artifact == draft
            assert error.value.current == winner

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
