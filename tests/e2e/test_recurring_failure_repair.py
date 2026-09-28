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

"""Cross-component acceptance for recurring failure repair.

These scenarios follow one backlog item through several windows, so they stay
valid if the internals are rewritten: they only assert what an operator can
observe in the ledger, in statistics, and in the Review Inbox.
"""

from __future__ import annotations

import asyncio

from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.experience import Experience
from powercontext.builtin.artifacts.experience.models import (
    ExperienceContent,
    FailureRecord,
    FailureSignature,
    FailureVerification,
    RepairSurface,
)
from powercontext.builtin.artifacts.handoff import Handoff
from powercontext.builtin.artifacts.handoff.models import (
    HandoffArtifactCitation,
    HandoffContent,
    HandoffStatement,
)
from powercontext.builtin.evidence.resolver import EvidenceResolver
from powercontext.builtin.persistence import RecurrenceRepository
from powercontext.builtin.persistence.artifacts import ArtifactRepository
from powercontext.builtin.persistence.sources import SourceRepository
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.persistence.tables import (
    RECURRENCE_TABLES,
    SCOPE_TABLES,
    SCOPES_TABLE,
    SHARED_TABLES,
)
from powercontext.builtin.runtime.recurrence import RelationalRecurrenceLedger
from powercontext.builtin.sources.content import CONTENT_SOURCE_ADAPTER, ContentSource
from powercontext.builtin.work.models import HandoffReceipt, TaskCheck, TaskOutcome, WorkClaim
from powercontext.sources import SourceMaterialization, SourceRef

SCOPE = "scope-a"
CUE = "pytest fails because the port is already in use"
CONDITION = "the test suite selected a fixed port"
CHECK_SUBJECT = "the test suite binds an ephemeral port"

EXPERIENCE_ID = "experience-1"
EXPERIENCE_REF = ArtifactRef(family="experience", artifact_id=EXPERIENCE_ID, revision=1)
HANDOFF_REF = ArtifactRef(family="handoff", artifact_id="handoff-1", revision=1)
RECEIPT_REF = SourceRef(source_type="content", source_id="receipt-1")
CITATION = HandoffArtifactCitation(artifact_ref=EXPERIENCE_REF)
TABLES = SCOPE_TABLES + SHARED_TABLES + RECURRENCE_TABLES


def _content(*, repair_surface: RepairSurface) -> ExperienceContent:
    return ExperienceContent(
        situation="pytest bound a fixed port that a stale process still held.",
        action="Selected an ephemeral port instead.",
        outcome="The suite became repeatable.",
        lesson="Never rely on a fixed port in the integration suite.",
        failure=FailureRecord(
            signature=FailureSignature(recall_cue=CUE, symptom="connection refused on port 8000"),
            repair_surface=repair_surface,
            verification=FailureVerification(condition=CONDITION, check_subject=CHECK_SUBJECT),
        ),
    )


def _recurring_failure() -> TaskOutcome:
    """A recurrence whose failure item names the recalled cue exactly."""

    return TaskOutcome(
        objective="Run the integration suite.",
        status="failed",
        summary="pytest could not bind its port.",
        observations=(WorkClaim(text="the port was already bound", basis="declared"),),
        checks=(TaskCheck(name=CUE, status="failed", basis="verified", evidence=(CITATION,)),),
    )


async def _seed(
    connection: AsyncConnection,
    sources: SourceRepository,
    artifacts: ArtifactRepository,
    surface: RepairSurface,
) -> None:
    await connection.execute(
        insert(SCOPES_TABLE).values(
            scope_id=SCOPE,
            title="Scope A",
            summary="Recurring failure acceptance scope.",
            scope_id_search=SCOPE,
            title_search="scope a",
            summary_search="recurring failure acceptance scope",
            version=1,
        )
    )
    await artifacts.create(
        connection,
        SCOPE,
        EXPERIENCE_ID,
        artifacts.draft(Experience.family, _content(repair_surface=surface).model_dump(mode="json")),
    )
    await artifacts.create(
        connection,
        SCOPE,
        HANDOFF_REF.artifact_id,
        artifacts.draft(
            Handoff.family,
            HandoffContent(
                objective="Run the integration suite reliably.",
                state=(
                    HandoffStatement(
                        text="The suite still binds a fixed port.",
                        citations=(CITATION,),
                    ),
                ),
                disposition="continuable",
            ).model_dump(mode="json"),
        ),
    )


async def _add_failure(sources: SourceRepository, connection: AsyncConnection, name: str, /) -> None:
    await sources.add(
        connection,
        SCOPE,
        ContentSource(
            name=name,
            materialization=SourceMaterialization.CAPTURED,
            content=_recurring_failure().model_dump_json(),
            metadata={"kind": "task-outcome"},
        ),
    )


async def _record(sources: SourceRepository, ledger: RelationalRecurrenceLedger, after: int, /):
    async with ledger.database.transaction() as connection:
        through = await sources.journal_position(connection, SCOPE)
        rows = await sources.list_window(connection, SCOPE, after=after, through=through)
        return through, await ledger.record_window(connection, rows)


async def _missing_memory(*_args, **_kwargs):
    raise AssertionError("recurring failure e2e does not resolve memory citations")  # noqa: TRY003


def _ledger(database, sources, artifacts, repository) -> RelationalRecurrenceLedger:
    return RelationalRecurrenceLedger(
        database=database,
        scope_id=SCOPE,
        sources=sources,
        artifacts=artifacts,
        recurrence=repository,
        evidence=EvidenceResolver(
            scope_id=SCOPE,
            sources=sources,
            artifacts=artifacts,
            memory_reader=_missing_memory,
        ),
    )


def test_a_third_recurrence_asks_for_review_when_the_fix_is_content() -> None:
    """Three unbroken recurrences surface a revision candidate; human decides."""

    async def scenario() -> None:
        async with SQLiteProfile.open(SQLiteConfig(), tables=TABLES) as profile:
            sources = SourceRepository((CONTENT_SOURCE_ADAPTER,))
            artifacts = ArtifactRepository((Handoff, Experience), sources=sources)
            repository = RecurrenceRepository()
            ledger = _ledger(profile.database, sources, artifacts, repository)
            async with profile.database.transaction() as connection:
                await _seed(connection, sources, artifacts, "experience_content")

            cursor = 0
            proposals: tuple[object, ...] = ()
            for name in ("failure-1", "failure-2", "failure-3"):
                async with profile.database.transaction() as connection:
                    await _add_failure(sources, connection, name)
                cursor, proposals = await _record(sources, ledger, cursor)

            assert len(proposals) == 1
            proposal = proposals[0]
            assert proposal.target == EXPERIENCE_REF
            assert "recurred 3 times" in proposal.reason

    asyncio.run(scenario())


def test_a_recall_policy_repair_proposes_no_artifact_change() -> None:
    """When retrieval is the broken layer, the ledger records but never proposes."""

    async def scenario() -> None:
        async with SQLiteProfile.open(SQLiteConfig(), tables=TABLES) as profile:
            sources = SourceRepository((CONTENT_SOURCE_ADAPTER,))
            artifacts = ArtifactRepository((Handoff, Experience), sources=sources)
            repository = RecurrenceRepository()
            ledger = _ledger(profile.database, sources, artifacts, repository)
            async with profile.database.transaction() as connection:
                await _seed(connection, sources, artifacts, "recall_policy")

            cursor = 0
            proposals: tuple[object, ...] = ()
            for name in ("failure-1", "failure-2", "failure-3"):
                async with profile.database.transaction() as connection:
                    await _add_failure(sources, connection, name)
                cursor, proposals = await _record(sources, ledger, cursor)

            assert proposals == ()

            async with profile.database.transaction() as connection:
                stored = await repository.observations(connection, SCOPE)

            assert tuple(row.event for row in stored) == ("recurred",) * 3

    asyncio.run(scenario())


def test_a_streak_never_crosses_a_revision_boundary() -> None:
    """Publishing revision two freezes history: older matches stay untouched."""

    async def scenario() -> None:
        async with SQLiteProfile.open(SQLiteConfig(), tables=TABLES) as profile:
            sources = SourceRepository((CONTENT_SOURCE_ADAPTER,))
            artifacts = ArtifactRepository((Handoff, Experience), sources=sources)
            repository = RecurrenceRepository()
            ledger = _ledger(profile.database, sources, artifacts, repository)
            async with profile.database.transaction() as connection:
                await _seed(connection, sources, artifacts, "experience_content")

            async with profile.database.transaction() as connection:
                await _add_failure(sources, connection, "failure-1")
            cursor, _ = await _record(sources, ledger, 0)

            async with profile.database.transaction() as connection:
                head = await artifacts.get(connection, SCOPE, EXPERIENCE_REF)
                next_draft = artifacts.draft(
                    Experience.family,
                    _content(repair_surface="experience_content").model_dump(mode="json"),
                )
                revised = await artifacts.revise(connection, SCOPE, head, next_draft)

            async with profile.database.transaction() as connection:
                await _add_failure(sources, connection, "failure-2")
            _, _ = await _record(sources, ledger, cursor)

            async with profile.database.transaction() as connection:
                stored = await repository.observations(connection, SCOPE)
                matches = await repository.matches(connection, SCOPE)

            assert revised.revision == 2
            # Each window froze its own head, so the earlier match still targets
            # revision one and the newer one targets revision two: the streak is
            # split rather than moved onto whichever revision is current.
            assert sorted(match.artifact_ref.revision for match in matches if match.artifact_ref is not None) == [1, 2]
            assert sorted(row.artifact_ref.revision for row in stored) == [1, 2]

    asyncio.run(scenario())


def test_an_accepted_handoff_with_an_outcome_records_selection() -> None:
    """``selected`` is rebuilt from the Receipt chain, never from the read path."""

    async def scenario() -> None:
        async with SQLiteProfile.open(SQLiteConfig(), tables=TABLES) as profile:
            sources = SourceRepository((CONTENT_SOURCE_ADAPTER,))
            artifacts = ArtifactRepository((Handoff, Experience), sources=sources)
            repository = RecurrenceRepository()
            ledger = _ledger(profile.database, sources, artifacts, repository)
            async with profile.database.transaction() as connection:
                await _seed(connection, sources, artifacts, "experience_content")
                await sources.add(
                    connection,
                    SCOPE,
                    ContentSource(
                        name=RECEIPT_REF.source_id,
                        materialization=SourceMaterialization.CAPTURED,
                        content=HandoffReceipt(
                            receiver="agent-b",
                            status="accepted",
                            selection="exact",
                            selected_revision=HANDOFF_REF,
                            evidence_status="available",
                        ).model_dump_json(),
                        metadata={"kind": "handoff-receipt"},
                    ),
                )

            async with profile.database.transaction() as connection:
                outcome = TaskOutcome(
                    objective="Run the integration suite.",
                    status="succeeded",
                    summary="Suite passed on an ephemeral port.",
                    handoff_receipt_ref=RECEIPT_REF,
                    observations=(WorkClaim(text=CONDITION, basis="verified", evidence=(CITATION,)),),
                    checks=(TaskCheck(name=CHECK_SUBJECT, status="passed", basis="verified", evidence=(CITATION,)),),
                )
                await sources.add(
                    connection,
                    SCOPE,
                    ContentSource(
                        name="outcome-pass",
                        materialization=SourceMaterialization.CAPTURED,
                        content=outcome.model_dump_json(),
                        metadata={"kind": "task-outcome"},
                    ),
                )

            await _record(sources, ledger, 1)

            async with profile.database.transaction() as connection:
                stored = await repository.observations(connection, SCOPE)
                events = tuple(row.event for row in stored)

            assert "selected" in events
            assert "avoided" in events

    asyncio.run(scenario())
