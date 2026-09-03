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

"""Fenced leader scheduler with durable bounded scan continuations."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from contextlib import AbstractContextManager, nullcontext, suppress
from dataclasses import dataclass
from datetime import timedelta
from typing import Protocol

from powercontext.builtin.persistence.coordination import (
    CoordinationRepository,
    CoordinatorLease,
    StaleCoordinatorLeaseError,
)
from powercontext.builtin.persistence.database import AsyncDatabase, database_now
from powercontext.builtin.persistence.work import WorkRepository, WorkSpec
from powercontext.builtin.runtime.config import CoordinationConfig
from powercontext.builtin.runtime.protocols import RuntimeSpan, RuntimeTracing
from powercontext.builtin.runtime.readiness import ReadinessCheckStatus
from powercontext.builtin.runtime.work_observability import WorkObserver, refresh_work_queue

_SCHEDULER_LEASE_NAME = "work-discovery"
_EMPTY_DISCOVERERS = "at least one work discoverer is required"
_INVALID_DISCOVERER_INTERVAL = "discoverer interval must be positive"
_INVALID_DISCOVERER_NAME = "discoverer name must be a non-empty trimmed string"


@dataclass(frozen=True)
class DiscoveryPage:
    """One bounded page and its next opaque keyset position."""

    specs: tuple[WorkSpec, ...]
    continuation: str | None


class WorkDiscoverer(Protocol):
    """Find logical work without executing external side effects."""

    name: str
    interval_seconds: float

    async def page(self, continuation: str | None, limit: int, /) -> DiscoveryPage: ...


class DurableScheduler:
    """Acquire one leader lease and enqueue pages under its exact fence."""

    def __init__(
        self,
        *,
        database: AsyncDatabase,
        scheduler_id: str,
        discoverers: Iterable[WorkDiscoverer],
        config: CoordinationConfig,
        coordination: CoordinationRepository | None = None,
        work: WorkRepository | None = None,
        observer: WorkObserver | None = None,
        tracing: RuntimeTracing | None = None,
    ) -> None:
        self._database = database
        self._scheduler_id = scheduler_id
        self._discoverers = _discoverer_map(discoverers)
        self._config = config
        self._coordination = CoordinationRepository() if coordination is None else coordination
        self._work = WorkRepository(observer=observer) if work is None else work
        self._observer = observer
        self._tracing = tracing
        self._poll_seconds = min(
            float(config.scheduler_renew_seconds),
            *(discoverer.interval_seconds for discoverer in self._discoverers.values()),
        )
        self._lease: CoordinatorLease | None = None
        self._stop_requested = asyncio.Event()
        self._failed = False

    async def tick(self) -> bool:
        """Renew or acquire leadership and process at most one page per discoverer."""

        previous = self._lease
        with self._background(
            "scheduler.tick",
            operation="scheduler.tick",
            attributes={"powercontext.scheduler.discoverer_count": len(self._discoverers)},
        ) as span:
            async with self._database.transaction() as connection:
                lease = await self._coordination.acquire_lease(
                    connection,
                    lease_name=_SCHEDULER_LEASE_NAME,
                    owner_id=self._scheduler_id,
                    lease_seconds=self._config.scheduler_lease_seconds,
                )
            self._lease = lease
            self._observe_leadership(previous, lease)
            if lease is None:
                if span is not None:
                    span.set_outcome("standby")
                return False
            for discoverer in self._discoverers.values():
                await self._scan_once(discoverer, lease)
            if span is not None:
                span.set_outcome("leader")
        await refresh_work_queue(self._database, self._work, self._observer)
        return True

    async def run(self) -> None:
        """Maintain leadership until stopped; standby instances keep polling."""

        while not self._stop_requested.is_set():
            try:
                await self.tick()
                self._failed = False
            except asyncio.CancelledError:
                raise
            except StaleCoordinatorLeaseError:
                self._observe_leadership(self._lease, None)
                self._lease = None
            except Exception:
                self._observe_leadership(self._lease, None)
                self._lease = None
                self._failed = True
            with suppress(TimeoutError):
                await asyncio.wait_for(
                    self._stop_requested.wait(),
                    timeout=self._poll_seconds,
                )

    async def readiness(self) -> str:
        """Treat both a healthy leader and a healthy standby as ready."""

        return ReadinessCheckStatus.UNAVAILABLE if self._failed else ReadinessCheckStatus.READY

    async def stop(self) -> None:
        """Stop discovery and conditionally release current leadership."""

        self._stop_requested.set()
        lease = self._lease
        self._lease = None
        self._observe_leadership(lease, None)
        if lease is None:
            return
        async with self._database.transaction() as connection:
            await self._coordination.release_lease(connection, lease)

    async def _scan_once(self, discoverer: WorkDiscoverer, lease: CoordinatorLease) -> None:
        async with self._database.transaction() as connection:
            await self._coordination.assert_lease(connection, lease)
            scan = await self._coordination.load_scan(connection, discoverer.name)
            now = await database_now(connection)
        if scan is not None and scan.next_run_at > now:
            return

        continuation = None if scan is None else scan.continuation
        page = await discoverer.page(continuation, self._config.scan_page_size)
        if len(page.specs) > self._config.scan_page_size:
            message = f"discoverer {discoverer.name} exceeded the configured scan page"
            raise ValueError(message)
        for spec in page.specs:
            with self._stage(
                "work.enqueue",
                attributes={
                    "powercontext.work.kind": spec.kind,
                    "powercontext.work.payload_version": spec.payload_version,
                },
            ) as span:
                async with self._database.transaction() as connection:
                    await self._coordination.assert_lease(connection, lease)
                    result = await self._work.enqueue(connection, spec)
                if span is not None:
                    span.set_outcome("created" if result.created else "joined")

        async with self._database.transaction() as connection:
            await self._coordination.assert_lease(connection, lease)
            now = await database_now(connection)
            await self._coordination.save_scan(
                connection,
                discoverer.name,
                next_run_at=now
                if page.continuation is not None
                else now + timedelta(seconds=discoverer.interval_seconds),
                continuation=page.continuation,
                expected_version=None if scan is None else scan.state_version,
            )

    def _observe_leadership(
        self,
        previous: CoordinatorLease | None,
        current: CoordinatorLease | None,
    ) -> None:
        if self._observer is None:
            return
        outcome = None
        if previous is None and current is not None:
            outcome = "acquired"
        elif previous is not None and current is None:
            outcome = "lost"
        elif previous is not None and current is not None and previous.fence != current.fence:
            outcome = "reacquired"
        if outcome is not None:
            with suppress(Exception):
                self._observer.observe_scheduler_leadership(outcome=outcome)

    def _stage(
        self,
        name: str,
        *,
        attributes: dict[str, str | bool | int | float],
    ) -> AbstractContextManager[RuntimeSpan | None]:
        if self._tracing is None:
            return nullcontext(None)
        return self._tracing.stage(name, attributes=attributes)

    def _background(
        self,
        name: str,
        *,
        operation: str,
        attributes: dict[str, str | bool | int | float],
    ) -> AbstractContextManager[RuntimeSpan | None]:
        if self._tracing is None:
            return nullcontext(None)
        return self._tracing.background(name, operation=operation, attributes=attributes)


def _discoverer_map(discoverers: Iterable[WorkDiscoverer]) -> dict[str, WorkDiscoverer]:
    registered: dict[str, WorkDiscoverer] = {}
    for discoverer in discoverers:
        if not discoverer.name.strip() or discoverer.name != discoverer.name.strip():
            raise ValueError(_INVALID_DISCOVERER_NAME)
        if discoverer.interval_seconds <= 0:
            raise ValueError(_INVALID_DISCOVERER_INTERVAL)
        if discoverer.name in registered:
            message = f"duplicate discoverer name: {discoverer.name}"
            raise ValueError(message)
        registered[discoverer.name] = discoverer
    if not registered:
        raise ValueError(_EMPTY_DISCOVERERS)
    return registered


__all__ = [
    "DiscoveryPage",
    "DurableScheduler",
    "WorkDiscoverer",
]
