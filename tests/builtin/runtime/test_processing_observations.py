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

"""Processing observations reflect durable outcomes without doing work on reads."""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest

from powercontext.builtin.persistence.processing_intents import ArtifactProcessingIntentRepository
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.persistence.tables import SHARED_TABLES
from powercontext.builtin.runtime.artifact_processing import ArtifactProcessingBinding, ArtifactProcessingSupervisor
from powercontext.builtin.runtime.processing_contracts import (
    ArtifactProcessingBlock,
    ArtifactProcessingBlockedError,
    ArtifactProcessingWorkerCompletion,
    ArtifactProcessingWorkerOutcome,
)


@pytest.mark.parametrize("outcome", ["success", "noop", "hold", "failure", "blocked", "cancelled"])
def test_attempt_observations_preserve_durable_outcomes(tmp_path, outcome):  # noqa: C901 - outcome matrix
    async def scenario():  # noqa: C901 - outcome matrix
        async with SQLiteProfile.open(
            SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'observations.db'}"), tables=SHARED_TABLES
        ) as profile:
            intents = ArtifactProcessingIntentRepository()
            started = asyncio.Event()

            class Handle:
                async def wait(self):
                    if outcome == "cancelled":
                        started.set()
                        await asyncio.Event().wait()
                    if outcome == "blocked":
                        raise ArtifactProcessingBlockedError(
                            ArtifactProcessingBlock("private-key", "stage", "private-code")
                        )
                    return ArtifactProcessingWorkerCompletion(noop=outcome == "noop", held_count=int(outcome == "hold"))

                async def terminate(self):
                    pass

            class Launcher:
                async def start(self, assignment):
                    if outcome in {"success", "noop", "hold"}:
                        async with profile.database.transaction() as connection:
                            await intents.acknowledge(
                                connection, "private-scope", "binding", assignment.claimed_request_generation
                            )
                    return Handle()

            async with profile.database.transaction() as connection:
                await intents.request(connection, "private-scope", "binding")
            supervisor = ArtifactProcessingSupervisor(
                database=profile.database,
                bindings=[ArtifactProcessingBinding("binding", "memory", Launcher())],
                lease_mode="single-process",
            )
            try:
                await supervisor.start()
                if outcome == "cancelled":
                    await asyncio.wait_for(started.wait(), 3)
                    await supervisor.close()
                else:
                    async with asyncio.timeout(3):
                        while not supervisor.processing_observations["memory"].attempt_durations:  # noqa: ASYNC110
                            await asyncio.sleep(0.01)
                        if outcome in {"success", "noop", "hold"}:
                            while supervisor.family_status["memory"]["completed"] == 0:  # noqa: ASYNC110
                                await asyncio.sleep(0.01)
                observation = supervisor.processing_observations["memory"]
                assert len(observation.attempt_durations) == 1
                duration = observation.attempt_durations[0]
                assert duration.outcome == outcome
                assert duration.count == 1
                assert duration.buckets[-1][1] == 1
                assert duration.total_seconds >= 0
                status = supervisor.family_status["memory"]
                assert float(status["requested_snapshot_timestamp_seconds"]) > 0
                assert (float(status["last_acknowledgement_timestamp_seconds"]) > 0) == (
                    outcome in {"success", "noop", "hold"}
                )
                assert "private" not in repr(observation)
                # Repeated snapshot reads do not increment observations or dispatch work.
                assert supervisor.processing_observations == supervisor.processing_observations
            finally:
                await supervisor.close()

    asyncio.run(scenario())


def test_pending_age_tracks_only_observed_pending_work(tmp_path, monkeypatch):
    now = 100.0
    # Control only the supervisor's local observation clock, leaving asyncio's
    # real scheduling clock and operation-duration measurements unchanged.
    monkeypatch.setattr(
        "powercontext.builtin.runtime.artifact_processing.time",
        SimpleNamespace(monotonic=lambda: now, perf_counter=time.perf_counter, time=time.time, sleep=time.sleep),
    )

    async def scenario():
        nonlocal now
        async with SQLiteProfile.open(
            SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'pending.db'}"), tables=SHARED_TABLES
        ) as profile:

            class Handle:
                async def wait(self):
                    await asyncio.Event().wait()

                async def terminate(self):
                    pass

            class Launcher:
                async def start(self, assignment):
                    return Handle()

            supervisor = ArtifactProcessingSupervisor(
                database=profile.database,
                bindings=[ArtifactProcessingBinding("binding", "memory", Launcher(), max_workers=2)],
                pending_observation_interval_seconds=0.1,
                lease_mode="single-process",
            )
            assert supervisor.family_status["memory"]["requested_snapshot_timestamp_seconds"] == 0
            async with profile.database.transaction() as connection:
                await ArtifactProcessingIntentRepository().request(connection, "scope", "binding")
            await supervisor.start()
            try:
                now = 150.0
                first = supervisor.family_status["memory"]
                assert first["observed_pending_scopes"] == 1
                assert first["oldest_observed_pending_age_seconds"] == 50
                now = 160.0
                second = supervisor.family_status["memory"]
                assert float(second["oldest_observed_pending_age_seconds"]) > float(
                    first["oldest_observed_pending_age_seconds"]
                )
                assert float(second["requested_snapshot_timestamp_seconds"]) >= float(
                    first["requested_snapshot_timestamp_seconds"]
                )
                # A fresh cohort must not inherit an acknowledged predecessor's age,
                # even while the old worker has not yet returned to the supervisor.
                async with profile.database.transaction() as connection:
                    intents = ArtifactProcessingIntentRepository()
                    await intents.acknowledge(connection, "scope", "binding", 1)
                    await intents.request(connection, "scope", "binding")
                now = 200.0
                supervisor.wake()
                async with asyncio.timeout(3):
                    while (  # noqa: ASYNC110 - poll the externally visible discovery observation
                        supervisor.family_status["memory"]["oldest_observed_pending_age_seconds"] != 0
                    ):
                        await asyncio.sleep(0.001)
                renewed = supervisor.family_status["memory"]
                assert renewed["unacknowledged_requests"] == 1
                assert renewed["observed_pending_scopes"] == 1
                assert renewed["oldest_observed_pending_age_seconds"] == 0

            finally:
                await supervisor.close()
            assert supervisor.family_status["memory"]["observed_pending_scopes"] == 0
            assert supervisor.family_status["memory"]["oldest_observed_pending_age_seconds"] == 0
            assert supervisor.family_status["memory"]["supervisor_running"] is False

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "conflict", [ArtifactProcessingWorkerOutcome.CURSOR_CONFLICT, ArtifactProcessingWorkerOutcome.HEAD_CONFLICT]
)
def test_retry_reason_describes_current_event_after_failure(tmp_path, conflict):
    async def scenario():
        async with SQLiteProfile.open(
            SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'retries.db'}"), tables=SHARED_TABLES
        ) as profile:
            attempts = 0

            class Handle:
                async def wait(self):
                    if attempts == 1:
                        raise RuntimeError("private error")  # noqa: TRY003 - sensitive failure fixture
                    if attempts == 2:
                        return ArtifactProcessingWorkerCompletion(conflict)
                    await asyncio.Event().wait()

                async def terminate(self):
                    pass

            class Launcher:
                async def start(self, assignment):
                    nonlocal attempts
                    attempts += 1
                    return Handle()

            async with profile.database.transaction() as connection:
                await ArtifactProcessingIntentRepository().request(connection, "scope", "binding")
            supervisor = ArtifactProcessingSupervisor(
                database=profile.database,
                bindings=[ArtifactProcessingBinding("binding", "memory", Launcher())],
                lease_mode="single-process",
                retry_base_seconds=0.001,
                retry_jitter=lambda: 1,
            )
            try:
                await supervisor.start()
                async with asyncio.timeout(3):
                    while attempts < 3:  # noqa: ASYNC110 - public scheduler observation
                        await asyncio.sleep(0.01)
                assert dict(supervisor.processing_observations["memory"].retry_counts) == {"failure": 1, "conflict": 1}
            finally:
                await supervisor.close()

    asyncio.run(scenario())


def test_durable_pending_observation_refreshes_while_worker_capacity_is_full(tmp_path):
    async def scenario():
        async with SQLiteProfile.open(
            SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'durable-pending.db'}"), tables=SHARED_TABLES
        ) as profile:
            intents = ArtifactProcessingIntentRepository()
            started = asyncio.Event()

            class Handle:
                async def wait(self):
                    started.set()
                    await asyncio.Event().wait()

                async def terminate(self):
                    pass

            class Launcher:
                async def start(self, assignment):
                    return Handle()

            async with profile.database.transaction() as connection:
                await intents.request(connection, "oldest", "binding")
            for _ in range(2):
                supervisor = ArtifactProcessingSupervisor(
                    database=profile.database,
                    bindings=[ArtifactProcessingBinding("binding", "memory", Launcher())],
                    lease_mode="single-process",
                    pending_observation_interval_seconds=0.02,
                )
                try:
                    await supervisor.start()
                    await asyncio.wait_for(started.wait(), 3)
                    before = supervisor.family_status["memory"]
                    assert before["used_workers"] == 1
                    assert int(before["unacknowledged_requests"]) >= 1
                    assert float(before["requested_snapshot_timestamp_seconds"]) > 0
                    assert float(before["oldest_observed_pending_age_seconds"]) >= 0
                    assert not {
                        "oldest_pending_timestamp_seconds",
                        "oldest_pending_age_seconds",
                        "pending_age_unknown_scopes",
                    }.intersection(before)
                    # Discovery cannot admit more work while the only worker is
                    # stalled, but the global pending snapshot must still advance.
                    async with profile.database.transaction() as connection:
                        await intents.request(connection, "newer", "binding")
                    async with asyncio.timeout(3):
                        while supervisor.family_status["memory"]["unacknowledged_requests"] != 2:  # noqa: ASYNC110
                            await asyncio.sleep(0.01)
                    assert supervisor.family_status["memory"]["last_acknowledgement_timestamp_seconds"] == 0
                finally:
                    await supervisor.close()
                # Durable backlog survives restart, but the bounded local age resets.
                assert supervisor.family_status["memory"]["oldest_observed_pending_age_seconds"] == 0
                assert supervisor.family_status["memory"]["observed_pending_scopes"] == 0
                started.clear()

    asyncio.run(scenario())


def test_local_pending_history_resets_after_lease_loss_and_reacquisition(tmp_path):
    async def scenario():
        async with SQLiteProfile.open(
            SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'lease-reset.db'}"), tables=SHARED_TABLES
        ) as profile:
            started = asyncio.Event()

            class Handle:
                async def wait(self):
                    await asyncio.Event().wait()

                async def terminate(self):
                    pass

            class Launcher:
                async def start(self, assignment):
                    started.set()
                    return Handle()

            async with profile.database.transaction() as connection:
                await ArtifactProcessingIntentRepository().request(connection, "pending-scope", "binding")
            binding = ArtifactProcessingBinding("binding", "memory", Launcher())
            supervisor = ArtifactProcessingSupervisor(
                database=profile.database,
                bindings=[binding],
                lease_mode="oceanbase",
                oceanbase_lease_seconds=0.6,
                oceanbase_tick_seconds=0.01,
                pending_observation_interval_seconds=0.01,
            )
            try:
                await supervisor.start()
                await asyncio.wait_for(started.wait(), 3)
                initial = supervisor.family_status["memory"]
                assert initial["observed_pending_scopes"] == 1
                assert float(initial["oldest_observed_pending_age_seconds"]) > 0
                # A new embedded term replaces the persisted lease. The original
                # supervisor detects the lost fence through its actual renewal loop.
                async with ArtifactProcessingSupervisor(
                    database=profile.database, bindings=[binding], lease_mode="single-process"
                ):
                    async with asyncio.timeout(3):
                        while (  # noqa: ASYNC110 - wait for public cleanup observations
                            supervisor.family_status["memory"]["supervisor_role"] != "standby"
                            or supervisor.family_status["memory"]["observed_pending_scopes"] != 0
                        ):
                            await asyncio.sleep(0.001)
                    standby = supervisor.family_status["memory"]
                    assert standby["supervisor_running"] is True
                    assert standby["observed_pending_scopes"] == 0
                    assert standby["oldest_observed_pending_age_seconds"] == 0
                    assert standby["unacknowledged_requests"] == 1
                    assert standby["last_acknowledgement_timestamp_seconds"] == 0
                    started.clear()
                # Closing the replacement revokes its term; the same original
                # supervisor reacquires and repopulates its local observation.
                await asyncio.wait_for(started.wait(), 3)
                reacquired = supervisor.family_status["memory"]
                assert reacquired["supervisor_role"] == "leader"
                assert reacquired["observed_pending_scopes"] == 1
                assert reacquired["unacknowledged_requests"] == 1
                assert reacquired["last_acknowledgement_timestamp_seconds"] == 0
                assert float(reacquired["requested_snapshot_timestamp_seconds"]) > float(
                    initial["requested_snapshot_timestamp_seconds"]
                )
            finally:
                await supervisor.close()

    asyncio.run(scenario())
