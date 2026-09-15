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

"""Write-path behaviour of the recurrence ledger.

These tests cover the end of the pipeline that only the Task Outcome incubation
window may invoke. They deliberately include one regression assertion against
the read path: preparing context must never add a ledger row.
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
from powercontext.builtin.work.models import (
    HandoffReceipt,
    TaskCheck,
    TaskOutcome,
    TaskOutcomeStatus,
    WorkClaim,
)
from powercontext.sources import SourceMaterialization, SourceRef

SCOPE = "scope-a"
CUE = "openapi contract changed without regenerating the client"
CONDITION = "the contract file was edited"
CHECK_SUBJECT = "generated code matches contract"

EXPERIENCE_REF = ArtifactRef(family="experience", artifact_id="experience-1", revision=1)
HANDOFF_REF = ArtifactRef(family="handoff", artifact_id="handoff-1", revision=1)
RECEIPT_REF = SourceRef(source_type="content", source_id="receipt-1")

CITATION = HandoffArtifactCitation(artifact_ref=EXPERIENCE_REF)


def _experience(*, repair_surface: RepairSurface = "experience_content") -> ExperienceContent:
    return ExperienceContent(
        situation="openapi/powercontext.yaml changed.",
        action="Regenerated the client before committing.",
        outcome="The API contract and the generated code stayed in sync.",
        lesson="Always rerun make api-generate after editing the contract.",
        failure=FailureRecord(
            signature=FailureSignature(recall_cue=CUE, symptom="generated client diverges from the contract"),
            repair_surface=repair_surface,
            verification=FailureVerification(condition=CONDITION, check_subject=CHECK_SUBJECT),
        ),
    )


def _handoff_content() -> HandoffContent:
    return HandoffContent(
        objective="Keep the contract and the generated client in sync.",
        state=(
            HandoffStatement(
                text="The contract was edited but the client was not regenerated.",
                citations=(CITATION,),
            ),
        ),
        disposition="continuable",
    )


def _receipt() -> HandoffReceipt:
    return HandoffReceipt(
        receiver="agent-b",
        status="accepted",
        selection="exact",
        selected_revision=HANDOFF_REF,
        evidence_status="available",
    )


def _outcome(
    *,
    status: TaskOutcomeStatus,
    observations: tuple[WorkClaim, ...],
    checks: tuple[TaskCheck, ...],
    linked: bool = True,
) -> TaskOutcome:
    return TaskOutcome(
        objective="Keep the contract and the generated client in sync.",
        status=status,
        summary="Attempt completed.",
        handoff_receipt_ref=RECEIPT_REF if linked else None,
        observations=observations,
        checks=checks,
    )


def _passed_outcome() -> TaskOutcome:
    """A task whose verdict is fully proven: the condition held and the check passed."""

    return _outcome(
        status="succeeded",
        observations=(WorkClaim(text=CONDITION, basis="verified", evidence=(CITATION,)),),
        checks=(TaskCheck(name=CHECK_SUBJECT, status="passed", basis="verified", evidence=(CITATION,)),),
    )


def _unknown_outcome() -> TaskOutcome:
    """A task that proves nothing: the bound check never ran."""

    return _outcome(
        status="succeeded",
        observations=(WorkClaim(text=CONDITION, basis="declared"),),
        checks=(TaskCheck(name=CHECK_SUBJECT, status="skipped", basis="declared"),),
    )


def _failed_outcome() -> TaskOutcome:
    """A recurrence: the failure item names the recalled cue exactly."""

    return _outcome(
        status="failed",
        observations=(WorkClaim(text="pytest failed because the port was busy", basis="declared"),),
        checks=(TaskCheck(name=CUE, status="failed", basis="verified", evidence=(CITATION,)),),
    )


async def _seed_scope(connection: AsyncConnection) -> None:
    await connection.execute(
        insert(SCOPES_TABLE).values(
            scope_id=SCOPE,
            title="Scope A",
            summary="Recurrence ledger write-path fixture scope.",
            scope_id_search=SCOPE,
            title_search="scope a",
            summary_search="recurrence ledger write-path fixture scope",
            version=1,
        )
    )


async def _seed_artifacts(connection: AsyncConnection, sources: SourceRepository, /) -> ArtifactRepository:
    artifacts = ArtifactRepository((Handoff, Experience), sources=sources)
    await artifacts.create(
        connection,
        SCOPE,
        EXPERIENCE_REF.artifact_id,
        artifacts.draft(Experience.family, _experience().model_dump(mode="json")),
    )
    await artifacts.create(
        connection,
        SCOPE,
        HANDOFF_REF.artifact_id,
        artifacts.draft(Handoff.family, _handoff_content().model_dump(mode="json")),
    )
    return artifacts


async def _add_source(sources: SourceRepository, connection: AsyncConnection, name: str, content: str, /) -> None:
    await sources.add(
        connection,
        SCOPE,
        ContentSource(
            name=name,
            materialization=SourceMaterialization.CAPTURED,
            content=content,
            metadata={"kind": "task-outcome"},
        ),
    )


async def _add_receipt(sources: SourceRepository, connection: AsyncConnection, /) -> None:
    await sources.add(
        connection,
        SCOPE,
        ContentSource(
            name=RECEIPT_REF.source_id,
            materialization=SourceMaterialization.CAPTURED,
            content=_receipt().model_dump_json(),
            metadata={"kind": "handoff-receipt"},
        ),
    )


async def _window(
    sources: SourceRepository,
    connection: AsyncConnection,
    /,
    *,
    after: int = 0,
):
    through = await sources.journal_position(connection, SCOPE)
    return await sources.list_window(connection, SCOPE, after=after, through=through)


def test_fully_proven_verdict_is_recorded_as_avoided() -> None:
    async def scenario() -> None:
        async with SQLiteProfile.open(
            SQLiteConfig(), tables=SCOPE_TABLES + SHARED_TABLES + RECURRENCE_TABLES
        ) as profile:
            sources = SourceRepository((CONTENT_SOURCE_ADAPTER,))
            repository = RecurrenceRepository()
            async with profile.database.transaction() as connection:
                await _seed_scope(connection)
                artifacts = await _seed_artifacts(connection, sources)
                await _add_receipt(sources, connection)
                await _add_source(sources, connection, "outcome-pass", _passed_outcome().model_dump_json())

            async with profile.database.transaction() as connection:
                rows = await _window(sources, connection, after=1)
                ledger = RelationalRecurrenceLedger(
                    database=profile.database,
                    scope_id=SCOPE,
                    sources=sources,
                    artifacts=artifacts,
                    recurrence=repository,
                )
                proposals = await ledger.record_window(connection, rows)

            async with profile.database.transaction() as connection:
                events = tuple(row.event for row in await repository.observations(connection, SCOPE))

            assert proposals == ()
            assert "selected" in events
            assert "avoided" in events
            assert "recurred" not in events

    asyncio.run(scenario())


def test_unproven_outcome_records_no_verdict() -> None:
    """A completed task writes nothing on its own: missing evidence stays unknown."""

    async def scenario() -> None:
        async with SQLiteProfile.open(
            SQLiteConfig(), tables=SCOPE_TABLES + SHARED_TABLES + RECURRENCE_TABLES
        ) as profile:
            sources = SourceRepository((CONTENT_SOURCE_ADAPTER,))
            repository = RecurrenceRepository()
            async with profile.database.transaction() as connection:
                await _seed_scope(connection)
                artifacts = await _seed_artifacts(connection, sources)
                await _add_receipt(sources, connection)
                await _add_source(sources, connection, "outcome-unknown", _unknown_outcome().model_dump_json())

            async with profile.database.transaction() as connection:
                rows = await _window(sources, connection, after=1)
                ledger = RelationalRecurrenceLedger(
                    database=profile.database,
                    scope_id=SCOPE,
                    sources=sources,
                    artifacts=artifacts,
                    recurrence=repository,
                )
                await ledger.record_window(connection, rows)

            async with profile.database.transaction() as connection:
                rows = await repository.observations(connection, SCOPE)
                events = tuple(row.event for row in rows)

            assert events == ("selected",)
            assert "unknown" not in events

    asyncio.run(scenario())


def test_a_recurring_failure_is_recorded_as_recurred_not_avoided() -> None:
    async def scenario() -> None:
        async with SQLiteProfile.open(
            SQLiteConfig(), tables=SCOPE_TABLES + SHARED_TABLES + RECURRENCE_TABLES
        ) as profile:
            sources = SourceRepository((CONTENT_SOURCE_ADAPTER,))
            repository = RecurrenceRepository()
            async with profile.database.transaction() as connection:
                await _seed_scope(connection)
                artifacts = await _seed_artifacts(connection, sources)
                await _add_receipt(sources, connection)
                await _add_source(sources, connection, "outcome-recurred", _failed_outcome().model_dump_json())

            async with profile.database.transaction() as connection:
                rows = await _window(sources, connection, after=1)
                ledger = RelationalRecurrenceLedger(
                    database=profile.database,
                    scope_id=SCOPE,
                    sources=sources,
                    artifacts=artifacts,
                    recurrence=repository,
                )
                await ledger.record_window(connection, rows)

            async with profile.database.transaction() as connection:
                stored = await repository.observations(connection, SCOPE)
                events = tuple(row.event for row in stored)
                matches = await repository.matches(connection, SCOPE)

            assert "recurred" in events
            assert "avoided" not in events
            assert len(matches) == 1
            assert matches[0].result == "matched"
            assert matches[0].candidate_set_mode == "handoff_citations"

    asyncio.run(scenario())


def test_replaying_one_window_adds_no_duplicate_rows() -> None:
    async def scenario() -> None:
        async with SQLiteProfile.open(
            SQLiteConfig(), tables=SCOPE_TABLES + SHARED_TABLES + RECURRENCE_TABLES
        ) as profile:
            sources = SourceRepository((CONTENT_SOURCE_ADAPTER,))
            repository = RecurrenceRepository()
            async with profile.database.transaction() as connection:
                await _seed_scope(connection)
                artifacts = await _seed_artifacts(connection, sources)
                await _add_receipt(sources, connection)
                await _add_source(sources, connection, "outcome-recurred", _failed_outcome().model_dump_json())

            async with profile.database.transaction() as connection:
                rows = await _window(sources, connection, after=1)
                ledger = RelationalRecurrenceLedger(
                    database=profile.database,
                    scope_id=SCOPE,
                    sources=sources,
                    artifacts=artifacts,
                    recurrence=repository,
                )
                first = await ledger.record_window(connection, rows)
                second = await ledger.record_window(connection, rows)

            async with profile.database.transaction() as connection:
                stored = await repository.observations(connection, SCOPE)
                matches = await repository.matches(connection, SCOPE)

            assert first == second
            assert len(stored) == len({row.observation_id for row in stored})
            assert len(matches) == 1

    asyncio.run(scenario())


def test_unlinked_failure_window_still_records_recurrence() -> None:
    """Without a Receipt there is no selection chain, but a failure still counts."""

    async def scenario() -> None:
        async with SQLiteProfile.open(
            SQLiteConfig(), tables=SCOPE_TABLES + SHARED_TABLES + RECURRENCE_TABLES
        ) as profile:
            sources = SourceRepository((CONTENT_SOURCE_ADAPTER,))
            repository = RecurrenceRepository()
            async with profile.database.transaction() as connection:
                await _seed_scope(connection)
                artifacts = await _seed_artifacts(connection, sources)
                await _add_source(
                    sources,
                    connection,
                    "outcome-unlinked",
                    _outcome(
                        status="failed",
                        observations=_failed_outcome().observations,
                        checks=_failed_outcome().checks,
                        linked=False,
                    ).model_dump_json(),
                )

            async with profile.database.transaction() as connection:
                rows = await _window(sources, connection)
                ledger = RelationalRecurrenceLedger(
                    database=profile.database,
                    scope_id=SCOPE,
                    sources=sources,
                    artifacts=artifacts,
                    recurrence=repository,
                )
                await ledger.record_window(connection, rows)

            async with profile.database.transaction() as connection:
                stored = await repository.observations(connection, SCOPE)
                events = tuple(row.event for row in stored)
                matches = await repository.matches(connection, SCOPE)

            assert events == ("recurred",)
            assert matches[0].candidate_set_mode == "scope_heads"

    asyncio.run(scenario())


def test_unresolvable_receipt_does_not_fallback_to_scope_heads() -> None:
    async def scenario() -> None:
        async with SQLiteProfile.open(
            SQLiteConfig(), tables=SCOPE_TABLES + SHARED_TABLES + RECURRENCE_TABLES
        ) as profile:
            sources = SourceRepository((CONTENT_SOURCE_ADAPTER,))
            repository = RecurrenceRepository()
            async with profile.database.transaction() as connection:
                await _seed_scope(connection)
                artifacts = await _seed_artifacts(connection, sources)
                outcome = _failed_outcome().model_copy(update={"handoff_receipt_ref": RECEIPT_REF})
                await _add_source(sources, connection, "outcome-unresolved-receipt", outcome.model_dump_json())

            async with profile.database.transaction() as connection:
                rows = await _window(sources, connection)
                ledger = RelationalRecurrenceLedger(
                    database=profile.database,
                    scope_id=SCOPE,
                    sources=sources,
                    artifacts=artifacts,
                    recurrence=repository,
                )
                await ledger.record_window(connection, rows)
                matches = await repository.matches(connection, SCOPE)

            assert matches == ()

    asyncio.run(scenario())


def test_receipt_pointing_to_missing_handoff_does_not_fallback_to_scope_heads() -> None:
    async def scenario() -> None:
        async with SQLiteProfile.open(
            SQLiteConfig(), tables=SCOPE_TABLES + SHARED_TABLES + RECURRENCE_TABLES
        ) as profile:
            sources = SourceRepository((CONTENT_SOURCE_ADAPTER,))
            repository = RecurrenceRepository()
            async with profile.database.transaction() as connection:
                await _seed_scope(connection)
                artifacts = await _seed_artifacts(connection, sources)
                missing_handoff = ArtifactRef(family=Handoff.family, artifact_id="missing-handoff", revision=1)
                await _add_source(
                    sources,
                    connection,
                    RECEIPT_REF.source_id,
                    HandoffReceipt(
                        receiver="agent-b",
                        status="accepted",
                        selection="exact",
                        selected_revision=missing_handoff,
                        evidence_status="available",
                    ).model_dump_json(),
                )
                outcome = _failed_outcome()
                await _add_source(sources, connection, "outcome-missing-handoff", outcome.model_dump_json())

            async with profile.database.transaction() as connection:
                rows = await _window(sources, connection)
                ledger = RelationalRecurrenceLedger(
                    database=profile.database,
                    scope_id=SCOPE,
                    sources=sources,
                    artifacts=artifacts,
                    recurrence=repository,
                )
                await ledger.record_window(connection, rows)
                assert await repository.matches(connection, SCOPE) == ()
                assert await repository.observations(connection, SCOPE) == ()

    asyncio.run(scenario())


def test_the_ledger_module_performs_no_read_path_instrumentation() -> None:
    """The write path must stay out of retrieval: only this module may append."""

    from pathlib import Path

    source = Path("src/powercontext/builtin/artifacts/experience/recurrence.py").read_text(encoding="utf-8")
    for forbidden in ("sqlalchemy", "AsyncConnection", "Repository"):
        assert forbidden not in source, forbidden
