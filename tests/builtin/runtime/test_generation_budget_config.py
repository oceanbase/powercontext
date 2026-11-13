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

"""Deployment capacity and operation allowances resolve without widening in silence."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from powercontext.builtin.artifacts.topic_memory.generation import narrow_stage_budget, topic_memory_stage_budget
from powercontext.builtin.runtime.config import InferenceConfig


def _config(**changes: Any) -> InferenceConfig:
    return InferenceConfig(**changes)


def _rejected(**changes: Any) -> str:
    with pytest.raises(ValidationError) as rejected:
        _config(**changes)
    return str(rejected.value)


def test_default_deployment_reports_its_derived_reserve_and_margin() -> None:
    budget = _config().generation_budget("atomic_memory.extract")

    assert budget.output_reserve_tokens == 4_096
    assert budget.safety_margin_tokens == 6_250
    assert budget.context_limit_tokens == 125_000 - 4_096 - 6_250
    assert budget.provenance.context_window == "generation_model_context_window_tokens"


def test_operation_allowance_narrows_the_deployment_budget() -> None:
    budget = _config(
        generation_request_budget={"input_tokens_limit": 60_000, "output_tokens_limit": 8_000},
        generation_operation_budgets={"skill.generate": {"input_tokens_limit": 30_000}},
    ).generation_budget("skill.generate")

    assert budget.allowed_input_tokens == 30_000
    assert budget.binding_dimension == "input_tokens"
    assert budget.provenance.input_limit == "generation_operation_budgets.skill.generate"
    assert budget.provenance.output_limit == "generation_request_budget"


def test_unknown_operation_key_is_a_configuration_error() -> None:
    message = _rejected(generation_operation_budgets={"atomic_memory.summarize": {"input_tokens_limit": 10}})

    assert "unknown generation operation" in message


def test_operation_allowance_cannot_exceed_a_deployment_limit() -> None:
    message = _rejected(
        generation_request_budget={"input_tokens_limit": 1_000},
        generation_operation_budgets={"skill.generate": {"input_tokens_limit": 2_000}},
    )
    assert "must not exceed generation_request_budget.input_tokens_limit" in message

    message = _rejected(
        generation_request_budget={"output_tokens_limit": 1_000},
        generation_operation_budgets={"skill.generate": {"output_tokens_limit": 2_000}},
    )
    assert "must not exceed generation_request_budget.output_tokens_limit" in message

    message = _rejected(
        generation_max_requests=2,
        generation_operation_budgets={"skill.generate": {"max_requests": 3}},
    )
    assert "max_requests must not exceed generation_max_requests" in message

    message = _rejected(
        generation_timeout_seconds=30.0,
        generation_operation_budgets={"skill.generate": {"timeout_seconds": 60.0}},
    )
    assert "timeout_seconds must not exceed generation_timeout_seconds" in message


def test_count_limits_reject_booleans() -> None:
    message = _rejected(generation_request_budget={"input_tokens_limit": True})

    assert "must be an integer, not a boolean" in message


def test_safety_margin_ratio_stays_below_one() -> None:
    message = _rejected(generation_request_budget={"safety_margin_ratio": 1.0})

    assert "less than 1" in message


def test_separate_limits_require_a_declared_input_limit() -> None:
    message = _rejected(generation_request_budget={"context_semantics": "separate_limits"})

    assert "separate_limits requires input_tokens_limit" in message


def test_model_settings_and_declared_output_limit_must_agree() -> None:
    message = _rejected(
        generation_model="test/model",
        generation_request_budget={"output_tokens_limit": 4_096},
        generation_model_settings={"max_tokens": 2_048},
    )
    assert "generation_model_settings.max_tokens must match" in message

    budget = _config(
        generation_model="test/model",
        generation_request_budget={"output_tokens_limit": 4_096},
        generation_model_settings={"max_tokens": 4_096},
    ).generation_budget("skill.generate")
    assert budget.output_tokens_limit == 4_096


def test_unknown_estimator_identity_is_rejected() -> None:
    message = _rejected(generation_request_budget={"estimator_id": "tiktoken", "estimator_version": "1"})

    assert "unknown token estimator" in message


def test_output_reserve_must_leave_executable_context() -> None:
    message = _rejected(
        generation_model_context_window_tokens=8_000,
        generation_request_budget={"output_reserve_tokens": 8_000},
    )

    assert "leave no executable context" in message


def test_generation_operation_allowance_requires_a_known_operation() -> None:
    with pytest.raises(ValueError, match="unknown generation operation"):
        _config().generation_budget("atomic_memory.summarize")


def test_embedding_budget_declares_only_the_dimensions_the_deployment_set() -> None:
    budget = _config(embedding_input_tokens_per_text_limit=128, embedding_batch_items_limit=8).embedding_budget

    assert budget.max_input_tokens_per_text == 128
    assert budget.max_input_tokens_per_batch is None
    assert budget.has_limits


def test_embedding_budget_stays_unset_when_the_deployment_declares_nothing() -> None:
    budget = _config().embedding_budget

    assert budget.has_limits is False


def test_embedding_budget_rejects_an_unknown_estimator_identity() -> None:
    with pytest.raises(ValidationError, match="unknown token estimator"):
        _config(embedding_estimator="vendor:unknown")


def test_embedding_budget_rejects_a_boolean_count() -> None:
    with pytest.raises(ValidationError):
        _config(embedding_batch_items_limit=True)


def test_topic_stage_allowance_narrows_the_derived_stage_budget() -> None:
    config = _config(
        generation_model="test/model",
        generation_operation_budgets={"topic_memory.probe": {"input_tokens_limit": 10_000}},
    )
    derived = topic_memory_stage_budget(
        context_window_tokens=config.generation_model_context_window_tokens,
        max_requests=config.generation_max_requests,
        model_settings=config.generation_model_settings,
    )

    narrowed = narrow_stage_budget(derived, config.generation_budget("topic_memory.probe"))

    assert narrowed.input_tokens_limit == 10_000
    assert narrowed.output_tokens_limit == derived.output_tokens_limit
    assert narrowed.max_output_tokens_per_request == derived.max_output_tokens_per_request


def test_decision_endpoint_capacity_is_declared_separately() -> None:
    inherited = _config(generation_model="test/model")
    assert inherited.decision_budget is None

    declared = _config(
        generation_model="test/model",
        decision_model="test/decision",
        decision_context_window_tokens=8_000,
        decision_request_budget={"output_reserve_tokens": 512},
    )
    budget = declared.decision_budget
    assert budget is not None
    assert budget.context_window_tokens == 8_000
    assert budget.allowed_input_tokens == 8_000 - 512 - 400
    assert budget.provenance.context_window == "decision_context_window_tokens"


def test_decision_capacity_must_be_declared_together() -> None:
    message = _rejected(
        generation_model="test/model",
        decision_model="test/decision",
        decision_request_budget={"output_reserve_tokens": 512},
    )
    assert "declared together" in message

    message = _rejected(
        generation_model="test/model",
        decision_context_window_tokens=8_000,
    )
    assert "declared together" in message

    message = _rejected(
        generation_model="test/model",
        decision_context_window_tokens=8_000,
        decision_request_budget={"output_reserve_tokens": 512},
    )
    assert "requires decision_model" in message


def test_rerank_endpoint_capacity_is_declared_separately() -> None:
    inherited = _config(generation_model="test/model", rerank_model="test/rerank")
    assert inherited.rerank_budget is None

    declared = _config(
        generation_model="test/model",
        rerank_model="test/rerank",
        rerank_context_window_tokens=4_000,
        rerank_request_budget={"output_reserve_tokens": 256},
    )
    budget = declared.rerank_budget
    assert budget is not None
    assert budget.context_window_tokens == 4_000
    assert budget.provenance.context_window == "rerank_context_window_tokens"
    assert budget.allowed_input_tokens == 4_000 - 256 - 200


def test_rerank_capacity_follows_the_same_pair_rule() -> None:
    message = _rejected(
        generation_model="test/model",
        rerank_model="test/rerank",
        rerank_context_window_tokens=4_000,
    )
    assert "declared together" in message

    message = _rejected(
        generation_model="test/model",
        rerank_context_window_tokens=4_000,
        rerank_request_budget={"output_reserve_tokens": 256},
    )
    assert "requires rerank_model" in message
