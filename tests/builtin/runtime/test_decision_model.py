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
from pydantic import ValidationError

from powercontext.builtin.inference import GenerationResult, InferenceUnavailableError, InferenceUsage
from powercontext.builtin.runtime import (
    DecisionModelOption,
    DecisionModelRequest,
    DecisionModelResult,
    StructuredDecisionModel,
)


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
