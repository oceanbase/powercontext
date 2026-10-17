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

"""Readiness observations describe existing probes without adding provider traffic."""

import asyncio

import pytest

from powercontext.builtin.runtime.observations import DurationObservations
from powercontext.builtin.runtime.readiness import (
    CachedReadinessProbe,
    ReadinessCheckStatus,
    ReadinessProbeDefinition,
    RuntimeReadinessChecks,
)


def test_readiness_snapshot_distinguishes_disabled_unobserved_and_observed(monkeypatch) -> None:
    now = [100.0]
    monkeypatch.setattr("powercontext.builtin.runtime.readiness.time", lambda: now[0])
    outcomes = iter((ReadinessCheckStatus.READY, ReadinessCheckStatus.TIMEOUT))

    async def probe():
        return next(outcomes)

    checks = RuntimeReadinessChecks(
        {"database": ReadinessProbeDefinition(probe=probe, blocking=True)},
        disabled_dependencies=("inference.generation",),
    )
    initial = checks.snapshot()
    assert initial.status == "unobserved"
    assert initial.observed_at is None
    assert initial.dependencies[0].status == "unobserved"
    assert initial.dependencies[0].last_checked_at is None
    assert initial.dependencies[0].durations == ()
    assert initial.dependencies[1].status == "disabled"
    assert initial.dependencies[1].last_checked_at is None
    asyncio.run(checks.run())
    first = checks.snapshot()
    assert first.status == "ready"
    assert first.observed_at == 100.0
    assert first.dependencies[0].last_success_at == 100.0
    now[0] = 200.0
    asyncio.run(checks.run())
    second = checks.snapshot()
    assert second.status == "not_ready"
    assert second.dependencies[0].status == "timeout"
    assert second.dependencies[0].last_checked_at == 200.0
    assert second.dependencies[0].last_success_at == 100.0
    assert {item.outcome: item.count for item in second.dependencies[0].durations} == {"ready": 1, "timeout": 1}
    assert first.dependencies[0].status == "ready"
    assert initial.dependencies[0].status == "unobserved"
    assert checks.snapshot() == second


def test_cached_readiness_observes_refreshes_not_cache_hits(monkeypatch) -> None:
    now = [100.0]
    monkeypatch.setattr("powercontext.builtin.runtime.readiness.time", lambda: now[0])
    monkeypatch.setattr("powercontext.builtin.runtime.readiness.monotonic", lambda: now[0])
    calls = 0

    async def probe():
        nonlocal calls
        calls += 1
        await asyncio.sleep(0)
        now[0] += 0.5
        return ReadinessCheckStatus.READY

    checks = RuntimeReadinessChecks({
        "inference.embedding": ReadinessProbeDefinition(probe=CachedReadinessProbe(probe), blocking=False),
    })

    async def run():
        await asyncio.gather(checks.run(), checks.run())
        observed = checks.snapshot().dependencies[0]
        assert calls == 1
        assert observed.last_checked_at == 100.5
        (duration,) = observed.durations
        assert duration.count == 1
        assert duration.total_seconds == 0.5
        assert dict(duration.buckets)[0.25] == 0
        assert dict(duration.buckets)[0.5] == 1
        assert dict(duration.buckets)[float("inf")] == 1
        now[0] = 200.0
        await checks.run()
        assert checks.snapshot().dependencies[0] == observed
        now[0] = 401.0
        await checks.run()
        assert calls == 2
        assert checks.snapshot().dependencies[0].durations[0].count == 2

    asyncio.run(run())


def test_probe_failure_and_configuration_details_have_bounded_outcomes() -> None:
    async def failed():
        message = "secret provider response"
        raise ValueError(message)

    async def misconfigured():
        return "misconfigured: private configuration detail"

    checks = RuntimeReadinessChecks({
        "database": ReadinessProbeDefinition(probe=failed, blocking=True),
        "inference.embedding": ReadinessProbeDefinition(probe=misconfigured, blocking=False),
    })
    result = asyncio.run(checks.run())
    snapshot = checks.snapshot()
    assert result.status == "not_ready"
    assert [item.status for item in snapshot.dependencies] == ["unavailable", "misconfigured"]
    assert [item.durations[0].outcome for item in snapshot.dependencies] == ["unavailable", "misconfigured"]
    assert "secret" not in repr(snapshot)
    assert "private" not in repr(snapshot)


def test_cancelled_probe_preserves_cancellation_and_unobserved_health() -> None:
    async def cancelled():
        raise asyncio.CancelledError

    checks = RuntimeReadinessChecks({
        "database": ReadinessProbeDefinition(probe=cancelled, blocking=True),
    })
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(checks.run())
    snapshot = checks.snapshot()
    assert snapshot.status == "unobserved"
    (dependency,) = snapshot.dependencies
    assert dependency.status == "unobserved"
    assert dependency.last_checked_at is None
    assert dependency.last_success_at is None
    assert dependency.durations[0].outcome == "cancelled"
    assert dependency.durations[0].count == 1


def test_duration_snapshot_is_cumulative_and_detached() -> None:
    observations = DurationObservations()
    assert observations.snapshot() == ()
    observations.record("success", 0.0)
    first = observations.snapshot()
    observations.record("success", 100.0)
    second = observations.snapshot()
    assert first[0].count == 1
    assert first[0].total_seconds == 0.0
    assert second[0].count == 2
    assert second[0].total_seconds == 100.0
    assert dict(second[0].buckets)[60.0] == 1
    assert dict(second[0].buckets)[float("inf")] == 2


def test_duration_failure_does_not_change_readiness_or_cancellation(monkeypatch) -> None:
    def fail_record(*args):
        raise RuntimeError

    monkeypatch.setattr(DurationObservations, "record", fail_record)

    async def healthy():
        return ReadinessCheckStatus.READY

    async def cancelled():
        raise asyncio.CancelledError

    checks = RuntimeReadinessChecks({
        "database": ReadinessProbeDefinition(probe=CachedReadinessProbe(healthy), blocking=True),
    })
    assert asyncio.run(checks.run()).status == "ready"
    cancelled_checks = RuntimeReadinessChecks({
        "database": ReadinessProbeDefinition(probe=CachedReadinessProbe(cancelled), blocking=True),
    })
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(cancelled_checks.run())


def test_builtin_runtime_snapshot_reuses_composed_readiness(tmp_path) -> None:
    from powercontext.builtin.persistence.sqlite import SQLiteConfig
    from powercontext.builtin.runtime import BuiltinConfig, open_builtin_runtime

    async def run():
        async with open_builtin_runtime(
            BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'runtime.db'}")),
        ) as runtime:
            before = runtime.readiness_snapshot()
            dependencies = {item.name: item for item in before.dependencies}
            assert dependencies["database"].status == "unobserved"
            assert dependencies["inference.generation"].status == "disabled"
            await runtime.readiness()
            after = runtime.readiness_snapshot()
            assert after.status == "ready"
            assert runtime.readiness_snapshot() == after
            database = next(item for item in after.dependencies if item.name == "database")
            assert database.status == "ready"
            assert database.durations[0].count == 1
        assert runtime.readiness_snapshot().status == "not_ready"

    asyncio.run(run())
