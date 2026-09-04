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

from powercontext.builtin.persistence.coordination import CoordinationRepository, StaleCoordinatorLeaseError
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.persistence.tables import COORDINATION_TABLES, SCHEDULER_LEASES_TABLE, WORK_TABLES
from powercontext.builtin.persistence.work import WorkRepository, WorkSpec
from powercontext.builtin.runtime.config import CoordinationConfig
from powercontext.builtin.runtime.durable_scheduler import DiscoveryPage, DurableScheduler


class _Discoverer:
    name = "test-discoverer"
    interval_seconds = 60.0

    def __init__(self) -> None:
        self.pages: list[str | None] = []

    async def page(self, continuation: str | None, limit: int) -> DiscoveryPage:
        self.pages.append(continuation)
        if continuation is None:
            return DiscoveryPage(specs=(_spec(1),), continuation="next")
        return DiscoveryPage(specs=(_spec(2),), continuation=None)


class _PausingDiscoverer:
    name = "pausing-discoverer"
    interval_seconds = 60.0

    def __init__(self) -> None:
        self.entered = asyncio.Event()
        self.resume = asyncio.Event()

    async def page(self, continuation: str | None, limit: int) -> DiscoveryPage:
        del continuation, limit
        self.entered.set()
        await self.resume.wait()
        return DiscoveryPage(specs=(_spec(1),), continuation=None)


def _spec(index: int) -> WorkSpec:
    return WorkSpec(
        kind="test.handler.v1",
        payload_version=1,
        scope_id=f"scope:{index}",
        lane_key=f"{index + 1:064x}",
        logical_key=f"{index + 101:064x}",
        payload={},
    )


def test_scheduler_persists_keyset_continuation_and_enqueues_under_its_fence() -> None:
    async def scenario() -> None:
        tables = WORK_TABLES + COORDINATION_TABLES
        async with SQLiteProfile.open(SQLiteConfig(), tables=tables) as profile:
            discoverer = _Discoverer()
            scheduler = DurableScheduler(
                database=profile.database,
                scheduler_id="scheduler-a",
                discoverers=(discoverer,),
                config=CoordinationConfig(),
            )
            assert await scheduler.tick() is True
            assert await scheduler.tick() is True

            async with profile.database.transaction() as connection:
                work = await WorkRepository().list(connection)
            assert len(work) == 2
            assert discoverer.pages == [None, "next"]

            await scheduler.stop()

    asyncio.run(scenario())


def test_old_scheduler_cannot_enqueue_after_a_higher_fence_takes_over() -> None:
    async def scenario() -> None:
        tables = WORK_TABLES + COORDINATION_TABLES
        async with SQLiteProfile.open(SQLiteConfig(), tables=tables) as profile:
            discoverer = _PausingDiscoverer()
            scheduler = DurableScheduler(
                database=profile.database,
                scheduler_id="scheduler-a",
                discoverers=(discoverer,),
                config=CoordinationConfig(),
            )
            stale_tick = asyncio.create_task(scheduler.tick())
            await asyncio.wait_for(discoverer.entered.wait(), timeout=1)

            coordination = CoordinationRepository()
            async with profile.database.transaction() as connection:
                await connection.execute(
                    update(SCHEDULER_LEASES_TABLE)
                    .where(SCHEDULER_LEASES_TABLE.c.lease_name == "work-discovery")
                    .values(lease_expires_at=datetime(2000, 1, 1, tzinfo=UTC))
                )
                replacement = await coordination.acquire_lease(
                    connection,
                    lease_name="work-discovery",
                    owner_id="scheduler-b",
                    lease_seconds=30,
                )
            assert replacement is not None

            discoverer.resume.set()
            with pytest.raises(StaleCoordinatorLeaseError):
                await stale_tick
            async with profile.database.transaction() as connection:
                assert await WorkRepository().list(connection) == ()

    asyncio.run(scenario())
