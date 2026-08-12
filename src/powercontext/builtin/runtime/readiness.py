"""Runtime-owned dependency readiness checks."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from time import monotonic

from powercontext.builtin.inference import InferenceConfigurationError

READINESS_PROBE_TIMEOUT_SECONDS = 2.0
READINESS_PROBE_CACHE_SECONDS = 300.0

DependencyOperation = Callable[[], Awaitable[object]]
Clock = Callable[[], float]


class ReadinessCheckStatus(StrEnum):
    """Stable outcomes exposed for one Runtime dependency."""

    READY = "ready"
    UNAVAILABLE = "unavailable"
    TIMEOUT = "timeout"
    MISCONFIGURED = "misconfigured"


ReadinessProbe = Callable[[], Awaitable[ReadinessCheckStatus]]


@dataclass(frozen=True, slots=True)
class RuntimeReadiness:
    """Aggregate the safe readiness outcomes for one Runtime."""

    checks: Mapping[str, ReadinessCheckStatus]

    @property
    def ready(self) -> bool:
        """Return whether every registered dependency is ready."""

        return all(status is ReadinessCheckStatus.READY for status in self.checks.values())


class RuntimeReadinessChecks:
    """Run independent dependency probes concurrently."""

    def __init__(self, probes: Mapping[str, ReadinessProbe] | None = None) -> None:
        self._probes = {} if probes is None else dict(probes)

    async def run(self) -> RuntimeReadiness:
        """Return safe results in registration order without exposing failures."""

        names = tuple(self._probes)
        results = await asyncio.gather(*(self._run(self._probes[name]) for name in names))
        return RuntimeReadiness(checks=dict(zip(names, results, strict=True)))

    @staticmethod
    async def _run(probe: ReadinessProbe) -> ReadinessCheckStatus:
        try:
            return await probe()
        except asyncio.CancelledError:
            raise
        except Exception:
            return ReadinessCheckStatus.UNAVAILABLE


class CachedReadinessProbe:
    """Cache one dependency result and collapse concurrent refreshes."""

    def __init__(
        self,
        probe: ReadinessProbe,
        *,
        ttl_seconds: float = READINESS_PROBE_CACHE_SECONDS,
        clock: Clock = monotonic,
    ) -> None:
        self._probe = probe
        self._ttl_seconds = ttl_seconds
        self._clock = clock
        self._lock = asyncio.Lock()
        self._result: ReadinessCheckStatus | None = None
        self._expires_at = 0.0

    async def __call__(self) -> ReadinessCheckStatus:
        """Return a fresh cached result, refreshing it at most once."""

        result = self._fresh_result()
        if result is not None:
            return result
        async with self._lock:
            result = self._fresh_result()
            if result is not None:
                return result
            result = await self._probe()
            self._result = result
            self._expires_at = self._clock() + self._ttl_seconds
            return result

    def _fresh_result(self) -> ReadinessCheckStatus | None:
        return self._result if self._result is not None and self._clock() < self._expires_at else None


def dependency_readiness_probe(
    operation: DependencyOperation,
    *,
    timeout_seconds: float = READINESS_PROBE_TIMEOUT_SECONDS,
) -> ReadinessProbe:
    """Convert one dependency operation into a bounded, redacted probe."""

    async def probe() -> ReadinessCheckStatus:
        try:
            await asyncio.wait_for(operation(), timeout=timeout_seconds)
        except asyncio.CancelledError:
            raise
        except InferenceConfigurationError:
            return ReadinessCheckStatus.MISCONFIGURED
        except TimeoutError:
            return ReadinessCheckStatus.TIMEOUT
        except Exception:
            return ReadinessCheckStatus.UNAVAILABLE
        return ReadinessCheckStatus.READY

    return probe


__all__ = [
    "READINESS_PROBE_CACHE_SECONDS",
    "READINESS_PROBE_TIMEOUT_SECONDS",
    "CachedReadinessProbe",
    "ReadinessCheckStatus",
    "ReadinessProbe",
    "RuntimeReadiness",
    "RuntimeReadinessChecks",
    "dependency_readiness_probe",
]
