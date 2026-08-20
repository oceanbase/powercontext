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
READINESS_PROBE_TRANSIENT_CACHE_SECONDS = 30.0

DependencyOperation = Callable[[], Awaitable[object]]
Clock = Callable[[], float]


class ReadinessCheckStatus(StrEnum):
    """Stable outcomes exposed for one Runtime dependency."""

    READY = "ready"
    UNAVAILABLE = "unavailable"
    TIMEOUT = "timeout"
    MISCONFIGURED = "misconfigured"


ReadinessProbe = Callable[[], Awaitable[ReadinessCheckStatus]]


class RuntimeReadinessStatus(StrEnum):
    """Aggregate availability of one Runtime and its dependencies."""

    READY = "ready"
    DEGRADED = "degraded"
    NOT_READY = "not_ready"


@dataclass(frozen=True, slots=True)
class ReadinessProbeDefinition:
    """Bind one probe to whether its failure blocks Runtime readiness."""

    probe: ReadinessProbe
    blocking: bool


@dataclass(frozen=True, slots=True)
class RuntimeReadiness:
    """Aggregate the safe readiness outcomes for one Runtime."""

    status: RuntimeReadinessStatus
    checks: Mapping[str, ReadinessCheckStatus]

    @property
    def ready(self) -> bool:
        """Return whether every registered dependency is fully ready."""

        return self.status is RuntimeReadinessStatus.READY


class RuntimeReadinessChecks:
    """Run independent dependency probes concurrently."""

    def __init__(self, probes: Mapping[str, ReadinessProbeDefinition] | None = None) -> None:
        self._probes = {} if probes is None else dict(probes)

    async def run(self) -> RuntimeReadiness:
        """Return safe results in registration order without exposing failures."""

        names = tuple(self._probes)
        results = await asyncio.gather(*(self._run(self._probes[name].probe) for name in names))
        checks = dict(zip(names, results, strict=True))
        blocking_failure = any(
            checks[name] is not ReadinessCheckStatus.READY and self._probes[name].blocking for name in names
        )
        if blocking_failure:
            status = RuntimeReadinessStatus.NOT_READY
        elif any(result is not ReadinessCheckStatus.READY for result in results):
            status = RuntimeReadinessStatus.DEGRADED
        else:
            status = RuntimeReadinessStatus.READY
        return RuntimeReadiness(status=status, checks=checks)

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
        transient_ttl_seconds: float = READINESS_PROBE_TRANSIENT_CACHE_SECONDS,
        clock: Clock | None = None,
    ) -> None:
        self._probe = probe
        self._ttl_seconds = ttl_seconds
        self._transient_ttl_seconds = transient_ttl_seconds
        self._clock = monotonic if clock is None else clock
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
            ttl_seconds = (
                self._transient_ttl_seconds
                if result in {ReadinessCheckStatus.TIMEOUT, ReadinessCheckStatus.UNAVAILABLE}
                else self._ttl_seconds
            )
            self._expires_at = self._clock() + ttl_seconds
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
    "READINESS_PROBE_TRANSIENT_CACHE_SECONDS",
    "CachedReadinessProbe",
    "ReadinessCheckStatus",
    "ReadinessProbe",
    "ReadinessProbeDefinition",
    "RuntimeReadiness",
    "RuntimeReadinessChecks",
    "RuntimeReadinessStatus",
    "dependency_readiness_probe",
]
