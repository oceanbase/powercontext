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

"""Behavior contracts for family-independent Artifact fusion."""

import math
import sys
from decimal import Decimal

import pytest
from pydantic import ValidationError


def test_rrf_preserves_missing_channels_and_rank_gaps():
    from powercontext.artifacts.fusion import FusionCandidate, FusionChannel, RrfParameters, fuse_rrf

    channels = (
        FusionChannel("text", 1.0, (FusionCandidate("both", 1), FusionCandidate("text-only", 3))),
        FusionChannel("vector", 1.0, (FusionCandidate("both", 1),)),
    )
    hits = fuse_rrf(channels, RrfParameters(), tie_break=lambda key: key)

    assert [hit.key for hit in hits] == ["both", "text-only"]
    assert hits[0].score == 1.0
    assert hits[0].raw_score == 2 / 61
    assert hits[1].score == pytest.approx(61 / 126)
    assert hits[1].raw_score == 1 / 63


def test_rrf_empty_enabled_channel_contributes_to_normalization():
    from powercontext.artifacts.fusion import FusionCandidate, FusionChannel, RrfParameters, fuse_rrf

    hits = fuse_rrf(
        (FusionChannel("text", 1, (FusionCandidate("a", 1),)), FusionChannel("vector", 1, ())),
        RrfParameters(),
        tie_break=lambda key: key,
    )
    assert hits[0].score == 0.5


def test_rrf_uses_family_tie_break_and_excludes_zero_weight_only_candidates():
    from powercontext.artifacts.fusion import FusionCandidate, FusionChannel, RrfParameters, fuse_rrf

    hits = fuse_rrf(
        (
            FusionChannel("left", 1, (FusionCandidate("b", 1),)),
            FusionChannel("right", 1, (FusionCandidate("a", 1),)),
            FusionChannel("ignored", 0, (FusionCandidate("phantom", 1),)),
        ),
        RrfParameters(),
        tie_break=lambda key: key,
    )
    assert [hit.key for hit in hits] == ["a", "b"]
    assert [hit.score for hit in hits] == [0.5, 0.5]


@pytest.mark.parametrize("value", [True, "1", 1.5, 0, -1])
def test_rrf_rank_constant_is_a_positive_strict_integer(value):
    from powercontext.artifacts.fusion import RrfParameters

    with pytest.raises(ValidationError):
        RrfParameters(rank_constant=value)


@pytest.mark.parametrize("value", [True, "1", -0.1, math.inf, -math.inf, math.nan])
def test_rrf_weights_require_finite_nonnegative_real_numbers(value):
    from powercontext.artifacts.fusion import RrfParameters

    with pytest.raises(ValidationError):
        RrfParameters(weights={"text": value})


def test_fusion_selection_owns_method_specific_parameters():
    from powercontext.artifacts.fusion import FusionSelection, RrfParameters, parse_fusion_parameters

    selection = FusionSelection(method="rrf", params={"weights": {"text": 0.0}, "rank_constant": 7})
    params = parse_fusion_parameters(selection)
    assert isinstance(params, RrfParameters)
    assert params.weights == {"text": 0.0}
    assert params.rank_constant == 7
    with pytest.raises(ValidationError):
        FusionSelection.model_validate({"method": "rrf", "rank_constant": 7})
    with pytest.raises(ValidationError):
        parse_fusion_parameters(FusionSelection(method="rrf", params={"unknown": 1}))
    with pytest.raises(ValueError, match="unsupported fusion method"):
        parse_fusion_parameters(FusionSelection(method="weighted_score"))


def test_fusion_method_dispatch_preserves_family_supplied_channel_weights():
    from powercontext.artifacts.fusion import FusionCandidate, FusionChannel, FusionSelection, fuse

    hits = fuse(
        (FusionChannel("text", 3, (FusionCandidate("a", 1),)), FusionChannel("vector", 1, ())),
        FusionSelection(method="rrf"),
        tie_break=lambda key: key,
    )
    assert hits[0].score == 0.75


@pytest.mark.parametrize("payload", [{"method": " "}, {"method": 1}, {"method": "rrf", "params": []}])
def test_fusion_selection_requires_nonempty_method_and_object_parameters(payload):
    from powercontext.artifacts.fusion import FusionSelection

    with pytest.raises(ValidationError):
        FusionSelection.model_validate(payload)


@pytest.mark.parametrize(
    "defect",
    ["duplicate-channel", "duplicate-key", "unordered-ranks", "duplicate-rank", "bad-rank", "bad-weight", "all-zero"],
)
def test_rrf_rejects_invalid_internal_input(defect):
    from powercontext.artifacts.fusion import FusionCandidate, FusionChannel, RrfParameters, fuse_rrf

    if defect == "duplicate-channel":
        channels = (FusionChannel("text", 1, ()), FusionChannel("text", 1, ()))
    elif defect == "duplicate-key":
        channels = (FusionChannel("text", 1, (FusionCandidate("a", 1), FusionCandidate("a", 2))),)
    elif defect == "unordered-ranks":
        channels = (FusionChannel("text", 1, (FusionCandidate("a", 3), FusionCandidate("b", 1))),)
    elif defect == "duplicate-rank":
        channels = (FusionChannel("text", 1, (FusionCandidate("a", 1), FusionCandidate("b", 1))),)
    elif defect == "bad-rank":
        channels = (FusionChannel("text", 1, (FusionCandidate("a", True),)),)
    elif defect == "bad-weight":
        channels = (FusionChannel("text", math.inf, ()),)
    else:
        channels = (FusionChannel("text", 0, ()),)
    with pytest.raises(ValueError):
        fuse_rrf(channels, RrfParameters(), tie_break=lambda key: key)


def test_rrf_large_weights_normalize_without_overflow():
    from powercontext.artifacts.fusion import FusionCandidate, FusionChannel, RrfParameters, fuse_rrf

    hits = fuse_rrf(
        (FusionChannel("text", 1e308, (FusionCandidate("a", 1),)), FusionChannel("vector", 1e308, ())),
        RrfParameters(),
        tie_break=lambda key: key,
    )
    assert hits[0].score == 0.5
    assert math.isfinite(hits[0].raw_score)


@pytest.mark.parametrize(
    "weight", [math.ulp(0.0), 1e-320, sys.float_info.min, sys.float_info.min * 30.5, sys.float_info.max]
)
def test_rrf_uniform_weight_scaling_preserves_normalized_scores_and_threshold(weight):
    from powercontext.artifacts.fusion import FusionCandidate, FusionChannel, RrfParameters, fuse_rrf

    def search(channel_weight):
        return fuse_rrf(
            (
                FusionChannel("left", channel_weight, (FusionCandidate("a", 1), FusionCandidate("b", 2))),
                FusionChannel("right", channel_weight, (FusionCandidate("b", 1), FusionCandidate("a", 2))),
            ),
            RrfParameters(),
            tie_break=lambda key: key,
        )

    ordinary = search(1.0)
    scaled = search(weight)
    assert [hit.key for hit in scaled] == [hit.key for hit in ordinary]
    assert [hit.score for hit in scaled] == pytest.approx([hit.score for hit in ordinary], rel=1e-15, abs=0)
    assert [hit.key for hit in scaled if hit.score >= 0.999] == []


@pytest.mark.parametrize("weight", [math.ulp(0.0), 1e-320, sys.float_info.min, sys.float_info.max])
def test_rrf_uniform_weight_scaling_preserves_distinct_candidate_order(weight):
    from powercontext.artifacts.fusion import FusionCandidate, FusionChannel, RrfParameters, fuse_rrf

    hits = fuse_rrf(
        (
            FusionChannel("left", weight, (FusionCandidate("z", 1), FusionCandidate("a", 2))),
            FusionChannel("right", weight, (FusionCandidate("a", 3), FusionCandidate("z", 4))),
        ),
        RrfParameters(),
        tie_break=lambda key: key,
    )
    assert [hit.key for hit in hits] == ["z", "a"]
    assert hits[0].score > hits[1].score


def test_rrf_mixed_normal_and_subnormal_weights_preserve_normal_raw_score():
    from powercontext.artifacts.fusion import FusionCandidate, FusionChannel, RrfParameters, fuse_rrf

    hits = fuse_rrf(
        (
            FusionChannel("normal", 1.0, (FusionCandidate("both", 1),)),
            FusionChannel("subnormal", math.ulp(0.0), (FusionCandidate("both", 1), FusionCandidate("tiny", 2))),
        ),
        RrfParameters(),
        tie_break=lambda key: key,
    )
    assert [hit.key for hit in hits] == ["both", "tiny"]
    assert hits[0].score == 1.0
    assert isinstance(hits[0].raw_score, float)
    assert hits[0].raw_score == 1 / 61
    assert hits[1].raw_score > 0


@pytest.mark.parametrize("empty_weight, expected_score", [(120.0, math.ulp(0.0)), (124.0, math.ulp(0.0)), (244.0, 0.0)])
def test_rrf_subnormal_contributions_combine_before_normalization(empty_weight, expected_score):
    from powercontext.artifacts.fusion import FusionCandidate, FusionChannel, RrfParameters, fuse_rrf

    hits = fuse_rrf(
        (
            FusionChannel("empty", empty_weight, ()),
            FusionChannel("left", 61 * math.ulp(0.0), (FusionCandidate("a", 1),)),
            FusionChannel("right", 61 * math.ulp(0.0), (FusionCandidate("a", 1),)),
        ),
        RrfParameters(),
        tie_break=lambda key: key,
    )
    assert hits[0].raw_score > 0
    assert hits[0].score == expected_score


def test_rrf_large_integer_rank_constant_normalizes_without_overflow():
    from powercontext.artifacts.fusion import FusionCandidate, FusionChannel, RrfParameters, fuse_rrf

    hits = fuse_rrf(
        (FusionChannel("text", 1, (FusionCandidate("a", 1),)), FusionChannel("vector", 1, ())),
        RrfParameters(rank_constant=10**400),
        tie_break=lambda key: key,
    )
    assert hits[0].score == 0.5
    assert hits[0].raw_score > 0


def test_rrf_raw_overflow_uses_a_finite_decimal():
    from powercontext.artifacts.fusion import FusionCandidate, FusionChannel, RrfParameters, fuse_rrf

    hits = fuse_rrf(
        tuple(FusionChannel(str(index), 1e308, (FusionCandidate("a", 1),)) for index in range(4)),
        RrfParameters(rank_constant=1),
        tie_break=lambda key: key,
    )
    assert hits[0].score == 1.0
    assert isinstance(hits[0].raw_score, Decimal)
    assert hits[0].raw_score.is_finite()
    assert hits[0].raw_score > Decimal("1e308")


def test_rrf_decimal_raw_order_preserves_ranks_beyond_float_precision():
    from powercontext.artifacts.fusion import FusionCandidate, FusionChannel, RrfParameters, fuse_rrf

    hits = fuse_rrf(
        (FusionChannel("text", 1, (FusionCandidate("z", 1), FusionCandidate("a", 3))),),
        RrfParameters(rank_constant=10**400),
        tie_break=lambda key: key,
    )
    assert [hit.key for hit in hits] == ["z", "a"]
    assert hits[0].raw_score > hits[1].raw_score


def test_rrf_normal_float_path_preserves_legacy_topic_accumulation():
    from powercontext.artifacts.fusion import FusionCandidate, FusionChannel, RrfParameters, fuse_rrf

    ranks = (1, 1, 1, 9)
    hits = fuse_rrf(
        tuple(FusionChannel(str(index), 1, (FusionCandidate("a", rank),)) for index, rank in enumerate(ranks)),
        RrfParameters(),
        tie_break=lambda key: key,
    )
    assert hits[0].raw_score == 0.06367308149204086
    assert hits[0].score * 100 == 97.10144927536231
