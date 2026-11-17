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
the sample boundary, bucket isolation, and the read-only promise, and they
append every fixture event through the real ledger write path.
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
    aggregate_effectiveness,
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


def _selected(ref: ArtifactRef, key: str, position: int):
    return new_observation(
        event="selected",
        scope_id=SCOPE,
        artifact_ref=ref,
        signature_key=key,
        task_outcome_ref=_outcome_ref(position),
        task_outcome_position=position,
        handoff_receipt_ref=_outcome_ref(position),
        handoff_ref=HANDOFF_REF,
    )


def _recurred(ref: ArtifactRef, key: str, position: int):
    return new_observation(
        event="recurred",
        scope_id=SCOPE,
        artifact_ref=ref,
        signature_key=key,
        task_outcome_ref=_outcome_ref(position),
        task_outcome_position=position,
        failure_ref=_item_ref(_outcome_ref(position), "check"),
        recurrence_match_digest=_MATCH_DIGEST,
    )


def _avoided(ref: ArtifactRef, key: str, position: int):
    return new_observation(
        event="avoided",
        scope_id=SCOPE,
        artifact_ref=ref,
        signature_key=key,
        task_outcome_ref=_outcome_ref(position),
        task_outcome_position=position,
        handoff_receipt_ref=_outcome_ref(position),
        handoff_ref=HANDOFF_REF,
        condition_ref=_item_ref(_outcome_ref(position), "observation"),
        check_ref=_item_ref(_outcome_ref(position), "check"),
    )


def _verdict_events(ref: ArtifactRef, key: str, total: int, avoided: int):
    """Build ``avoided`` verdicts first, then ``recurred`` ones, each on its own Outcome."""

    return (
        *(_avoided(ref, key, position) for position in range(1, avoided + 1)),
        *(_recurred(ref, key, position) for position in range(avoided + 1, total + 1)),
    )


async def _seed_scope(connection: AsyncConnection) -> None:
    await connection.execute(
        insert(SCOPES_TABLE).values(
            scope_id=SCOPE,
            title="Scope A",
            summary="Effectiveness read-path fixture scope.",
            scope_id_search=SCOPE,
            title_search="scope a",
            summary_search="effectiveness read-path fixture scope",
            version=1,
        )
    )


def _ledger(profile, sources, artifacts, repository) -> RelationalRecurrenceLedger:
    return RelationalRecurrenceLedger(
        database=profile.database,
        scope_id=SCOPE,
        sources=sources,
        artifacts=artifacts,
        recurrence=repository,
        evidence=EvidenceResolver(scope_id=SCOPE, sources=sources, artifacts=artifacts),
    )


def _projection(*events, ref: ArtifactRef = EXPERIENCE_REF, key: str = KEY):
    """Append events through the real write path, then read one projection back."""

    async def scenario():
        async with SQLiteProfile.open(SQLiteConfig(), tables=SCOPE_TABLES + RECURRENCE_TABLES) as profile:
            sources = SourceRepository((CONTENT_SOURCE_ADAPTER,))
            artifacts = ArtifactRepository((Experience,), sources=sources)
            repository = RecurrenceRepository()
            ledger = _ledger(profile, sources, artifacts, repository)
            async with profile.database.transaction() as connection:
                await _seed_scope(connection)
                for observation in events:
                    await repository.append_observation(connection, observation)
            async with profile.database.transaction() as connection:
                return await ledger.effectiveness(connection, ref, key)

    return asyncio.run(scenario())


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
    projection = aggregate_effectiveness(
        EXPERIENCE_REF,
        KEY,
        _verdict_events(EXPERIENCE_REF, KEY, verdicts, avoided),
    )

    assert projection.selected == 0
    assert projection.recurred == verdicts - avoided
    assert projection.avoided == avoided
    assert projection.verdicts == verdicts
    if expected is None:
        assert projection.effectiveness is None
    else:
        assert projection.effectiveness is not None
        assert projection.effectiveness == pytest.approx(expected)


def test_effectiveness_counts_every_event_of_its_bucket() -> None:
    async def scenario() -> None:
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

        assert tuple(row.event for row in bucket) == ("selected", "selected", "recurred", "avoided")
        assert projection.artifact_ref == EXPERIENCE_REF
        assert projection.signature_key == KEY
        assert projection.selected == 2
        assert projection.recurred == 1
        assert projection.avoided == 1
        assert projection.verdicts == 2
        assert projection.effectiveness is None

    asyncio.run(scenario())


def test_effectiveness_buckets_do_not_merge_revisions() -> None:
    revision_two = ArtifactRef(family="experience", artifact_id=EXPERIENCE_REF.artifact_id, revision=2)

    first = _projection(*_verdict_events(EXPERIENCE_REF, KEY, 3, 0))
    second = _projection(*_verdict_events(revision_two, KEY, 3, 0), ref=revision_two)

    assert first.recurred == 3
    assert first.verdicts == 3
    assert first.effectiveness is None
    assert second.recurred == 3
    assert second.verdicts == 3
    assert second.effectiveness is None


def test_effectiveness_reads_only_its_own_bucket() -> None:
    empty_ref = ArtifactRef(family="experience", artifact_id="experience-empty", revision=1)

    target = _projection(*_verdict_events(EXPERIENCE_REF, KEY, 5, 0))
    other_signature = _projection(
        *_verdict_events(EXPERIENCE_REF, OTHER_KEY, 5, 5),
        *_verdict_events(OTHER_EXPERIENCE_REF, KEY, 2, 2),
        key=OTHER_KEY,
    )
    empty = _projection(*_verdict_events(EXPERIENCE_REF, KEY, 5, 0), ref=empty_ref)

    assert (target.selected, target.recurred, target.avoided) == (0, 5, 0)
    assert target.verdicts == 5
    assert target.effectiveness == 0.0
    assert (other_signature.selected, other_signature.recurred, other_signature.avoided) == (0, 0, 5)
    assert other_signature.effectiveness == 1.0
    assert (empty.selected, empty.recurred, empty.avoided, empty.verdicts) == (0, 0, 0, 0)
    assert empty.effectiveness is None


def test_effectiveness_leaves_the_ledger_unchanged() -> None:
    async def scenario() -> None:
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

        assert first == second
        assert after == before
        assert len(after) == len(before) == 6

    asyncio.run(scenario())


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


def test_aggregation_rejects_events_from_another_bucket() -> None:
    foreign = _recurred(OTHER_EXPERIENCE_REF, KEY, 1)

    with pytest.raises(ValueError, match="requested bucket"):
        aggregate_effectiveness(EXPERIENCE_REF, KEY, (foreign,))


def test_target_query_reads_through_an_index_not_a_full_scan() -> None:
    async def scenario(captured: list[tuple[str, Any]]) -> list[str]:
        async with SQLiteProfile.open(SQLiteConfig(), tables=SCOPE_TABLES + RECURRENCE_TABLES) as profile:
            repository = RecurrenceRepository()

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
    assert any("ix_pc_recurrence_observation_revision" in detail for detail in details)
