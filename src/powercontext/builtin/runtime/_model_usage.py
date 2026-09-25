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

"""Bounded, best-effort model usage, owned by one relational runtime."""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from powercontext._logging import log_safely
from powercontext.builtin.inference import InferenceUsage
from powercontext.builtin.persistence.database import AsyncDatabase, is_transaction_contention
from powercontext.builtin.persistence.statistics import StatisticsRepository
from powercontext.builtin.persistence.tables import SCOPES_TABLE
from powercontext.builtin.statistics import ModelUsageOperation, ModelUsagePurpose

_LOGGER = logging.getLogger(__name__)

# A contended attempt on SQLite usually returns at once: the transaction begins
# deferred, and the write upgrade does not consult the busy handler. The budget
# bounds the retry window, and the backoff interval bounds how many attempts fit
# inside it; the slice below only caps a single attempt that does wait.
_WRITE_ATTEMPT_SLICES = 4
_MIN_WRITE_ATTEMPT_SECONDS = 0.02
_RETRY_BACKOFF_SECONDS = 0.02


@dataclass(frozen=True, slots=True)
class _ModelUsageRecord:
    scope_id: str
    usage_date: date
    purpose: ModelUsagePurpose
    operation: ModelUsageOperation
    usage: InferenceUsage


def _snapshot(
    scope_id: str,
    purpose: ModelUsagePurpose,
    operation: ModelUsageOperation,
    usage: InferenceUsage,
    usage_date: date,
) -> _ModelUsageRecord:
    return _ModelUsageRecord(scope_id, usage_date, purpose, operation, usage.model_copy(deep=True))


class _ModelUsageRecorder:
    """Offer without I/O; serialize independent writes on one owned consumer.

    Checkpoints identify accepted records, not successful writes. A failed or
    indeterminate transaction is settled once and is never retried. This is
    deliberately lossy telemetry, not an authoritative billing ledger.
    """

    def __init__(
        self,
        database: AsyncDatabase,
        repository: StatisticsRepository,
        *,
        queue_capacity: int = 256,
        write_timeout_seconds: float = 1.0,
        flush_timeout_seconds: float = 0.5,
    ) -> None:
        if queue_capacity < 1 or write_timeout_seconds <= 0 or flush_timeout_seconds <= 0:
            raise ValueError("model usage capacity and timeouts must be positive")  # noqa: TRY003
        self._database = database
        self._repository = repository
        self._capacity = queue_capacity
        self._write_timeout_seconds = write_timeout_seconds
        self._flush_timeout_seconds = flush_timeout_seconds
        self._pending: deque[tuple[int, _ModelUsageRecord]] = deque()
        self._offered = 0
        self._settled = 0
        self._ready = asyncio.Event()
        self._advanced = asyncio.Event()
        self._consumer: asyncio.Task[None] | None = None
        self._closed = False

    def offer(
        self,
        scope_id: str,
        purpose: ModelUsagePurpose,
        operation: ModelUsageOperation,
        usage: InferenceUsage,
        usage_date: date,
    ) -> None:
        """Copy and enqueue a record without awaiting or accessing the database."""

        try:
            if self._closed:
                return
            if usage.requests == 0:
                return
            if len(self._pending) >= self._capacity:
                log_safely(_LOGGER, logging.WARNING, "Model usage queue full; record dropped")
                return
            record = _snapshot(scope_id, purpose, operation, usage, usage_date)
            if self._consumer is None:
                # Get the loop before constructing the coroutine so a sync caller
                # outside a loop cannot leave an unawaited coroutine behind.
                loop = asyncio.get_running_loop()
                self._consumer = loop.create_task(self._consume(), name="powercontext-model-usage")
                self._consumer.add_done_callback(self._consumer_finished)
            self._offered += 1
            self._pending.append((self._offered, record))
            self._ready.set()
        except Exception:
            # Never log usage payloads, identifiers, SQL or driver error text.
            log_safely(_LOGGER, logging.WARNING, "Model usage record could not be queued")

    def checkpoint(self) -> int:
        return self._offered

    async def flush(self, through: int | None = None) -> None:
        """Wait only for the entry prefix, without cancelling its consumer."""

        target = self.checkpoint() if through is None else min(through, self.checkpoint())
        if self._consumer is None or self._settled >= target:
            return
        try:
            async with asyncio.timeout(self._flush_timeout_seconds):
                while self._settled < target:
                    await self._advanced.wait()
        except TimeoutError:
            log_safely(_LOGGER, logging.WARNING, "Model usage flush timed out; continuing without statistics")

    async def close(self) -> None:
        """Stop offers, then drain every accepted record within a bounded budget."""

        self._closed = True
        consumer = self._consumer
        if consumer is None:
            return
        self._ready.set()
        # Each accepted record already carries a bounded write budget, so waiting
        # one record plus one flush window lets an in-flight write finish instead
        # of abandoning it to a database that is about to close underneath it.
        budget = self._write_timeout_seconds + self._flush_timeout_seconds
        try:
            async with asyncio.timeout(budget):
                while self._settled < self._offered:
                    await self._advanced.wait()
        except TimeoutError:
            log_safely(_LOGGER, logging.WARNING, "Model usage shutdown dropped queued records")
        finally:
            self._pending.clear()
            self._ready.set()
        # No task cancellation is sent into SQLite. Its native deadline stops VM
        # work; cleanup is still owned by the consumer if the OS itself stalls.
        _, pending = await asyncio.wait((consumer,), timeout=self._write_timeout_seconds)
        if pending:
            log_safely(_LOGGER, logging.WARNING, "Model usage native cleanup still pending at shutdown")

    async def _consume(self) -> None:
        try:
            while True:
                if not self._pending:
                    if self._closed:
                        return
                    self._ready.clear()
                    await self._ready.wait()
                    continue
                sequence, record = self._pending.popleft()
                try:
                    await self._write(record)
                except Exception:
                    log_safely(_LOGGER, logging.WARNING, "Model usage write failed; record will not be retried")
                finally:
                    self._settle(sequence)
        finally:
            self._closed = True
            self._pending.clear()
            self._settle(self._offered)

    async def _write(self, record: _ModelUsageRecord) -> None:
        """Write one record, retrying only a failure that rolled back cleanly.

        A busy or conflict error leaves nothing applied, so repeating it cannot
        double count and is worth the rest of this record's budget. Any other
        failure may have committed with an unknown outcome and is never retried.
        """

        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._write_timeout_seconds
        # A fixed slice per attempt rather than a re-sliced remainder, so one
        # attempt that really does wait cannot consume the whole budget. Contended
        # attempts normally return immediately, so the backoff interval, not this
        # slice, is what decides how many attempts fit inside the deadline.
        attempt_timeout = max(self._write_timeout_seconds / _WRITE_ATTEMPT_SLICES, _MIN_WRITE_ATTEMPT_SECONDS)
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise TimeoutError
            try:
                async with self._database._model_usage_transaction(min(remaining, attempt_timeout)) as connection:
                    # Lock the Scope row on MySQL so delete cannot race the
                    # increment. SQLite ignores FOR UPDATE; its real snapshot
                    # makes a competing delete fail the write upgrade instead.
                    scope = (
                        select(SCOPES_TABLE.c.scope_id)
                        .where(SCOPES_TABLE.c.scope_id == record.scope_id)
                        .with_for_update()
                    )
                    if (await connection.execute(scope)).first() is not None:
                        await self._repository.record(
                            connection,
                            record.scope_id,
                            record.usage_date,
                            record.purpose,
                            record.operation,
                            record.usage,
                        )
            except OperationalError as error:
                if not is_transaction_contention(error):
                    raise
                # Let the competing writer finish before taking another slice.
                await asyncio.sleep(min(_RETRY_BACKOFF_SECONDS, max(remaining, 0)))
            else:
                return

    def _settle(self, sequence: int) -> None:
        self._settled = sequence
        self._advanced.set()
        self._advanced = asyncio.Event()

    @staticmethod
    def _consumer_finished(consumer: asyncio.Task[None]) -> None:
        if not consumer.cancelled() and consumer.exception() is not None:
            log_safely(_LOGGER, logging.WARNING, "Model usage consumer stopped unexpectedly")
