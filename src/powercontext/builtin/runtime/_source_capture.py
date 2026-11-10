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

"""Coalesce queued Content captures while retaining each caller's outcome."""

from __future__ import annotations

import asyncio
from collections import deque
from contextlib import AbstractAsyncContextManager, suppress
from dataclasses import dataclass

from powercontext.builtin.records import RecordService, SourceRecord
from powercontext.builtin.sources.content import ContentCapture


@dataclass(eq=False)
class _Capture:
    value: ContentCapture
    result: asyncio.Future[SourceRecord]


class SourceCaptureCoalescer:
    """Use the existing Scope lock and caller lifecycle; never start detached work.

    A caller takes already waiting captures with it into a bounded transaction.
    An uncontended capture starts immediately. Failed batches roll back and retry
    individually so one request cannot change another request's outcome.
    """

    def __init__(self) -> None:
        self._queues: dict[str, deque[_Capture]] = {}

    async def capture(
        self,
        scope_id: str,
        value: ContentCapture,
        records: RecordService,
        lock: AbstractAsyncContextManager[None],
    ) -> SourceRecord:
        pending = _Capture(value, asyncio.get_running_loop().create_future())
        queue = self._queues.setdefault(scope_id, deque())
        queue.append(pending)
        try:
            async with lock:
                while not pending.result.done():
                    batch = []
                    while queue and len(batch) < 32:
                        item = queue.popleft()
                        if not item.result.cancelled():
                            batch.append(item)
                    try:
                        await self._store(scope_id, batch, records)
                    except BaseException:
                        # Cancellation can arrive after commit. Replaying the
                        # remaining stable identities is safe in either case.
                        queue.extendleft(
                            item for item in reversed(batch) if item is not pending and not item.result.done()
                        )
                        raise
                return await pending.result
        finally:
            if pending.result.done() and not pending.result.cancelled():
                # A caller cancelled while waiting for its lock may already
                # have an error from another caller's transaction.
                pending.result.exception()
            else:
                pending.result.cancel()
            with suppress(ValueError):
                queue.remove(pending)
            if not queue and self._queues.get(scope_id) is queue:
                del self._queues[scope_id]

    @staticmethod
    async def _store(  # noqa: C901 -- Batch rollback, individual errors and cancellation have distinct outcomes.
        scope_id: str, batch: list[_Capture], records: RecordService
    ) -> None:
        try:
            try:
                stored = await records.capture_sources(scope_id, tuple(item.value for item in batch))
            except Exception:
                if len(batch) == 1:
                    raise
                # Isolate input and persistence errors, including conflicts.
                # Stable identities also make an uncertain commit replayable.
                for item in batch:
                    if item.result.cancelled():
                        continue
                    try:
                        record = (await records.capture_sources(scope_id, (item.value,)))[0]
                    except Exception as error:
                        if not item.result.done():
                            item.result.set_exception(error)
                    else:
                        if not item.result.done():
                            item.result.set_result(record)
            else:
                for item, record in zip(batch, stored, strict=True):
                    if not item.result.done():
                        item.result.set_result(record)
        except Exception as error:
            for item in batch:
                if not item.result.done():
                    item.result.set_exception(error)
