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

from __future__ import annotations

import pytest

from powercontext_e2e.stats import bootstrap_mean_interval, wilson_interval


@pytest.mark.parametrize(
    ("successes", "trials", "low", "high"),
    [
        (0, 2, 0.0, 0.6576),
        (2, 2, 0.3424, 1.0),
        (1, 2, 0.0945, 0.9055),
        (7, 10, 0.3968, 0.8922),
    ],
)
def test_wilson_interval_matches_the_published_values(successes: int, trials: int, low: float, high: float) -> None:
    interval = wilson_interval(successes, trials)

    assert interval is not None
    assert (interval.low, interval.high) == (pytest.approx(low, abs=1e-4), pytest.approx(high, abs=1e-4))


def test_wilson_interval_needs_a_trial() -> None:
    assert wilson_interval(0, 0) is None


def test_bootstrap_interval_is_deterministic_and_covers_the_resampled_range() -> None:
    deltas = [1.0, 0.0, 1.0, -1.0]

    interval = bootstrap_mean_interval(deltas, resamples=2000)

    assert interval == bootstrap_mean_interval(deltas, resamples=2000)
    assert interval is not None
    assert -1 <= interval.low <= 0.25 <= interval.high <= 1


def test_bootstrap_interval_of_one_value_is_that_value() -> None:
    interval = bootstrap_mean_interval([1.0])

    assert interval is not None
    assert (interval.low, interval.high) == (1.0, 1.0)


def test_bootstrap_interval_needs_a_value() -> None:
    assert bootstrap_mean_interval([]) is None
