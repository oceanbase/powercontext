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

import pytest
from sqlalchemy import update

from powercontext.builtin.persistence.coordination import (
    CoordinationRepository,
    StaleCoordinatorLeaseError,
    StaleScanStateError,
)
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.persistence.tables import COORDINATION_TABLES, SCHEDULER_LEASES_TABLE


def test_scheduler_lease_takeover_increments_fence_and_rejects_the_old_owner() -> None:
    async def scenario() -> None:
        async with SQLiteProfile.open(SQLiteConfig(), tables=COORDINATION_TABLES) as profile:
            repository = CoordinationRepository()
            async with profile.database.transaction() as connection:
                first = await repository.acquire_lease(
                    connection,
                    lease_name="scheduler",
                    owner_id="scheduler-a",
                    lease_seconds=30,
                )
                assert first is not None
                renewed = await repository.acquire_lease(
                    connection,
                    lease_name="scheduler",
                    owner_id="scheduler-a",
                    lease_seconds=30,
                )
                assert renewed is not None
                assert renewed.fence == first.fence

            async with profile.database.transaction() as connection:
                await connection.execute(
                    update(SCHEDULER_LEASES_TABLE)
                    .where(SCHEDULER_LEASES_TABLE.c.lease_name == "scheduler")
                    .values(lease_expires_at=datetime(2000, 1, 1, tzinfo=UTC))
                )
                second = await repository.acquire_lease(
                    connection,
                    lease_name="scheduler",
                    owner_id="scheduler-b",
                    lease_seconds=30,
                )

            assert second is not None
            assert second.fence > first.fence
            with pytest.raises(StaleCoordinatorLeaseError):
                async with profile.database.transaction() as connection:
                    await repository.assert_lease(connection, first)

    asyncio.run(scenario())


def test_scheduler_scan_state_uses_versioned_keyset_continuation() -> None:
    async def scenario() -> None:
        async with SQLiteProfile.open(SQLiteConfig(), tables=COORDINATION_TABLES) as profile:
            repository = CoordinationRepository()
            async with profile.database.transaction() as connection:
                initial = await repository.load_scan(connection, "memory")
                assert initial is None
                first = await repository.save_scan(
                    connection,
                    "memory",
                    next_run_at=datetime(2030, 1, 1, tzinfo=UTC),
                    continuation="project:a",
                    expected_version=None,
                )
            assert first.state_version == 1
            assert first.continuation == "project:a"

            async with profile.database.transaction() as connection:
                second = await repository.save_scan(
                    connection,
                    "memory",
                    next_run_at=datetime(2030, 1, 2, tzinfo=UTC),
                    continuation=None,
                    expected_version=first.state_version,
                )
            assert second.state_version == 2

            with pytest.raises(StaleScanStateError):
                async with profile.database.transaction() as connection:
                    await repository.save_scan(
                        connection,
                        "memory",
                        next_run_at=datetime(2030, 1, 3, tzinfo=UTC),
                        continuation=None,
                        expected_version=first.state_version,
                    )

    asyncio.run(scenario())
