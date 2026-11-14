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

"""A rejected request sends nothing; a growing retry is re-admitted first."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

import httpx2
import pytest

pytest.importorskip("pydantic_ai")

from openai import AsyncOpenAI
from pydantic import BaseModel
from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, TextPart, UserPromptPart
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.settings import ModelSettings

from powercontext.builtin.inference import request_accounting
from powercontext.builtin.inference.budget import BudgetProvenance, EffectiveRequestBudget
from powercontext.builtin.inference.errors import InferenceBudgetExceededError, InferenceConfigurationError
from powercontext.builtin.inference.pydantic_ai import (
    InferenceLimits,
    PydanticAIStructuredGenerator,
    StreamingAggregateModel,
)
from powercontext.builtin.inference.request_accounting import BudgetedModel, budgeted_exchange
from powercontext.builtin.inference.tokens import character_token_estimator


class Input(BaseModel):
    payload: str


class Output(BaseModel):
    answer: str


def _budget(input_tokens_limit: int) -> EffectiveRequestBudget:
    return EffectiveRequestBudget(
        estimator=character_token_estimator().profile,
        provenance=BudgetProvenance(
            context_window="test",
            input_limit="test",
            output_limit="test",
            output_reserve="test",
            safety_margin="test",
            estimator="test",
        ),
        context_window_tokens=400_000,
        input_tokens_limit=input_tokens_limit,
        output_reserve_tokens=1_000,
        safety_margin_tokens=0,
    )


def _completion(content: str) -> dict[str, Any]:
    return {
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "created": 1,
        "model": "gpt-4o",
        "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }


class _Provider:
    """A real provider SDK client whose transport never touches the network."""

    def __init__(self, contents: list[str] | None = None) -> None:
        self.calls: list[bytes] = []
        self._contents = list(contents or [])
        self._client = httpx2.AsyncClient(transport=httpx2.MockTransport(self._handler), follow_redirects=False)

    async def _handler(self, request: httpx2.Request) -> httpx2.Response:
        self.calls.append(request.content)
        content = self._contents.pop(0) if self._contents else '{"answer": "ok"}'
        return httpx2.Response(200, json=_completion(content), request=request)

    def model(self, *, profile=None) -> OpenAIChatModel:
        sdk = AsyncOpenAI(base_url="https://example.test/v1", api_key="k", http_client=self._client, max_retries=0)
        return OpenAIChatModel("gpt-4o", provider=OpenAIProvider(openai_client=sdk), profile=profile)

    async def aclose(self) -> None:
        await self._client.aclose()


def _generator(
    provider: _Provider,
    budget: EffectiveRequestBudget,
    *,
    operation: str = "skill.generate",
    max_requests: int = 1,
    model_settings: ModelSettings | None = None,
) -> PydanticAIStructuredGenerator[Input, Output]:
    return PydanticAIStructuredGenerator(
        model=provider.model(),
        instructions="SYSTEM-INSTRUCTIONS",
        input_type=Input,
        output_type=Output,
        limits=InferenceLimits(max_requests=max_requests),
        model_settings=model_settings,
        budget=budget,
        operation=operation,
    )


def test_rejected_request_never_reaches_the_provider() -> None:
    async def scenario() -> None:
        provider = _Provider()
        try:
            generator = _generator(provider, _budget(5))
            with pytest.raises(InferenceBudgetExceededError) as rejected:
                await generator.generate(Input(payload="HELLO-WORLD"))

            assert provider.calls == []
            assert rejected.value.decision.dimension == "input_tokens"
            assert rejected.value.decision.phase == "initial_dispatch"
        finally:
            await provider.aclose()

    asyncio.run(scenario())


def test_admitted_request_sends_exactly_one_body() -> None:
    async def scenario() -> None:
        provider = _Provider()
        try:
            generator = _generator(provider, _budget(100_000))
            result = await generator.generate(Input(payload="HELLO-WORLD"))

            assert result.output == Output(answer="ok")
            assert len(provider.calls) == 1
            body = json.loads(provider.calls[0])
            assert body["messages"][0]["content"] == "SYSTEM-INSTRUCTIONS"
            assert any("HELLO-WORLD" in str(message.get("content")) for message in body["messages"])
        finally:
            await provider.aclose()

    asyncio.run(scenario())


def test_retry_growth_is_admitted_before_it_is_sent(monkeypatch: pytest.MonkeyPatch) -> None:
    estimates: list[int] = []
    original = request_accounting.estimate_http_request

    def record(*args: Any, **kwargs: Any) -> Any:
        estimate = original(*args, **kwargs)
        estimates.append(estimate.estimated_input_tokens)
        return estimate

    monkeypatch.setattr(request_accounting, "estimate_http_request", record)

    async def scenario() -> None:
        probe = _Provider(contents=['{"wrong": 1}'])
        growing = _Provider(contents=['{"wrong": 1}'])
        try:
            await _generator(probe, _budget(100_000), max_requests=2).generate(Input(payload="PAYLOAD"))
            assert len(estimates) == 2
            first, second = estimates
            assert second > first, "a rejected schema response must grow the next request"

            limit = first + (second - first) // 2
            with pytest.raises(InferenceBudgetExceededError) as rejected:
                await _generator(growing, _budget(limit), max_requests=2).generate(Input(payload="PAYLOAD"))
        finally:
            await probe.aclose()
            await growing.aclose()

        assert len(growing.calls) == 1, "the rejected retry must not leave the process"
        assert rejected.value.decision.phase == "retry_dispatch"

    asyncio.run(scenario())


def test_streaming_request_is_rejected_before_the_stream_opens() -> None:
    opened: list[bool] = []

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del messages, info
        opened.append(True)
        yield '{"answer": "ok"}'

    async def scenario() -> None:
        generator = PydanticAIStructuredGenerator(
            model=StreamingAggregateModel(FunctionModel(stream_function=stream), idle_timeout_seconds=5),
            instructions="SYSTEM-INSTRUCTIONS",
            input_type=Input,
            output_type=Output,
            budget=_budget(5),
            operation="skill.generate",
        )
        with pytest.raises(InferenceBudgetExceededError):
            await generator.generate(Input(payload="PAYLOAD"))

    asyncio.run(scenario())
    assert opened == []


def test_streamed_request_is_rejected_before_the_stream_opens() -> None:
    opened: list[bool] = []
    entered: list[bool] = []

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del messages, info
        opened.append(True)
        yield '{"answer": "ok"}'

    async def scenario() -> None:
        model = BudgetedModel(FunctionModel(stream_function=stream))
        with budgeted_exchange(_budget(5), operation="skill.generate"), pytest.raises(InferenceBudgetExceededError):
            async with model.request_stream(
                [ModelRequest(parts=[UserPromptPart("PAYLOAD")])],
                None,
                ModelRequestParameters(output_mode="prompted"),
            ):
                entered.append(True)

    asyncio.run(scenario())
    assert entered == []
    assert opened == []


def test_extra_body_cannot_bypass_the_check(monkeypatch: pytest.MonkeyPatch) -> None:
    estimates: list[int] = []
    original = request_accounting.estimate_http_request

    def record(*args: Any, **kwargs: Any) -> Any:
        estimate = original(*args, **kwargs)
        estimates.append(estimate.estimated_input_tokens)
        return estimate

    monkeypatch.setattr(request_accounting, "estimate_http_request", record)
    padding = "X" * 4_000
    settings = ModelSettings(extra_body={"padding": padding})

    async def scenario() -> None:
        plain = _Provider()
        padded = _Provider()
        rejected = _Provider()
        try:
            await _generator(plain, _budget(100_000)).generate(Input(payload="PAYLOAD"))
            await _generator(padded, _budget(100_000), model_settings=settings).generate(Input(payload="PAYLOAD"))
            assert estimates[1] - estimates[0] >= 1_000

            with pytest.raises(InferenceBudgetExceededError):
                await _generator(rejected, _budget(estimates[0] + 10), model_settings=settings).generate(
                    Input(payload="PAYLOAD")
                )
        finally:
            await plain.aclose()
            await padded.aclose()
            await rejected.aclose()

        assert rejected.calls == []

    asyncio.run(scenario())


def test_concurrent_exchanges_keep_their_own_operation(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[str, int]] = []
    original = request_accounting.estimate_http_request

    def record(*args: Any, **kwargs: Any) -> Any:
        exchange = request_accounting.current_exchange()
        assert exchange is not None
        estimate = original(*args, **kwargs)
        seen.append((exchange.operation, exchange.sent_requests))
        return estimate

    monkeypatch.setattr(request_accounting, "estimate_http_request", record)

    async def scenario() -> None:
        left = _Provider()
        right = _Provider()
        try:
            await asyncio.gather(
                _generator(left, _budget(100_000), operation="skill.generate").generate(Input(payload="LEFT")),
                _generator(right, _budget(100_000), operation="handoff.generate").generate(Input(payload="RIGHT")),
            )
        finally:
            await left.aclose()
            await right.aclose()

        assert sorted(seen) == [("handoff.generate", 0), ("skill.generate", 0)]
        assert b"LEFT" in left.calls[0]
        assert b"RIGHT" in right.calls[0]

    asyncio.run(scenario())


def test_estimate_counts_history_instructions_and_output_schema() -> None:
    parameters = ModelRequestParameters(output_mode="prompted")
    short = request_accounting.render_outbound_request(
        [ModelRequest(parts=[UserPromptPart("FIRST")])], None, parameters
    )
    longer = request_accounting.render_outbound_request(
        [
            ModelRequest(parts=[UserPromptPart("FIRST")]),
            ModelResponse(parts=[TextPart("SECOND")]),
            ModelRequest(parts=[UserPromptPart("THIRD")]),
        ],
        ModelSettings(extra_body={"padding": "X"}),
        parameters,
    )

    assert "compatible with this schema" in short
    assert "SECOND" not in short
    assert "SECOND" in longer
    assert len(longer) > len(short)


def test_model_snapshot_is_only_a_selection_estimate() -> None:
    estimate = request_accounting.estimate_outbound_request(
        [ModelRequest(parts=[UserPromptPart("PAYLOAD")])],
        None,
        ModelRequestParameters(output_mode="prompted"),
        estimator=character_token_estimator(),
    )

    assert estimate.scope == "selection"
    assert estimate.estimated_input_tokens >= len("PAYLOAD") // 4


def test_fixed_instruction_context_that_does_not_fit_is_rejected_before_sending() -> None:
    """Even an empty payload cannot fit, so the fixed request context alone rejects it."""

    async def scenario() -> None:
        provider = _Provider()
        try:
            with pytest.raises(InferenceBudgetExceededError) as rejected:
                await _generator(provider, _budget(5)).generate(Input(payload=""))

            decision = rejected.value.decision
            assert decision.dimension == "input_tokens"
            assert decision.estimated_value > decision.limit_value
            assert decision.limit_value == 5
            assert provider.calls == []
        finally:
            await provider.aclose()

    asyncio.run(scenario())


def test_multibyte_payload_is_admitted_by_the_same_estimator(monkeypatch: pytest.MonkeyPatch) -> None:
    estimates: list[int] = []
    original = request_accounting.estimate_http_request

    def record(*args: Any, **kwargs: Any) -> Any:
        estimate = original(*args, **kwargs)
        estimates.append(estimate.estimated_input_tokens)
        return estimate

    monkeypatch.setattr(request_accounting, "estimate_http_request", record)
    payload = "中文内容" * 40

    async def scenario() -> None:
        probe = _Provider()
        try:
            await _generator(probe, _budget(10_000_000)).generate(Input(payload=payload))
        finally:
            await probe.aclose()
        (measured,) = estimates

        rejected_provider = _Provider()
        try:
            with pytest.raises(InferenceBudgetExceededError):
                await _generator(rejected_provider, _budget(measured - 1)).generate(Input(payload=payload))
            assert rejected_provider.calls == []
        finally:
            await rejected_provider.aclose()

        admitted_provider = _Provider()
        try:
            await _generator(admitted_provider, _budget(measured + 1)).generate(Input(payload=payload))
            assert len(admitted_provider.calls) == 1
        finally:
            await admitted_provider.aclose()

    asyncio.run(scenario())


def test_provider_profile_template_is_counted_after_request_assembly() -> None:
    async def scenario() -> None:
        provider = _Provider()
        try:
            generator = PydanticAIStructuredGenerator(
                model=provider.model(profile={"prompted_output_template": "X" * 20_000 + " {schema}"}),
                instructions="Short instructions.",
                input_type=Input,
                output_type=Output,
                budget=_budget(2_000),
                operation="skill.generate",
            )
            with pytest.raises(InferenceBudgetExceededError) as rejected:
                await generator.generate(Input(payload="small"))
            assert rejected.value.decision.estimated_value > 5_000
            assert provider.calls == []
        finally:
            await provider.aclose()

    asyncio.run(scenario())


def test_operation_output_limit_is_used_in_the_serialized_request() -> None:
    async def scenario() -> None:
        provider = _Provider()
        try:
            budget = _budget(100_000).model_copy(update={"output_tokens_limit": 128, "output_reserve_tokens": 128})
            await _generator(provider, budget, model_settings=ModelSettings(max_tokens=10_000)).generate(
                Input(payload="x")
            )
            body = json.loads(provider.calls[0])
            assert body.get("max_completion_tokens", body.get("max_tokens")) == 128
        finally:
            await provider.aclose()

    asyncio.run(scenario())


def test_extra_body_cannot_increase_the_reserved_output_limit() -> None:
    async def scenario() -> None:
        provider = _Provider()
        try:
            with pytest.raises(InferenceBudgetExceededError) as rejected:
                await _generator(
                    provider, _budget(100_000), model_settings=ModelSettings(extra_body={"max_tokens": 10_000})
                ).generate(Input(payload="small"))
            assert rejected.value.decision.dimension == "output_tokens"
            assert provider.calls == []
        finally:
            await provider.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("protocol", ["responses", "anthropic"])
def test_supported_sdk_protocols_reject_before_transport(protocol: str) -> None:
    async def scenario() -> None:
        sent = []

        async def handler(request):
            sent.append(request)
            raise AssertionError("budget-bypass")

        async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
            if protocol == "responses":
                from pydantic_ai.models.openai import OpenAIResponsesModel

                model = OpenAIResponsesModel(
                    "gpt-4o", provider=OpenAIProvider(openai_client=AsyncOpenAI(api_key="k", http_client=client))
                )
            else:
                from anthropic import AsyncAnthropic
                from pydantic_ai.models.anthropic import AnthropicModel
                from pydantic_ai.providers.anthropic import AnthropicProvider

                model = AnthropicModel(
                    "claude-sonnet-4-6",
                    provider=AnthropicProvider(anthropic_client=AsyncAnthropic(api_key="k", http_client=client)),
                )
            generator = PydanticAIStructuredGenerator(
                model=model,
                instructions="Instructions",
                input_type=Input,
                output_type=Output,
                budget=_budget(5),
                operation="skill.generate",
            )
            with pytest.raises(InferenceBudgetExceededError):
                await generator.generate(Input(payload="some text"))
            assert sent == []

    asyncio.run(scenario())


def test_opaque_responses_reference_without_a_type_is_rejected() -> None:
    """An item reference with a missing type is opaque and must be rejected before send."""

    with pytest.raises(InferenceConfigurationError) as rejected:
        request_accounting.estimate_http_request(
            {"input": [{"id": "msg_previous", "type": None}]},
            estimator=character_token_estimator(),
        )
    assert "budget-inline-context-required" in str(rejected.value)
