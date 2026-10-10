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

"""Settle a PowerContext Scope between agent sessions and record what the Server observed."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from powercontext.client import UnavailableResponseError
from powercontext.http import FlushMemoryRequest, GetStatsRequest

from .models import SessionSnapshot

if TYPE_CHECKING:
    from harbor.trial.hooks import TrialHookEvent
    from powercontext.client import PowerContextClient

MAX_FLUSH_ROUNDS = 20
OWNER_PENDING_DELAYS = (1.0, 2.0, 4.0, 8.0)


async def settle_session(client: PowerContextClient, scope_id: str, session: int, *, flush: bool) -> SessionSnapshot:
    """Snapshot the Scope after one session, first flushing captured Sources into Memory when a session follows.

    The flush stands in for the time that passes between real sessions. Host plugins flush on different schedules, so
    the harness flushes the same way for every host.
    """

    rounds = 0
    while flush and rounds < MAX_FLUSH_ROUNDS:
        response = await _while_owner_pending(lambda: client.flush_memory(FlushMemoryRequest(scope_id=scope_id)))
        rounds += 1
        if response.current_cursor >= response.high_watermark or response.current_cursor <= response.previous_cursor:
            break
    stats = await _while_owner_pending(
        lambda: client.get_stats(
            GetStatsRequest.model_validate({"selection": {"mode": "exact", "scope_ids": [scope_id]}})
        )
    )
    usage = stats.usage.totals
    return SessionSnapshot(
        session=session,
        flush_rounds=rounds,
        sources=stats.inventory.sources.total,
        memory_pending=stats.inventory.sources.memory_pending,
        memory_entries=stats.inventory.memory.entries.total,
        preparations=stats.recall.totals.preparations,
        ready_preparations=stats.recall.totals.ready_preparations,
        generation_requests=usage.generation.requests,
        generation_input_tokens=usage.generation.input_tokens,
        generation_output_tokens=usage.generation.output_tokens,
        embedding_requests=usage.embedding.requests,
        embedding_input_tokens=usage.embedding.input_tokens,
        recalled_tokens=stats.recall.totals.recalled_tokens,
    )


async def _while_owner_pending[T](call: Callable[[], Awaitable[T]]) -> T:
    """Retry while the Server reports Memory whose owner another request has not yet recorded.

    A host plugin's own flush can still be running when the harness settles the Scope; until it records the owner of
    the Memory it created, the Server answers 503 ``artifact_owner_pending``, which clears once that request finishes.
    """

    for delay in OWNER_PENDING_DELAYS:
        try:
            return await call()
        except UnavailableResponseError as error:
            if error.code != "artifact_owner_pending":
                raise
        await asyncio.sleep(delay)
    return await call()


class SessionRecorder:
    """Harbor agent-end hook that settles one Scope after every agent session of a single-trial job.

    Harbor fires the hook after the agent's timed phase, so the flush neither uses the agent's time budget nor
    appears in its execution time. Harbor awaits the hook in a ``finally`` block, where an exception would replace the
    agent's own, such as a timeout, so failures are recorded instead of raised.
    """

    def __init__(self, client: PowerContextClient, scope_id: str, *, final_session: int) -> None:
        self._client = client
        self._scope_id = scope_id
        self._final_session = final_session
        self.snapshots: list[SessionSnapshot] = []
        self.failures: list[str] = []

    async def __call__(self, event: TrialHookEvent) -> None:
        del event
        session = len(self.snapshots) + len(self.failures)
        try:
            snapshot = await settle_session(self._client, self._scope_id, session, flush=session < self._final_session)
        except Exception as exc:
            self.failures.append(f"Settling the Scope after session {session} failed: {type(exc).__name__}: {exc}")
        else:
            self.snapshots.append(snapshot)
