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
from collections import Counter
from datetime import timedelta

import pytest

from powercontext.builtin.persistence.errors import ArtifactProcessingLeadershipLostError
from powercontext.builtin.persistence.processing_intents import ArtifactProcessingIntentRepository
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.persistence.supervision import (
    ArtifactProcessingBindingStateRepository,
    ArtifactProcessingLeaseRepository,
)
from powercontext.builtin.persistence.tables import SHARED_TABLES
from powercontext.builtin.runtime import artifact_processing as processing
from powercontext.builtin.runtime.artifact_processing import ArtifactProcessingBinding, ArtifactProcessingSupervisor
from powercontext.builtin.runtime.processing_contracts import (
    ArtifactProcessingWorkerCompletion,
    ArtifactProcessingWorkerOutcome,
)


async def _wait(predicate, timeout_seconds=5):
    async with asyncio.timeout(timeout_seconds):
        while not predicate():  # noqa: ASYNC110 - bounded observation of worker events
            await asyncio.sleep(0.005)


async def _wait_scan_finished(database, binding):
    # Workers can finish before discovery commits its final empty page.
    async with asyncio.timeout(5):
        while True:
            async with database.transaction() as connection:
                state = await ArtifactProcessingBindingStateRepository().load(connection, binding)
            if state is not None and not state.scan_in_progress:
                return state
            await asyncio.sleep(0.005)


async def _request(database, binding, scope, *, dirty=True, requested=True):
    intents = ArtifactProcessingIntentRepository()
    async with database.transaction() as connection:
        if dirty:
            await intents.mark_dirty(connection, scope, binding)
        if requested:
            return await intents.request(connection, scope, binding)
        return await intents.load(connection, scope, binding)


async def _intent(database, binding, scope):
    async with database.transaction() as connection:
        return await ArtifactProcessingIntentRepository().load(connection, scope, binding)


class _Launcher:
    def __init__(self, database, *, on_work=None, gates=None):
        self.database = database
        self.on_work = on_work
        self.gates = {} if gates is None else gates
        self.assignments = []
        self.active = set()
        self.maximum_active = 0
        self.terminated = []

    async def start(self, assignment):
        key = (assignment.binding_name, assignment.scope_id)
        assert key not in self.active
        self.assignments.append(assignment)
        self.active.add(key)
        self.maximum_active = max(self.maximum_active, len(self.active))
        return _Handle(self, assignment)


class _Handle:
    def __init__(self, launcher, assignment):
        self.launcher = launcher
        self.assignment = assignment

    async def wait(self):
        work = self.assignment
        try:
            gate = self.launcher.gates.get(work.scope_id)
            if gate is not None:
                await gate.wait()
            if self.launcher.on_work is not None:
                completion = await self.launcher.on_work(work)
                if completion is not None:
                    return completion
            async with self.launcher.database.transaction() as connection:
                await ArtifactProcessingLeaseRepository().require_fence(connection, work.fence)
                intents = ArtifactProcessingIntentRepository()
                row = await intents.load(connection, work.scope_id, work.binding_name, for_update=True)
                assert row is not None
                await intents.acknowledge(
                    connection,
                    work.scope_id,
                    work.binding_name,
                    work.claimed_request_generation,
                    clean_generation=row.dirty_generation,
                )
            return ArtifactProcessingWorkerCompletion()
        finally:
            self.launcher.active.discard((work.binding_name, work.scope_id))

    async def terminate(self):
        self.launcher.terminated.append(self.assignment.scope_id)
        self.launcher.active.discard((self.assignment.binding_name, self.assignment.scope_id))


class _ObservedIntents(ArtifactProcessingIntentRepository):
    def __init__(self):
        self.scans = []

    async def scan(self, connection, binding_name, /, **kwargs):
        self.scans.append((binding_name, dict(kwargs)))
        return await super().scan(connection, binding_name, **kwargs)


class _CycleSupervisor(ArtifactProcessingSupervisor):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.cycles = 0
        self.max_resident = 0

    async def __aenter__(self):
        await self.start()
        return self

    async def _cycle(self):
        self.cycles += 1
        await super()._cycle()
        for state in self.family_status.values():
            resident = int(state["retry_wait"]) + int(state["ready"]) + int(state["used_workers"])
            self.max_resident = max(self.max_resident, resident)


def _profile(tmp_path, name):
    return SQLiteProfile.open(
        SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / (name + '.db')}"), tables=SHARED_TABLES
    )


def test_each_family_has_independent_capacity_and_a_scope_is_single_flight(tmp_path):
    async def scenario():
        async with _profile(tmp_path, "family-capacity") as profile:
            blocked = asyncio.Event()
            memory = _Launcher(profile.database, gates={"a": blocked, "b": blocked, "c": blocked})
            topic = _Launcher(profile.database)
            for scope in ("a", "b", "c"):
                await _request(profile.database, "memory-binding", scope)
            await _request(profile.database, "topic-binding", "peer")
            async with ArtifactProcessingSupervisor(
                database=profile.database,
                bindings=(
                    ArtifactProcessingBinding("memory-binding", "memory", memory, max_workers=2),
                    ArtifactProcessingBinding("topic-binding", "topic-memory", topic, max_workers=1),
                ),
                lease_mode="single-process",
            ) as supervisor:
                await _wait(lambda: supervisor.family_status["topic-memory"]["completed"] == 1)
                assert len(memory.assignments) == 2 and memory.maximum_active == 2
                assert supervisor.family_status["memory"]["used_workers"] == 2
                assert (await _intent(profile.database, "topic-binding", "peer")).handled_generation == 1
                # A second accepted request coalesces behind the active Scope.
                await _request(profile.database, "memory-binding", "a", dirty=False)
                supervisor.wake("memory-binding")
                await asyncio.sleep(0.03)
                assert [work.scope_id for work in memory.assignments].count("a") == 1
                blocked.set()
                await _wait(lambda: supervisor.family_status["memory"]["completed"] == 4)
                assert memory.maximum_active == 2
                assert (await _intent(profile.database, "memory-binding", "a")).handled_generation == 2

    asyncio.run(scenario())


def test_success_without_durable_acknowledgement_is_a_failure_and_retries(tmp_path):
    async def scenario():
        async with _profile(tmp_path, "no-ack") as profile:
            await _request(profile.database, "memory-binding", "scope")
            attempts = 0

            async def forget_once(_work):
                nonlocal attempts
                attempts += 1
                return ArtifactProcessingWorkerCompletion() if attempts == 1 else None

            launcher = _Launcher(profile.database, on_work=forget_once)
            async with ArtifactProcessingSupervisor(
                database=profile.database,
                bindings=(ArtifactProcessingBinding("memory-binding", "memory", launcher),),
                lease_mode="single-process",
                retry_base_seconds=0.15,
                retry_cap_seconds=0.15,
                retry_jitter=lambda: 1,
            ) as supervisor:
                await _wait(lambda: supervisor.family_status["memory"]["failed"] == 1)
                assert (await _intent(profile.database, "memory-binding", "scope")).handled_generation == 0
                await _wait(lambda: supervisor.family_status["memory"]["completed"] == 1)
                assert attempts == 2
                assert (await _intent(profile.database, "memory-binding", "scope")).handled_generation == 1

    asyncio.run(scenario())


def test_retry_backoff_is_quiet_and_failure_does_not_starve_other_scopes(tmp_path, monkeypatch):
    monkeypatch.setattr(processing, "_DISCOVERY_PAGE_SIZE", 2)

    async def scenario():
        async with _profile(tmp_path, "fair-retry") as profile:
            for scope in ("bad", "a", "b", "c", "d", "e"):
                await _request(profile.database, "memory-binding", scope)

            async def fail_bad(work):
                if work.scope_id == "bad":
                    raise RuntimeError

            launcher = _Launcher(profile.database, on_work=fail_bad)
            async with _CycleSupervisor(
                database=profile.database,
                bindings=(ArtifactProcessingBinding("memory-binding", "memory", launcher),),
                lease_mode="single-process",
                retry_base_seconds=1,
                retry_cap_seconds=1,
                retry_jitter=lambda: 1,
            ) as supervisor:
                await _wait(lambda: supervisor.family_status["memory"]["completed"] == 5)
                assert Counter(work.scope_id for work in launcher.assignments)["bad"] == 1
                before = supervisor.cycles
                await asyncio.sleep(0.1)
                assert supervisor.cycles - before <= 2
                assert (await _intent(profile.database, "memory-binding", "bad")).handled_generation == 0

    asyncio.run(scenario())


def test_notification_during_requested_scan_revisits_a_scope_behind_frontier(tmp_path, monkeypatch):
    monkeypatch.setattr(processing, "_DISCOVERY_PAGE_SIZE", 2)

    async def scenario():
        async with _profile(tmp_path, "notification") as profile:
            await _request(profile.database, "memory-binding", "first", requested=False)
            for scope in ("b", "c", "d", "e"):
                await _request(profile.database, "memory-binding", scope)
            gate = asyncio.Event()
            launcher = _Launcher(profile.database, gates={"b": gate})
            async with ArtifactProcessingSupervisor(
                database=profile.database,
                bindings=(ArtifactProcessingBinding("memory-binding", "memory", launcher),),
                lease_mode="single-process",
            ) as supervisor:
                await _wait(lambda: len(launcher.assignments) == 1)
                await _request(profile.database, "memory-binding", "first", dirty=False)
                supervisor.wake("memory-binding")
                gate.set()
                await _wait(lambda: supervisor.family_status["memory"]["completed"] == 5)
                assert (await _intent(profile.database, "memory-binding", "first")).handled_generation == 1

    asyncio.run(scenario())


def test_automatic_scan_is_bounded_and_excludes_new_registration_until_next_schedule(tmp_path, monkeypatch):
    monkeypatch.setattr(processing, "_DISCOVERY_PAGE_SIZE", 2)

    async def scenario():
        async with _profile(tmp_path, "automatic") as profile:
            for scope in ("a", "b", "c", "d", "e"):
                await _request(profile.database, "memory-binding", scope, requested=False)
            gate = asyncio.Event()
            launcher = _Launcher(profile.database, gates={"a": gate})
            observed = _ObservedIntents()
            async with ArtifactProcessingSupervisor(
                database=profile.database,
                bindings=(
                    ArtifactProcessingBinding(
                        "memory-binding", "memory", launcher, automatic_processing_interval=timedelta(hours=1)
                    ),
                ),
                lease_mode="single-process",
                intents=observed,
            ) as supervisor:
                await _wait(lambda: bool(launcher.assignments))
                async with profile.database.transaction() as connection:
                    state = await ArtifactProcessingBindingStateRepository().load(connection, "memory-binding")
                assert state is not None
                initial_checkpoint = state.last_schedule_checkpoint_at
                assert state.scan_in_progress
                await _request(profile.database, "memory-binding", "late", requested=False)
                supervisor.wake("memory-binding")
                gate.set()
                await _wait(lambda: supervisor.family_status["memory"]["completed"] == 5)
                state = await _wait_scan_finished(profile.database, "memory-binding")
                assert state is not None
                assert not state.scan_in_progress and state.last_schedule_checkpoint_at == initial_checkpoint
                assert {work.scope_id for work in launcher.assignments} == {"a", "b", "c", "d", "e"}
                assert (await _intent(profile.database, "memory-binding", "late")).requested_generation == 0
                assert all(options["limit"] <= 2 for _, options in observed.scans)

    asyncio.run(scenario())


def test_worker_timeout_reclaims_slot_and_preserves_durable_intent(tmp_path):
    async def scenario():
        async with _profile(tmp_path, "timeout") as profile:
            await _request(profile.database, "memory-binding", "hang")
            launcher = _Launcher(profile.database, gates={"hang": asyncio.Event()})
            async with ArtifactProcessingSupervisor(
                database=profile.database,
                bindings=(
                    ArtifactProcessingBinding("memory-binding", "memory", launcher, worker_timeout_seconds=0.05),
                ),
                lease_mode="single-process",
                retry_base_seconds=10,
            ) as supervisor:
                await _wait(lambda: supervisor.family_status["memory"]["timeouts"] == 1)
                assert launcher.terminated == ["hang"]
                assert supervisor.family_status["memory"]["used_workers"] == 0
                assert (await _intent(profile.database, "memory-binding", "hang")).handled_generation == 0

    asyncio.run(scenario())


def test_restart_uses_durable_requests_and_close_revokes_the_previous_fence(tmp_path):
    async def scenario():
        async with _profile(tmp_path, "restart") as profile:
            await _request(profile.database, "memory-binding", "scope")
            blocked = _Launcher(profile.database, gates={"scope": asyncio.Event()})
            async with ArtifactProcessingSupervisor(
                database=profile.database,
                bindings=(ArtifactProcessingBinding("memory-binding", "memory", blocked),),
                lease_mode="single-process",
            ) as supervisor:
                await _wait(lambda: bool(blocked.assignments))
                old_fence = supervisor.fence
                assert old_fence is not None
            assert blocked.terminated == ["scope"]
            async with profile.database.transaction() as connection:
                with pytest.raises(ArtifactProcessingLeadershipLostError):
                    await ArtifactProcessingLeaseRepository().require_fence(connection, old_fence)
            replacement = _Launcher(profile.database)
            async with ArtifactProcessingSupervisor(
                database=profile.database,
                bindings=(ArtifactProcessingBinding("memory-binding", "memory", replacement),),
                lease_mode="single-process",
            ) as successor:
                await _wait(lambda: successor.family_status["memory"]["completed"] == 1)
                assert successor.fence is not None
                assert successor.fence.supervisor_generation > old_fence.supervisor_generation
                assert (await _intent(profile.database, "memory-binding", "scope")).handled_generation == 1

    asyncio.run(scenario())


def test_domain_control_conflict_retries_without_counting_a_provider_failure(tmp_path):
    async def scenario():
        async with _profile(tmp_path, "conflict") as profile:
            await _request(profile.database, "memory-binding", "scope")
            attempts = 0

            async def conflict_once(_work):
                nonlocal attempts
                attempts += 1
                return (
                    ArtifactProcessingWorkerCompletion(ArtifactProcessingWorkerOutcome.CURSOR_CONFLICT)
                    if attempts == 1
                    else None
                )

            launcher = _Launcher(profile.database, on_work=conflict_once)
            async with ArtifactProcessingSupervisor(
                database=profile.database,
                bindings=(ArtifactProcessingBinding("memory-binding", "memory", launcher),),
                lease_mode="single-process",
            ) as supervisor:
                await _wait(lambda: supervisor.family_status["memory"]["completed"] == 1)
                assert attempts == 2 and supervisor.family_status["memory"]["failed"] == 0

    asyncio.run(scenario())


def test_discovery_failure_is_confined_to_its_family(tmp_path):
    class BrokenDiscovery:
        async def reconcile(self, after_scope_id, limit, *, fence):
            raise RuntimeError

    async def scenario():
        async with _profile(tmp_path, "discovery-failure") as profile:
            await _request(profile.database, "topic-binding", "healthy")
            memory = _Launcher(profile.database)
            topic = _Launcher(profile.database)
            async with ArtifactProcessingSupervisor(
                database=profile.database,
                bindings=(
                    ArtifactProcessingBinding("memory-binding", "memory", memory, pending_provider=BrokenDiscovery()),
                    ArtifactProcessingBinding("topic-binding", "topic-memory", topic),
                ),
                lease_mode="single-process",
            ) as supervisor:
                await _wait(lambda: supervisor.family_status["topic-memory"]["completed"] == 1)
                assert supervisor.family_status["memory"]["status"] == "degraded"
                assert supervisor.family_status["topic-memory"]["status"] == "leader"
                assert (await _intent(profile.database, "topic-binding", "healthy")).handled_generation == 1

    asyncio.run(scenario())


def test_automatic_scan_restart_reuses_frozen_members_without_double_admission(tmp_path, monkeypatch):
    monkeypatch.setattr(processing, "_DISCOVERY_PAGE_SIZE", 2)

    async def scenario():
        async with _profile(tmp_path, "scan-restart") as profile:
            for scope in ("a", "b", "c", "d", "e"):
                await _request(profile.database, "memory-binding", scope, requested=False)
            blocked = _Launcher(profile.database, gates={"a": asyncio.Event()})
            async with ArtifactProcessingSupervisor(
                database=profile.database,
                bindings=(
                    ArtifactProcessingBinding(
                        "memory-binding", "memory", blocked, automatic_processing_interval=timedelta(hours=1)
                    ),
                ),
                lease_mode="single-process",
            ):
                await _wait(lambda: bool(blocked.assignments))
            async with profile.database.transaction() as connection:
                before = await ArtifactProcessingBindingStateRepository().load(connection, "memory-binding")
            assert before is not None
            assert before.scan_in_progress
            await _request(profile.database, "memory-binding", "late", requested=False)
            recovered = _Launcher(profile.database)
            async with ArtifactProcessingSupervisor(
                database=profile.database,
                bindings=(
                    ArtifactProcessingBinding(
                        "memory-binding", "memory", recovered, automatic_processing_interval=timedelta(hours=1)
                    ),
                ),
                lease_mode="single-process",
            ) as supervisor:
                await _wait(lambda: supervisor.family_status["memory"]["completed"] == 5)
                after = await _wait_scan_finished(profile.database, "memory-binding")
                assert after is not None
                assert after.scan_generation == before.scan_generation and not after.scan_in_progress
                assert after.last_schedule_checkpoint_at == before.last_schedule_checkpoint_at
                for scope in ("a", "b", "c", "d", "e"):
                    row = await _intent(profile.database, "memory-binding", scope)
                    assert row.requested_generation == row.handled_generation == 1
                assert (await _intent(profile.database, "memory-binding", "late")).requested_generation == 0

    asyncio.run(scenario())


def test_new_requests_preserve_failure_count_until_success(tmp_path, caplog):
    async def scenario():
        async with _profile(tmp_path, "retry-generations") as profile:
            await _request(profile.database, "memory-binding", "scope")
            attempts = 0

            async def intermittent_failure(_work):
                nonlocal attempts
                attempts += 1
                if attempts in (1, 2, 4):
                    raise RuntimeError

            launcher = _Launcher(profile.database, on_work=intermittent_failure)
            async with ArtifactProcessingSupervisor(
                database=profile.database,
                bindings=(ArtifactProcessingBinding("memory-binding", "memory", launcher),),
                lease_mode="single-process",
                retry_base_seconds=0.1,
                retry_cap_seconds=0.2,
                retry_jitter=lambda: 1,
            ) as supervisor:
                await _wait(lambda: supervisor.family_status["memory"]["failed"] == 1)
                await _request(profile.database, "memory-binding", "scope", dirty=False)
                supervisor.wake("memory-binding")
                await _wait(lambda: supervisor.family_status["memory"]["completed"] == 1)
                await _request(profile.database, "memory-binding", "scope", dirty=False)
                supervisor.wake("memory-binding")
                await _wait(lambda: supervisor.family_status["memory"]["completed"] == 2)
            retry_counts = [
                record.retry_count
                for record in caplog.records
                if getattr(record, "scope", None) == "scope" and getattr(record, "stage", None) == "worker"
            ]
            assert retry_counts == [1, 2, 1]

    asyncio.run(scenario())


def test_retry_residency_budget_serves_healthy_suffix_after_large_failure_population(tmp_path, caplog):
    """Keep the inherited 1512 cache budget while requiring progress past it."""
    caplog.set_level(logging.CRITICAL, logger=processing.__name__)
    retry_budget = 1000
    ready_budget = 100
    workers = 16

    async def scenario():
        async with _profile(tmp_path, "retry-budget") as profile:
            intents = ArtifactProcessingIntentRepository()
            async with profile.database.transaction() as connection:
                for number in range(1200):
                    await intents.request(connection, f"failed-{number:04}", "memory-binding")
                await intents.request(connection, "healthy-tail", "memory-binding")

            async def permanent_failure(work):
                if work.scope_id != "healthy-tail":
                    raise RuntimeError

            launcher = _Launcher(profile.database, on_work=permanent_failure)
            async with _CycleSupervisor(
                database=profile.database,
                bindings=(ArtifactProcessingBinding("memory-binding", "memory", launcher, max_workers=workers),),
                lease_mode="single-process",
                retry_base_seconds=0.5,
                retry_cap_seconds=0.5,
                retry_jitter=lambda: 1,
            ) as supervisor:
                await _wait(lambda: supervisor.family_status["memory"]["completed"] == 1, timeout_seconds=60)
                assert (await _intent(profile.database, "memory-binding", "healthy-tail")).handled_generation == 1
                # Public residency counters measure the background memory
                # contract without depending on dict layout or eviction order.
                assert supervisor.max_resident <= retry_budget + ready_budget + workers

    asyncio.run(scenario())


@pytest.mark.parametrize("admission", ["requested", "automatic"])
def test_continuously_due_retries_do_not_starve_fresh_discovery(tmp_path, monkeypatch, caplog, admission):
    caplog.set_level(logging.CRITICAL, logger=processing.__name__)
    monkeypatch.setattr(processing, "_DISCOVERY_PAGE_SIZE", 4)
    monkeypatch.setattr(processing, "_CHANNEL_QUEUE_LIMIT", 2)

    async def scenario():
        async with _profile(tmp_path, "continuously-due") as profile:
            for number in range(12):
                await _request(profile.database, "memory-binding", f"bad-{number:02}")
            await _request(profile.database, "memory-binding", "healthy-tail", requested=admission == "requested")

            async def always_bad(work):
                if work.scope_id != "healthy-tail":
                    raise RuntimeError

            launcher = _Launcher(profile.database, on_work=always_bad)
            async with ArtifactProcessingSupervisor(
                database=profile.database,
                bindings=(
                    ArtifactProcessingBinding(
                        "memory-binding",
                        "memory",
                        launcher,
                        max_workers=2,
                        automatic_processing_interval=timedelta(hours=1) if admission == "automatic" else None,
                    ),
                ),
                lease_mode="single-process",
                retry_base_seconds=0.001,
                retry_cap_seconds=0.001,
                retry_jitter=lambda: 1,
            ) as supervisor:
                await _wait(lambda: supervisor.family_status["memory"]["completed"] == 1, timeout_seconds=3)
                assert (await _intent(profile.database, "memory-binding", "healthy-tail")).handled_generation == 1

    asyncio.run(scenario())


def test_overflow_flush_cannot_advance_retry_and_cached_failures_cannot_extend_gate(tmp_path, monkeypatch, caplog):
    caplog.set_level(logging.CRITICAL, logger=processing.__name__)
    monkeypatch.setattr(processing, "_RETRY_STATE_LIMIT", 1)
    monkeypatch.setattr(processing, "_DISCOVERY_PAGE_SIZE", 4)
    monkeypatch.setattr(processing, "_CHANNEL_QUEUE_LIMIT", 2)
    monkeypatch.setattr(processing, "_CONTROL_CONFLICT_RETRY_SECONDS", 0.2)

    async def scenario():
        async with _profile(tmp_path, "overflow-pressure") as profile:
            await _request(profile.database, "memory-binding", "cached")
            await _request(profile.database, "memory-binding", "overflow")
            attempts = {"cached": [], "overflow": [], "healthy-tail": []}

            async def pressure(work):
                attempts[work.scope_id].append(asyncio.get_running_loop().time())
                if work.scope_id == "cached":
                    return ArtifactProcessingWorkerCompletion(ArtifactProcessingWorkerOutcome.CURSOR_CONFLICT)
                if work.scope_id == "overflow":
                    raise RuntimeError

            launcher = _Launcher(profile.database, on_work=pressure)
            async with _CycleSupervisor(
                database=profile.database,
                bindings=(ArtifactProcessingBinding("memory-binding", "memory", launcher),),
                lease_mode="single-process",
                retry_base_seconds=0.8,
                retry_cap_seconds=0.8,
                retry_jitter=lambda: 1,
            ) as supervisor:
                await _wait(lambda: supervisor.family_status["memory"]["failed"] == 1)
                assert supervisor.family_status["memory"]["retry_wait"] == 1
                await _request(profile.database, "memory-binding", "overflow", dirty=False)
                await _request(profile.database, "memory-binding", "healthy-tail")
                # Repeated API wakes must respect the omitted key's cooldown.
                for _ in range(4):
                    supervisor.wake("memory-binding")
                    await asyncio.sleep(0.04)
                assert len(attempts["overflow"]) == 1
                # Cached retries keep failing, but cannot keep shifting the
                # finite overflow cohort's gate beyond the original deadline.
                await _wait(lambda: supervisor.family_status["memory"]["completed"] == 1, timeout_seconds=1.5)
                assert len(attempts["cached"]) >= 3
                await _wait(lambda: len(attempts["overflow"]) >= 2, timeout_seconds=1.5)
                assert attempts["overflow"][1] - attempts["overflow"][0] >= 0.78
                assert supervisor.max_resident <= 1 + 4 + 1
                assert (await _intent(profile.database, "memory-binding", "healthy-tail")).handled_generation == 1

    asyncio.run(scenario())
