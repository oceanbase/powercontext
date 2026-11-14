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

"""A configured allowance reaches the business pipelines, and rejection sends nothing."""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.usage import RequestUsage

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.atomic_memory.extraction import (
    AtomicMemoryEvidence,
    AtomicMemoryExtractionInput,
)
from powercontext.builtin.artifacts.memory import MemoryHit
from powercontext.builtin.artifacts.profile.service import _generate_within_budget
from powercontext.builtin.inference import InferenceBudgetExceededError
from powercontext.builtin.inference.budget import RequestEstimate, admit_request
from powercontext.builtin.runtime import composition
from powercontext.builtin.runtime.config import (
    GenerationOperationBudgetConfig,
    GenerationRequestBudgetConfig,
    InferenceConfig,
    RuntimeConfig,
)
from powercontext.builtin.sources import BUILTIN_SOURCE_REGISTRY
from powercontext.sources import SourceRef


def _value() -> AtomicMemoryExtractionInput:
    return AtomicMemoryExtractionInput(
        evidence=tuple(
            AtomicMemoryEvidence(
                evidence_id=f"source:{index}",
                source_ref=SourceRef(source_type="content", source_id=f"turn-{index}"),
                journal_position=index + 1,
                content={"content": "Use migration tests.", "metadata": {"note": "preserve evidence; " * 120}},
                source_metadata={"metadata": {"note": "preserve evidence; " * 120}},
            )
            for index in range(3)
        )
    )


def test_atomic_extraction_rejects_before_dispatch_when_the_payload_does_not_fit(monkeypatch) -> None:
    sent: list[Any] = []

    async def respond(messages, info):
        sent.append(messages)
        return ModelResponse(parts=[TextPart('{"candidates":[]}')], usage=RequestUsage(input_tokens=1, output_tokens=1))

    async def open_model(*args, **kwargs):
        model = FunctionModel(respond)
        return model, model

    monkeypatch.setattr(composition, "_open_pydantic_ai_model", open_model)

    async def scenario() -> None:
        async with AsyncExitStack() as resources:
            pipelines = await composition._generation_pipelines(
                InferenceConfig(
                    generation_model="test",
                    generation_request_budget=GenerationRequestBudgetConfig(input_tokens_limit=1_000),
                    generation_operation_budgets={
                        "atomic_memory.extract": GenerationOperationBudgetConfig(input_tokens_limit=50)
                    },
                ),
                RuntimeConfig(),
                resources,
                None,
                BUILTIN_SOURCE_REGISTRY,
            )
            pipeline = pipelines[1]
            assert pipeline is not None
            with pytest.raises(InferenceBudgetExceededError) as rejected:
                await pipeline.extract(_value())
            assert rejected.value.decision.operation == "atomic_memory.extract"
            assert rejected.value.decision.dimension == "input_tokens"
            assert rejected.value.decision.limit_value == 50

    asyncio.run(scenario())

    assert sent == []


class _BudgetedProfileGenerator:
    """Admits a Profile request only while it holds at most ``max_sources`` Sources."""

    def __init__(self, max_sources: int) -> None:
        self.max_sources = max_sources
        self.calls: list[int] = []

    async def generate(self, value):
        self.calls.append(len(value.sources))
        if len(value.sources) <= self.max_sources:
            return "# Profile"
        raise rejected_profile_request()


def rejected_profile_request() -> InferenceBudgetExceededError:
    config = InferenceConfig(
        generation_model="test",
        generation_request_budget=GenerationRequestBudgetConfig(input_tokens_limit=100),
        generation_operation_budgets={"profile.generate": GenerationOperationBudgetConfig(input_tokens_limit=10)},
    )
    budget = config.generation_budget("profile.generate")
    estimate = RequestEstimate(
        estimated_input_tokens=10_000,
        estimator=budget.estimator,
        scope="complete_request",
    )
    return InferenceBudgetExceededError(admit_request(budget, estimate, operation="profile.generate"))


def _window(count: int) -> tuple[Any, ...]:
    return tuple(
        SimpleNamespace(
            journal_position=index + 1,
            value=SimpleNamespace(model_dump_json=lambda index=index: '{"content":"evidence"}'),
        )
        for index in range(count)
    )


def test_profile_generation_shrinks_the_window_instead_of_failing() -> None:
    generator = _BudgetedProfileGenerator(max_sources=2)

    async def scenario():
        return await _generate_within_budget(generator, _window(4), previous_content="# Profile")

    evidence, through, markdown = asyncio.run(scenario())

    assert generator.calls == [4, 2]
    assert len(evidence) == 2
    assert through == 2
    assert markdown == "# Profile"


def test_profile_generation_rejects_a_single_source_that_does_not_fit() -> None:
    generator = _BudgetedProfileGenerator(max_sources=0)

    async def scenario():
        return await _generate_within_budget(generator, _window(2), previous_content=None)

    with pytest.raises(InferenceBudgetExceededError):
        asyncio.run(scenario())
    assert generator.calls == [2, 1]


def _hits() -> tuple[MemoryHit, ...]:
    return tuple(
        MemoryHit(
            memory_ref=ArtifactRef(family="atomic-memory", artifact_id="memory", revision=1),
            entry_id=f"entry-{rank}",
            entry_version_id=f"entry-{rank}-v1",
            text=f"Candidate {rank}",
            score=1 / rank,
            matched_by=("fts",),
        )
        for rank in range(1, 2)
    )


def test_rerank_admits_against_the_declared_endpoint_capacity(monkeypatch) -> None:
    sent: list[Any] = []

    async def respond(messages, info):
        sent.append(messages)
        return ModelResponse(
            parts=[TextPart('{"selected_ranks":[1]}')], usage=RequestUsage(input_tokens=1, output_tokens=1)
        )

    async def open_model(*args, **kwargs):
        model = FunctionModel(respond)
        return model, model

    monkeypatch.setattr(composition, "_open_pydantic_ai_model", open_model)

    async def scenario() -> None:
        async with AsyncExitStack() as resources:
            pipelines = await composition._generation_pipelines(
                InferenceConfig(
                    generation_model="test",
                    rerank_model="test/rerank",
                    rerank_context_window_tokens=200,
                    rerank_request_budget=GenerationRequestBudgetConfig(
                        input_tokens_limit=10,
                        output_reserve_tokens=1,
                    ),
                ),
                RuntimeConfig(memory_rerank_enabled=True),
                resources,
                None,
                BUILTIN_SOURCE_REGISTRY,
            )
            reranker = pipelines[6]
            assert reranker is not None
            with pytest.raises(InferenceBudgetExceededError) as rejected:
                await reranker.rerank("query", _hits(), 1)
            assert rejected.value.decision.operation == "memory.rerank"
            assert rejected.value.decision.dimension == "input_tokens"

    asyncio.run(scenario())

    assert sent == []


def test_operation_request_limit_is_enforced_by_the_composed_skill_generator(monkeypatch) -> None:
    calls = []

    async def respond(messages, info):
        calls.append(messages)
        return ModelResponse(parts=[TextPart('{"proposal":"invalid"}')])

    async def open_model(*args, **kwargs):
        model = FunctionModel(respond)
        return model, model

    monkeypatch.setattr(composition, "_open_pydantic_ai_model", open_model)

    async def scenario():
        from powercontext.builtin.artifacts.generation import (
            ArtifactGenerationInput,
            GenerationEvidence,
            GenerationEvidenceKind,
        )
        from powercontext.builtin.inference.errors import InferenceError

        async with AsyncExitStack() as resources:
            pipelines = await composition._generation_pipelines(
                InferenceConfig(
                    generation_model="test",
                    generation_operation_budgets={"skill.generate": GenerationOperationBudgetConfig(max_requests=1)},
                ),
                RuntimeConfig(),
                resources,
                None,
                BUILTIN_SOURCE_REGISTRY,
            )
            generator = pipelines[4]
            assert generator is not None
            with pytest.raises(InferenceError):
                await generator.generate(
                    ArtifactGenerationInput(
                        evidence=(
                            GenerationEvidence(evidence_id="e", kind=GenerationEvidenceKind.SOURCE, content="Fact"),
                        )
                    )
                )

    asyncio.run(scenario())
    assert len(calls) == 1


def test_operation_timeout_is_enforced_by_the_composed_skill_generator(monkeypatch) -> None:
    from powercontext.builtin.inference import InferenceTimeoutError

    async def respond(messages, info):
        await asyncio.sleep(0.5)
        return ModelResponse(parts=[TextPart('{"proposal":null}')])

    async def open_model(*args, **kwargs):
        model = FunctionModel(respond)
        return model, model

    monkeypatch.setattr(composition, "_open_pydantic_ai_model", open_model)

    async def scenario():
        from powercontext.builtin.artifacts.generation import (
            ArtifactGenerationInput,
            GenerationEvidence,
            GenerationEvidenceKind,
        )

        async with AsyncExitStack() as resources:
            pipelines = await composition._generation_pipelines(
                InferenceConfig(
                    generation_model="test",
                    generation_operation_budgets={
                        "skill.generate": GenerationOperationBudgetConfig(timeout_seconds=0.05)
                    },
                ),
                RuntimeConfig(),
                resources,
                None,
                BUILTIN_SOURCE_REGISTRY,
            )
            generator = pipelines[4]
            assert generator is not None
            with pytest.raises(InferenceTimeoutError):
                await generator.generate(
                    ArtifactGenerationInput(
                        evidence=(
                            GenerationEvidence(evidence_id="e", kind=GenerationEvidenceKind.SOURCE, content="Fact"),
                        )
                    )
                )

    asyncio.run(scenario())


def test_profile_retry_growth_does_not_start_a_new_generation() -> None:
    import json

    from pydantic_ai.messages import UserPromptPart

    from powercontext.builtin.artifacts.profile.generation import LLMProfileGenerator
    from powercontext.builtin.artifacts.profile.service import ProfileGenerationInput, ProfileGenerationOutput
    from powercontext.builtin.inference.pydantic_ai import InferenceLimits, PydanticAIStructuredGenerator

    calls = []

    async def respond(messages, info):
        payloads = [part.content for message in messages for part in message.parts if isinstance(part, UserPromptPart)]
        calls.append(len(json.loads(payloads[-1])["sources"]))
        return ModelResponse(
            parts=[TextPart(json.dumps({"content": ["X" * 10_000]}))],
            usage=RequestUsage(input_tokens=10, output_tokens=100),
        )

    async def scenario():
        raw = PydanticAIStructuredGenerator(
            model=FunctionModel(respond),
            instructions="Update profile",
            input_type=ProfileGenerationInput,
            output_type=ProfileGenerationOutput,
            budget=InferenceConfig(
                generation_request_budget=GenerationRequestBudgetConfig(input_tokens_limit=1500)
            ).generation_budget("profile.generate"),
            operation="profile.generate",
            limits=InferenceLimits(max_requests=2),
        )
        with pytest.raises(InferenceBudgetExceededError) as rejected:
            await _generate_within_budget(LLMProfileGenerator(raw), _window(4), None)
        assert rejected.value.decision.phase == "retry_dispatch"
        assert rejected.value.requests_sent == 1
        assert rejected.value.usage is not None
        assert rejected.value.usage.requests == 1

    asyncio.run(scenario())
    assert calls == [4]


def test_profile_local_reselection_shares_one_deadline() -> None:
    from powercontext.builtin.inference import InferenceTimeoutError

    class SlowSelection(_BudgetedProfileGenerator):
        timeout_seconds = 0.05

        async def generate(self, value):
            await asyncio.sleep(0.03)
            return await super().generate(value)

    async def scenario():
        with pytest.raises(InferenceTimeoutError):
            await _generate_within_budget(SlowSelection(max_sources=1), _window(4), None)

    asyncio.run(scenario())
