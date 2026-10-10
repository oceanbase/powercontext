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
from datetime import UTC, datetime, timedelta

import pytest

from powercontext.builtin.persistence import DecisionObservationRepository
from powercontext.builtin.persistence.decision_observations import (
    DecisionObservationRepository as SameDecisionObservationRepository,
)
from powercontext.builtin.persistence.errors import StoredPayloadConflictError
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.persistence.tables import (
    DECISION_OBSERVATION_TABLES,
    SHARED_TABLES,
)
from powercontext.builtin.runtime.decision_observation_ledger import RelationalDecisionObservationSink
from powercontext.builtin.runtime.decision_policy import (
    DecisionAssessment,
    DecisionAssessmentSource,
    DecisionCoverage,
    DecisionObservation,
    DecisionPolicyMode,
    DecisionPrivacyBoundary,
    DecisionVerdict,
)

SCOPE = "scope-a"
CREATED_AT = datetime(2026, 10, 10, 8, 30, tzinfo=UTC)


def _observation(
    *, observation_id: str = "decision-observation-1", created_at: datetime = CREATED_AT
) -> DecisionObservation:
    return DecisionObservation(
        observation_id=observation_id,
        created_at=created_at,
        operation_id="memory-write:memory-a@2",
        scope_id=SCOPE,
        consumer="memory_write_gate",
        policy_id="memory.write.evidence_sufficiency.v1",
        policy_version="1",
        mode=DecisionPolicyMode.ADVISORY,
        subject_refs=("entry:entry-a@version-a",),
        evidence_refs=("source:task:1",),
        privacy_boundary=DecisionPrivacyBoundary.LOCAL_ONLY,
        provider_id="local-provider",
        backend_model_id="decision-model-a",
        model_policy_id="powercontext.decision.static.v1",
        assessment=DecisionAssessment(
            policy_id="memory.write.evidence_sufficiency.v1",
            policy_version="1",
            mode=DecisionPolicyMode.ADVISORY,
            coverage=DecisionCoverage.ADJUDICATED,
            verdict=DecisionVerdict.DENY,
            source=DecisionAssessmentSource.DECISION_MODEL,
            reason="decision_model_verdict",
        ),
        final_action="memory_write_flag",
        metadata={"candidate_count": 1, "evidence_count": 1},
    )


def test_repository_is_exported_from_the_persistence_package() -> None:
    assert DecisionObservationRepository is SameDecisionObservationRepository


def test_decision_observation_tables_are_registered_in_builtin_tables() -> None:
    from powercontext.builtin.persistence.tables import BUILTIN_TABLES

    assert {table.name for table in DECISION_OBSERVATION_TABLES} <= {table.name for table in BUILTIN_TABLES}


def test_repository_appends_and_reads_back_one_bounded_observation() -> None:
    async def scenario() -> None:
        repository = DecisionObservationRepository()
        async with SQLiteProfile.open(SQLiteConfig(), tables=SHARED_TABLES + DECISION_OBSERVATION_TABLES) as profile:
            async with profile.database.transaction() as connection:
                inserted = await repository.append(connection, _observation())
            async with profile.database.transaction() as connection:
                stored = await repository.observations(connection, SCOPE)

        assert inserted is True
        assert stored == (_observation(),)

    asyncio.run(scenario())


def test_repository_accepts_gate_generated_ordinal_references() -> None:
    async def scenario() -> None:
        repository = DecisionObservationRepository()
        observation = _observation().model_copy(
            update={"subject_refs": ("candidate:1",), "evidence_refs": ("evidence:1",)}
        )
        async with SQLiteProfile.open(SQLiteConfig(), tables=SHARED_TABLES + DECISION_OBSERVATION_TABLES) as profile:
            async with profile.database.transaction() as connection:
                inserted = await repository.append(connection, observation)
            async with profile.database.transaction() as connection:
                stored = await repository.observations(connection, SCOPE)

        assert inserted is True
        assert stored == (observation,)

    asyncio.run(scenario())


def test_repository_treats_an_exact_observation_replay_as_idempotent() -> None:
    async def scenario() -> None:
        repository = DecisionObservationRepository()
        observation = _observation()
        async with SQLiteProfile.open(SQLiteConfig(), tables=SHARED_TABLES + DECISION_OBSERVATION_TABLES) as profile:
            async with profile.database.transaction() as connection:
                first = await repository.append(connection, observation)
                replayed = await repository.append(connection, observation)
            async with profile.database.transaction() as connection:
                stored = await repository.observations(connection, SCOPE)

        assert first is True
        assert replayed is False
        assert stored == (observation,)

    asyncio.run(scenario())


def test_repository_rejects_an_observation_with_a_mismatched_assessment_policy() -> None:
    async def scenario() -> None:
        repository = DecisionObservationRepository()
        inconsistent = _observation().model_copy(update={"policy_id": "memory.write.other_policy.v1"})
        async with (
            SQLiteProfile.open(SQLiteConfig(), tables=SHARED_TABLES + DECISION_OBSERVATION_TABLES) as profile,
            profile.database.transaction() as connection,
        ):
            with pytest.raises(ValueError, match="assessment policy"):
                await repository.append(connection, inconsistent)

    asyncio.run(scenario())


def test_repository_rejects_an_observation_id_reused_for_different_content() -> None:
    async def scenario() -> None:
        repository = DecisionObservationRepository()
        observation = _observation()
        conflicting = observation.model_copy(update={"final_action": "memory_write_accept"})
        async with (
            SQLiteProfile.open(SQLiteConfig(), tables=SHARED_TABLES + DECISION_OBSERVATION_TABLES) as profile,
            profile.database.transaction() as connection,
        ):
            await repository.append(connection, observation)
            with pytest.raises(StoredPayloadConflictError):
                await repository.append(connection, conflicting)

    asyncio.run(scenario())


def test_repository_rejects_unbounded_metadata_before_persistence() -> None:
    async def scenario() -> None:
        repository = DecisionObservationRepository()
        forbidden = _observation().model_copy(update={"metadata": {"prompt": "secret candidate text"}})
        async with (
            SQLiteProfile.open(SQLiteConfig(), tables=SHARED_TABLES + DECISION_OBSERVATION_TABLES) as profile,
            profile.database.transaction() as connection,
        ):
            with pytest.raises(ValueError, match="metadata"):
                await repository.append(connection, forbidden)

    asyncio.run(scenario())


def test_repository_prunes_only_observations_older_than_the_retention_boundary() -> None:
    async def scenario() -> None:
        repository = DecisionObservationRepository()
        old = _observation(observation_id="decision-observation-old", created_at=CREATED_AT - timedelta(days=1))
        current = _observation(observation_id="decision-observation-current")
        async with (
            SQLiteProfile.open(SQLiteConfig(), tables=SHARED_TABLES + DECISION_OBSERVATION_TABLES) as profile,
            profile.database.transaction() as connection,
        ):
            await repository.append(connection, old)
            await repository.append(connection, current)
            removed = await repository.prune_before(connection, SCOPE, CREATED_AT)
            stored = await repository.observations(connection, SCOPE)

        assert removed == 1
        assert stored == (current,)

    asyncio.run(scenario())


def test_relational_sink_prunes_only_the_current_scope_when_retention_is_configured() -> None:
    async def scenario() -> None:
        repository = DecisionObservationRepository()
        current = _observation(observation_id="decision-observation-current", created_at=datetime.now(UTC))
        old_current_scope = _observation(
            observation_id="decision-observation-old", created_at=current.created_at - timedelta(days=3)
        )
        other_scope = _observation(
            observation_id="decision-observation-other-scope",
            created_at=current.created_at - timedelta(days=3),
        ).model_copy(update={"scope_id": "scope-b"})
        async with SQLiteProfile.open(SQLiteConfig(), tables=SHARED_TABLES + DECISION_OBSERVATION_TABLES) as profile:
            async with profile.database.transaction() as connection:
                await repository.append(connection, old_current_scope)
                await repository.append(connection, other_scope)
            sink = RelationalDecisionObservationSink(profile.database, repository, retention_days=2)
            await sink.record(current)
            async with profile.database.transaction() as connection:
                current_scope = await repository.observations(connection, SCOPE)
                other_scope_observations = await repository.observations(connection, "scope-b")

        assert current_scope == (current,)
        assert other_scope_observations == (other_scope,)

    asyncio.run(scenario())
