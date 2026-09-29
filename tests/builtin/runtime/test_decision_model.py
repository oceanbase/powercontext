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
import dataclasses
import inspect

import pytest
from pydantic import ValidationError

from powercontext.builtin.inference import GenerationResult, InferenceUnavailableError, InferenceUsage
from powercontext.builtin.runtime import (
    DecisionModelOption,
    DecisionModelRequest,
    DecisionModelResult,
    DecisionOutcome,
    DecisionRequest,
    DecisionResult,
    StructuredDecisionModel,
)
from powercontext.builtin.runtime.decision_model import (
    DECISION_INSTRUCTIONS_VERSION,
    DecisionInput,
    DecisionOutput,
    FailOpenDecisionModel,
    LLMDecisionModel,
)


class _FakeGenerator:
    """Capture the schema-bound input and return one prepared structured answer."""

    def __init__(self, output: DecisionOutput, usage: InferenceUsage | None = None) -> None:
        self._output = output
        self._usage = InferenceUsage(requests=1, input_tokens=2, output_tokens=1) if usage is None else usage
        self.inputs: list[DecisionInput] = []

    async def generate(self, value: DecisionInput, /) -> GenerationResult[DecisionOutput]:
        self.inputs.append(value)
        return GenerationResult(output=self._output, usage=self._usage)


class _StubDecisionModel:
    """Minimal structural DecisionModel used to exercise the port and envelopes."""

    def __init__(self, policy_id: str = DECISION_INSTRUCTIONS_VERSION) -> None:
        self.policy_id = policy_id

    async def evaluate(self, request: DecisionRequest, /) -> DecisionResult:
        return DecisionResult(
            outcome=DecisionOutcome.YES,
            policy_id=self.policy_id,
            usage=InferenceUsage(requests=1),
        )


@pytest.mark.parametrize("answer", list(DecisionOutcome))
def test_llm_decision_model_maps_each_outcome(answer: DecisionOutcome) -> None:
    async def scenario() -> None:
        generator = _FakeGenerator(DecisionOutput(answer=answer, confidence=0.5, rationale="because"))
        model = LLMDecisionModel(generator)

        result = await model.evaluate(
            DecisionRequest(
                decision_kind="memory.write-gate",
                question="Keep this note?",
                subject="note",
                evidence=("evidence",),
            )
        )

        assert result == DecisionResult(
            outcome=answer,
            policy_id=DECISION_INSTRUCTIONS_VERSION,
            usage=InferenceUsage(requests=1, input_tokens=2, output_tokens=1),
            rationale="because",
            confidence=0.5,
        )
        assert result.used_fallback is False
        assert generator.inputs == [
            DecisionInput(
                decision_kind="memory.write-gate",
                question="Keep this note?",
                subject="note",
                evidence=("evidence",),
            )
        ]

    asyncio.run(scenario())


def test_llm_decision_model_leaves_optional_fields_unset() -> None:
    async def scenario() -> None:
        model = LLMDecisionModel(_FakeGenerator(DecisionOutput(answer=DecisionOutcome.ABSTAIN)))

        result = await model.evaluate(DecisionRequest("memory.write-gate", "Keep this?", "note"))

        assert result.outcome is DecisionOutcome.ABSTAIN
        assert result.rationale is None
        assert result.confidence is None
        assert result.used_fallback is False

    asyncio.run(scenario())


def test_llm_decision_model_uses_the_decision_policy_identity() -> None:
    assert LLMDecisionModel.policy_id == DECISION_INSTRUCTIONS_VERSION
    assert DECISION_INSTRUCTIONS_VERSION == "powercontext.decision.evaluate.v1"


def test_fail_open_decision_model_inherits_the_delegate_policy_identity() -> None:
    delegate = _StubDecisionModel(policy_id="powercontext.decision.custom.v7")

    assert FailOpenDecisionModel(delegate).policy_id == "powercontext.decision.custom.v7"


def test_decision_implementations_expose_the_decision_model_port() -> None:
    models = (
        LLMDecisionModel(_FakeGenerator(DecisionOutput(answer=DecisionOutcome.YES))),
        FailOpenDecisionModel(_StubDecisionModel()),
    )

    for model in models:
        assert isinstance(model.policy_id, str)
        assert inspect.iscoroutinefunction(model.evaluate)


def test_decision_values_are_frozen() -> None:
    request = DecisionRequest("memory.write-gate", "Keep this?", "note")
    result = DecisionResult(DecisionOutcome.YES, DECISION_INSTRUCTIONS_VERSION, InferenceUsage(requests=1))

    with pytest.raises(dataclasses.FrozenInstanceError):
        request.subject = "other"  # ty: ignore[invalid-assignment]
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.outcome = DecisionOutcome.NO  # ty: ignore[invalid-assignment]


def test_decision_request_requires_unique_explicit_options() -> None:
    with pytest.raises(ValidationError, match="option IDs must be unique"):
        DecisionModelRequest(
            operation="memory.write_gate",
            question="Should this candidate be written?",
            options=(
                DecisionModelOption(option_id="write", label="Write"),
                DecisionModelOption(option_id="write", label="Also write"),
            ),
        )


def test_decision_result_fallback_must_be_explicit_and_non_selecting() -> None:
    with pytest.raises(ValidationError, match="fallback results must not select an option"):
        DecisionModelResult(
            selected_option_id="write",
            used_fallback=True,
            fallback_reason="provider unavailable",
        )

    with pytest.raises(ValidationError, match="fallback results require fallback_reason"):
        DecisionModelResult(used_fallback=True)


def test_decision_result_can_report_fail_open_without_usage() -> None:
    result = DecisionModelResult(used_fallback=True, fallback_reason="provider unavailable")

    assert result.selected_option_id is None
    assert result.usage.requests == 0


def test_structured_decision_model_reports_usage_from_generator() -> None:
    class Generator:
        async def generate(self, value: DecisionModelRequest, /) -> GenerationResult[DecisionModelResult]:
            assert value.operation == "handoff.consult"
            return GenerationResult(
                output=DecisionModelResult(selected_option_id="continue", confidence=0.8),
                usage=InferenceUsage(requests=1, input_tokens=7, output_tokens=3),
            )

    async def scenario() -> None:
        model = StructuredDecisionModel(Generator(), policy_id="test")
        result = await model.evaluate(
            DecisionModelRequest(
                operation="handoff.consult",
                question="Can the next agent continue?",
                options=(
                    DecisionModelOption(option_id="continue", label="Continue"),
                    DecisionModelOption(option_id="escalate", label="Escalate"),
                ),
            )
        )

        assert result.selected_option_id == "continue"
        assert result.usage.input_tokens == 7

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "output",
    [
        DecisionModelResult(selected_option_id="missing"),
        DecisionModelResult(),
        DecisionModelResult(selected_option_id="write", scores={"missing": 0.9}),
    ],
)
def test_structured_decision_model_rejects_outputs_outside_requested_options(output) -> None:
    class Generator:
        async def generate(self, value: DecisionModelRequest, /) -> GenerationResult[DecisionModelResult]:
            return GenerationResult(
                output=output,
                usage=InferenceUsage(requests=1, input_tokens=7, output_tokens=3),
            )

    async def scenario() -> None:
        model = StructuredDecisionModel(Generator(), policy_id="test")
        result = await model.evaluate(
            DecisionModelRequest(
                operation="memory.write_gate",
                question="Should this candidate be written?",
                options=(
                    DecisionModelOption(option_id="write", label="Write"),
                    DecisionModelOption(option_id="defer", label="Defer"),
                ),
            )
        )

        assert result.selected_option_id is None
        assert result.used_fallback is True
        assert result.fallback_reason == "invalid_output"
        assert result.usage.input_tokens == 7

    asyncio.run(scenario())


def test_structured_decision_model_fails_open_on_provider_unavailable() -> None:
    class Generator:
        async def generate(self, value: DecisionModelRequest, /) -> GenerationResult[DecisionModelResult]:
            raise InferenceUnavailableError("decision")

    async def scenario() -> None:
        model = StructuredDecisionModel(Generator(), policy_id="test")
        result = await model.evaluate(
            DecisionModelRequest(
                operation="memory.write_gate",
                question="Should this candidate be written?",
                options=(
                    DecisionModelOption(option_id="write", label="Write"),
                    DecisionModelOption(option_id="defer", label="Defer"),
                ),
            )
        )

        assert result.selected_option_id is None
        assert result.used_fallback is True
        assert result.fallback_reason == "InferenceUnavailableError"

    asyncio.run(scenario())
