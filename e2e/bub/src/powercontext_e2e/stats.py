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

"""Interval estimates for the paired report."""

from __future__ import annotations

from random import Random
from statistics import NormalDist, fmean
from typing import TYPE_CHECKING

from .models import Interval

if TYPE_CHECKING:
    from collections.abc import Sequence

CONFIDENCE = 0.95
BOOTSTRAP_RESAMPLES = 10_000
BOOTSTRAP_SEED = 0


def wilson_interval(successes: int, trials: int, *, confidence: float = CONFIDENCE) -> Interval | None:
    """Return the Wilson score interval for a success rate, or nothing without a trial."""

    if trials <= 0:
        return None
    z = NormalDist().inv_cdf((1 + confidence) / 2)
    rate = successes / trials
    denominator = 1 + z**2 / trials
    centre = (rate + z**2 / (2 * trials)) / denominator
    margin = z * ((rate * (1 - rate) + z**2 / (4 * trials)) / trials) ** 0.5 / denominator
    return Interval(low=max(0.0, centre - margin), high=min(1.0, centre + margin))


def bootstrap_mean_interval(
    values: Sequence[float],
    *,
    confidence: float = CONFIDENCE,
    resamples: int = BOOTSTRAP_RESAMPLES,
    seed: int = BOOTSTRAP_SEED,
) -> Interval | None:
    """Return a percentile bootstrap interval for the mean of ``values``, or nothing without a value.

    The resampling is seeded, so the same observations give the same interval on every run.
    """

    if not values:
        return None
    random = Random(seed)  # noqa: S311 - resampling, not secrets
    means = sorted(fmean(random.choices(values, k=len(values))) for _ in range(resamples))
    cut = int((1 - confidence) / 2 * resamples)
    return Interval(low=means[cut], high=means[resamples - 1 - cut])
