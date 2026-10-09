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

"""Family-independent reciprocal rank fusion with explicit enabled channels."""

from __future__ import annotations

import json
import math
import sys
from collections.abc import Callable, Hashable, Iterable
from dataclasses import dataclass
from decimal import MAX_EMAX, MIN_EMIN, Decimal, localcontext
from fractions import Fraction
from numbers import Real
from typing import Annotated, Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StrictInt

KeyT = TypeVar("KeyT", bound=Hashable)


class FusionSelection(BaseModel):
    """A method name and its JSON parameters, without algorithm-specific fields."""

    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True, allow_inf_nan=False)

    method: str = Field(min_length=1)
    params: dict[str, JsonValue] = Field(default_factory=dict)


class RrfParameters(BaseModel):
    """RRF parameters; a Family applies partial weights to its enabled channels."""

    model_config = ConfigDict(extra="forbid", strict=True)

    rank_constant: StrictInt = Field(default=60, ge=1)
    weights: dict[str, Annotated[float, Field(ge=0, allow_inf_nan=False)]] = Field(default_factory=dict)


@dataclass(frozen=True)
class FusionCandidate(Generic[KeyT]):
    """A hashable candidate identity at its original positive rank."""

    key: KeyT
    rank: int


@dataclass(frozen=True)
class FusionChannel(Generic[KeyT]):
    """One enabled channel, including channels with no admitted candidates."""

    name: str
    weight: float
    candidates: tuple[FusionCandidate[KeyT], ...]


@dataclass(frozen=True)
class FusionHit(Generic[KeyT]):
    """A normalized score and raw RRF sum, ordered with the Family's tie break.

    Ordinary raw sums remain floats. A Decimal preserves positive raw values
    when float accumulation would overflow, underflow, or produce a subnormal
    result. This internal value is never a public channel score.
    """

    key: KeyT
    score: float
    raw_score: float | Decimal


def fuse_rrf(
    channels: Iterable[FusionChannel[KeyT]],
    params: RrfParameters,
    *,
    tie_break: Callable[[KeyT], Any],
) -> tuple[FusionHit[KeyT], ...]:
    """Fuse admitted candidates, preserving enabled empties and original ranks."""

    enabled = tuple(channels)
    _validate_channels(enabled)
    if type(params.rank_constant) is not int or params.rank_constant < 1:
        raise ValueError("rank_constant must be a positive integer")  # noqa: TRY003
    terms: dict[KeyT, list[tuple[float, int]]] = {}
    for channel in enabled:
        if channel.weight > 0:
            for candidate in channel.candidates:
                terms.setdefault(candidate.key, []).append((channel.weight, candidate.rank))
    if not terms:
        return ()

    upper = _raw_sum([(channel.weight, 1) for channel in enabled if channel.weight > 0], params.rank_constant)
    exact_upper: Fraction | None = None
    hits = []
    for key, contributions in terms.items():
        raw = _raw_sum(contributions, params.rank_constant)
        if isinstance(raw, float) and isinstance(upper, float):
            score = raw / upper
        else:
            # Round only the combined ratio, including at subnormal midpoints.
            if exact_upper is None:
                exact_upper = sum((Fraction.from_float(float(channel.weight)) for channel in enabled), Fraction(0)) / (
                    params.rank_constant + 1
                )
            exact_raw = sum(
                (Fraction.from_float(float(weight)) / (params.rank_constant + rank) for weight, rank in contributions),
                Fraction(0),
            )
            score = float(exact_raw / exact_upper)
        hits.append(FusionHit(key, score, raw))
    return tuple(
        sorted(
            hits,
            key=lambda hit: (
                hit.raw_score.copy_negate() if isinstance(hit.raw_score, Decimal) else -hit.raw_score,
                tie_break(hit.key),
            ),
        )
    )


def _validate_channels(channels: tuple[FusionChannel[KeyT], ...]) -> None:
    names: set[str] = set()
    positive_weight = False
    for channel in channels:
        if not isinstance(channel.name, str) or not channel.name.strip() or channel.name in names:
            raise ValueError("fusion channel names must be non-empty and unique")  # noqa: TRY003
        names.add(channel.name)
        try:
            valid_weight = (
                not isinstance(channel.weight, bool)
                and isinstance(channel.weight, Real)
                and math.isfinite(channel.weight)
                and channel.weight >= 0
            )
        except OverflowError:
            valid_weight = False
        if not valid_weight:
            raise ValueError("fusion channel weights must be finite nonnegative numbers")  # noqa: TRY003
        positive_weight |= channel.weight > 0
        keys: set[KeyT] = set()
        previous_rank = 0
        for candidate in channel.candidates:
            if type(candidate.rank) is not int or candidate.rank <= previous_rank:
                raise ValueError("fusion ranks must be positive, unique, and ordered")  # noqa: TRY003
            previous_rank = candidate.rank
            try:
                duplicate = candidate.key in keys
                keys.add(candidate.key)
            except TypeError as exc:
                raise ValueError("fusion candidate keys must be hashable") from exc  # noqa: TRY003
            if duplicate:
                raise ValueError("fusion candidate keys must be unique within a channel")  # noqa: TRY003
    if not positive_weight:
        raise ValueError("enabled fusion channels must have a positive total weight")  # noqa: TRY003


def _raw_sum(terms: list[tuple[float, int]], rank_constant: int) -> float | Decimal:
    try:
        # The legacy Topic score is based on sequential float additions.
        raw = 0.0
        for weight, rank in terms:
            raw += weight / (rank_constant + rank)
        # Subnormal sums can lose rank differences before normalization.
        if math.isfinite(raw) and raw >= sys.float_info.min:
            return raw
    except OverflowError:
        pass
    largest_denominator = max(rank_constant + rank for _, rank in terms)
    with localcontext() as context:
        # Enough digits to retain rank differences even when K is an enormous
        # integer, rather than merging distinct candidates into false ties.
        context.prec = max(34, largest_denominator.bit_length() * 30103 // 100000 + 34)
        context.Emax = MAX_EMAX
        context.Emin = MIN_EMIN
        return sum(
            (Decimal.from_float(float(weight)) / Decimal(rank_constant + rank) for weight, rank in terms),
            Decimal(0),
        )


_FUSION_METHODS = {"rrf": (RrfParameters, fuse_rrf)}


def parse_fusion_parameters(selection: FusionSelection) -> RrfParameters:
    """Strictly parse parameters using the selected method's own model."""

    entry = _FUSION_METHODS.get(selection.method)
    if entry is None:
        raise ValueError(f"unsupported fusion method: {selection.method}")  # noqa: TRY003
    model, _ = entry
    return model.model_validate_json(json.dumps(selection.params, ensure_ascii=False, allow_nan=False), strict=True)


def fuse(
    channels: Iterable[FusionChannel[KeyT]],
    selection: FusionSelection,
    *,
    tie_break: Callable[[KeyT], Any],
) -> tuple[FusionHit[KeyT], ...]:
    """Select the supported method and fuse already admitted Family candidates."""

    params = parse_fusion_parameters(selection)
    _, implementation = _FUSION_METHODS[selection.method]
    return implementation(channels, params, tie_break=tie_break)


__all__ = [
    "FusionCandidate",
    "FusionChannel",
    "FusionHit",
    "FusionSelection",
    "RrfParameters",
    "fuse",
    "fuse_rrf",
    "parse_fusion_parameters",
]
