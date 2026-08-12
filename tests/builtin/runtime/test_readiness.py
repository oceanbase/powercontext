from __future__ import annotations

import asyncio
from pathlib import Path

from powercontext.builtin.inference import InferenceConfigurationError
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import (
    BuiltinConfig,
    ReadinessCheckStatus,
    open_builtin_runtime,
)
from powercontext.builtin.runtime.readiness import CachedReadinessProbe, dependency_readiness_probe


def test_dependency_readiness_maps_failures_to_stable_redacted_statuses() -> None:
    async def ready() -> None:
        return None

    async def misconfigured() -> None:
        raise InferenceConfigurationError("secret provider response")  # noqa: TRY003 - verifies redaction

    async def unavailable() -> None:
        raise OSError("https://user:secret@example.test/v1")

    async def slow() -> None:
        await asyncio.Event().wait()

    async def scenario() -> None:
        results = await asyncio.gather(
            dependency_readiness_probe(ready)(),
            dependency_readiness_probe(misconfigured)(),
            dependency_readiness_probe(unavailable)(),
            dependency_readiness_probe(slow, timeout_seconds=0.01)(),
        )

        assert results == [
            ReadinessCheckStatus.READY,
            ReadinessCheckStatus.MISCONFIGURED,
            ReadinessCheckStatus.UNAVAILABLE,
            ReadinessCheckStatus.TIMEOUT,
        ]
        assert "secret" not in repr(results)

    asyncio.run(scenario())


def test_cached_readiness_probe_collapses_concurrency_and_refreshes_after_ttl() -> None:
    now = 0.0
    calls = 0

    async def probe() -> ReadinessCheckStatus:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0)
        return ReadinessCheckStatus.UNAVAILABLE

    async def scenario() -> None:
        nonlocal now
        cached = CachedReadinessProbe(probe, ttl_seconds=300, clock=lambda: now)

        assert await asyncio.gather(*(cached() for _ in range(5))) == [ReadinessCheckStatus.UNAVAILABLE] * 5
        assert await cached() is ReadinessCheckStatus.UNAVAILABLE
        assert calls == 1

        now = 300
        assert await cached() is ReadinessCheckStatus.UNAVAILABLE
        assert calls == 2

    asyncio.run(scenario())


def test_builtin_runtime_reports_runtime_and_database_readiness(tmp_path: Path) -> None:
    async def scenario() -> None:
        config = BuiltinConfig(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'runtime.db'}"),
        )
        async with open_builtin_runtime(config) as runtime:
            readiness = await runtime.readiness()

        assert readiness.ready is True
        assert readiness.checks == {
            "runtime": ReadinessCheckStatus.READY,
            "database": ReadinessCheckStatus.READY,
        }

    asyncio.run(scenario())
