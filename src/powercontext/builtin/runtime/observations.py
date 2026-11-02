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

"""Bounded process-local duration observations, independent of telemetry exporters."""

from __future__ import annotations

from dataclasses import dataclass

DURATION_BUCKETS_SECONDS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, float("inf"))


@dataclass(frozen=True, slots=True)
class DurationObservationSnapshot:
    """Cumulative observations for one domain-owned bounded outcome."""

    outcome: str
    count: int
    total_seconds: float
    buckets: tuple[tuple[float, int], ...]


class DurationObservations:
    """Retain fixed-size histogram aggregates, never individual event history."""

    def __init__(self) -> None:
        self._outcomes: dict[str, DurationObservationSnapshot] = {}

    def record(self, outcome: str, duration_seconds: float) -> None:
        """Record a completed duration under the caller's bounded outcome vocabulary."""

        duration_seconds = max(duration_seconds, 0.0)
        previous = self._outcomes.get(outcome)
        buckets = tuple((bound, 0) for bound in DURATION_BUCKETS_SECONDS) if previous is None else previous.buckets
        self._outcomes[outcome] = DurationObservationSnapshot(
            outcome=outcome,
            count=1 if previous is None else previous.count + 1,
            total_seconds=duration_seconds if previous is None else previous.total_seconds + duration_seconds,
            buckets=tuple((bound, count + int(duration_seconds <= bound)) for bound, count in buckets),
        )

    def snapshot(self) -> tuple[DurationObservationSnapshot, ...]:
        """Return immutable cumulative values; reading does not reset observations."""

        return tuple(self._outcomes.values())
