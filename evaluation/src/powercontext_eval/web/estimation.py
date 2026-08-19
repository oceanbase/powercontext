"""Evidence-based token and duration estimates for controlled benchmark batches."""

from __future__ import annotations

from collections.abc import Sequence
from enum import StrEnum
from statistics import fmean
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class EstimateQuality(StrEnum):
    UNAVAILABLE = "unavailable"
    PRELIMINARY = "preliminary"
    MEASURED = "measured"


class EstimateBasis(StrEnum):
    NONE = "none"
    CURRENT_BATCH = "current_batch"
    HISTORICAL_COMPATIBLE = "historical_compatible"


class EstimateSample(_FrozenModel):
    """One complete paired OFF/ON benchmark observation."""

    tokens: Annotated[int, Field(ge=0)]
    duration_seconds: Annotated[int, Field(ge=0)]


class BatchEstimate(_FrozenModel):
    quality: EstimateQuality
    basis: EstimateBasis
    sample_size: Annotated[int, Field(ge=0)]
    remaining_tasks: Annotated[int, Field(ge=0)]
    remaining_tokens: Annotated[int, Field(ge=0)] | None = None
    remaining_duration_seconds: Annotated[int, Field(ge=0)] | None = None
    low_tokens: Annotated[int, Field(ge=0)] | None = None
    high_tokens: Annotated[int, Field(ge=0)] | None = None
    low_duration_seconds: Annotated[int, Field(ge=0)] | None = None
    high_duration_seconds: Annotated[int, Field(ge=0)] | None = None

    @classmethod
    def unavailable(cls, *, remaining_tasks: int = 0) -> Self:
        _validate_remaining_tasks(remaining_tasks)
        return cls(
            quality=EstimateQuality.UNAVAILABLE,
            basis=EstimateBasis.NONE,
            sample_size=0,
            remaining_tasks=remaining_tasks,
        )


def estimate_batch(
    *,
    samples: Sequence[EstimateSample],
    remaining_tasks: int,
    basis: EstimateBasis = EstimateBasis.CURRENT_BATCH,
) -> BatchEstimate:
    """Scale observed pair metrics without inventing a zero-sample baseline."""

    _validate_remaining_tasks(remaining_tasks)
    if not samples:
        return BatchEstimate.unavailable(remaining_tasks=remaining_tasks)
    if basis is EstimateBasis.NONE:
        raise ValueError("A populated estimate must identify its sample basis")

    token_values = sorted(sample.tokens for sample in samples)
    duration_values = sorted(sample.duration_seconds for sample in samples)
    return BatchEstimate(
        quality=EstimateQuality.PRELIMINARY if len(samples) < 5 else EstimateQuality.MEASURED,
        basis=basis,
        sample_size=len(samples),
        remaining_tasks=remaining_tasks,
        remaining_tokens=_scaled(fmean(token_values), remaining_tasks),
        remaining_duration_seconds=_scaled(fmean(duration_values), remaining_tasks),
        low_tokens=_scaled(_percentile(token_values, 0.25), remaining_tasks),
        high_tokens=_scaled(_percentile(token_values, 0.75), remaining_tasks),
        low_duration_seconds=_scaled(_percentile(duration_values, 0.25), remaining_tasks),
        high_duration_seconds=_scaled(_percentile(duration_values, 0.75), remaining_tasks),
    )


def _validate_remaining_tasks(value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("remaining_tasks must be a non-negative integer")


def _scaled(value: float, remaining_tasks: int) -> int:
    return int(value * remaining_tasks + 0.5)


def _percentile(values: Sequence[int], fraction: float) -> float:
    if len(values) == 1:
        return float(values[0])
    position = (len(values) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(values) - 1)
    weight = position - lower
    return values[lower] * (1 - weight) + values[upper] * weight
