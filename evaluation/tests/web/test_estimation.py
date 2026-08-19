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

from powercontext_eval.web.estimation import (
    BatchEstimate,
    EstimateBasis,
    EstimateQuality,
    EstimateSample,
    estimate_batch,
)


def test_no_compatible_samples_returns_unavailable() -> None:
    assert estimate_batch(samples=(), remaining_tasks=731) == BatchEstimate.unavailable(remaining_tasks=731)


def test_four_samples_are_preliminary_and_use_observed_pair_duration() -> None:
    estimate = estimate_batch(
        samples=(
            EstimateSample(tokens=100, duration_seconds=10),
            EstimateSample(tokens=200, duration_seconds=20),
            EstimateSample(tokens=300, duration_seconds=30),
            EstimateSample(tokens=400, duration_seconds=40),
        ),
        remaining_tasks=10,
        basis=EstimateBasis.CURRENT_BATCH,
    )

    assert estimate.quality is EstimateQuality.PRELIMINARY
    assert estimate.basis is EstimateBasis.CURRENT_BATCH
    assert estimate.sample_size == 4
    assert estimate.remaining_tasks == 10
    assert estimate.remaining_tokens == 2_500
    assert estimate.remaining_duration_seconds == 250
    assert (estimate.low_tokens, estimate.high_tokens) == (1_750, 3_250)
    assert (estimate.low_duration_seconds, estimate.high_duration_seconds) == (175, 325)


def test_five_samples_are_measured_and_historical_basis_is_visible() -> None:
    estimate = estimate_batch(
        samples=tuple(
            EstimateSample(tokens=value, duration_seconds=value // 10) for value in (100, 200, 300, 400, 500)
        ),
        remaining_tasks=2,
        basis=EstimateBasis.HISTORICAL_COMPATIBLE,
    )

    assert estimate.quality is EstimateQuality.MEASURED
    assert estimate.basis is EstimateBasis.HISTORICAL_COMPATIBLE
    assert estimate.sample_size == 5
    assert estimate.remaining_tokens == 600
    assert estimate.remaining_duration_seconds == 60


@pytest.mark.parametrize("remaining_tasks", [-1, True])
def test_remaining_task_count_must_be_a_non_negative_integer(remaining_tasks: object) -> None:
    with pytest.raises(ValueError, match="remaining_tasks"):
        estimate_batch(
            samples=(EstimateSample(tokens=100, duration_seconds=10),),
            remaining_tasks=remaining_tasks,  # ty: ignore[invalid-argument-type]
        )
