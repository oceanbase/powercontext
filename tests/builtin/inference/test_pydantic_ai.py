from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
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
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.instrumented import InstrumentationSettings, InstrumentedModel
from pydantic_ai.models.test import TestModel
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
    probe_pydantic_ai_model,
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


def test_generation_readiness_probe_uses_one_bounded_text_request() -> None:
    observed_max_tokens: list[int | None] = []

    async def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        assert messages
        observed_max_tokens.append(None if info.model_settings is None else info.model_settings.get("max_tokens"))
        return ModelResponse(parts=[TextPart("ok")])

    asyncio.run(probe_pydantic_ai_model(FunctionModel(respond), timeout_seconds=1))

    assert observed_max_tokens == [1]


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
