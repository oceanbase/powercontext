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

"""Admission is exact, dimension-bound, and never admits an incomplete estimate."""

from __future__ import annotations

from typing import Any, Literal

import pytest
from pydantic import ValidationError

from powercontext.builtin.inference.budget import (
    AdmissionDecision,
    BudgetProvenance,
    EffectiveRequestBudget,
    RequestEstimate,
    admit_request,
    raise_if_rejected,
    resolve_request_budget,
)
from powercontext.builtin.inference.errors import InferenceBudgetExceededError
from powercontext.builtin.inference.tokens import TokenEstimatorProfile, character_token_estimator

ESTIMATOR = TokenEstimatorProfile(estimator_id="character:weighted", version="1")
OTHER_ESTIMATOR = TokenEstimatorProfile(estimator_id="character:weighted", version="2")


def _provenance() -> BudgetProvenance:
    return BudgetProvenance(
        context_window="test",
        input_limit="test",
        output_limit="test",
        output_reserve="test",
        safety_margin="test",
        estimator="character:weighted@1",
    )


def _budget(**changes: Any) -> EffectiveRequestBudget:
    values: dict[str, Any] = {
        "estimator": ESTIMATOR,
        "provenance": _provenance(),
        "context_window_tokens": 10_000,
        "output_reserve_tokens": 1_000,
        "safety_margin_tokens": 500,
    }
    values.update(changes)
    return EffectiveRequestBudget(**values)


def _estimate(tokens: int, scope: Literal["complete_request", "selection"] = "complete_request") -> RequestEstimate:
    return RequestEstimate(estimated_input_tokens=tokens, estimator=ESTIMATOR, scope=scope)


def test_admission_allows_exactly_the_limit_and_rejects_one_unit_more() -> None:
    budget = _budget(input_tokens_limit=2_000)

    allowed = admit_request(budget, _estimate(2_000), operation="skill.generate")
    rejected = admit_request(budget, _estimate(2_001), operation="skill.generate")

    assert allowed.allowed is True
    assert allowed.dimension == "input_tokens"
    assert allowed.limit_value == 2_000
    assert rejected.allowed is False
    assert rejected.dimension == "input_tokens"
    assert rejected.estimated_value == 2_001
    assert rejected.limit_value == 2_000
    assert rejected.unit == "tokens"


def test_output_reserve_narrows_the_allowed_input() -> None:
    narrow = _budget(output_reserve_tokens=1_000)
    wide = _budget(output_reserve_tokens=3_000)

    assert narrow.allowed_input_tokens - wide.allowed_input_tokens == 2_000


def test_context_window_binds_before_a_wider_input_limit() -> None:
    budget = _budget(input_tokens_limit=99_999)

    assert budget.context_limit_tokens == 8_500
    assert budget.binding_dimension == "context_tokens"
    assert budget.allowed_input_tokens == 8_500
    assert admit_request(budget, _estimate(8_501), operation="profile.generate").dimension == "context_tokens"


def test_safety_margin_changes_the_decision() -> None:
    without_margin = _budget(safety_margin_tokens=0)
    with_margin = _budget(safety_margin_tokens=4_000)

    estimate = _estimate(7_000)
    assert admit_request(without_margin, estimate, operation="skill.generate").allowed is True
    assert admit_request(with_margin, estimate, operation="skill.generate").allowed is False


def test_separate_limits_subtract_the_margin_from_the_input_limit() -> None:
    budget = _budget(
        context_semantics="separate_limits",
        context_window_tokens=20_000,
        input_tokens_limit=8_000,
        output_reserve_tokens=2_000,
        safety_margin_tokens=500,
    )

    assert budget.allowed_input_tokens == 7_500
    assert budget.joint_allowed_total_tokens == 19_500
    assert admit_request(budget, _estimate(7_500), operation="skill.generate").allowed is True
    assert admit_request(budget, _estimate(7_501), operation="skill.generate").allowed is False


def test_separate_limits_reject_when_input_plus_output_exceeds_the_joint_ceiling() -> None:
    budget = _budget(
        context_semantics="separate_limits",
        context_window_tokens=10_000,
        input_tokens_limit=9_500,
        output_reserve_tokens=1_000,
        safety_margin_tokens=0,
    )

    decision = admit_request(budget, _estimate(9_100), operation="skill.generate")

    assert decision.allowed is False
    assert decision.dimension == "context_tokens"
    assert decision.limit_value == 10_000
    assert decision.estimated_value == 10_100


def test_selection_estimates_cannot_admit_a_send() -> None:
    budget = _budget(input_tokens_limit=2_000)

    with pytest.raises(ValueError, match="complete-request estimate"):
        admit_request(budget, _estimate(10, scope="selection"), operation="skill.generate")


def test_estimate_from_another_estimator_is_a_programming_error() -> None:
    budget = _budget(input_tokens_limit=2_000)
    estimate = RequestEstimate(estimated_input_tokens=10, estimator=OTHER_ESTIMATOR)

    with pytest.raises(ValueError, match="does not match the resolved budget"):
        admit_request(budget, estimate, operation="skill.generate")


def test_rejection_raises_a_typed_error_with_safe_details() -> None:
    budget = _budget(input_tokens_limit=2_000)
    decision = admit_request(budget, _estimate(2_001), operation="handoff.generate", stage="draft")

    assert decision.allowed is False
    assert isinstance(decision, AdmissionDecision)
    with pytest.raises(InferenceBudgetExceededError) as rejected:
        raise_if_rejected(decision)

    error = rejected.value
    assert error.code == "inference_budget_exceeded"
    assert str(error) == "inference budget exceeded for handoff.generate (input_tokens)"
    details = error.details()
    assert details["operation"] == "handoff.generate"
    assert details["stage"] == "draft"
    assert details["dimension"] == "input_tokens"
    assert details["estimated_value"] == 2_001
    assert details["limit_value"] == 2_000
    assert details["estimator_id"] == "character:weighted"
    assert details["estimator_version"] == "1"


def test_allowed_decision_reports_the_binding_dimension() -> None:
    decision = admit_request(_budget(input_tokens_limit=2_000), _estimate(10), operation="skill.generate")

    assert decision.allowed is True
    assert decision.dimension == "input_tokens"
    assert raise_if_rejected(decision) is decision


def test_budget_rejects_unexecutable_capacity() -> None:
    with pytest.raises(ValidationError, match="joint_window requires context_window_tokens"):
        EffectiveRequestBudget(estimator=ESTIMATOR, provenance=_provenance(), input_tokens_limit=1_000)
    with pytest.raises(ValidationError, match="separate_limits requires input_tokens_limit"):
        EffectiveRequestBudget(estimator=ESTIMATOR, provenance=_provenance(), context_semantics="separate_limits")
    with pytest.raises(ValidationError, match="leave no executable context"):
        _budget(context_window_tokens=2_000, output_reserve_tokens=1_000, safety_margin_tokens=1_000)
    with pytest.raises(ValidationError, match="exceeds the declared output limit"):
        _budget(output_tokens_limit=500, output_reserve_tokens=1_000)


def test_resolve_applies_default_reserve_and_ratio_margin() -> None:
    budget = resolve_request_budget(
        estimator=character_token_estimator().profile,
        provenance=_provenance(),
        context_window_tokens=20_000,
    )

    assert budget.output_reserve_tokens == 4_000
    assert budget.safety_margin_tokens == 1_000
    assert budget.allowed_input_tokens == 15_000


def test_resolve_prefers_an_explicit_absolute_margin_over_the_ratio() -> None:
    budget = resolve_request_budget(
        estimator=character_token_estimator().profile,
        provenance=_provenance(),
        context_window_tokens=20_000,
        safety_margin_ratio=0.05,
        safety_margin_tokens=2_500,
    )

    assert budget.safety_margin_tokens == 2_500


def test_character_estimator_counts_non_ascii_per_character() -> None:
    estimator = character_token_estimator()

    assert estimator.estimate("") == 0
    assert estimator.estimate("abcd") == 1
    assert estimator.estimate("abcde") == 2
    assert estimator.estimate("中" * 10) == 10
    assert estimator.estimate("😀" * 10) == 10
    assert estimator.estimate("abcd中") == 2


def test_estimator_units_for_cjk_and_emoji_are_one_token_per_character() -> None:
    estimator = character_token_estimator()
    budget = _budget(input_tokens_limit=estimator.estimate("中" * 10))

    allowed = admit_request(budget, _estimate(estimator.estimate("中" * 10)), operation="skill.generate")
    rejected = admit_request(budget, _estimate(estimator.estimate("中" * 11)), operation="skill.generate")

    assert allowed.allowed is True
    assert rejected.allowed is False
    assert rejected.estimated_value == 11


def test_character_contract_rejection_is_not_reported_as_a_token_estimate() -> None:
    from powercontext.builtin.inference.budget import hard_limit_decision
    from powercontext.builtin.processing_diagnostics import ProcessingFailureReason, classify_processing_error

    error = InferenceBudgetExceededError(
        hard_limit_decision(
            operation="experience.incubate",
            dimension="input_bytes",
            estimated_value=64001,
            limit_value=64000,
            unit="characters",
        )
    )
    diagnostics = classify_processing_error(error)
    assert diagnostics is not None
    assert diagnostics.reason is ProcessingFailureReason.USAGE_LIMIT
    assert diagnostics.estimated_input_tokens is None
    assert diagnostics.effective_budget is None
