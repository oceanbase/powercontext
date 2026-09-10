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
import logging
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.persistence.tables import WORK_ATTEMPTS_TABLE, WORK_ITEMS_TABLE, WORK_TABLES
from powercontext.builtin.persistence.work import WorkRepository, WorkResult, WorkSpec, WorkStatus
from powercontext.builtin.runtime.config import WorkerConfig
from powercontext.builtin.runtime.protocols import RuntimeTraceContext
from powercontext.builtin.runtime.worker import DurableWorker, PreparedWork, WorkExecutionError


class _Handler:
    kind = "test.handler.v1"
    supported_versions = frozenset({1})

    def __init__(self, executed: list[str]) -> None:
        self._executed = executed

    async def prepare(self, claim) -> PreparedWork:
        async def commit(_connection: AsyncConnection) -> None:
            self._executed.append(claim.work_id)

        return PreparedWork(result=WorkResult(code="done", payload={}), commit=commit)


class _OverlapHandler:
    kind = "test.handler.v1"
    supported_versions = frozenset({1})

    def __init__(self) -> None:
        self.active = 0
        self.maximum_active = 0
        self.both_entered = asyncio.Event()

    async def prepare(self, _claim) -> PreparedWork:
        self.active += 1
        self.maximum_active = max(self.maximum_active, self.active)
        if self.active == 2:
            self.both_entered.set()
        await asyncio.wait_for(self.both_entered.wait(), timeout=1)
        self.active -= 1
        return PreparedWork(result=WorkResult(code="done", payload={}))


class _FlakyHandler:
    kind = "test.handler.v1"
    supported_versions = frozenset({1})

    def __init__(self) -> None:
        self.calls = 0

    async def prepare(self, _claim) -> PreparedWork:
        self.calls += 1
        if self.calls == 1:
            raise WorkExecutionError(category="provider", code="unavailable", retryable=True)
        return PreparedWork(result=WorkResult(code="done", payload={}))


class _BlockingHandler:
    kind = "test.handler.v1"
    supported_versions = frozenset({1})

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def prepare(self, _claim) -> PreparedWork:
        self.started.set()
        await self.release.wait()
        return PreparedWork(result=WorkResult(code="done", payload={}))


class _SensitiveFailureHandler:
    kind = "test.handler.v1"
    supported_versions = frozenset({1})

    def __init__(self, sensitive_text: str) -> None:
        self._sensitive_text = sensitive_text

    async def prepare(self, _claim) -> PreparedWork:
        raise RuntimeError(self._sensitive_text)


class _TraceSpan:
    def __init__(self, trace_context: RuntimeTraceContext | None = None) -> None:
        self.trace_context = trace_context

    def set_attributes(self, _attributes) -> None:
        return

    def set_outcome(self, _outcome: str) -> None:
        return


class _Tracing:
    def __init__(self) -> None:
        self.execute_links: list[tuple[RuntimeTraceContext, ...]] = []
        self.attributes: list[dict[str, str | bool | int | float]] = []

    @contextmanager
    def stage(
        self,
        name: str,
        *,
        attributes: Mapping[str, str | bool | int | float],
    ) -> Iterator[_TraceSpan]:
        del name
        self.attributes.append(dict(attributes))
        yield _TraceSpan()

    @contextmanager
    def background(
        self,
        name: str,
        *,
        operation: str,
        attributes: Mapping[str, str | bool | int | float],
        links: Sequence[RuntimeTraceContext] = (),
    ) -> Iterator[_TraceSpan]:
        del operation
        self.attributes.append(dict(attributes))
        links = tuple(links)
        if name == "work.execute":
            self.execute_links.append(links)
            index = len(self.execute_links)
            yield _TraceSpan(RuntimeTraceContext(trace_id=f"{index:032x}", span_id=f"{index:016x}"))
            return
        yield _TraceSpan()


def _spec(index: int) -> WorkSpec:
    return WorkSpec(
        kind="test.handler.v1",
        payload_version=1,
        scope_id=f"scope:{index}",
        lane_key=f"{index + 1:064x}",
        logical_key=f"{index + 101:064x}",
        payload={},
    )


def test_worker_claims_only_free_slots_and_executes_different_lanes_concurrently() -> None:
    async def scenario() -> None:
        async with SQLiteProfile.open(SQLiteConfig(), tables=WORK_TABLES) as profile:
            executed: list[str] = []
            handler = _Handler(executed)
            repository = WorkRepository()
            async with profile.database.transaction() as connection:
                first = await repository.enqueue(connection, _spec(1))
                second = await repository.enqueue(connection, _spec(2))

            worker = DurableWorker(
                database=profile.database,
                worker_id="worker-a",
                handlers=(handler,),
                config=WorkerConfig(concurrency=2),
            )
            assert await worker.run_once() == 2

            async with profile.database.transaction() as connection:
                first_stored = await repository.get(connection, first.work.work_id)
                second_stored = await repository.get(connection, second.work.work_id)
            assert first_stored.status is WorkStatus.SUCCEEDED
            assert second_stored.status is WorkStatus.SUCCEEDED
            assert set(executed) == {first.work.work_id, second.work.work_id}

    asyncio.run(scenario())


def test_worker_overlaps_different_lanes_but_never_claims_an_unknown_payload() -> None:
    async def scenario() -> None:
        async with SQLiteProfile.open(SQLiteConfig(), tables=WORK_TABLES) as profile:
            handler = _OverlapHandler()
            repository = WorkRepository()
            async with profile.database.transaction() as connection:
                await repository.enqueue(connection, _spec(1))
                await repository.enqueue(connection, _spec(2))

            worker = DurableWorker(
                database=profile.database,
                worker_id="worker-a",
                handlers=(handler,),
                config=WorkerConfig(concurrency=2),
            )
            assert await worker.run_once() == 2
            assert handler.maximum_active == 2

            unknown = _spec(3).model_copy(update={"payload_version": 2})
            async with profile.database.transaction() as connection:
                enqueued = await repository.enqueue(connection, unknown)
            assert await worker.readiness() == "misconfigured"
            assert await worker.run_once() == 0
            async with profile.database.transaction() as connection:
                stored = await repository.get(connection, enqueued.work.work_id)
            assert stored.status is WorkStatus.QUEUED

    asyncio.run(scenario())


def test_retried_worker_attempt_links_to_the_persisted_previous_span() -> None:
    async def scenario() -> None:
        async with SQLiteProfile.open(SQLiteConfig(), tables=WORK_TABLES) as profile:
            repository = WorkRepository()
            async with profile.database.transaction() as connection:
                enqueued = await repository.enqueue(connection, _spec(1))

            tracing = _Tracing()
            worker = DurableWorker(
                database=profile.database,
                worker_id="worker-a",
                handlers=(_FlakyHandler(),),
                config=WorkerConfig(concurrency=1, retry_base_seconds=0.01, retry_max_seconds=0.01),
                random_source=lambda _low, _high: 0,
                tracing=tracing,
            )
            assert await worker.run_once() == 1
            assert await worker.run_once() == 1
            async with profile.database.transaction() as connection:
                completed = await repository.get(connection, enqueued.work.work_id)

            assert completed.status is WorkStatus.SUCCEEDED
            assert tracing.execute_links == [
                (),
                (RuntimeTraceContext(trace_id=f"{1:032x}", span_id=f"{1:016x}"),),
            ]

    asyncio.run(scenario())


def test_worker_converges_a_running_cancel_without_waiting_for_lease_expiry() -> None:
    async def scenario() -> None:
        async with SQLiteProfile.open(SQLiteConfig(), tables=WORK_TABLES) as profile:
            repository = WorkRepository()
            handler = _BlockingHandler()
            async with profile.database.transaction() as connection:
                enqueued = await repository.enqueue(connection, _spec(1))

            worker = DurableWorker(
                database=profile.database,
                worker_id="worker-a",
                handlers=(handler,),
                config=WorkerConfig(concurrency=1),
            )
            attempt = asyncio.create_task(worker.run_once())
            await asyncio.wait_for(handler.started.wait(), timeout=1)
            async with profile.database.transaction() as connection:
                running = await repository.get(connection, enqueued.work.work_id)
                cancelling = await repository.cancel(
                    connection,
                    running.work_id,
                    expected_version=running.state_version,
                )
            assert cancelling.status is WorkStatus.CANCELLING

            handler.release.set()
            assert await asyncio.wait_for(attempt, timeout=1) == 1
            async with profile.database.transaction() as connection:
                cancelled = await repository.get(connection, enqueued.work.work_id)
                outcome = await connection.scalar(
                    select(WORK_ATTEMPTS_TABLE.c.outcome).where(WORK_ATTEMPTS_TABLE.c.work_id == enqueued.work.work_id)
                )

            assert cancelled.status is WorkStatus.CANCELLED
            assert outcome == "cancelled"

    asyncio.run(scenario())


def test_worker_does_not_persist_or_observe_sensitive_exception_text(caplog) -> None:
    sensitive_text = "source-content=private credential=https://user:secret@example.test"

    async def scenario() -> None:
        async with SQLiteProfile.open(SQLiteConfig(), tables=WORK_TABLES) as profile:
            repository = WorkRepository()
            async with profile.database.transaction() as connection:
                enqueued = await repository.enqueue(connection, _spec(1))

            tracing = _Tracing()
            worker = DurableWorker(
                database=profile.database,
                worker_id="worker-a",
                handlers=(_SensitiveFailureHandler(sensitive_text),),
                config=WorkerConfig(concurrency=1),
                random_source=lambda _low, _high: 0,
                tracing=tracing,
            )
            with caplog.at_level(logging.DEBUG):
                assert await worker.run_once() == 1

            async with profile.database.transaction() as connection:
                work = (
                    (
                        await connection.execute(
                            select(WORK_ITEMS_TABLE).where(WORK_ITEMS_TABLE.c.work_id == enqueued.work.work_id)
                        )
                    )
                    .mappings()
                    .one()
                )
                attempt = (
                    (
                        await connection.execute(
                            select(WORK_ATTEMPTS_TABLE).where(WORK_ATTEMPTS_TABLE.c.work_id == enqueued.work.work_id)
                        )
                    )
                    .mappings()
                    .one()
                )

            assert work["status"] == WorkStatus.RETRY_WAIT.value
            assert work["error_category"] == "internal"
            assert work["error_code"] == "unhandled_handler_error"
            assert sensitive_text not in repr(dict(work))
            assert sensitive_text not in repr(dict(attempt))
            assert sensitive_text not in caplog.text
            assert sensitive_text not in repr(tracing.attributes)

    asyncio.run(scenario())
