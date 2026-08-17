"""Optional adapters backed by application-injected Pydantic AI objects."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Generic, TypeVar, cast

from pydantic import BaseModel, Field

from powercontext.builtin.artifacts.memory.canonical import canonical_embedding
from powercontext.builtin.artifacts.memory.models import EmbeddingProfile
from powercontext.builtin.inference.errors import (
    InferenceConfigurationError,
    InferenceError,
    InferenceTimeoutError,
    InferenceUnavailableError,
    InvalidInferenceOutputError,
)
from powercontext.builtin.inference.models import (
    EmbeddingResult,
    GenerationResult,
    InferenceUsage,
)


class PydanticAIConfigurationError(InferenceConfigurationError):
    """Raised when the Pydantic AI adapter cannot be configured safely."""

    def __init__(self, code: str, detail: object | None = None) -> None:
        self.code = code
        self.detail = detail
        messages = {
            "dependency": "Pydantic AI adapters require the 'powercontext[builtin]' optional dependency",
            "model-instance": "model must be an already constructed Pydantic AI Model",
            "instructions": "Pydantic AI instructions must not be empty",
            "schema": "Pydantic AI input or output schema is not supported",
            "input-type": f"generation input must be {detail}",
            "serialize": "generation input could not be serialized",
            "embedder-instance": "embedder must contain an already constructed Pydantic AI embedding model",
            "embedding-model": "embedder does not contain a Pydantic AI embedding model",
            "embedding-batch-size": "embedding batch size must be positive",
            "dimension-positive": "embedding profile dimension must be positive",
            "profile-identifiers": "embedding profile identifiers must not be empty",
            "provider-rejected": "provider rejected the configured Pydantic AI request",
            "pydantic-rejected": "Pydantic AI rejected the configured request",
        }
        super().__init__(messages.get(code, f"Pydantic AI adapter is not configured correctly: {code}"))


try:
    from pydantic import PydanticSchemaGenerationError, PydanticUserError, TypeAdapter, ValidationError
    from pydantic_ai import Agent, Embedder, PromptedOutput
    from pydantic_ai.embeddings import EmbeddingModel as PydanticAIEmbeddingModelBase
    from pydantic_ai.exceptions import (
        ConcurrencyLimitExceeded,
        ModelAPIError,
        ModelHTTPError,
        UnexpectedModelBehavior,
        UsageLimitExceeded,
        UserError,
    )
    from pydantic_ai.messages import ModelRequest, UserPromptPart
    from pydantic_ai.models import Model, ModelRequestParameters
    from pydantic_ai.settings import ModelSettings
    from pydantic_ai.usage import RunUsage, UsageLimits
    from pydantic_core import PydanticSerializationError
except ModuleNotFoundError as error:  # pragma: no cover - exercised in a dependency-free environment
    if error.name is None or error.name.split(".", maxsplit=1)[0] not in {"pydantic", "pydantic_ai", "pydantic_core"}:
        raise
    raise PydanticAIConfigurationError("dependency") from error

InputT = TypeVar("InputT")
OutputT = TypeVar("OutputT")


class InferenceLimits(BaseModel):
    """Bound one Pydantic AI call by wall-clock time and model requests."""

    timeout_seconds: float = Field(default=30.0, gt=0)
    max_requests: int = Field(default=2, ge=1)


class PydanticAIStructuredGenerator(Generic[InputT, OutputT]):
    """Bind one injected Pydantic AI model to fixed instructions and schemas."""

    def __init__(
        self,
        *,
        model: Model,
        instructions: str,
        input_type: type[InputT],
        output_type: type[OutputT],
        limits: InferenceLimits | None = None,
        model_settings: ModelSettings | None = None,
        name: str | None = None,
    ) -> None:
        if isinstance(model, str) or not isinstance(model, Model):
            raise PydanticAIConfigurationError("model-instance")
        if not instructions.strip():
            raise PydanticAIConfigurationError("instructions")
        self._limits = InferenceLimits() if limits is None else limits
        self._input_type = input_type
        try:
            self._input_adapter = TypeAdapter(input_type)
            self._agent = Agent(
                model,
                output_type=PromptedOutput(output_type),
                instructions=instructions,
                model_settings=model_settings,
                retries=self._limits.max_requests - 1,
                name=name,
            )
        except (PydanticSchemaGenerationError, PydanticUserError, UserError) as error:
            raise PydanticAIConfigurationError("schema") from error

    async def generate(self, value: InputT, /) -> GenerationResult[OutputT]:
        """Generate one structured value within the configured request and time limits."""

        if not isinstance(value, self._input_type):
            raise PydanticAIConfigurationError("input-type", self._input_type.__qualname__)
        try:
            prompt = self._input_adapter.dump_json(value).decode("utf-8")
        except (PydanticSerializationError, PydanticUserError, ValidationError, UnicodeError) as error:
            raise PydanticAIConfigurationError("serialize") from error

        try:
            result = await asyncio.wait_for(
                self._agent.run(
                    prompt,
                    usage_limits=UsageLimits(request_limit=self._limits.max_requests),
                ),
                timeout=self._limits.timeout_seconds,
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            mapped = _map_error(error, operation="generate", timeout_seconds=self._limits.timeout_seconds)
            if mapped is None:
                raise
            if mapped is error:
                raise
            raise mapped from error

        usage = _generation_usage(result.usage)
        return GenerationResult(output=cast(OutputT, result.output), usage=usage)


class PydanticAIEmbeddingModel:
    """Adapt an injected Pydantic AI Embedder to the PowerContext embedding port."""

    def __init__(
        self,
        *,
        embedder: Embedder,
        profile: EmbeddingProfile,
        batch_size: int = 10,
        limits: InferenceLimits | None = None,
    ) -> None:
        if not isinstance(embedder, Embedder) or isinstance(embedder.model, str):
            raise PydanticAIConfigurationError("embedder-instance")
        if not isinstance(embedder.model, PydanticAIEmbeddingModelBase):
            raise PydanticAIConfigurationError("embedding-model")
        if profile.dimension < 1:
            raise PydanticAIConfigurationError("dimension-positive")
        if batch_size < 1:
            raise PydanticAIConfigurationError("embedding-batch-size")
        if not profile.profile_id.strip() or not profile.model.strip() or not profile.normalization.strip():
            raise PydanticAIConfigurationError("profile-identifiers")
        self._embedder = embedder
        self.profile = profile
        self._batch_size = batch_size
        self._limits = InferenceLimits() if limits is None else limits

    async def embed(self, texts: tuple[str, ...], /) -> EmbeddingResult:
        """Embed documents and validate order, count, dimension, and finite values."""

        if not texts:
            return EmbeddingResult(vectors=())

        try:
            result = await asyncio.wait_for(self._embed_batches(texts), timeout=self._limits.timeout_seconds)
        except asyncio.CancelledError:
            raise
        except ValueError as error:
            raise InferenceUnavailableError("embed") from error
        except Exception as error:
            mapped = _map_error(error, operation="embed", timeout_seconds=self._limits.timeout_seconds)
            if mapped is None:
                raise
            if mapped is error:
                raise
            raise mapped from error

        return result

    async def _embed_batches(self, texts: tuple[str, ...]) -> EmbeddingResult:
        vectors: list[tuple[float, ...]] = []
        requests = 0
        input_tokens = 0
        for start in range(0, len(texts), self._batch_size):
            batch = texts[start : start + self._batch_size]
            result = await self._embedder.embed_documents(batch)
            vectors.extend(self._validated_vectors(batch, result.inputs, result.input_type, result.embeddings))
            requests += 1
            input_tokens += result.usage.input_tokens
        return EmbeddingResult(
            vectors=tuple(vectors),
            usage=InferenceUsage(requests=requests, input_tokens=input_tokens, output_tokens=None),
        )

    def _validated_vectors(
        self,
        texts: tuple[str, ...],
        inputs: Sequence[str],
        input_type: str,
        values: Sequence[Sequence[float]],
    ) -> tuple[tuple[float, ...], ...]:
        if input_type != "document":
            raise InvalidInferenceOutputError("embed", "provider returned the wrong input type")
        returned_inputs = tuple(inputs)
        rows = tuple(values)
        if returned_inputs != texts:
            raise InvalidInferenceOutputError("embed", "provider changed input order")
        if len(rows) != len(texts):
            raise InvalidInferenceOutputError("embed", "provider returned the wrong vector count")

        vectors: list[tuple[float, ...]] = []
        for row in rows:
            try:
                vectors.append(
                    canonical_embedding(
                        row,
                        dimension=self.profile.dimension,
                        normalization=self.profile.normalization,
                    )
                )
            except (TypeError, ValueError) as error:
                raise InvalidInferenceOutputError("embed", str(error)) from error
        return tuple(vectors)


async def probe_pydantic_ai_model(
    model: Model,
    /,
    *,
    timeout_seconds: float,
) -> None:
    """Send one minimal text request through an assembled generation model."""

    if isinstance(model, str) or not isinstance(model, Model):
        raise PydanticAIConfigurationError("model-instance")
    try:
        await asyncio.wait_for(
            model.request(
                [ModelRequest(parts=[UserPromptPart("Reply with one token.")])],
                ModelSettings(max_tokens=1),
                ModelRequestParameters(),
            ),
            timeout=timeout_seconds,
        )
    except asyncio.CancelledError:
        raise
    except Exception as error:
        mapped = _map_error(error, operation="generate", timeout_seconds=timeout_seconds)
        if mapped is None:
            raise
        if mapped is error:
            raise
        raise mapped from error


def _generation_usage(value: RunUsage) -> InferenceUsage:
    return InferenceUsage(
        requests=value.requests,
        input_tokens=value.input_tokens,
        output_tokens=value.output_tokens,
    )


def _map_error(
    error: Exception,
    *,
    operation: str,
    timeout_seconds: float,
) -> InferenceError | None:
    if isinstance(error, InferenceError):
        return error
    if isinstance(error, TimeoutError):
        return InferenceTimeoutError(operation, timeout_seconds)
    if isinstance(error, ModelHTTPError):
        if error.status_code in {408, 504}:
            return InferenceTimeoutError(operation, timeout_seconds)
        if error.status_code in {409, 425, 429} or error.status_code >= 500:
            return InferenceUnavailableError(operation)
        return PydanticAIConfigurationError("provider-rejected")
    if isinstance(error, (ModelAPIError, ConcurrencyLimitExceeded, OSError)):
        return InferenceUnavailableError(operation)
    if isinstance(error, (UnexpectedModelBehavior, UsageLimitExceeded, ValidationError)):
        return InvalidInferenceOutputError(operation, "provider did not return a valid result")
    if isinstance(error, (PydanticSchemaGenerationError, PydanticUserError, UserError)):
        return PydanticAIConfigurationError("pydantic-rejected")
    return None


__all__ = [
    "InferenceLimits",
    "PydanticAIConfigurationError",
    "PydanticAIEmbeddingModel",
    "PydanticAIStructuredGenerator",
    "probe_pydantic_ai_model",
]
