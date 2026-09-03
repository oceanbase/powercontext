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

"""Bounded-label observability port for durable background work."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from powercontext.builtin.persistence.database import AsyncDatabase
    from powercontext.builtin.persistence.work import WorkQueueStatistic, WorkRepository


class WorkObserver(Protocol):
    """Receive work events containing only bounded dimensions."""

    def observe_work_enqueue(self, kind: str, *, created: bool) -> None: ...

    def observe_work_claim(self, kind: str, *, latency_seconds: float) -> None: ...

    def observe_work_attempt(
        self,
        kind: str,
        *,
        outcome: str,
        error_category: str,
        duration_seconds: float,
    ) -> None: ...

    def observe_work_lease_expiry(self, kind: str, *, outcome: str) -> None: ...

    def observe_scheduler_leadership(self, *, outcome: str) -> None: ...

    def set_work_queue(self, samples: Sequence[WorkQueueStatistic]) -> None: ...

    def set_runtime_members(self, counts: Mapping[str, int]) -> None: ...


async def refresh_work_queue(
    database: AsyncDatabase,
    repository: WorkRepository,
    observer: WorkObserver | None,
) -> None:
    """Best-effort refresh aggregate queue gauges from durable state."""

    if observer is None:
        return
    try:
        async with database.transaction() as connection:
            samples = await repository.queue_statistics(connection)
        observer.set_work_queue(samples)
    except Exception:
        return


__all__ = ["WorkObserver", "refresh_work_queue"]
