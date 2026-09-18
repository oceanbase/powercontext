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
import re
from pathlib import Path

import pytest
from sqlalchemy import String, insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.experience.recurrence import (
    CandidateSetMode,
    MatchResult,
    RecurrenceEvent,
    RecurrenceMatch,
    RecurrenceObservation,
    RecurrenceObservationIdentity,
    TaskOutcomeItemKind,
    TaskOutcomeItemRef,
    canonical_digest,
    freeze_candidate_set,
    match_key,
    signature_key,
)
from powercontext.builtin.persistence import RecurrenceRepository
from powercontext.builtin.persistence.recurrence import RecurrenceRepository as SameRecurrenceRepository
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.persistence.tables import (
    RECURRENCE_MATCH_TABLE,
    RECURRENCE_TABLES,
    SCOPE_TABLES,
    SCOPES_TABLE,
    SHARED_TABLES,
)
from powercontext.sources import SourceRef

SCOPE = "scope-a"
OUTCOME_REF = SourceRef(source_type="task-outcome", source_id="outcome-1")
RECEIPT_REF = SourceRef(source_type="handoff-receipt", source_id="receipt-1")
HANDOFF_REF = ArtifactRef(family="handoff", artifact_id="handoff-1", revision=1)
EXPERIENCE_REF = ArtifactRef(family="experience", artifact_id="experience-1", revision=1)
OTHER_EXPERIENCE_REF = ArtifactRef(family="experience", artifact_id="experience-1", revision=2)
KEY = signature_key("openapi contract changed without regenerating the client")


def test_recurrence_match_mode_column_fits_every_declared_mode() -> None:
    column = RECURRENCE_MATCH_TABLE.c.candidate_set_mode

    assert isinstance(column.type, String)
    assert column.type.length is not None
    assert column.type.length >= max(len("handoff_citations"), len("scope_heads"))


async def _seed_scope(connection: AsyncConnection) -> None:
    """Create the parent scope row the ledger tables reference."""

    await connection.execute(
        insert(SCOPES_TABLE).values(
            scope_id=SCOPE,
            title="Scope A",
            summary="Recurrence ledger fixture scope.",
            scope_id_search=SCOPE,
            title_search="scope a",
            summary_search="recurrence ledger fixture scope",
            version=1,
        )
    )


def _item_ref(
    *, kind: TaskOutcomeItemKind = "check", index: int = 0, digest: str = "sha256:" + "a" * 64
) -> TaskOutcomeItemRef:
    return TaskOutcomeItemRef(
        task_outcome_ref=OUTCOME_REF,
        item_kind=kind,
        item_index=index,
        item_digest=digest,
    )


def _match(
    *,
    result: MatchResult = "matched",
    position: int = 1,
    failure_ref: TaskOutcomeItemRef | None = None,
    candidates: tuple[ArtifactRef, ...] = (EXPERIENCE_REF,),
    mode: CandidateSetMode = "handoff_citations",
    artifact_ref: ArtifactRef | None = EXPERIENCE_REF,
    key: str | None = KEY,
) -> RecurrenceMatch:
    from powercontext.builtin.artifacts.experience.recurrence import candidate_set_digest

    frozen = freeze_candidate_set(mode=mode, refs=candidates)  # type: ignore[arg-type]
    return RecurrenceMatch(
        scope_id=SCOPE,
        task_outcome_ref=OUTCOME_REF,
        task_outcome_position=position,
        failure_ref=failure_ref if failure_ref is not None else _item_ref(),
        candidate_set_mode=mode,
        candidate_refs=frozen,
        candidate_set_digest=candidate_set_digest(frozen),
        result=result,
        artifact_ref=artifact_ref if result == "matched" else None,
        signature_key=key if result == "matched" else None,
    )


def _observation(
    event: RecurrenceEvent,
    *,
    position: int = 1,
    artifact_ref: ArtifactRef = EXPERIENCE_REF,
    key: str = KEY,
    outcome_ref: SourceRef = OUTCOME_REF,
    handoff_receipt_ref: SourceRef | None = None,
    handoff_ref: ArtifactRef | None = None,
    condition_ref: TaskOutcomeItemRef | None = None,
    check_ref: TaskOutcomeItemRef | None = None,
    failure_ref: TaskOutcomeItemRef | None = None,
    match_digest: str | None = None,
) -> RecurrenceObservation:
    identity = RecurrenceObservationIdentity(
        event=event,
        scope_id=SCOPE,
        artifact_ref=artifact_ref,
        signature_key=key,
        task_outcome_ref=outcome_ref,
        task_outcome_position=position,
        handoff_receipt_ref=handoff_receipt_ref,
        handoff_ref=handoff_ref,
        condition_ref=condition_ref,
        check_ref=check_ref,
        failure_ref=failure_ref,
        recurrence_match_digest=match_digest,
    )
    return RecurrenceObservation(observation_id=canonical_digest(identity), **identity.model_dump())


def _selected(
    *, position: int = 1, artifact_ref: ArtifactRef = EXPERIENCE_REF, key: str = KEY
) -> RecurrenceObservation:
    return _observation(
        "selected",
        position=position,
        artifact_ref=artifact_ref,
        key=key,
        handoff_receipt_ref=RECEIPT_REF,
        handoff_ref=HANDOFF_REF,
    )


def _avoided(*, position: int = 1, artifact_ref: ArtifactRef = EXPERIENCE_REF, key: str = KEY) -> RecurrenceObservation:
    return _observation(
        "avoided",
        position=position,
        artifact_ref=artifact_ref,
        key=key,
        handoff_receipt_ref=RECEIPT_REF,
        handoff_ref=HANDOFF_REF,
        condition_ref=_item_ref(kind="observation", index=0, digest="sha256:" + "b" * 64),
        check_ref=_item_ref(kind="check", index=0, digest="sha256:" + "c" * 64),
    )


def _recurred(
    *,
    position: int = 1,
    artifact_ref: ArtifactRef = EXPERIENCE_REF,
    key: str = KEY,
    digest: str | None = None,
) -> RecurrenceObservation:
    failure_ref = _item_ref(kind="check", index=1, digest="sha256:" + "d" * 64)
    return _observation(
        "recurred",
        position=position,
        artifact_ref=artifact_ref,
        key=key,
        failure_ref=failure_ref,
        match_digest=digest if digest is not None else match_key(_match()),
    )


def test_repository_is_exported_from_the_persistence_package() -> None:
    assert RecurrenceRepository is SameRecurrenceRepository


def test_recurrence_tables_are_registered_in_builtin_tables() -> None:
    from powercontext.builtin.persistence.tables import BUILTIN_TABLES

    names = {table.name for table in BUILTIN_TABLES}
    assert {"pc_recurrence_match", "pc_recurrence_observation"} <= names


def test_repository_exposes_no_update_or_delete_statement() -> None:
    source = Path("src/powercontext/builtin/persistence/recurrence.py").read_text(encoding="utf-8")
    assert re.search(r"\bupdate\(", source) is None
    assert re.search(r"\bdelete\(", source) is None


def test_match_append_is_idempotent_under_replay() -> None:
    async def scenario() -> None:
        repository = RecurrenceRepository()
        async with SQLiteProfile.open(
            SQLiteConfig(), tables=SCOPE_TABLES + SHARED_TABLES + RECURRENCE_TABLES
        ) as profile:
            async with profile.database.transaction() as connection:
                await _seed_scope(connection)
                first = await repository.append_match(connection, _match())
            async with profile.database.transaction() as connection:
                second = await repository.append_match(connection, _match())
                stored = await repository.matches(connection, SCOPE)
                found = await repository.find_match(connection, SCOPE, OUTCOME_REF, _item_ref())

            assert first == second == match_key(_match())
            assert len(stored) == 1
            assert found is not None and found.result == "matched"

    asyncio.run(scenario())


def test_match_replay_returns_the_persisted_key_when_the_new_payload_differs() -> None:
    async def scenario() -> None:
        repository = RecurrenceRepository()
        async with SQLiteProfile.open(
            SQLiteConfig(), tables=SCOPE_TABLES + SHARED_TABLES + RECURRENCE_TABLES
        ) as profile:
            async with profile.database.transaction() as connection:
                await _seed_scope(connection)
                first = await repository.append_match(connection, _match())
                replay = await repository.append_match(
                    connection,
                    _match(result="ambiguous", artifact_ref=None, key=None),
                )
            assert replay == first

    asyncio.run(scenario())


def test_match_savepoint_rolls_back_with_outer_sqlite_transaction_after_read_only_work() -> None:
    async def scenario() -> None:
        repository = RecurrenceRepository()
        async with SQLiteProfile.open(
            SQLiteConfig(), tables=SCOPE_TABLES + SHARED_TABLES + RECURRENCE_TABLES
        ) as profile:
            async with profile.database.transaction() as connection:
                await _seed_scope(connection)
            with pytest.raises(RuntimeError, match="rollback"):
                async with profile.database.transaction() as connection:
                    assert await repository.find_match(connection, SCOPE, OUTCOME_REF, _item_ref()) is None
                    await repository.append_match(connection, _match())
                    raise RuntimeError("rollback")
            async with profile.database.transaction() as connection:
                assert await repository.matches(connection, SCOPE) == ()

    asyncio.run(scenario())


def test_observation_integrity_errors_outside_replay_are_not_suppressed() -> None:
    async def scenario() -> None:
        repository = RecurrenceRepository()
        async with (
            SQLiteProfile.open(SQLiteConfig(), tables=SCOPE_TABLES + SHARED_TABLES + RECURRENCE_TABLES) as profile,
            profile.database.transaction() as connection,
        ):
            with pytest.raises(IntegrityError):
                await repository.append_observation(connection, _selected())

    asyncio.run(scenario())


def test_observation_savepoint_rolls_back_with_outer_sqlite_transaction_after_read_only_work() -> None:
    async def scenario() -> None:
        repository = RecurrenceRepository()
        async with SQLiteProfile.open(
            SQLiteConfig(), tables=SCOPE_TABLES + SHARED_TABLES + RECURRENCE_TABLES
        ) as profile:
            async with profile.database.transaction() as connection:
                await _seed_scope(connection)
            with pytest.raises(RuntimeError, match="rollback"):
                async with profile.database.transaction() as connection:
                    assert await repository.observations(connection, SCOPE) == ()
                    await repository.append_observation(connection, _selected())
                    raise RuntimeError("rollback")
            async with profile.database.transaction() as connection:
                assert await repository.observations(connection, SCOPE) == ()

    asyncio.run(scenario())


def test_a_second_decision_for_the_same_failure_locator_is_rejected() -> None:
    async def scenario() -> None:
        repository = RecurrenceRepository()
        async with SQLiteProfile.open(
            SQLiteConfig(), tables=SCOPE_TABLES + SHARED_TABLES + RECURRENCE_TABLES
        ) as profile:
            async with profile.database.transaction() as connection:
                await _seed_scope(connection)
                await repository.append_match(connection, _match())
                await repository.append_match(connection, _match(result="ambiguous", artifact_ref=None, key=None))
                stored = await repository.matches(connection, SCOPE)

            assert len(stored) == 1

    asyncio.run(scenario())


def test_unmatched_and_ambiguous_decisions_persist_without_a_target() -> None:
    async def scenario() -> None:
        repository = RecurrenceRepository()
        async with SQLiteProfile.open(
            SQLiteConfig(), tables=SCOPE_TABLES + SHARED_TABLES + RECURRENCE_TABLES
        ) as profile:
            async with profile.database.transaction() as connection:
                await _seed_scope(connection)
                await repository.append_match(connection, _match(result="unmatched", artifact_ref=None, key=None))
                await repository.append_match(
                    connection,
                    _match(
                        result="ambiguous",
                        failure_ref=_item_ref(index=1),
                        candidates=(EXPERIENCE_REF, OTHER_EXPERIENCE_REF),
                        artifact_ref=None,
                        key=None,
                    ),
                )
                stored = await repository.matches(connection, SCOPE)

            assert [item.result for item in stored] == ["unmatched", "ambiguous"]
            assert all(item.artifact_ref is None and item.signature_key is None for item in stored)

    asyncio.run(scenario())


def test_observation_append_is_idempotent_under_replay() -> None:
    async def scenario() -> None:
        repository = RecurrenceRepository()
        async with SQLiteProfile.open(
            SQLiteConfig(), tables=SCOPE_TABLES + SHARED_TABLES + RECURRENCE_TABLES
        ) as profile:
            async with profile.database.transaction() as connection:
                await _seed_scope(connection)
                assert await repository.append_observation(connection, _selected()) is True
            async with profile.database.transaction() as connection:
                assert await repository.append_observation(connection, _selected()) is False
                stored = await repository.observations(connection, SCOPE)
                assert await repository.count_observations(connection, SCOPE) == 1

            assert len(stored) == 1
            assert stored[0].event == "selected"

    asyncio.run(scenario())


def test_selected_collapses_duplicate_rows_for_one_outcome_and_one_revision() -> None:
    async def scenario() -> None:
        repository = RecurrenceRepository()
        async with SQLiteProfile.open(
            SQLiteConfig(), tables=SCOPE_TABLES + SHARED_TABLES + RECURRENCE_TABLES
        ) as profile:
            async with profile.database.transaction() as connection:
                await _seed_scope(connection)
                assert await repository.append_observation(connection, _selected()) is True
                assert (
                    await repository.append_observation(
                        connection,
                        _observation(
                            "selected",
                            handoff_receipt_ref=SourceRef(source_type="handoff-receipt", source_id="receipt-2"),
                            handoff_ref=HANDOFF_REF,
                        ),
                    )
                    is False
                )
                stored = await repository.observations(connection, SCOPE)

            assert [item.event for item in stored] == ["selected"]

    asyncio.run(scenario())


def test_one_outcome_holds_at_most_one_terminal_verdict_per_revision() -> None:
    async def scenario() -> None:
        repository = RecurrenceRepository()
        async with SQLiteProfile.open(
            SQLiteConfig(), tables=SCOPE_TABLES + SHARED_TABLES + RECURRENCE_TABLES
        ) as profile:
            async with profile.database.transaction() as connection:
                await _seed_scope(connection)
                await repository.append_observation(connection, _selected())
                assert await repository.append_observation(connection, _avoided()) is True
                assert (
                    await repository.append_observation(
                        connection,
                        _observation(
                            "recurred",
                            failure_ref=_item_ref(kind="check", index=1, digest="sha256:" + "d" * 64),
                            match_digest=match_key(_match()),
                        ),
                    )
                    is False
                )
                stored = await repository.observations(connection, SCOPE)

            assert sorted(item.event for item in stored) == ["avoided", "selected"]

    asyncio.run(scenario())


def test_independent_revisions_of_one_outcome_are_independent_observations() -> None:
    async def scenario() -> None:
        repository = RecurrenceRepository()
        async with SQLiteProfile.open(
            SQLiteConfig(), tables=SCOPE_TABLES + SHARED_TABLES + RECURRENCE_TABLES
        ) as profile:
            async with profile.database.transaction() as connection:
                await _seed_scope(connection)
                assert await repository.append_observation(connection, _selected()) is True
                assert (
                    await repository.append_observation(connection, _selected(artifact_ref=OTHER_EXPERIENCE_REF))
                    is True
                )
                stored = await repository.observations(connection, SCOPE)

            assert [item.artifact_ref.revision for item in stored] == [1, 2]

    asyncio.run(scenario())


def test_observations_are_read_back_in_immutable_journal_order() -> None:
    async def scenario() -> None:
        repository = RecurrenceRepository()
        async with SQLiteProfile.open(
            SQLiteConfig(), tables=SCOPE_TABLES + SHARED_TABLES + RECURRENCE_TABLES
        ) as profile:
            async with profile.database.transaction() as connection:
                await _seed_scope(connection)
                await repository.append_observation(connection, _recurred(position=7))
                await repository.append_observation(connection, _selected(position=3))
                await repository.append_observation(connection, _avoided(position=5))
                stored = await repository.observations(connection, SCOPE)

            assert [item.task_outcome_position for item in stored] == [3, 5, 7]
            assert [item.event for item in stored] == ["selected", "avoided", "recurred"]

    asyncio.run(scenario())


def test_unlinked_handoff_citations_counts_only_visible_but_unattached_citations() -> None:
    async def scenario() -> None:
        repository = RecurrenceRepository()
        tables = SCOPE_TABLES + SHARED_TABLES + RECURRENCE_TABLES
        async with (
            SQLiteProfile.open(SQLiteConfig(), tables=tables) as profile,
            profile.database.transaction() as connection,
        ):
            await _seed_scope(connection)
            await repository.append_observation(connection, _selected())
            linked = (
                ("experience", "experience-1", 1, KEY),
                ("experience", "experience-1", 2, KEY),
                ("experience", "experience-9", 1, KEY),
            )
            assert await repository.unlinked_handoff_citations(connection, SCOPE, linked) == 2
            assert await repository.unlinked_handoff_citations(connection, SCOPE, ()) == 0
            assert (
                await repository.unlinked_handoff_citations(
                    connection, SCOPE, (("experience", "experience-1", 1, KEY),)
                )
                == 0
            )

    asyncio.run(scenario())


def test_payload_round_trips_through_strict_json_decoding() -> None:
    async def scenario() -> None:
        repository = RecurrenceRepository()
        async with SQLiteProfile.open(
            SQLiteConfig(), tables=SCOPE_TABLES + SHARED_TABLES + RECURRENCE_TABLES
        ) as profile:
            async with profile.database.transaction() as connection:
                await _seed_scope(connection)
                await repository.append_match(connection, _match())
                await repository.append_observation(connection, _selected())
            async with profile.database.transaction() as connection:
                matches = await repository.matches_for_outcome(connection, SCOPE, OUTCOME_REF)
                observations = await repository.observations(connection, SCOPE)

            assert matches == (_match(),)
            assert observations == (_selected(),)

    asyncio.run(scenario())
