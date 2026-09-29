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

from typing import TYPE_CHECKING

from powercontext.http import FlushMemoryRequest, GetStatsRequest

from .models import SessionSnapshot

if TYPE_CHECKING:
    from harbor.trial.hooks import TrialHookEvent
    from powercontext.client import PowerContextClient

MAX_FLUSH_ROUNDS = 20


async def settle_session(client: PowerContextClient, scope_id: str, session: int, *, flush: bool) -> SessionSnapshot:
    """Snapshot the Scope after one session, first flushing captured Sources into Memory when a session follows.

    The flush stands in for the time that passes between real sessions. Host plugins flush on different schedules, so
    the harness flushes the same way for every host.
    """

    rounds = 0
    while flush and rounds < MAX_FLUSH_ROUNDS:
        response = await client.flush_memory(FlushMemoryRequest(scope_id=scope_id))
        rounds += 1
        if response.current_cursor >= response.high_watermark or response.current_cursor <= response.previous_cursor:
            break
    stats = await client.get_stats(
        GetStatsRequest.model_validate({"selection": {"mode": "exact", "scope_ids": [scope_id]}})
    )
    return SessionSnapshot(
        session=session,
        flush_rounds=rounds,
        sources=stats.inventory.sources.total,
        memory_pending=stats.inventory.sources.memory_pending,
        memory_entries=stats.inventory.memory.entries.total,
        preparations=stats.recall.totals.preparations,
        ready_preparations=stats.recall.totals.ready_preparations,
    )


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
