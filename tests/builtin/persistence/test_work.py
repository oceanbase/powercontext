# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import func, select, update

from powercontext.builtin.persistence.errors import RepositoryNotFoundError
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.persistence.tables import WORK_ATTEMPTS_TABLE, WORK_ITEMS_TABLE, WORK_TABLES
from powercontext.builtin.persistence.work import (
    StaleWorkClaimError,
    WorkFailure,
    WorkRepository,
    WorkResult,
    WorkSpec,
    WorkStateConflictError,
    WorkStatus,
)


def _spec(*, logical_key: str = "a" * 64, lane_key: str = "b" * 64) -> WorkSpec:
    return WorkSpec(
        kind="powercontext.memory.source-window.v1",
        payload_version=1,
        scope_id="project:test",
        lane_key=lane_key,
        logical_key=logical_key,
        payload={"after": 0, "through": 2},
    )


def test_enqueue_deduplicates_one_logical_window_and_orders_each_lane() -> None:
    async def scenario() -> None:
        async with SQLiteProfile.open(SQLiteConfig(), tables=WORK_TABLES) as profile:
            repository = WorkRepository()
            async with profile.database.transaction() as connection:
                first = await repository.enqueue(connection, _spec())
                duplicate = await repository.enqueue(connection, _spec())
                second = await repository.enqueue(connection, _spec(logical_key="c" * 64))

            assert first.created is True
            assert duplicate.created is False
            assert duplicate.work.work_id == first.work.work_id
            assert first.work.lane_sequence == 1
            assert second.work.lane_sequence == 2

    asyncio.run(scenario())


def test_only_the_lane_head_can_be_claimed_and_completion_advances_it() -> None:
    async def scenario() -> None:
        async with SQLiteProfile.open(SQLiteConfig(), tables=WORK_TABLES) as profile:
            repository = WorkRepository()
            async with profile.database.transaction() as connection:
                first = await repository.enqueue(connection, _spec())
                second = await repository.enqueue(connection, _spec(logical_key="c" * 64))

            async with profile.database.transaction() as connection:
                claims = await repository.claim(
                    connection,
                    worker_id="worker-a",
                    supported={first.work.kind: frozenset({1})},
                    lease_seconds=120,
                    limit=2,
                )
            assert len(claims) == 1
            assert claims[0].work_id == first.work.work_id

            async with profile.database.transaction() as connection:
                await repository.complete(
                    connection,
                    claims[0],
                    WorkResult(code="processed", payload={"current_cursor": 2}),
                )
            async with profile.database.transaction() as connection:
                next_claims = await repository.claim(
                    connection,
                    worker_id="worker-b",
                    supported={second.work.kind: frozenset({1})},
                    lease_seconds=120,
                    limit=2,
                )

            assert len(next_claims) == 1
            assert next_claims[0].work_id == second.work.work_id

    asyncio.run(scenario())


def test_concurrent_workers_cannot_claim_the_same_work(tmp_path: Path) -> None:
    async def scenario() -> None:
        config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'work.db'}")
        async with SQLiteProfile.open(config, tables=WORK_TABLES) as first_profile:
            async with SQLiteProfile.open(config, tables=WORK_TABLES) as second_profile:
                repository = WorkRepository()
                async with first_profile.database.transaction() as connection:
                    enqueued = await repository.enqueue(connection, _spec())

                async def claim(profile: SQLiteProfile, worker_id: str):
                    async with profile.database.transaction() as connection:
                        return await repository.claim(
                            connection,
                            worker_id=worker_id,
                            supported={enqueued.work.kind: frozenset({1})},
                            lease_seconds=120,
                            limit=1,
                        )

                results = await asyncio.gather(
                    claim(first_profile, "worker-a"),
                    claim(second_profile, "worker-b"),
                )

            assert sum(len(result) for result in results) == 1

    asyncio.run(scenario())


def test_expired_claim_is_fenced_before_another_attempt_runs() -> None:
    async def scenario() -> None:
        async with SQLiteProfile.open(SQLiteConfig(), tables=WORK_TABLES) as profile:
            repository = WorkRepository()
            async with profile.database.transaction() as connection:
                enqueued = await repository.enqueue(connection, _spec())
                first = (
                    await repository.claim(
                        connection,
                        worker_id="worker-a",
                        supported={enqueued.work.kind: frozenset({1})},
                        lease_seconds=120,
                        limit=1,
                    )
                )[0]

            async with profile.database.transaction() as connection:
                await connection.execute(
                    update(WORK_ITEMS_TABLE)
                    .where(WORK_ITEMS_TABLE.c.work_id == first.work_id)
                    .values(lease_expires_at=datetime(2000, 1, 1, tzinfo=UTC))
                )
                assert (
                    await repository.claim(
                        connection,
                        worker_id="worker-b",
                        supported={enqueued.work.kind: frozenset({1})},
                        lease_seconds=120,
                        limit=1,
                        expired_retry_delay_seconds=0,
                    )
                ) == ()

            async with profile.database.transaction() as connection:
                second = (
                    await repository.claim(
                        connection,
                        worker_id="worker-b",
                        supported={enqueued.work.kind: frozenset({1})},
                        lease_seconds=120,
                        limit=1,
                    )
                )[0]

            assert second.fence > first.fence
            with pytest.raises(StaleWorkClaimError):
                async with profile.database.transaction() as connection:
                    await repository.complete(connection, first, WorkResult(code="late", payload={}))

    asyncio.run(scenario())


def test_retry_budget_blocks_the_lane_until_an_operator_recovers_or_cancels() -> None:
    async def scenario() -> None:
        async with SQLiteProfile.open(SQLiteConfig(), tables=WORK_TABLES) as profile:
            repository = WorkRepository()
            async with profile.database.transaction() as connection:
                enqueued = await repository.enqueue(connection, _spec().model_copy(update={"max_attempts": 1}))
                claim = (
                    await repository.claim(
                        connection,
                        worker_id="worker-a",
                        supported={enqueued.work.kind: frozenset({1})},
                        lease_seconds=120,
                        limit=1,
                    )
                )[0]
                failed = await repository.fail(
                    connection,
                    claim,
                    WorkFailure(category="provider", code="temporarily_unavailable", retryable=True),
                    retry_delay_seconds=0,
                )

            assert failed.status is WorkStatus.FAILED
            async with profile.database.transaction() as connection:
                duplicate = await repository.enqueue(connection, _spec())
            assert duplicate.created is False
            assert duplicate.work.status is WorkStatus.FAILED

            async with profile.database.transaction() as connection:
                recovered = await repository.retry(
                    connection,
                    failed.work_id,
                    expected_version=failed.state_version,
                )
            assert recovered.status is WorkStatus.QUEUED
            assert recovered.recovery_generation == 1

            with pytest.raises(WorkStateConflictError):
                async with profile.database.transaction() as connection:
                    await repository.cancel(
                        connection,
                        recovered.work_id,
                        expected_version=failed.state_version,
                    )

    asyncio.run(scenario())


def test_running_cancel_linearizes_before_commit_and_recovers_after_expiry() -> None:
    async def scenario() -> None:
        async with SQLiteProfile.open(SQLiteConfig(), tables=WORK_TABLES) as profile:
            repository = WorkRepository()
            async with profile.database.transaction() as connection:
                enqueued = await repository.enqueue(connection, _spec())
                claim = (
                    await repository.claim(
                        connection,
                        worker_id="worker-a",
                        supported={enqueued.work.kind: frozenset({1})},
                        lease_seconds=120,
                        limit=1,
                    )
                )[0]
                running = await repository.get(connection, claim.work_id)

            async with profile.database.transaction() as connection:
                cancelling = await repository.cancel(
                    connection,
                    claim.work_id,
                    expected_version=running.state_version,
                )
            assert cancelling.status is WorkStatus.CANCELLING
            with pytest.raises(WorkStateConflictError):
                async with profile.database.transaction() as connection:
                    await repository.cancel(
                        connection,
                        claim.work_id,
                        expected_version=cancelling.state_version,
                    )

            committed = False

            async def domain_commit(_connection):
                nonlocal committed
                committed = True
                return WorkResult(code="late", payload={})

            with pytest.raises(StaleWorkClaimError):
                async with profile.database.transaction() as connection:
                    await repository.complete(connection, claim, None, commit=domain_commit)
            assert committed is False

            async with profile.database.transaction() as connection:
                await connection.execute(
                    update(WORK_ITEMS_TABLE)
                    .where(WORK_ITEMS_TABLE.c.work_id == claim.work_id)
                    .values(lease_expires_at=datetime(2000, 1, 1, tzinfo=UTC))
                )
                assert (
                    await repository.claim(
                        connection,
                        worker_id="worker-b",
                        supported={enqueued.work.kind: frozenset({1})},
                        lease_seconds=120,
                        limit=1,
                    )
                ) == ()
                cancelled = await repository.get(connection, claim.work_id)
            assert cancelled.status is WorkStatus.CANCELLED

    asyncio.run(scenario())


def test_retry_claim_links_to_the_previous_attempt_trace_context() -> None:
    async def scenario() -> None:
        async with SQLiteProfile.open(SQLiteConfig(), tables=WORK_TABLES) as profile:
            repository = WorkRepository()
            async with profile.database.transaction() as connection:
                enqueued = await repository.enqueue(connection, _spec())
                first = (
                    await repository.claim(
                        connection,
                        worker_id="worker-a",
                        supported={enqueued.work.kind: frozenset({1})},
                        lease_seconds=120,
                        limit=1,
                    )
                )[0]
                await repository.record_attempt_trace(
                    connection,
                    first,
                    trace_id="1" * 32,
                    span_id="2" * 16,
                )
                await repository.fail(
                    connection,
                    first,
                    WorkFailure(category="provider", code="unavailable", retryable=True),
                    retry_delay_seconds=0,
                )

            async with profile.database.transaction() as connection:
                second = (
                    await repository.claim(
                        connection,
                        worker_id="worker-b",
                        supported={enqueued.work.kind: frozenset({1})},
                        lease_seconds=120,
                        limit=1,
                    )
                )[0]

            assert second.previous_trace_id == "1" * 32
            assert second.previous_span_id == "2" * 16

    asyncio.run(scenario())


def test_retention_purges_only_old_success_and_cancelled_history() -> None:
    async def scenario() -> None:
        async with SQLiteProfile.open(SQLiteConfig(), tables=WORK_TABLES) as profile:
            repository = WorkRepository()
            async with profile.database.transaction() as connection:
                succeeded = await repository.enqueue(connection, _spec())
                success_claim = (
                    await repository.claim(
                        connection,
                        worker_id="worker-a",
                        supported={succeeded.work.kind: frozenset({1})},
                        lease_seconds=120,
                        limit=1,
                    )
                )[0]
                await repository.complete(connection, success_claim, WorkResult(code="done", payload={}))

                cancelled = await repository.enqueue(
                    connection,
                    _spec(logical_key="c" * 64, lane_key="d" * 64),
                )
                await repository.cancel(
                    connection,
                    cancelled.work.work_id,
                    expected_version=cancelled.work.state_version,
                )

                failed = await repository.enqueue(
                    connection,
                    _spec(logical_key="e" * 64, lane_key="f" * 64).model_copy(update={"max_attempts": 1}),
                )
                failed_claim = (
                    await repository.claim(
                        connection,
                        worker_id="worker-a",
                        supported={failed.work.kind: frozenset({1})},
                        lease_seconds=120,
                        limit=1,
                    )
                )[0]
                await repository.fail(
                    connection,
                    failed_claim,
                    WorkFailure(category="provider", code="secret_free_code", retryable=False),
                    retry_delay_seconds=0,
                )

                old = datetime(2000, 1, 1, tzinfo=UTC)
                await connection.execute(
                    update(WORK_ITEMS_TABLE)
                    .where(WORK_ITEMS_TABLE.c.work_id.in_((succeeded.work.work_id, cancelled.work.work_id)))
                    .values(completed_at=old)
                )
                assert (
                    await repository.purge_terminal(
                        connection,
                        completed_before=datetime(2020, 1, 1, tzinfo=UTC),
                        limit=500,
                    )
                    == 2
                )
                attempts = await connection.scalar(select(func.count()).select_from(WORK_ATTEMPTS_TABLE))

            assert attempts == 1
            async with profile.database.transaction() as connection:
                with pytest.raises(RepositoryNotFoundError):
                    await repository.get(connection, succeeded.work.work_id)
                with pytest.raises(RepositoryNotFoundError):
                    await repository.get(connection, cancelled.work.work_id)
                blocked = await repository.get(connection, failed.work.work_id)
            assert blocked.status is WorkStatus.FAILED

    asyncio.run(scenario())
