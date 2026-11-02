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
import json
import logging
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast

import httpx
import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic import BaseModel, ValidationError
from pydantic_ai import Embedder
from pydantic_ai.embeddings import (
    EmbeddingModel as PydanticAIEmbeddingModelBase,
)
from pydantic_ai.embeddings import (
    EmbeddingResult as PydanticAIEmbeddingResult,
)
from pydantic_ai.embeddings import (
    EmbeddingSettings,
    TestEmbeddingModel,
)
from pydantic_ai.embeddings.result import EmbedInputType
from pydantic_ai.exceptions import ModelHTTPError, UsageLimitExceeded
from pydantic_ai.messages import (
    FinishReason,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    TextPart,
    ToolCallPart,
    UserPromptPart,
)
from pydantic_ai.models import Model, ModelRequestParameters, ModelResponseState, StreamedResponse
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.instrumented import InstrumentationSettings, InstrumentedModel
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIChatModelSettings
from pydantic_ai.models.test import TestModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import RequestUsage

from powercontext.builtin.artifacts.memory import EmbeddingProfile
from powercontext.builtin.inference import (
    InferenceTimeoutError,
    InferenceUnavailableError,
    InvalidInferenceOutputError,
)
from powercontext.builtin.inference.pydantic_ai import (
    InferenceLimits,
    PydanticAIConfigurationError,
    PydanticAIEmbeddingModel,
    PydanticAIStructuredGenerator,
    StreamingAggregateModel,
    _map_error,
    probe_pydantic_ai_model,
)
from powercontext.builtin.processing_diagnostics import (
    ProcessingFailureReason,
    ProcessingStage,
    classify_processing_error,
)


@dataclass(frozen=True, slots=True)
class Question:
    value: str


@dataclass(frozen=True, slots=True)
class Answer:
    value: str


# Nested on purpose: retry feedback only carries the raw model output when the validation
# error location is longer than one element, which a flat output type cannot produce.
@dataclass(frozen=True, slots=True)
class Candidate:
    text: str
    intent: str


@dataclass(frozen=True, slots=True)
class Proposal:
    candidates: tuple[Candidate, ...]


TEST_PROFILE = EmbeddingProfile(
    profile_id="test-v1",
    model="test:test",
    dimension=3,
    distance="l2",
    normalization="none",
)


class ResultEmbeddingModel(PydanticAIEmbeddingModelBase):
    def __init__(
        self,
        embeddings: Sequence[Sequence[float]],
        *,
        returned_inputs: Sequence[str] | None = None,
        error: Exception | None = None,
    ) -> None:
        super().__init__()
        self._embeddings = embeddings
        self._returned_inputs = returned_inputs
        self._error = error

    @property
    def model_name(self) -> str:
        return "result-model"

    @property
    def system(self) -> str:
        return "test"

    async def embed(
        self,
        inputs: str | Sequence[str],
        *,
        input_type: EmbedInputType,
        settings: EmbeddingSettings | None = None,
    ) -> PydanticAIEmbeddingResult:
        prepared, _ = self.prepare_embed(inputs, settings)
        if self._error is not None:
            raise self._error
        return PydanticAIEmbeddingResult(
            embeddings=self._embeddings,
            inputs=prepared if self._returned_inputs is None else self._returned_inputs,
            input_type=input_type,
            model_name=self.model_name,
            provider_name=self.system,
        )


class RecordingEmbeddingModel(PydanticAIEmbeddingModelBase):
    def __init__(self) -> None:
        super().__init__()
        self.calls: list[tuple[str, ...]] = []
        self.input_types: list[EmbedInputType] = []

    @property
    def model_name(self) -> str:
        return "recording-model"

    @property
    def system(self) -> str:
        return "test"

    async def embed(
        self,
        inputs: str | Sequence[str],
        *,
        input_type: EmbedInputType,
        settings: EmbeddingSettings | None = None,
    ) -> PydanticAIEmbeddingResult:
        prepared, _ = self.prepare_embed(inputs, settings)
        self.calls.append(tuple(prepared))
        self.input_types.append(input_type)
        return PydanticAIEmbeddingResult(
            embeddings=((1.0, 2.0, 3.0),) * len(prepared),
            inputs=prepared,
            input_type=input_type,
            model_name=self.model_name,
            provider_name=self.system,
            usage=RequestUsage(input_tokens=len(prepared)),
        )


def test_structured_generator_binds_schema_and_usage() -> None:
    async def scenario() -> None:
        generator = PydanticAIStructuredGenerator(
            model=TestModel(custom_output_text='{"value":"durable preference"}'),
            instructions="Return the durable value.",
            input_type=Question,
            output_type=Answer,
        )

        result = await generator.generate(Question("I prefer aisle seats."))

        assert result.output == Answer("durable preference")
        assert result.usage.requests == 1
        assert result.usage.input_tokens is not None
        assert result.usage.output_tokens is not None

    asyncio.run(scenario())


def test_structured_generator_passes_explicit_model_settings() -> None:
    observed_temperatures: list[float | None] = []

    async def record_settings(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        del messages
        observed_temperatures.append(None if info.model_settings is None else info.model_settings.get("temperature"))
        return ModelResponse(parts=[TextPart('{"value":"stable"}')])

    async def scenario() -> None:
        generator = PydanticAIStructuredGenerator(
            model=FunctionModel(record_settings),
            instructions="Return a value.",
            input_type=Question,
            output_type=Answer,
            model_settings={"temperature": 0.0},
        )

        result = await generator.generate(Question("bounded evidence"))

        assert result.output == Answer("stable")
        assert observed_temperatures == [0.0]

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("model_name", "overrides"),
    [
        ("o3", {}),
        ("gpt-4o", {}),
        ("gpt-5.1", {}),
        ("gpt-5.1", {"openai_reasoning_effort": "low"}),
        ("gpt-5.1", {"openai_reasoning_effort": "none"}),
        ("gpt-5.1", {"thinking": "medium"}),
        ("gpt-5.1", {"thinking": False}),
        ("gpt-5.1", {"openai_reasoning_effort": "none", "thinking": "medium"}),
    ],
)
@pytest.mark.filterwarnings("ignore:Sampling parameters.*:UserWarning")
def test_structured_generator_receipt_uses_reasoning_provider_settings(
    model_name: str, overrides: OpenAIChatModelSettings
) -> None:
    requests: list[dict[str, object]] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "id": "receipt-settings",
                "object": "chat.completion",
                "created": 0,
                "model": model_name,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": '{"value":"stable"}'},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            settings: OpenAIChatModelSettings = {"temperature": 0.0, "top_p": 0.2, "max_tokens": 512}
            settings.update(overrides)
            generator = PydanticAIStructuredGenerator(
                model=OpenAIChatModel(model_name, provider=OpenAIProvider(api_key="test", http_client=client)),
                instructions="Return a value.",
                input_type=Question,
                output_type=Answer,
                model_settings=settings,
            )
            result = await generator.generate(Question("settings evidence"))
            assert result.output == Answer("stable")
            effective = cast(dict[str, object], generator.receipt_configuration()["effective_settings"])
            sent = requests[0]
            for key in ("temperature", "top_p"):
                assert (key in effective) == (key in sent)
                assert effective.get(key) == sent.get(key)
            assert effective["max_tokens"] == sent["max_completion_tokens"] == 512

    asyncio.run(scenario())


def test_structured_generator_enforces_wire_and_cumulative_output_limits() -> None:
    observed_max_tokens: list[int | None] = []

    async def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        del messages
        observed_max_tokens.append(None if info.model_settings is None else info.model_settings.get("max_tokens"))
        return ModelResponse(
            parts=[TextPart('{"value":"stable"}')],
            usage=RequestUsage(output_tokens=11),
        )

    async def scenario() -> None:
        generator = PydanticAIStructuredGenerator(
            model=FunctionModel(respond),
            instructions="Return a value.",
            input_type=Question,
            output_type=Answer,
            limits=InferenceLimits(
                max_requests=1,
                max_output_tokens_per_request=7,
                output_tokens_limit=10,
            ),
            model_settings={"max_tokens": 100},
        )

        with pytest.raises(InvalidInferenceOutputError):
            await generator.generate(Question("bounded evidence"))
        assert observed_max_tokens == [7]

    asyncio.run(scenario())


def test_structured_generator_bounds_each_retry_and_cumulative_output() -> None:
    observed_max_tokens: list[int | None] = []
    retry_feedback: list[str] = []
    calls = 0

    async def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        nonlocal calls
        calls += 1
        observed_max_tokens.append(None if info.model_settings is None else info.model_settings.get("max_tokens"))
        retry_feedback.extend(
            part.model_response() for message in messages for part in message.parts if isinstance(part, RetryPromptPart)
        )
        text = (
            '{"candidates":[{"text":"candidate"}]}'
            if calls == 1
            else '{"candidates":[{"text":"candidate","intent":"durable"}]}'
        )
        return ModelResponse(parts=[TextPart(text)], usage=RequestUsage(output_tokens=3))

    async def scenario() -> None:
        generator = PydanticAIStructuredGenerator(
            model=FunctionModel(respond),
            instructions="Return proposals.",
            input_type=Question,
            output_type=Proposal,
            limits=InferenceLimits(
                max_requests=2,
                max_output_tokens_per_request=5,
                output_tokens_limit=6,
            ),
            model_settings={"max_tokens": 100},
        )

        result = await generator.generate(Question("bounded evidence"))

        assert result.output == Proposal(candidates=(Candidate(text="candidate", intent="durable"),))
        assert result.usage.requests == 2
        assert result.usage.output_tokens == 6
        assert observed_max_tokens == [5, 5]
        assert len(retry_feedback) == 1
        assert len(retry_feedback[0]) < 512

    asyncio.run(scenario())


def test_structured_generator_maps_rate_limit_without_exposing_provider_body() -> None:
    async def rate_limited(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        del messages, info
        raise ModelHTTPError(429, "test-model", {"secret": "provider response"})

    async def scenario() -> None:
        generator = PydanticAIStructuredGenerator(
            model=FunctionModel(rate_limited),
            instructions="Return a value.",
            input_type=Question,
            output_type=Answer,
        )

        with pytest.raises(InferenceUnavailableError) as error:
            await generator.generate(Question("bounded evidence"))
        assert "secret" not in str(error.value)
        assert isinstance(error.value.__cause__, ModelHTTPError)
        diagnostics = classify_processing_error(error.value)
        assert diagnostics is not None
        assert diagnostics.stage is ProcessingStage.GENERATION
        assert diagnostics.reason is ProcessingFailureReason.RATE_LIMIT
        assert diagnostics.cause_exception_type == "ModelHTTPError"

    asyncio.run(scenario())


def test_structured_generator_times_out_and_cancels_underlying_call() -> None:
    async def scenario() -> None:
        started = asyncio.Event()
        cleaned_up = asyncio.Event()

        async def never_finishes(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            del messages, info
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaned_up.set()
            raise AssertionError("unreachable")

        generator = PydanticAIStructuredGenerator(
            model=FunctionModel(never_finishes),
            instructions="Return a value.",
            input_type=Question,
            output_type=Answer,
            limits=InferenceLimits(timeout_seconds=1, max_requests=1),
        )

        generation = asyncio.create_task(generator.generate(Question("bounded evidence")))
        await asyncio.wait_for(started.wait(), timeout=2)
        with pytest.raises(InferenceTimeoutError):
            await generation
        assert cleaned_up.is_set()

    asyncio.run(scenario())


class _ScriptedStream(StreamedResponse):
    """Stream scripted chunk events to exercise aggregation edge cases."""

    def __init__(
        self,
        script,
        final_state: ModelResponseState = "complete",
        finish_reason: FinishReason | None = "stop",
        usage: RequestUsage | None = None,
    ) -> None:
        super().__init__(ModelRequestParameters())
        self._script = script
        self._final_state = final_state
        self._finish_reason = finish_reason
        self._usage = RequestUsage() if usage is None else usage

    async def _get_event_iterator(self):
        async for event in self._script(self):
            yield event
        self.state = self._final_state
        self.finish_reason = self._finish_reason

    @property
    def model_name(self) -> str:
        return "scripted-stream"

    @property
    def provider_name(self) -> str:
        return "test"

    @property
    def provider_url(self) -> str | None:
        return None

    async def close_stream(self) -> None:
        return None

    @property
    def timestamp(self) -> datetime:
        return datetime.now(tz=UTC)


class _ScriptedModel(Model):
    """Serve one scripted stream and reject the non-streaming request path."""

    system = "scripted"

    def __init__(self, factory, open_delay_seconds: float = 0.0) -> None:
        self._factory = factory
        self._open_delay_seconds = open_delay_seconds

    @property
    def model_name(self) -> str:
        return "scripted-model"

    async def request(self, messages, model_settings, model_request_parameters, run_context=None) -> ModelResponse:
        raise AssertionError("non-streaming path")  # noqa: TRY003

    @asynccontextmanager
    async def request_stream(self, messages, model_settings, model_request_parameters, run_context=None):
        await asyncio.sleep(self._open_delay_seconds)
        yield self._factory()


def test_streaming_aggregate_model_aggregates_chunks_usage_and_identity(caplog) -> None:
    async def chunked_script(stream: _ScriptedStream) -> AsyncIterator[object]:
        for event in stream._parts_manager.handle_text_delta(vendor_part_id=0, content=""):
            yield event
        for word in ("aggregated ", "value"):
            for event in stream._parts_manager.handle_text_delta(vendor_part_id=0, content=word):
                yield event

    async def scenario() -> None:
        stream = _ScriptedStream(chunked_script, usage=RequestUsage(input_tokens=5, output_tokens=7))
        model = StreamingAggregateModel(_ScriptedModel(lambda: stream), idle_timeout_seconds=5)

        with caplog.at_level(logging.WARNING, logger="powercontext.builtin.inference.pydantic_ai"):
            response = await model.request(
                [ModelRequest(parts=[UserPromptPart("bounded evidence")])],
                ModelSettings(),
                ModelRequestParameters(),
            )

        assert list(response.parts) == [TextPart("aggregated value")]
        assert response.state == "complete"
        assert response.usage == RequestUsage(input_tokens=5, output_tokens=7)
        assert response.model_name == "scripted-stream"
        assert response.provider_name == "test"
        assert not [record for record in caplog.records if record.levelno == logging.WARNING]

    asyncio.run(scenario())


def test_streaming_aggregate_model_warns_when_provider_usage_is_missing(caplog) -> None:
    async def silent_script(stream: _ScriptedStream) -> AsyncIterator[object]:
        for event in stream._parts_manager.handle_text_delta(vendor_part_id=0, content="aggregated value"):
            yield event

    async def scenario() -> None:
        # The default RequestUsage reports zero tokens: no final usage chunk arrived.
        stream = _ScriptedStream(silent_script)
        model = StreamingAggregateModel(_ScriptedModel(lambda: stream), idle_timeout_seconds=5)

        with caplog.at_level(logging.WARNING, logger="powercontext.builtin.inference.pydantic_ai"):
            response = await model.request(
                [ModelRequest(parts=[UserPromptPart("bounded evidence")])],
                ModelSettings(),
                ModelRequestParameters(),
            )

        assert response.state == "complete"
        warnings = [record for record in caplog.records if record.levelno == logging.WARNING]
        assert len(warnings) == 1
        assert warnings[0].event == "inference.generation.usage_missing"
        assert warnings[0].operation == "generate"
        assert warnings[0].unit == "inference"

    asyncio.run(scenario())


def test_streaming_aggregate_model_times_out_when_chunks_stall() -> None:
    cleaned_up = asyncio.Event()

    async def stalling_script(stream: _ScriptedStream) -> AsyncIterator[object]:
        for event in stream._parts_manager.handle_text_delta(vendor_part_id=0, content=""):
            yield event
        try:
            await asyncio.Event().wait()
        finally:
            cleaned_up.set()

    async def scenario() -> None:
        stream = _ScriptedStream(stalling_script)
        model = StreamingAggregateModel(_ScriptedModel(lambda: stream), idle_timeout_seconds=0.1)

        with pytest.raises(TimeoutError):
            await model.request(
                [ModelRequest(parts=[UserPromptPart("bounded evidence")])],
                ModelSettings(),
                ModelRequestParameters(),
            )
        assert await asyncio.wait_for(cleaned_up.wait(), timeout=2)

    asyncio.run(scenario())


def test_streaming_aggregate_model_rejects_incomplete_provider_response() -> None:
    async def partial_script(stream: _ScriptedStream) -> AsyncIterator[object]:
        for event in stream._parts_manager.handle_text_delta(vendor_part_id=0, content="partial"):
            yield event

    async def scenario() -> None:
        stream = _ScriptedStream(partial_script, final_state="incomplete")
        model = StreamingAggregateModel(_ScriptedModel(lambda: stream), idle_timeout_seconds=5)

        with pytest.raises(InvalidInferenceOutputError):
            await model.request(
                [ModelRequest(parts=[UserPromptPart("bounded evidence")])],
                ModelSettings(),
                ModelRequestParameters(),
            )

    asyncio.run(scenario())


def test_streaming_aggregate_model_keeps_interleaved_part_deltas() -> None:
    async def interleaved_script(stream: _ScriptedStream) -> AsyncIterator[object]:
        manager = stream._parts_manager
        if (
            event := manager.handle_tool_call_delta(
                vendor_part_id=0, tool_name="get_weather", args='{"city":', tool_call_id="call_0"
            )
        ) is not None:
            yield event
        if (
            event := manager.handle_tool_call_delta(
                vendor_part_id=1, tool_name="get_time", args='{"tz":', tool_call_id="call_1"
            )
        ) is not None:
            yield event
        # The first tool call keeps receiving argument deltas after the second one started.
        if (event := manager.handle_tool_call_delta(vendor_part_id=0, args=' "Beijing"}')) is not None:
            yield event
        if (event := manager.handle_tool_call_delta(vendor_part_id=1, args=' "UTC"}')) is not None:
            yield event

    async def scenario() -> None:
        stream = _ScriptedStream(interleaved_script)
        model = StreamingAggregateModel(_ScriptedModel(lambda: stream), idle_timeout_seconds=5)

        response = await model.request(
            [ModelRequest(parts=[UserPromptPart("weather and time")])],
            ModelSettings(),
            ModelRequestParameters(),
        )

        assert [(part.tool_name, part.args) for part in response.parts if isinstance(part, ToolCallPart)] == [
            ("get_weather", '{"city": "Beijing"}'),
            ("get_time", '{"tz": "UTC"}'),
        ]

    asyncio.run(scenario())


def test_streaming_aggregate_model_times_out_while_opening_the_stream() -> None:
    async def scripted_script(stream: _ScriptedStream) -> AsyncIterator[object]:
        for event in stream._parts_manager.handle_text_delta(vendor_part_id=0, content="late first chunk"):
            yield event

    async def scenario() -> None:
        stream = _ScriptedStream(scripted_script)
        # Opening the stream stalls before any chunk exists; the watchdog must
        # cover that phase instead of waiting for the overall generation timeout.
        model = StreamingAggregateModel(
            _ScriptedModel(lambda: stream, open_delay_seconds=0.3), idle_timeout_seconds=0.05
        )

        with pytest.raises(TimeoutError):
            await model.request(
                [ModelRequest(parts=[UserPromptPart("bounded evidence")])],
                ModelSettings(),
                ModelRequestParameters(),
            )

    asyncio.run(scenario())


def test_streaming_aggregate_model_rejects_eof_without_completion_signal() -> None:
    async def truncated_script(stream: _ScriptedStream) -> AsyncIterator[object]:
        for event in stream._parts_manager.handle_text_delta(vendor_part_id=0, content="partial answer"):
            yield event

    async def scenario() -> None:
        # The stream ends cleanly but never carried a finish reason, like an
        # OpenAI-compatible gateway dropping the connection before the final chunk.
        stream = _ScriptedStream(truncated_script, finish_reason=None)
        model = StreamingAggregateModel(_ScriptedModel(lambda: stream), idle_timeout_seconds=5)

        with pytest.raises(InvalidInferenceOutputError):
            await model.request(
                [ModelRequest(parts=[UserPromptPart("bounded evidence")])],
                ModelSettings(),
                ModelRequestParameters(),
            )

    asyncio.run(scenario())


def test_streaming_aggregate_model_accepts_unmapped_raw_finish_reason() -> None:
    async def exotic_script(stream: _ScriptedStream) -> AsyncIterator[object]:
        stream.provider_details = {"finish_reason": "eos"}
        for event in stream._parts_manager.handle_text_delta(vendor_part_id=0, content="done"):
            yield event

    async def scenario() -> None:
        # The gateway reported a finish reason that pydantic_ai does not map to a
        # known value; the raw signal still proves the provider finished generating.
        stream = _ScriptedStream(exotic_script, finish_reason=None)
        model = StreamingAggregateModel(_ScriptedModel(lambda: stream), idle_timeout_seconds=5)

        response = await model.request(
            [ModelRequest(parts=[UserPromptPart("bounded evidence")])],
            ModelSettings(),
            ModelRequestParameters(),
        )

        assert response.state == "complete"
        assert [part.content for part in response.parts if isinstance(part, TextPart)] == ["done"]

    asyncio.run(scenario())


def test_structured_generator_runs_the_aggregate_wrapper_end_to_end() -> None:
    async def chunked_stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        del messages, info
        for word in ('{"value":', ' "chunked"}'):
            yield word

    async def scenario() -> None:
        generator = PydanticAIStructuredGenerator(
            model=StreamingAggregateModel(FunctionModel(stream_function=chunked_stream), idle_timeout_seconds=5),
            instructions="Return a value.",
            input_type=Question,
            output_type=Answer,
            limits=InferenceLimits(timeout_seconds=10, max_requests=2),
        )

        result = await generator.generate(Question("bounded evidence"))

        assert result.output == Answer("chunked")
        assert result.usage.requests == 1

    asyncio.run(scenario())


def test_generation_readiness_probe_uses_one_bounded_text_request() -> None:
    observed_settings: list[dict[str, object] | None] = []

    async def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        assert messages
        observed_settings.append(None if info.model_settings is None else dict(info.model_settings))
        return ModelResponse(parts=[TextPart("ok")])

    asyncio.run(
        probe_pydantic_ai_model(
            FunctionModel(respond),
            timeout_seconds=1,
            model_settings={
                "max_tokens": 100,
                "extra_body": {"chat_template_kwargs": {"enable_thinking": False}},
            },
        )
    )

    assert observed_settings == [
        {
            "max_tokens": 16,
            "extra_body": {"chat_template_kwargs": {"enable_thinking": False}},
        }
    ]


def test_generation_readiness_probe_maps_bad_provider_endpoint_without_leaking_body() -> None:
    async def reject(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        del messages, info
        raise ModelHTTPError(404, "test-model", {"api_key": "secret-provider-body"})

    async def scenario() -> None:
        with pytest.raises(PydanticAIConfigurationError) as error:
            await probe_pydantic_ai_model(FunctionModel(reject), timeout_seconds=1)
        assert "secret-provider-body" not in str(error.value)

    asyncio.run(scenario())


def test_invalid_structured_output_maps_to_stable_error() -> None:
    async def scenario() -> None:
        generator = PydanticAIStructuredGenerator(
            model=TestModel(custom_output_text='{"missing":"value"}'),
            instructions="Return a value.",
            input_type=Question,
            output_type=Answer,
            limits=InferenceLimits(max_requests=1),
        )

        with pytest.raises(InvalidInferenceOutputError):
            await generator.generate(Question("bounded evidence"))

    asyncio.run(scenario())


def test_instrumented_generation_spans_exclude_schema_retry_content() -> None:
    exporter = InMemorySpanExporter()
    provider = TracerProvider(shutdown_on_exit=False)
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    instrumentation = InstrumentationSettings(
        tracer_provider=provider,
        include_content=False,
        include_binary_content=False,
        include_model_request_parameters=False,
    )
    # The first response drops the required `intent`, so the retry feedback quotes it back.
    pending = [
        '{"candidates":[{"text":"traveler prefers aisle seats"}]}',
        '{"candidates":[{"text":"redacted","intent":"add"}]}',
    ]

    async def reply(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        del messages, info
        return ModelResponse(parts=[TextPart(pending.pop(0))])

    async def scenario() -> None:
        generator = PydanticAIStructuredGenerator(
            model=InstrumentedModel(FunctionModel(reply), instrumentation),
            instructions="Propose candidates.",
            input_type=Question,
            output_type=Proposal,
        )

        result = await generator.generate(Question("bounded evidence"))

        assert result.output.candidates[0].intent == "add"

    asyncio.run(scenario())

    spans = exporter.get_finished_spans()
    # Both responses consumed and two chat spans recorded prove the retry path ran.
    assert not pending
    assert len([span for span in spans if span.name.startswith("chat ")]) == 2
    for span in spans:
        assert "traveler prefers aisle seats" not in str(span.attributes)


def test_embedding_adapter_returns_validated_vectors_and_usage() -> None:
    async def scenario() -> None:
        model = PydanticAIEmbeddingModel(
            embedder=Embedder(TestEmbeddingModel(dimensions=3)),
            profile=TEST_PROFILE,
        )

        result = await model.embed(("alpha", "beta"))

        assert result.vectors == ((1.0, 1.0, 1.0), (1.0, 1.0, 1.0))
        assert result.usage.requests == 1

    asyncio.run(scenario())


def test_embedding_adapter_preserves_order_across_bounded_provider_batches() -> None:
    async def scenario() -> None:
        provider = RecordingEmbeddingModel()
        model = PydanticAIEmbeddingModel(
            embedder=Embedder(provider),
            profile=TEST_PROFILE,
            batch_size=2,
        )

        result = await model.embed(("alpha", "beta", "gamma", "delta", "epsilon"))

        assert provider.calls == [("alpha", "beta"), ("gamma", "delta"), ("epsilon",)]
        assert result.vectors == ((1.0, 2.0, 3.0),) * 5
        assert result.usage.requests == 3
        assert result.usage.input_tokens == 5

    asyncio.run(scenario())


def test_embedding_adapter_forwards_query_input_type() -> None:
    async def scenario() -> None:
        provider = RecordingEmbeddingModel()
        model = PydanticAIEmbeddingModel(
            embedder=Embedder(provider),
            profile=TEST_PROFILE,
        )

        result = await model.embed_query(("find this",))

        assert provider.calls == [("find this",)]
        assert provider.input_types == ["query"]
        assert result.vectors == ((1.0, 2.0, 3.0),)

    asyncio.run(scenario())


def test_embedding_adapter_enforces_unit_normalization_profile() -> None:
    async def scenario() -> None:
        model = PydanticAIEmbeddingModel(
            embedder=Embedder(ResultEmbeddingModel(((3.0, 4.0),))),
            profile=EmbeddingProfile(
                profile_id="unit-v1",
                model="test:result-model",
                dimension=2,
                normalization="unit",
            ),
        )

        result = await model.embed(("alpha",))

        assert result.vectors[0] == pytest.approx((0.6, 0.8))

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "embeddings",
    [
        ((1.0, 2.0),),
        ((1.0, 2.0, float("inf")),),
    ],
)
def test_embedding_adapter_preserves_invalid_output_classification(
    embeddings: tuple[tuple[float, ...], ...],
) -> None:
    async def scenario() -> None:
        adapter = PydanticAIEmbeddingModel(
            embedder=Embedder(ResultEmbeddingModel(embeddings)),
            profile=TEST_PROFILE,
        )

        with pytest.raises(InvalidInferenceOutputError):
            await adapter.embed(("bounded text",))

    asyncio.run(scenario())


def test_embedding_adapter_maps_provider_errors_and_preserves_cause() -> None:
    async def scenario() -> None:
        provider_error = ModelHTTPError(503, "result-model", {"secret": "provider response"})
        adapter = PydanticAIEmbeddingModel(
            embedder=Embedder(ResultEmbeddingModel((), error=provider_error)),
            profile=TEST_PROFILE,
        )

        with pytest.raises(InferenceUnavailableError) as error:
            await adapter.embed(("bounded text",))
        assert error.value.__cause__ is provider_error
        assert "secret" not in str(error.value)
        diagnostics = classify_processing_error(error.value)
        assert diagnostics is not None
        assert diagnostics.stage is ProcessingStage.EMBEDDING
        assert diagnostics.reason is ProcessingFailureReason.PROVIDER_UNAVAILABLE

    asyncio.run(scenario())


def test_embedding_adapter_maps_a_rejected_request_to_a_stable_reason_without_leaking_body() -> None:
    async def scenario() -> None:
        provider_error = ModelHTTPError(400, "result-model", {"error": {"message": "secret provider body"}})
        adapter = PydanticAIEmbeddingModel(
            embedder=Embedder(ResultEmbeddingModel((), error=provider_error)),
            profile=TEST_PROFILE,
        )

        with pytest.raises(PydanticAIConfigurationError) as error:
            await adapter.embed(("bounded text",))
        assert error.value.code == "provider-rejected"
        assert error.value.detail == "HTTP 400"
        assert "HTTP 400" in str(error.value)
        assert "secret provider body" not in str(error.value)
        diagnostics = classify_processing_error(error.value)
        assert diagnostics is not None
        assert diagnostics.reason is ProcessingFailureReason.CONFIGURATION

    asyncio.run(scenario())


def test_embedding_adapter_classifies_a_provider_context_limit_without_leaking_the_body() -> None:
    async def scenario() -> None:
        provider_error = ModelHTTPError(
            400,
            "result-model",
            {"error": {"message": "maximum context length is 8192 tokens, body secret-provider-body"}},
        )
        adapter = PydanticAIEmbeddingModel(
            embedder=Embedder(ResultEmbeddingModel((), error=provider_error)),
            profile=TEST_PROFILE,
        )

        with pytest.raises(PydanticAIConfigurationError) as error:
            await adapter.embed(("bounded text",))

        diagnostics = classify_processing_error(error.value)
        assert diagnostics is not None
        assert diagnostics.stage is ProcessingStage.EMBEDDING
        assert diagnostics.reason is ProcessingFailureReason.CONTEXT_OVERFLOW
        assert "secret-provider-body" not in str(error.value)

    asyncio.run(scenario())


def test_structured_generator_classifies_a_length_finish_reason_as_truncated_output() -> None:
    async def truncated(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        del messages, info
        return ModelResponse(
            parts=[TextPart("partial")],
            provider_response_id="req-truncated",
            finish_reason="length",
            state="incomplete",
        )

    async def scenario() -> None:
        generator = PydanticAIStructuredGenerator(
            model=FunctionModel(truncated),
            instructions="Return a value.",
            input_type=Question,
            output_type=Answer,
            limits=InferenceLimits(allow_continuations=False),
        )

        with pytest.raises(InvalidInferenceOutputError) as error:
            await generator.generate(Question("bounded evidence"))

        diagnostics = classify_processing_error(error.value)
        assert diagnostics is not None
        assert diagnostics.stage is ProcessingStage.GENERATION
        assert diagnostics.reason is ProcessingFailureReason.OUTPUT_TRUNCATED
        assert diagnostics.finish_reason == "length"
        assert diagnostics.provider_request_id == "req-truncated"

    asyncio.run(scenario())


def test_instrumented_embedding_spans_nest_under_the_active_span_without_recording_text() -> None:
    exporter = InMemorySpanExporter()
    provider = TracerProvider(shutdown_on_exit=False)
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    instrumentation = InstrumentationSettings(
        tracer_provider=provider,
        include_content=False,
        include_binary_content=False,
        include_model_request_parameters=False,
    )
    model = PydanticAIEmbeddingModel(
        embedder=Embedder(TestEmbeddingModel(dimensions=3), instrument=instrumentation),
        profile=TEST_PROFILE,
    )

    async def scenario() -> None:
        with provider.get_tracer("test").start_as_current_span("powercontext flush_memory"):
            await model.embed(("bounded evidence",))

    asyncio.run(scenario())

    spans = exporter.get_finished_spans()
    operation = next(span for span in spans if span.name == "powercontext flush_memory")
    embeddings = next(span for span in spans if span.name.startswith("embeddings "))
    assert embeddings.parent is not None
    assert embeddings.parent.span_id == operation.context.span_id
    assert "bounded evidence" not in str(embeddings.attributes)


def test_embedding_adapter_maps_empty_provider_data_to_unavailable() -> None:
    async def scenario() -> None:
        provider_error = ValueError("No embedding data received")
        adapter = PydanticAIEmbeddingModel(
            embedder=Embedder(ResultEmbeddingModel((), error=provider_error)),
            profile=TEST_PROFILE,
        )

        with pytest.raises(InferenceUnavailableError):
            await adapter.embed(("bounded text",))

    asyncio.run(scenario())


@pytest.mark.parametrize("status_code", [409, 425])
def test_structured_generator_keeps_transient_provider_conflicts_unavailable(status_code) -> None:
    async def conflict(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        del messages, info
        raise ModelHTTPError(status_code, "test-model", {"error": "try again"})

    async def scenario() -> None:
        generator = PydanticAIStructuredGenerator(
            model=FunctionModel(conflict),
            instructions="Return a value.",
            input_type=Question,
            output_type=Answer,
        )

        with pytest.raises(InferenceUnavailableError) as error:
            await generator.generate(Question("bounded evidence"))

        diagnostics = classify_processing_error(error.value)
        assert diagnostics is not None
        assert diagnostics.reason is ProcessingFailureReason.PROVIDER_UNAVAILABLE
        assert diagnostics.cause_exception_type == "ModelHTTPError"

    asyncio.run(scenario())


def test_usage_limit_maps_to_the_originating_generation_stage() -> None:
    mapped = _map_error(UsageLimitExceeded("request limit"), operation="generate", timeout_seconds=1.0)

    assert mapped is not None
    diagnostics = classify_processing_error(mapped)

    assert diagnostics is not None
    assert diagnostics.stage is ProcessingStage.GENERATION
    assert diagnostics.reason is ProcessingFailureReason.USAGE_LIMIT
    assert diagnostics.cause_exception_type == "UsageLimitExceeded"


def test_usage_limit_public_generate_path_reports_generation_stage() -> None:
    async def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        del messages, info
        return ModelResponse(parts=[TextPart('{"value":"stable"}')], usage=RequestUsage(output_tokens=11))

    async def scenario() -> None:
        generator = PydanticAIStructuredGenerator(
            model=FunctionModel(respond),
            instructions="Return a value.",
            input_type=Question,
            output_type=Answer,
            limits=InferenceLimits(output_tokens_limit=10),
        )

        with pytest.raises(InvalidInferenceOutputError) as error:
            await generator.generate(Question("bounded evidence"))

        diagnostics = classify_processing_error(error.value)
        assert diagnostics is not None
        assert diagnostics.stage is ProcessingStage.GENERATION
        assert diagnostics.reason is ProcessingFailureReason.USAGE_LIMIT
        assert diagnostics.cause_exception_type == "UsageLimitExceeded"

    asyncio.run(scenario())


def test_a_413_without_context_evidence_is_not_context_overflow() -> None:
    mapped = _map_error(
        ModelHTTPError(413, "test-model", {"error": "upload too large"}),
        operation="generate",
        timeout_seconds=1.0,
    )

    assert mapped is not None
    diagnostics = classify_processing_error(mapped)

    assert diagnostics is not None
    assert diagnostics.reason is ProcessingFailureReason.CONFIGURATION


def test_non_length_incomplete_response_does_not_claim_truncation() -> None:
    async def filtered(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        del messages, info
        return ModelResponse(parts=[TextPart("partial")], finish_reason="content_filter", state="incomplete")

    async def scenario() -> None:
        generator = PydanticAIStructuredGenerator(
            model=FunctionModel(filtered),
            instructions="Return a value.",
            input_type=Question,
            output_type=Answer,
            limits=InferenceLimits(allow_continuations=False),
        )

        with pytest.raises(InvalidInferenceOutputError) as error:
            await generator.generate(Question("bounded evidence"))

        diagnostics = classify_processing_error(error.value)
        assert diagnostics is not None
        assert diagnostics.reason is None
        assert diagnostics.finish_reason == "content_filter"

    asyncio.run(scenario())


def test_embedding_public_path_maps_validation_error_to_invalid_output() -> None:
    class Payload(BaseModel):
        value: int

    with pytest.raises(ValidationError) as captured:
        Payload.model_validate({"value": "not-an-integer"})
    validation_error = captured.value

    async def scenario() -> None:
        adapter = PydanticAIEmbeddingModel(
            embedder=Embedder(ResultEmbeddingModel((), error=validation_error)),
            profile=TEST_PROFILE,
        )

        with pytest.raises(InvalidInferenceOutputError) as error:
            await adapter.embed(("bounded text",))

        diagnostics = classify_processing_error(error.value)
        assert diagnostics is not None
        assert diagnostics.stage is ProcessingStage.EMBEDDING
        assert diagnostics.reason is ProcessingFailureReason.INVALID_OUTPUT
        assert diagnostics.cause_exception_type == "ValidationError"

    asyncio.run(scenario())


def test_embedding_public_path_reports_usage_limit_in_embedding_stage() -> None:
    async def scenario() -> None:
        adapter = PydanticAIEmbeddingModel(
            embedder=Embedder(ResultEmbeddingModel((), error=UsageLimitExceeded("limit"))),
            profile=TEST_PROFILE,
        )

        with pytest.raises(InvalidInferenceOutputError) as error:
            await adapter.embed(("bounded text",))

        diagnostics = classify_processing_error(error.value)
        assert diagnostics is not None
        assert diagnostics.stage is ProcessingStage.EMBEDDING
        assert diagnostics.reason is ProcessingFailureReason.USAGE_LIMIT
        assert diagnostics.cause_exception_type == "UsageLimitExceeded"

    asyncio.run(scenario())
