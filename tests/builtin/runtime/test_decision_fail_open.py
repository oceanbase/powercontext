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

from powercontext.builtin.inference import (
    InferenceConfigurationError,
    InferenceTimeoutError,
    InferenceUsage,
    InvalidInferenceOutputError,
)
from powercontext.builtin.runtime.decision_model import (
    DecisionOutcome,
    DecisionRequest,
    DecisionResult,
    FailOpenDecisionModel,
)


class _FailingDecisionModel:
    """A backend whose every evaluation raises the supplied failure."""

    policy_id = "powercontext.decision.failing.v1"

    def __init__(self, error: BaseException) -> None:
        self._error = error

    async def evaluate(self, request: DecisionRequest, /) -> DecisionResult:
        raise self._error


class _AnsweringDecisionModel:
    """A backend that always returns one prepared verdict."""

    policy_id = "powercontext.decision.answering.v1"

    def __init__(self, result: DecisionResult) -> None:
        self._result = result

    async def evaluate(self, request: DecisionRequest, /) -> DecisionResult:
        return self._result


@pytest.mark.parametrize(
    "error",
    [
        InferenceConfigurationError("missing provider key"),
        InferenceTimeoutError("generate", 1.0),
        InvalidInferenceOutputError("generate", "schema mismatch"),
        ValueError("empty structured output"),
    ],
)
def test_backend_failures_degrade_to_a_no_op_abstention(error: BaseException) -> None:
    async def scenario() -> None:
        envelope = FailOpenDecisionModel(_FailingDecisionModel(error))

        result = await envelope.evaluate(DecisionRequest("memory.write-gate", "Keep this?", "note"))

        assert result.outcome is DecisionOutcome.ABSTAIN
        assert result.used_fallback is True
        assert result.usage == InferenceUsage(requests=0)
        assert result.policy_id == envelope.policy_id

    asyncio.run(scenario())


def test_cancellation_passes_through_the_envelope() -> None:
    async def scenario() -> None:
        envelope = FailOpenDecisionModel(_FailingDecisionModel(asyncio.CancelledError()))

        with pytest.raises(asyncio.CancelledError):
            await envelope.evaluate(DecisionRequest("memory.write-gate", "Keep this?", "note"))

    asyncio.run(scenario())


def test_a_deliberate_abstention_is_not_a_fallback() -> None:
    async def scenario() -> None:
        deliberate = DecisionResult(
            DecisionOutcome.ABSTAIN,
            "powercontext.decision.answering.v1",
            InferenceUsage(requests=1),
        )
        envelope = FailOpenDecisionModel(_AnsweringDecisionModel(deliberate))

        result = await envelope.evaluate(DecisionRequest("memory.write-gate", "Keep this?", "note"))

        assert result == deliberate
        assert result.used_fallback is False

    asyncio.run(scenario())


def test_a_clear_verdict_passes_through_unchanged() -> None:
    async def scenario() -> None:
        verdict = DecisionResult(
            DecisionOutcome.NO,
            "powercontext.decision.answering.v1",
            InferenceUsage(requests=1),
        )
        envelope = FailOpenDecisionModel(_AnsweringDecisionModel(verdict))

        result = await envelope.evaluate(DecisionRequest("memory.write-gate", "Keep this?", "note"))

        assert result == verdict
        assert result.used_fallback is False

    asyncio.run(scenario())
