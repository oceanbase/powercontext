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
from datetime import UTC, datetime

import pytest
from sqlalchemy import update

from powercontext.builtin.persistence.errors import RepositoryNotFoundError
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.persistence.tables import COORDINATION_TABLES, WORK_ITEMS_TABLE, WORK_TABLES
from powercontext.builtin.persistence.work import WorkRepository, WorkResult, WorkSpec, WorkStatus
from powercontext.builtin.runtime.config import WorkerConfig
from powercontext.builtin.runtime.work_handlers import OperationMaintenanceDiscoverer, OperationMaintenanceHandler
from powercontext.builtin.runtime.worker import DurableWorker


def test_operation_retention_runs_as_bounded_durable_work() -> None:
    async def scenario() -> None:
        async with SQLiteProfile.open(SQLiteConfig(), tables=WORK_TABLES + COORDINATION_TABLES) as profile:
            repository = WorkRepository()
            old_spec = WorkSpec(
                kind="test.completed",
                payload_version=1,
                scope_id="project:test",
                lane_key="a" * 64,
                logical_key="b" * 64,
                payload={},
            )
            async with profile.database.transaction() as connection:
                old = await repository.enqueue(connection, old_spec)
                claim = (
                    await repository.claim(
                        connection,
                        worker_id="setup",
                        supported={old_spec.kind: frozenset({1})},
                        lease_seconds=120,
                        limit=1,
                    )
                )[0]
                await repository.complete(connection, claim, WorkResult(code="done", payload={}))
                await connection.execute(
                    update(WORK_ITEMS_TABLE)
                    .where(WORK_ITEMS_TABLE.c.work_id == old.work.work_id)
                    .values(completed_at=datetime(2000, 1, 1, tzinfo=UTC))
                )

            discoverer = OperationMaintenanceDiscoverer(interval_seconds=3600, max_attempts=5)
            page = await discoverer.page(None, 100)
            async with profile.database.transaction() as connection:
                maintenance = await repository.enqueue(connection, page.specs[0])

            worker = DurableWorker(
                database=profile.database,
                worker_id="maintenance-worker",
                handlers=(OperationMaintenanceHandler(retention_days=1, batch_size=500),),
                config=WorkerConfig(concurrency=1),
            )
            assert await worker.run_once() == 1

            async with profile.database.transaction() as connection:
                with pytest.raises(RepositoryNotFoundError):
                    await repository.get(connection, old.work.work_id)
                completed = await repository.get(connection, maintenance.work.work_id)
            assert completed.status is WorkStatus.SUCCEEDED
            assert completed.result_payload == {
                "operations_deleted": 1,
                "rate_limit_windows_deleted": 0,
            }

    asyncio.run(scenario())
