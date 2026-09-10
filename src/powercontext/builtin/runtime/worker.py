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

"""Lease-aware Worker loop over the durable Work Ledger."""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable, Iterable
from contextlib import AbstractContextManager, nullcontext, suppress
from dataclasses import dataclass
from typing import Protocol

from powercontext._logging import log_safely
from powercontext.builtin.persistence.database import AsyncDatabase
from powercontext.builtin.persistence.work import (
    StaleWorkClaimError,
    StoredWork,
    WorkClaim,
    WorkCommit,
    WorkFailure,
    WorkRepository,
    WorkResult,
)
from powercontext.builtin.runtime.config import WorkerConfig
from powercontext.builtin.runtime.protocols import (
    RuntimeSpan,
    RuntimeTraceContext,
    RuntimeTracing,
    runtime_trace_context,
)
from powercontext.builtin.runtime.readiness import ReadinessCheckStatus
from powercontext.builtin.runtime.work_observability import WorkObserver, refresh_work_queue

ClaimReadiness = Callable[[], Awaitable[str]]
WorkAuthorizer = Callable[[WorkClaim], Awaitable[None]]
WorkSucceededObserver = Callable[[StoredWork], Awaitable[None]]
_EMPTY_HANDLERS = "at least one handler is required"
_INVALID_HANDLER_KIND = "handler kind must be a non-empty trimmed string"
_INVALID_HANDLER_VERSIONS = "handler versions must be positive"

logger = logging.getLogger(__name__)


class WorkHandler(Protocol):
    """Prepare one versioned task outside a database transaction."""

    kind: str
    supported_versions: frozenset[int]

    async def prepare(self, claim: WorkClaim, /) -> PreparedWork: ...


@dataclass(frozen=True)
class PreparedWork:
    """Sanitized result and optional domain write for the final transaction."""

    result: WorkResult | None
    commit: WorkCommit | None = None


class WorkExecutionError(RuntimeError):
    """A handler failure safe to classify in persistent operation metadata."""

    def __init__(self, *, category: str, code: str, retryable: bool) -> None:
        self.failure = WorkFailure(category=category, code=code, retryable=retryable)
        super().__init__(f"work handler failed with {category}/{code}")


class DurableWorker:
    """Claim no more than available slots and recover through lease expiry."""

    def __init__(
        self,
        *,
        database: AsyncDatabase,
        worker_id: str,
        handlers: Iterable[WorkHandler],
        config: WorkerConfig,
        repository: WorkRepository | None = None,
        random_source: Callable[[float, float], float] = random.uniform,
        claim_readiness: ClaimReadiness | None = None,
        authorizer: WorkAuthorizer | None = None,
        succeeded_observer: WorkSucceededObserver | None = None,
        observer: WorkObserver | None = None,
        tracing: RuntimeTracing | None = None,
    ) -> None:
        self._database = database
        self._worker_id = worker_id
        self._config = config
        self._repository = WorkRepository(observer=observer) if repository is None else repository
        self._random_source = random_source
        self._claim_readiness = claim_readiness
        self._authorizer = authorizer
        self._succeeded_observer = succeeded_observer
        self._observer = observer
        self._tracing = tracing
        self._handlers = _handler_map(handlers)
        self._stop_requested = asyncio.Event()
        self._wake_requested = asyncio.Event()
        self._claim_lock = asyncio.Lock()
        self._running: set[asyncio.Task[None]] = set()
        self._failed = False

    @property
    def supported(self) -> dict[str, frozenset[int]]:
        """Return bounded handler compatibility advertised by this Worker."""

        return {kind: handler.supported_versions for kind, handler in self._handlers.items()}

    async def run_once(self) -> int:
        """Claim current free capacity and await those attempts."""

        async with self._claim_lock:
            capacity = self._config.concurrency - len(self._running)
            if capacity <= 0 or self._stop_requested.is_set():
                return 0
            if self._claim_readiness is not None and await self._claim_readiness() != ReadinessCheckStatus.READY:
                return 0
            with self._background(
                "work.claim",
                operation="work.claim",
                attributes={"powercontext.work.claim.capacity": capacity},
            ) as span:
                claims = await self._claim_capacity(capacity)
                if span is not None:
                    span.set_attributes({"powercontext.work.claim.count": len(claims)})
            tasks = {asyncio.create_task(self._execute(claim)) for claim in claims}
            self._running.update(tasks)
            for task in tasks:
                task.add_done_callback(self._running.discard)
        if tasks:
            await asyncio.gather(*tasks)
        await refresh_work_queue(self._database, self._repository, self._observer)
        return len(claims)

    async def _claim_capacity(self, capacity: int) -> tuple[WorkClaim, ...]:
        """Claim at most one lane per short transaction."""

        claims: list[WorkClaim] = []
        supported = self.supported
        for _ in range(capacity):
            async with self._database.transaction() as connection:
                claimed = await self._repository.claim(
                    connection,
                    worker_id=self._worker_id,
                    supported=supported,
                    lease_seconds=self._config.lease_seconds,
                    limit=1,
                )
            if claimed:
                claims.extend(claimed)
            else:
                break
        return tuple(claims)

    async def run(self) -> None:
        """Poll until shutdown, executing each claimed batch concurrently."""

        while not self._stop_requested.is_set():
            self._wake_requested.clear()
            try:
                claimed = await self.run_once()
                self._failed = False
            except asyncio.CancelledError:
                raise
            except Exception:
                self._failed = True
                claimed = 0
            if claimed:
                continue
            with suppress(TimeoutError):
                await asyncio.wait_for(self._wake_requested.wait(), timeout=self._config.poll_seconds)

    def notify(self) -> None:
        """Wake the polling loop after a producer commits new work."""

        self._wake_requested.set()

    async def readiness(self) -> str:
        """Report database-loop and registered payload compatibility."""

        if self._failed:
            return ReadinessCheckStatus.UNAVAILABLE
        if self._claim_readiness is not None:
            dependency = await self._claim_readiness()
            if dependency != ReadinessCheckStatus.READY:
                return dependency
        async with self._database.transaction() as connection:
            unknown = await self._repository.unsupported_head_count(connection, self.supported)
        return ReadinessCheckStatus.MISCONFIGURED if unknown else ReadinessCheckStatus.READY

    async def stop(self) -> None:
        """Stop claiming, then give in-flight handlers their configured grace."""

        self._stop_requested.set()
        self._wake_requested.set()
        if not self._running:
            return
        _, pending = await asyncio.wait(self._running, timeout=self._config.shutdown_grace_seconds)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    async def _execute(self, claim: WorkClaim) -> None:
        links = (
            ()
            if claim.previous_trace_id is None or claim.previous_span_id is None
            else (RuntimeTraceContext(trace_id=claim.previous_trace_id, span_id=claim.previous_span_id),)
        )
        with self._background(
            "work.execute",
            operation="work.execute",
            attributes={
                "powercontext.work.kind": claim.kind,
                "powercontext.work.payload_version": claim.payload_version,
                "powercontext.work.attempt": claim.attempt_no,
                "powercontext.work.recovery_generation": claim.recovery_generation,
            },
            links=links,
        ) as span:
            context = runtime_trace_context(span)
            if context is not None and not await self._bind_attempt_trace(claim, context):
                if span is not None:
                    span.set_outcome("stale")
                return
            outcome = await self._execute_claim(claim)
            if span is not None:
                span.set_outcome(outcome)

    async def _execute_claim(self, claim: WorkClaim) -> str:
        handler = self._handlers[claim.kind]
        heartbeat_stop = asyncio.Event()
        heartbeat = asyncio.create_task(self._heartbeat(claim, heartbeat_stop))
        try:
            if self._authorizer is not None:
                await self._authorizer(claim)
            prepared = await handler.prepare(claim)
            if heartbeat.done() and heartbeat.exception() is not None:
                await heartbeat
            with self._stage(
                "work.commit",
                attributes={
                    "powercontext.work.kind": claim.kind,
                    "powercontext.work.payload_version": claim.payload_version,
                },
            ):
                async with self._database.transaction() as connection:
                    completed = await self._repository.complete(
                        connection,
                        claim,
                        prepared.result,
                        commit=prepared.commit,
                    )
        except asyncio.CancelledError:
            raise
        except StaleWorkClaimError:
            return await self._finish_cancel_if_owned(claim)
        except WorkExecutionError as error:
            return await self._record_failure(claim, error.failure)
        except Exception:
            return await self._record_failure(
                claim,
                WorkFailure(category="internal", code="unhandled_handler_error", retryable=True),
            )
        else:
            await self._observe_success(completed)
            return "succeeded"
        finally:
            heartbeat_stop.set()
            await asyncio.gather(heartbeat, return_exceptions=True)

    async def _observe_success(self, work: StoredWork) -> None:
        if self._succeeded_observer is None:
            return
        try:
            await self._succeeded_observer(work)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            log_safely(
                logger,
                logging.ERROR,
                "Work success observer failed",
                extra={
                    "event": "work.success_observer_failed",
                    "work_kind": work.kind,
                    "error_type": type(error).__name__,
                },
            )

    async def _finish_cancel_if_owned(self, claim: WorkClaim) -> str:
        """Converge a fenced cancelling claim without waiting for lease expiry."""

        try:
            async with self._database.transaction() as connection:
                work = await self._repository.fail(
                    connection,
                    claim,
                    WorkFailure(category="cancellation", code="cancel_requested", retryable=False),
                    retry_delay_seconds=0,
                )
        except StaleWorkClaimError:
            return "stale"
        return work.status.value

    async def _heartbeat(self, claim: WorkClaim, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=self._config.heartbeat_seconds)
            except TimeoutError:
                async with self._database.transaction() as connection:
                    await self._repository.heartbeat(
                        connection,
                        claim,
                        lease_seconds=self._config.lease_seconds,
                    )

    async def _bind_attempt_trace(self, claim: WorkClaim, context: RuntimeTraceContext) -> bool:
        try:
            async with self._database.transaction() as connection:
                await self._repository.record_attempt_trace(
                    connection,
                    claim,
                    trace_id=context.trace_id,
                    span_id=context.span_id,
                )
        except StaleWorkClaimError:
            return False
        return True

    async def _record_failure(self, claim: WorkClaim, failure: WorkFailure) -> str:
        ceiling = min(
            self._config.retry_max_seconds,
            self._config.retry_base_seconds * (2 ** max(claim.generation_attempt_no - 1, 0)),
        )
        delay = self._random_source(0.0, ceiling)
        try:
            with self._stage(
                "work.retry",
                attributes={
                    "powercontext.work.kind": claim.kind,
                    "powercontext.work.error_category": failure.category,
                    "powercontext.work.retryable": failure.retryable,
                },
            ) as span:
                async with self._database.transaction() as connection:
                    work = await self._repository.fail(
                        connection,
                        claim,
                        failure,
                        retry_delay_seconds=max(0, round(delay)),
                    )
                if span is not None:
                    span.set_outcome(work.status.value)
                return work.status.value
        except StaleWorkClaimError:
            return "stale"

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
        links: tuple[RuntimeTraceContext, ...] = (),
    ) -> AbstractContextManager[RuntimeSpan | None]:
        if self._tracing is None:
            return nullcontext(None)
        return self._tracing.background(name, operation=operation, attributes=attributes, links=links)


def _handler_map(handlers: Iterable[WorkHandler]) -> dict[str, WorkHandler]:
    registered: dict[str, WorkHandler] = {}
    for handler in handlers:
        if not handler.kind.strip() or handler.kind != handler.kind.strip():
            raise ValueError(_INVALID_HANDLER_KIND)
        if not handler.supported_versions or any(version < 1 for version in handler.supported_versions):
            raise ValueError(_INVALID_HANDLER_VERSIONS)
        if handler.kind in registered:
            message = f"duplicate handler kind: {handler.kind}"
            raise ValueError(message)
        registered[handler.kind] = handler
    if not registered:
        raise ValueError(_EMPTY_HANDLERS)
    return registered


__all__ = [
    "DurableWorker",
    "PreparedWork",
    "WorkExecutionError",
    "WorkHandler",
]
