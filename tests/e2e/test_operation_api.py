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

import pytest

from powercontext.builtin.persistence.errors import RepositoryNotFoundError
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.persistence.work import (
    StaleWorkClaimError,
    WorkRepository,
    WorkSpec,
    WorkStateConflictError,
    WorkStatus,
)
from powercontext.builtin.runtime import BuiltinConfig, open_builtin_contexts
from powercontext.builtin.runtime.config import OperationsConfig, WorkerConfig
from powercontext.builtin.runtime.operations import OperationManager
from powercontext.builtin.runtime.work_handlers import EXPERIENCE_WORK_KIND
from powercontext.builtin.sources import ContentCapture


def _public_spec() -> WorkSpec:
    return WorkSpec(
        kind=EXPERIENCE_WORK_KIND,
        payload_version=1,
        scope_id="project",
        lane_key="a" * 64,
        logical_key="b" * 64,
        payload={
            "cursor_name": "experience.incubation",
            "cursor_generation": 0,
            "after": 0,
            "through": 1,
            "high_watermark": 1,
        },
    )


def _internal_spec() -> WorkSpec:
    return WorkSpec(
        kind="powercontext.maintenance.operations",
        payload_version=1,
        scope_id="system:operations",
        lane_key="c" * 64,
        logical_key="d" * 64,
        payload={},
    )


def test_operation_manager_queries_public_operations_and_mutates_optimistically(tmp_path) -> None:
    async def scenario() -> None:
        config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'operations.db'}"))
        async with open_builtin_contexts(config) as contexts:
            context = await contexts.get("project")
            await context.sources.capture(ContentCapture(source_id="one", content="queued work"))
            manager = OperationManager(
                contexts=contexts,
                operations=OperationsConfig(),
                worker=WorkerConfig(),
                local_worker=None,
                payload_version=1,
            )
            repository = WorkRepository()
            async with contexts.database.transaction() as connection:
                public = await repository.enqueue(connection, _public_spec())
                internal = await repository.enqueue(connection, _internal_spec())

            stored = await manager.get(public.work.work_id)
            assert stored.status is WorkStatus.QUEUED

            # Maintenance work stays private: the public facade hides it.
            with pytest.raises(RepositoryNotFoundError):
                await manager.get(internal.work.work_id)

            page = await manager.list(scope_id="project", kind=None, status=None, cursor=None, limit=10)
            assert [item.work_id for item in page.items] == [public.work.work_id]
            assert page.next_cursor is None

            # Optimistic mutations: a stale version loses, the current version wins.
            with pytest.raises(WorkStateConflictError):
                await manager.cancel(public.work.work_id, expected_version=stored.state_version + 1)
            cancelled = await manager.cancel(public.work.work_id, expected_version=stored.state_version)
            assert cancelled.status is WorkStatus.CANCELLED

            # Cancellation releases the logical key, so the closed operation can
            # neither be rejoined nor retried.
            with pytest.raises(StaleWorkClaimError):
                await manager.retry(public.work.work_id, expected_version=cancelled.state_version)

            observed = await manager.wait(public.work.work_id, timeout_seconds=0)
            assert observed is not None and observed.status is WorkStatus.CANCELLED

    asyncio.run(scenario())
