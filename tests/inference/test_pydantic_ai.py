from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass

import pytest
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
from pydantic_ai.messages import ModelMessage, ModelResponse
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel

from powercontext.inference import (
    InferenceTimeoutError,
    InferenceUnavailableError,
    InvalidInferenceOutputError,
)
from powercontext.inference.pydantic_ai import (
    InferenceLimits,
    PydanticAIConfigurationError,
    PydanticAIEmbeddingModel,
    PydanticAIStructuredGenerator,
)
from powercontext.memory import EmbeddingProfile


@dataclass(frozen=True, slots=True)
class Question:
    value: str


@dataclass(frozen=True, slots=True)
class Answer:
    value: str


@dataclass(frozen=True, slots=True)
class UnsupportedQuestion:
    value: object


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


def test_structured_generator_binds_schema_and_usage() -> None:
    async def scenario() -> None:
        generator = PydanticAIStructuredGenerator(
            model=TestModel(custom_output_args={"value": "durable preference"}),
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


def test_structured_generator_rejects_model_names_and_wrong_input_types() -> None:
    with pytest.raises(PydanticAIConfigurationError, match="constructed"):
        PydanticAIStructuredGenerator(
            model="test",  # ty: ignore[invalid-argument-type]
            instructions="Return a value.",
            input_type=Question,
            output_type=Answer,
        )

    async def scenario() -> None:
        generator = PydanticAIStructuredGenerator(
            model=TestModel(custom_output_args={"value": "answer"}),
            instructions="Return a value.",
            input_type=Question,
            output_type=Answer,
        )
        with pytest.raises(PydanticAIConfigurationError, match="Question"):
            await generator.generate("wrong")  # ty: ignore[invalid-argument-type]

    asyncio.run(scenario())


def test_structured_generator_maps_serialization_errors() -> None:
    async def scenario() -> None:
        generator = PydanticAIStructuredGenerator(
            model=TestModel(custom_output_args={"value": "answer"}),
            instructions="Return a value.",
            input_type=UnsupportedQuestion,
            output_type=Answer,
        )
        with pytest.raises(PydanticAIConfigurationError, match="serialized"):
            await generator.generate(UnsupportedQuestion(object()))

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


def test_structured_generator_propagates_cancellation() -> None:
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
        )
        task = asyncio.create_task(generator.generate(Question("bounded evidence")))
        await started.wait()
        task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await task
        assert cleaned_up.is_set()

    asyncio.run(scenario())


def test_invalid_structured_output_maps_to_stable_error() -> None:
    async def scenario() -> None:
        generator = PydanticAIStructuredGenerator(
            model=TestModel(custom_output_args={"missing": "value"}),
            instructions="Return a value.",
            input_type=Question,
            output_type=Answer,
            limits=InferenceLimits(max_requests=1),
        )

        with pytest.raises(InvalidInferenceOutputError):
            await generator.generate(Question("bounded evidence"))

    asyncio.run(scenario())


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


@pytest.mark.parametrize(
    "values",
    [
        {"timeout_seconds": 0},
        {"max_requests": 0},
    ],
)
def test_pydantic_ai_limits_must_be_positive(values: dict[str, int]) -> None:
    with pytest.raises(PydanticAIConfigurationError, match="positive"):
        InferenceLimits(**values)


@pytest.mark.parametrize(
    ("model", "message"),
    [
        (ResultEmbeddingModel(((1.0, 2.0),)), "dimension"),
        (ResultEmbeddingModel(((1.0, 2.0, float("nan")),)), "finite"),
        (ResultEmbeddingModel(()), "count"),
        (ResultEmbeddingModel(((1.0, 2.0, 3.0),), returned_inputs=("changed",)), "order"),
    ],
)
def test_embedding_adapter_rejects_invalid_provider_results(
    model: ResultEmbeddingModel,
    message: str,
) -> None:
    async def scenario() -> None:
        adapter = PydanticAIEmbeddingModel(embedder=Embedder(model), profile=TEST_PROFILE)
        with pytest.raises(InvalidInferenceOutputError, match=message):
            await adapter.embed(("original",))

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
