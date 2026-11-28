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

import asyncio

import pytest
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel

from powercontext.builtin.dream.generation import DreamGenerationInput, LLMDreamGenerator
from powercontext.builtin.dream.models import DreamPlan
from powercontext.builtin.evidence.models import EvidenceProjection
from powercontext.builtin.inference import InferenceBudgetExceededError
from powercontext.builtin.inference.budget import EffectiveRequestBudget
from powercontext.builtin.inference.pydantic_ai import InferenceLimits, PydanticAIStructuredGenerator
from powercontext.builtin.inference.request_accounting import BudgetedModel
from powercontext.builtin.runtime.config import GenerationRequestBudgetConfig, InferenceConfig


def _deployment_budget(input_tokens_limit: int | None) -> EffectiveRequestBudget:
    config = InferenceConfig(
        generation_model="test/model",
        generation_request_budget=GenerationRequestBudgetConfig(input_tokens_limit=input_tokens_limit),
    )
    return config.generation_deployment_budget


def _dream_input() -> DreamGenerationInput:
    return DreamGenerationInput(
        operation="revise_profile",
        target_evidence_id=None,
        evidence=EvidenceProjection(evidence=(), root_groups=()),
    )


def _generator(model: FunctionModel, budget: EffectiveRequestBudget | None) -> LLMDreamGenerator:
    return LLMDreamGenerator(
        PydanticAIStructuredGenerator(
            model=BudgetedModel(model),
            instructions="Review the artifact evidence and propose one change.",
            input_type=DreamGenerationInput,
            output_type=DreamPlan,
            limits=InferenceLimits(timeout_seconds=30.0, max_requests=1),
            model_settings={},
            name="artifact_dreaming",
        ),
        config_id="test-config",
        budget=budget,
    )


def test_dream_generation_is_rejected_before_any_provider_call() -> None:
    calls: list[object] = []

    def handler(messages, info):
        calls.append(messages)
        return ModelResponse(parts=[TextPart(content="{}")])

    generator = _generator(FunctionModel(handler), _deployment_budget(10))

    async def scenario():
        return await generator.generate(_dream_input())

    with pytest.raises(InferenceBudgetExceededError) as rejected:
        asyncio.run(scenario())

    assert rejected.value.decision.operation == "revise_profile"
    assert rejected.value.decision.dimension == "input_tokens"
    assert rejected.value.decision.phase == "initial_dispatch"
    assert calls == []


def test_dream_generation_without_deployment_capacity_reaches_the_model() -> None:
    calls: list[object] = []

    def handler(messages, info):
        calls.append(messages)
        return ModelResponse(parts=[TextPart(content="{}")])

    generator = _generator(FunctionModel(handler), _deployment_budget(None))

    async def scenario():
        return await generator.generate(_dream_input())

    with pytest.raises(Exception) as rejected:
        asyncio.run(scenario())

    assert not isinstance(rejected.value, InferenceBudgetExceededError)
    assert len(calls) == 1
