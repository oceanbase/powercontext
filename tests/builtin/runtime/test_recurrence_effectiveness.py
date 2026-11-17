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

"""Read-path behaviour of the recurrence effectiveness projection.

The projection turns events the incubation window already recorded into the
five readings of one ``(artifact_ref, signature_key)`` bucket. These tests pin
the sample boundary, bucket isolation inside one shared database, and the
read-only promise, and they append every fixture event through the real ledger
write path.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from pydantic import ValidationError
from sqlalchemy import event, insert
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.experience import Experience
from powercontext.builtin.artifacts.experience.recurrence import (
    TaskOutcomeItemKind,
    TaskOutcomeItemRef,
    effectiveness_from_counts,
    new_observation,
)
from powercontext.builtin.evidence.resolver import EvidenceResolver
from powercontext.builtin.persistence import RecurrenceRepository
from powercontext.builtin.persistence.artifacts import ArtifactRepository
from powercontext.builtin.persistence.sources import SourceRepository
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.persistence.tables import RECURRENCE_TABLES, SCOPE_TABLES, SCOPES_TABLE
from powercontext.builtin.runtime.recurrence import RelationalRecurrenceLedger
from powercontext.builtin.sources.content import CONTENT_SOURCE_ADAPTER
from powercontext.sources import SourceRef

SCOPE = "scope-a"
OTHER_SCOPE = "scope-b"
KEY = "openapi contract changed without regenerating the client"
OTHER_KEY = "the migration was never backfilled"
EXPERIENCE_REF = ArtifactRef(family="experience", artifact_id="experience-1", revision=1)
OTHER_EXPERIENCE_REF = ArtifactRef(family="experience", artifact_id="experience-2", revision=1)
HANDOFF_REF = ArtifactRef(family="handoff", artifact_id="handoff-1", revision=1)

_ITEM_DIGEST = f"sha256:{'a' * 64}"
_MATCH_DIGEST = f"sha256:{'b' * 64}"


def _outcome_ref(position: int) -> SourceRef:
    return SourceRef(source_type="content", source_id=f"outcome-{position:02d}")


def _item_ref(outcome_ref: SourceRef, item_kind: TaskOutcomeItemKind) -> TaskOutcomeItemRef:
    return TaskOutcomeItemRef(
        task_outcome_ref=outcome_ref,
        item_kind=item_kind,
        item_index=0,
        item_digest=_ITEM_DIGEST,
    )


def _selected(ref: ArtifactRef, key: str, position: int, scope: str = SCOPE):
    return new_observation(
        event="selected",
        scope_id=scope,
        artifact_ref=ref,
        signature_key=key,
        task_outcome_ref=_outcome_ref(position),
        task_outcome_position=position,
        handoff_receipt_ref=_outcome_ref(position),
        handoff_ref=HANDOFF_REF,
    )


def _recurred(ref: ArtifactRef, key: str, position: int, scope: str = SCOPE):
    return new_observation(
        event="recurred",
        scope_id=scope,
        artifact_ref=ref,
        signature_key=key,
        task_outcome_ref=_outcome_ref(position),
        task_outcome_position=position,
        failure_ref=_item_ref(_outcome_ref(position), "check"),
        recurrence_match_digest=_MATCH_DIGEST,
    )


def _avoided(ref: ArtifactRef, key: str, position: int, scope: str = SCOPE):
    return new_observation(
        event="avoided",
        scope_id=scope,
        artifact_ref=ref,
        signature_key=key,
        task_outcome_ref=_outcome_ref(position),
        task_outcome_position=position,
        handoff_receipt_ref=_outcome_ref(position),
        handoff_ref=HANDOFF_REF,
        condition_ref=_item_ref(_outcome_ref(position), "observation"),
        check_ref=_item_ref(_outcome_ref(position), "check"),
    )


def _verdict_events(ref: ArtifactRef, key: str, total: int, avoided: int, scope: str = SCOPE):
    """Build ``avoided`` verdicts first, then ``recurred`` ones, each on its own Outcome."""

    return (
        *(_avoided(ref, key, position, scope) for position in range(1, avoided + 1)),
        *(_recurred(ref, key, position, scope) for position in range(avoided + 1, total + 1)),
    )


async def _seed_scope(connection: AsyncConnection, scope: str = SCOPE) -> None:
    await connection.execute(
        insert(SCOPES_TABLE).values(
            scope_id=scope,
            title=f"Scope {scope}",
            summary="Effectiveness read-path fixture scope.",
            scope_id_search=scope,
            title_search=scope,
            summary_search="effectiveness read-path fixture scope",
            version=1,
        )
    )


def _ledger(profile, sources, artifacts, repository, scope: str = SCOPE) -> RelationalRecurrenceLedger:
    return RelationalRecurrenceLedger(
        database=profile.database,
        scope_id=scope,
        sources=sources,
        artifacts=artifacts,
        recurrence=repository,
        evidence=EvidenceResolver(scope_id=scope, sources=sources, artifacts=artifacts),
    )


def _capture_observation_statements(profile, captured: list[tuple[str, Any]]) -> None:
    def _before_cursor_execute(
        conn: Any,
        cursor: Any,
        statement: str,
        parameters: Any,
        context: Any,
        executemany: bool,
    ) -> None:
        if "FROM pc_recurrence_observation" in statement:
            captured.append((statement, parameters))

    event.listen(profile.database.engine.sync_engine, "before_cursor_execute", _before_cursor_execute)


@pytest.mark.parametrize(
    ("verdicts", "avoided", "expected"),
    [
        (4, 0, None),
        (4, 2, None),
        (5, 0, 0.0),
        (5, 1, 0.2),
        (5, 4, 0.8),
        (5, 5, 1.0),
    ],
)
def test_effectiveness_ratio_requires_the_minimum_verdict_sample(verdicts: int, avoided: int, expected) -> None:
    # A non-zero recall count proves ``selected`` never enters the denominator.
    projection = effectiveness_from_counts(
        EXPERIENCE_REF,
        KEY,
        selected=7,
        recurred=verdicts - avoided,
        avoided=avoided,
    )

    assert projection.selected == 7
    assert projection.recurred == verdicts - avoided
    assert projection.avoided == avoided
    assert projection.verdicts == verdicts
    if expected is None:
        assert projection.effectiveness is None
    else:
        assert projection.effectiveness is not None
        assert projection.effectiveness == pytest.approx(expected)


def test_an_unnormalized_signature_key_is_rejected_not_read_as_no_evidence() -> None:
    """A raw cue is a caller bug: it must fail loudly, not return an empty bucket."""

    raw = "  OPENAPI CONTRACT CHANGED WITHOUT REGENERATING THE CLIENT  "

    async def scenario() -> None:
        async with SQLiteProfile.open(SQLiteConfig(), tables=SCOPE_TABLES + RECURRENCE_TABLES) as profile:
            repository = RecurrenceRepository()
            async with profile.database.transaction() as connection:
                await _seed_scope(connection)
                with pytest.raises(ValueError, match="already be normalized"):
                    await repository.event_counts_for_target(connection, SCOPE, EXPERIENCE_REF, raw)
                with pytest.raises(ValueError, match="already be normalized"):
                    await repository.observations_for_target(connection, SCOPE, EXPERIENCE_REF, raw)

    with pytest.raises(ValueError, match="already be normalized"):
        effectiveness_from_counts(EXPERIENCE_REF, raw, selected=0, recurred=0, avoided=0)
    asyncio.run(scenario())


def test_effectiveness_counts_every_event_of_its_bucket() -> None:
    async def scenario() -> tuple[Any, Any]:
        async with SQLiteProfile.open(SQLiteConfig(), tables=SCOPE_TABLES + RECURRENCE_TABLES) as profile:
            sources = SourceRepository((CONTENT_SOURCE_ADAPTER,))
            artifacts = ArtifactRepository((Experience,), sources=sources)
            repository = RecurrenceRepository()
            ledger = _ledger(profile, sources, artifacts, repository)
            async with profile.database.transaction() as connection:
                await _seed_scope(connection)
                for observation in (
                    _selected(EXPERIENCE_REF, KEY, 1),
                    _selected(EXPERIENCE_REF, KEY, 2),
                    _recurred(EXPERIENCE_REF, KEY, 3),
                    _avoided(EXPERIENCE_REF, KEY, 4),
                ):
                    await repository.append_observation(connection, observation)

            async with profile.database.transaction() as connection:
                projection = await ledger.effectiveness(connection, EXPERIENCE_REF, KEY)
                bucket = await repository.observations_for_target(connection, SCOPE, EXPERIENCE_REF, KEY)

        return projection, tuple(row.event for row in bucket)

    projection, bucket_events = asyncio.run(scenario())

    assert bucket_events == ("selected", "selected", "recurred", "avoided")
    assert projection.artifact_ref == EXPERIENCE_REF
    assert projection.signature_key == KEY
    assert projection.selected == 2
    assert projection.recurred == 1
    assert projection.avoided == 1
    assert projection.verdicts == 2
    assert projection.effectiveness is None


def test_effectiveness_buckets_stay_isolated_in_one_database() -> None:
    """Every bucket filter is load-bearing inside one shared database.

    Scope, family, artifact, revision, and signature each own a bucket of
    events here, so deleting any one filter condition changes at least one
    asserted reading. Isolation is proven by event counts, not by which index
    the query planner happens to pick.
    """

    async def scenario():
        revision_two = ArtifactRef(family="experience", artifact_id=EXPERIENCE_REF.artifact_id, revision=2)
        async with SQLiteProfile.open(SQLiteConfig(), tables=SCOPE_TABLES + RECURRENCE_TABLES) as profile:
            sources = SourceRepository((CONTENT_SOURCE_ADAPTER,))
            artifacts = ArtifactRepository((Experience,), sources=sources)
            repository = RecurrenceRepository()
            own = _ledger(profile, sources, artifacts, repository)
            neighbour = _ledger(profile, sources, artifacts, repository, scope=OTHER_SCOPE)
            async with profile.database.transaction() as connection:
                for scope in (SCOPE, OTHER_SCOPE):
                    await _seed_scope(connection, scope)
                events = (
                    # The target bucket: scope-a, experience-1, revision 1, KEY.
                    *_verdict_events(EXPERIENCE_REF, KEY, 5, 0),
                    # Same artifact and key one revision later.
                    *_verdict_events(revision_two, KEY, 4, 4),
                    # Same artifact and revision under another signature.
                    *_verdict_events(EXPERIENCE_REF, OTHER_KEY, 2, 2),
                    # Another artifact under the same signature.
                    *_verdict_events(OTHER_EXPERIENCE_REF, KEY, 2, 2),
                    # A Handoff-family row under the same signature.
                    _recurred(HANDOFF_REF, KEY, 1),
                    # The same bucket in another scope.
                    *_verdict_events(EXPERIENCE_REF, KEY, 5, 4, scope=OTHER_SCOPE),
                )
                for observation in events:
                    await repository.append_observation(connection, observation)

            async with profile.database.transaction() as connection:
                target = await own.effectiveness(connection, EXPERIENCE_REF, KEY)
                revision = await own.effectiveness(connection, revision_two, KEY)
                signature = await own.effectiveness(connection, EXPERIENCE_REF, OTHER_KEY)
                artifact = await own.effectiveness(connection, OTHER_EXPERIENCE_REF, KEY)
                family = await own.effectiveness(connection, HANDOFF_REF, KEY)
                cross_scope = await neighbour.effectiveness(connection, EXPERIENCE_REF, KEY)

        return target, revision, signature, artifact, family, cross_scope

    target, revision, signature, artifact, family, cross_scope = asyncio.run(scenario())

    assert (target.selected, target.recurred, target.avoided, target.verdicts) == (0, 5, 0, 5)
    assert target.effectiveness == 0.0
    assert (revision.selected, revision.recurred, revision.avoided, revision.verdicts) == (0, 0, 4, 4)
    assert revision.effectiveness is None
    assert (signature.selected, signature.recurred, signature.avoided, signature.verdicts) == (0, 0, 2, 2)
    assert signature.effectiveness is None
    assert (artifact.selected, artifact.recurred, artifact.avoided, artifact.verdicts) == (0, 0, 2, 2)
    assert artifact.effectiveness is None
    assert (family.selected, family.recurred, family.avoided, family.verdicts) == (0, 1, 0, 1)
    assert family.effectiveness is None
    assert (cross_scope.selected, cross_scope.recurred, cross_scope.avoided, cross_scope.verdicts) == (0, 1, 4, 5)
    assert cross_scope.effectiveness == pytest.approx(0.8)


def test_an_empty_bucket_reads_as_no_evidence_not_zero() -> None:
    empty_ref = ArtifactRef(family="experience", artifact_id="experience-empty", revision=1)

    async def scenario():
        async with SQLiteProfile.open(SQLiteConfig(), tables=SCOPE_TABLES + RECURRENCE_TABLES) as profile:
            sources = SourceRepository((CONTENT_SOURCE_ADAPTER,))
            artifacts = ArtifactRepository((Experience,), sources=sources)
            repository = RecurrenceRepository()
            ledger = _ledger(profile, sources, artifacts, repository)
            async with profile.database.transaction() as connection:
                await _seed_scope(connection)
                for observation in _verdict_events(EXPERIENCE_REF, KEY, 5, 0):
                    await repository.append_observation(connection, observation)

            async with profile.database.transaction() as connection:
                return await ledger.effectiveness(connection, empty_ref, KEY)

    empty = asyncio.run(scenario())

    assert (empty.selected, empty.recurred, empty.avoided, empty.verdicts) == (0, 0, 0, 0)
    assert empty.effectiveness is None


def test_effectiveness_leaves_the_ledger_unchanged() -> None:
    async def scenario() -> tuple[Any, Any, Any, Any]:
        async with SQLiteProfile.open(SQLiteConfig(), tables=SCOPE_TABLES + RECURRENCE_TABLES) as profile:
            sources = SourceRepository((CONTENT_SOURCE_ADAPTER,))
            artifacts = ArtifactRepository((Experience,), sources=sources)
            repository = RecurrenceRepository()
            ledger = _ledger(profile, sources, artifacts, repository)
            async with profile.database.transaction() as connection:
                await _seed_scope(connection)
                for observation in (
                    _selected(EXPERIENCE_REF, KEY, 1),
                    *_verdict_events(EXPERIENCE_REF, KEY, 5, 2),
                ):
                    await repository.append_observation(connection, observation)

            async with profile.database.transaction() as connection:
                before = await repository.observations(connection, SCOPE)
                first = await ledger.effectiveness(connection, EXPERIENCE_REF, KEY)
                second = await ledger.effectiveness(connection, EXPERIENCE_REF, KEY)
                after = await repository.observations(connection, SCOPE)

        return before, first, second, after

    before, first, second, after = asyncio.run(scenario())

    assert first == second
    assert after == before
    assert len(after) == len(before) == 6


def test_an_avoided_event_requires_its_verification_refs() -> None:
    with pytest.raises(ValidationError, match="avoided requires a check locator"):
        new_observation(
            event="avoided",
            scope_id=SCOPE,
            artifact_ref=EXPERIENCE_REF,
            signature_key=KEY,
            task_outcome_ref=_outcome_ref(1),
            task_outcome_position=1,
            handoff_receipt_ref=_outcome_ref(1),
            handoff_ref=HANDOFF_REF,
            condition_ref=_item_ref(_outcome_ref(1), "observation"),
        )


def test_effectiveness_counts_are_aggregated_by_the_database() -> None:
    """The counting happens in SQL, so a growing bucket never means per-row decoding."""

    async def scenario(captured: list[tuple[str, Any]]):
        async with SQLiteProfile.open(SQLiteConfig(), tables=SCOPE_TABLES + RECURRENCE_TABLES) as profile:
            repository = RecurrenceRepository()
            _capture_observation_statements(profile, captured)
            async with profile.database.transaction() as connection:
                await _seed_scope(connection)
                for observation in _verdict_events(EXPERIENCE_REF, KEY, 120, 30):
                    await repository.append_observation(connection, observation)

            async with profile.database.transaction() as connection:
                counts = await repository.event_counts_for_target(connection, SCOPE, EXPERIENCE_REF, KEY)
                statement, parameters = captured[-1]
                plan = (await connection.exec_driver_sql(f"EXPLAIN QUERY PLAN {statement}", parameters)).all()

        return counts, statement, [str(row[3]) for row in plan]

    counts, statement, plan = asyncio.run(scenario([]))

    assert counts == (0, 90, 30)
    assert "GROUP BY" in statement
    assert "payload" not in statement
    assert plan
    assert not any(detail.startswith("SCAN pc_recurrence_observation") for detail in plan)


def test_target_query_reads_through_an_index_not_a_full_scan() -> None:
    async def scenario(captured: list[tuple[str, Any]]) -> list[str]:
        async with SQLiteProfile.open(SQLiteConfig(), tables=SCOPE_TABLES + RECURRENCE_TABLES) as profile:
            repository = RecurrenceRepository()
            _capture_observation_statements(profile, captured)
            async with profile.database.transaction() as connection:
                await _seed_scope(connection)
                for observation in _verdict_events(EXPERIENCE_REF, KEY, 5, 2):
                    await repository.append_observation(connection, observation)

            async with profile.database.transaction() as connection:
                await repository.observations_for_target(connection, SCOPE, EXPERIENCE_REF, KEY)
                statement, parameters = captured[-1]
                plan = (await connection.exec_driver_sql(f"EXPLAIN QUERY PLAN {statement}", parameters)).all()

        return [str(row[3]) for row in plan]

    details = asyncio.run(scenario([]))

    assert details
    assert not any(detail.startswith("SCAN pc_recurrence_observation") for detail in details)
