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

"""Optional adapters backed by application-injected Pydantic AI objects."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Sequence
from contextlib import AsyncExitStack, nullcontext
from copy import copy
from typing import Any, Generic, Literal, Self, TypeVar, cast

from pydantic import BaseModel, Field
from typing_extensions import override

from powercontext._logging import log_safely
from powercontext.builtin.artifacts.memory.canonical import canonical_embedding
from powercontext.builtin.artifacts.memory.models import EmbeddingProfile
from powercontext.builtin.artifacts.prompt.service import current_prompt
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
from powercontext.builtin.processing_diagnostics import (
    ProcessingDiagnostics,
    ProcessingFailureReason,
    ProcessingStage,
    attach_diagnostics,
    stage_for_operation,
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
        message = messages.get(code, f"Pydantic AI adapter is not configured correctly: {code}")
        if code == "provider-rejected" and detail is not None:
            # The detail is structured (e.g. "HTTP 400"), never the raw provider response body.
            message = f"{message} ({detail})"
        super().__init__(message)


try:
    from pydantic import PydanticSchemaGenerationError, PydanticUserError, TypeAdapter, ValidationError
    from pydantic_ai import Agent, Embedder, ModelRetry, PromptedOutput, RunContext
    from pydantic_ai.embeddings import EmbeddingModel as PydanticAIEmbeddingModelBase
    from pydantic_ai.exceptions import (
        ConcurrencyLimitExceeded,
        ModelAPIError,
        ModelHTTPError,
        UnexpectedModelBehavior,
        UsageLimitExceeded,
        UserError,
    )
    from pydantic_ai.messages import (
        ModelMessage,
        ModelRequest,
        ModelResponse,
        UserPromptPart,
    )
    from pydantic_ai.models import Model, ModelRequestParameters
    from pydantic_ai.models.wrapper import WrapperModel
    from pydantic_ai.settings import ModelSettings, merge_model_settings
    from pydantic_ai.usage import RunUsage, UsageLimits
    from pydantic_core import PydanticSerializationError
except ModuleNotFoundError as error:  # pragma: no cover - exercised in a dependency-free environment
    if error.name is None or error.name.split(".", maxsplit=1)[0] not in {"pydantic", "pydantic_ai", "pydantic_core"}:
        raise
    raise PydanticAIConfigurationError("dependency") from error

InputT = TypeVar("InputT")
OutputT = TypeVar("OutputT")

logger = logging.getLogger(__name__)


class InferenceLimits(BaseModel):
    """Bound one Pydantic AI call by wall-clock time and model requests."""

    timeout_seconds: float = Field(default=30.0, gt=0)
    max_requests: int = Field(default=2, ge=1)
    max_output_tokens_per_request: int | None = Field(default=None, ge=1)
    output_tokens_limit: int | None = Field(default=None, ge=1)
    allow_continuations: bool = True


class _CompleteResponseModel(WrapperModel):
    """Do not let Agent fold separately billed continuations into one request."""

    @override
    async def request(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        response = await self.wrapped.request(messages, model_settings, model_request_parameters)
        if response.state != "complete":
            finish_reason = getattr(response, "finish_reason", None)
            truncated = str(finish_reason or "").lower() in {"length", "max_tokens"}
            raise attach_diagnostics(
                InvalidInferenceOutputError("generate", "provider continuation is not allowed"),
                ProcessingDiagnostics(
                    stage=ProcessingStage.GENERATION,
                    reason=ProcessingFailureReason.OUTPUT_TRUNCATED if truncated else None,
                    provider_request_id=getattr(response, "provider_response_id", None),
                    finish_reason=finish_reason,
                    failure_location="provider-response",
                ),
            ) from None
        return response


class StreamingAggregateModel(WrapperModel):
    """Stream one request and aggregate the chunks into a complete response.

    Gateways in front of non-streaming inference endpoints may drop an idle
    HTTP exchange long before the configured generation timeout. Streaming the
    same request keeps the connection active. The watchdog bounds both opening
    the stream (sending the request and waiting for its first chunk) and the
    gap between model events (text, thinking, or tool-call parts). Provider
    heartbeat frames that are not yielded as model events do not reset it.
    The wall-clock budget stays owned by the calling generator.

    The aggregate is read from the drained stream, so parts whose deltas
    interleave keep every update. A provider stream is accepted only when it
    reports a finish reason: one that ends without its terminal event (for
    example a gateway that drops the connection before the final chunk) is
    rejected instead of being returned truncated. Streams without a provider
    behind them end when their event iterator ends and are accepted without a
    finish reason. The complete-state guard rejects provider continuations
    (separately billed follow-up requests) and streams the provider left
    unfinished.
    """

    def __init__(self, wrapped: Model, *, idle_timeout_seconds: float = 60.0) -> None:
        if idle_timeout_seconds <= 0:
            raise InferenceConfigurationError("stream-idle-timeout-seconds")
        super().__init__(wrapped)
        self._idle_timeout_seconds = idle_timeout_seconds

    @override
    async def request(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        async with AsyncExitStack() as stack:
            # Entering the stream context sends the provider request and waits for
            # its first chunk, so the watchdog bounds that phase too, not just the
            # gaps between later events.
            stream = await asyncio.wait_for(
                stack.enter_async_context(
                    self.wrapped.request_stream(messages, model_settings, model_request_parameters)
                ),
                timeout=self._idle_timeout_seconds,
            )
            events = stream.__aiter__()
            while True:
                try:
                    # Watch the gap between model events instead of the whole exchange.
                    await asyncio.wait_for(anext(events), timeout=self._idle_timeout_seconds)
                except StopAsyncIteration:
                    break
            # The parts manager kept applying every delta, including to parts whose
            # updates interleave with other parts, so the drained stream is the
            # authority for the aggregated response.
            response = stream.get()
        if response.state != "complete":
            raise InvalidInferenceOutputError(
                "generate",
                "provider continuation is not allowed"
                if response.state == "suspended"
                else "provider stream ended before completion",
            )
        if (
            stream.provider_name is not None
            and response.finish_reason is None
            and not (response.provider_details or {}).get("finish_reason")
        ):
            # A provider stream can end cleanly without its terminal event, for
            # example when a gateway drops the connection before the final chunk.
            # The accumulated content is then truncated even though the stream
            # state defaults to complete, so a missing finish reason is rejected.
            raise InvalidInferenceOutputError("generate", "provider stream ended without a completion signal")
        if response.usage.input_tokens == 0 and response.usage.output_tokens == 0:
            # Some gateways stream the response without a final usage chunk; usage
            # statistics for this request will be missing. Warn without failing: the
            # aggregated response itself is valid.
            log_safely(
                logger,
                logging.WARNING,
                "Streamed generation completed without provider usage; usage statistics will be incomplete",
                extra={
                    "event": "inference.generation.usage_missing",
                    "operation": "generate",
                    "unit": "inference",
                },
            )
        return response


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
        prompt_key: str | None = None,
        output_check: Callable[[InputT, OutputT], None] | None = None,
    ) -> None:
        if isinstance(model, str) or not isinstance(model, Model):
            raise PydanticAIConfigurationError("model-instance")
        if not instructions.strip():
            raise PydanticAIConfigurationError("instructions")
        self._limits = InferenceLimits() if limits is None else limits
        self._input_type = input_type
        self._prompt_key = prompt_key
        try:
            self._input_adapter = TypeAdapter(input_type)
            bounded_settings = model_settings
            if self._limits.max_output_tokens_per_request is not None:
                bounded_settings = merge_model_settings(
                    model_settings,
                    ModelSettings(max_tokens=self._limits.max_output_tokens_per_request),
                )
            self._agent = Agent(
                model if self._limits.allow_continuations else _CompleteResponseModel(model),
                deps_type=input_type,
                output_type=PromptedOutput(output_type),
                instructions=instructions,
                model_settings=bounded_settings,
                retries=self._limits.max_requests - 1,
                name=name,
            )
        except (PydanticSchemaGenerationError, PydanticUserError, UserError) as error:
            raise PydanticAIConfigurationError("schema") from error
        if output_check is not None:
            # A rule relating output to input is returned to the model like a schema error, within the same budget.
            # The InvalidInferenceOutputError detail is sent to the provider, so it must name the rule, not content.

            def check(context: RunContext[InputT], output: Any) -> Any:
                try:
                    output_check(context.deps, cast(OutputT, output))
                except InvalidInferenceOutputError as error:
                    raise ModelRetry(error.detail) from error
                return output

            self._agent.output_validator(check)

    async def generate(self, value: InputT, /) -> GenerationResult[OutputT]:
        """Generate one structured value within the configured request and time limits."""

        if not isinstance(value, self._input_type):
            raise PydanticAIConfigurationError("input-type", self._input_type.__qualname__)
        try:
            prompt = self._input_adapter.dump_json(value).decode("utf-8")
        except (PydanticSerializationError, PydanticUserError, ValidationError, UnicodeError) as error:
            raise PydanticAIConfigurationError("serialize") from error

        try:
            selection = None if self._prompt_key is None else current_prompt(self._prompt_key)
            # Agent.override uses task-local state; concurrent Scopes never mutate a shared Agent.
            override = (
                self._agent.override(instructions=selection.compiled_instructions)
                if selection is not None and selection.selection == "artifact"
                else nullcontext()
            )
            with override:
                result = await asyncio.wait_for(
                    self._agent.run(
                        prompt,
                        deps=value,
                        usage_limits=UsageLimits(
                            request_limit=self._limits.max_requests,
                            output_tokens_limit=self._limits.output_tokens_limit,
                        ),
                        metadata=None if selection is None else selection.trace_attributes(),
                    ),
                    timeout=self._limits.timeout_seconds,
                )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            # An exhausted retry budget reports the rule the last response broke, not a generic failure.
            rejected = _checked_rule(error) if isinstance(error, UnexpectedModelBehavior) else None
            if rejected is not None:
                raise rejected from error
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

    def _without_instrumentation(self) -> Self:
        """Copy this adapter for readiness without changing operational tracing."""

        adapter = copy(self)
        adapter._embedder = copy(self._embedder)
        adapter._embedder.instrument = False
        return adapter

    async def embed(self, texts: tuple[str, ...], /) -> EmbeddingResult:
        """Embed documents and validate order, count, dimension, and finite values."""

        return await self._embed(texts, input_type="document")

    async def embed_query(self, texts: tuple[str, ...], /) -> EmbeddingResult:
        """Embed retrieval queries and validate order, count, dimension, and finite values."""

        return await self._embed(texts, input_type="query")

    async def _embed(self, texts: tuple[str, ...], *, input_type: Literal["document", "query"]) -> EmbeddingResult:
        """Embed one document or query batch through Pydantic AI."""

        if not texts:
            return EmbeddingResult(vectors=())

        try:
            result = await asyncio.wait_for(
                self._embed_batches(texts, input_type=input_type), timeout=self._limits.timeout_seconds
            )
        except asyncio.CancelledError:
            raise
        except InvalidInferenceOutputError:
            raise
        except ValidationError as error:
            # Malformed provider output is an invalid-output failure, not a
            # transient availability failure; match the structured mapping.
            mapped = _map_error(error, operation="embed", timeout_seconds=self._limits.timeout_seconds)
            if mapped is None:
                raise
            raise mapped from error
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

    async def _embed_batches(
        self, texts: tuple[str, ...], *, input_type: Literal["document", "query"]
    ) -> EmbeddingResult:
        vectors: list[tuple[float, ...]] = []
        requests = 0
        input_tokens = 0
        for start in range(0, len(texts), self._batch_size):
            batch = texts[start : start + self._batch_size]
            result = await self._embedder.embed(batch, input_type=input_type)
            vectors.extend(
                self._validated_vectors(batch, result.inputs, result.input_type, result.embeddings, input_type)
            )
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
        expected_input_type: Literal["document", "query"],
    ) -> tuple[tuple[float, ...], ...]:
        if input_type != expected_input_type:
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
    model_settings: ModelSettings | None = None,
) -> None:
    """Send one minimal text request through an assembled generation model."""

    if isinstance(model, str) or not isinstance(model, Model):
        raise PydanticAIConfigurationError("model-instance")
    try:
        await asyncio.wait_for(
            model.request(
                [ModelRequest(parts=[UserPromptPart("Reply with one token.")])],
                merge_model_settings(model_settings, ModelSettings(max_tokens=16)),
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


def _checked_rule(error: BaseException) -> InvalidInferenceOutputError | None:
    cause = error.__cause__
    while cause is not None:
        if isinstance(cause, InvalidInferenceOutputError):
            return cause
        cause = cause.__cause__
    return None


def _map_error(  # noqa: C901 - bounded mapping across status codes and provider exception classes
    error: Exception,
    *,
    operation: str,
    timeout_seconds: float,
) -> InferenceError | None:
    if isinstance(error, InferenceError):
        return error
    stage = stage_for_operation(operation)
    cause_type = type(error).__name__
    if isinstance(error, TimeoutError):
        return attach_diagnostics(
            InferenceTimeoutError(operation, timeout_seconds),
            ProcessingDiagnostics(stage=stage, reason=ProcessingFailureReason.TIMEOUT, cause_exception_type=cause_type),
        )
    if isinstance(error, ModelHTTPError):
        if error.status_code in {408, 504}:
            return attach_diagnostics(
                InferenceTimeoutError(operation, timeout_seconds),
                ProcessingDiagnostics(
                    stage=stage,
                    reason=ProcessingFailureReason.TIMEOUT,
                    cause_exception_type=cause_type,
                ),
            )
        if error.status_code in {409, 425, 429}:
            return attach_diagnostics(
                InferenceUnavailableError(operation),
                ProcessingDiagnostics(
                    stage=stage,
                    reason=(
                        ProcessingFailureReason.RATE_LIMIT
                        if error.status_code == 429
                        else ProcessingFailureReason.PROVIDER_UNAVAILABLE
                    ),
                    cause_exception_type=cause_type,
                ),
            )
        if error.status_code >= 500:
            return attach_diagnostics(
                InferenceUnavailableError(operation),
                ProcessingDiagnostics(
                    stage=stage,
                    reason=ProcessingFailureReason.PROVIDER_UNAVAILABLE,
                    cause_exception_type=cause_type,
                ),
            )
        reason = (
            ProcessingFailureReason.CONTEXT_OVERFLOW
            if _looks_like_context_overflow(error)
            else ProcessingFailureReason.CONFIGURATION
        )
        return attach_diagnostics(
            PydanticAIConfigurationError("provider-rejected", detail=f"HTTP {error.status_code}"),
            ProcessingDiagnostics(
                stage=stage,
                reason=reason,
                failure_location="provider-response",
                cause_exception_type=cause_type,
            ),
        )
    if isinstance(error, (ModelAPIError, ConcurrencyLimitExceeded, OSError)):
        return attach_diagnostics(
            InferenceUnavailableError(operation),
            ProcessingDiagnostics(
                stage=stage,
                reason=ProcessingFailureReason.PROVIDER_UNAVAILABLE,
                cause_exception_type=cause_type,
            ),
        )
    if isinstance(error, UsageLimitExceeded):
        return attach_diagnostics(
            InvalidInferenceOutputError(operation, "provider did not return a valid result"),
            ProcessingDiagnostics(
                stage=stage or ProcessingStage.GENERATION,
                reason=ProcessingFailureReason.USAGE_LIMIT,
                failure_location="inference-limits",
                cause_exception_type=cause_type,
            ),
        )
    if isinstance(error, (UnexpectedModelBehavior, ValidationError)):
        embedding = stage is ProcessingStage.EMBEDDING
        return attach_diagnostics(
            InvalidInferenceOutputError(operation, "provider did not return a valid result"),
            ProcessingDiagnostics(
                stage=ProcessingStage.EMBEDDING if embedding else ProcessingStage.OUTPUT_PARSING,
                reason=(
                    ProcessingFailureReason.INVALID_OUTPUT if embedding else ProcessingFailureReason.SCHEMA_INVALID
                ),
                failure_location="provider-output",
                cause_exception_type=cause_type,
            ),
        )
    if isinstance(error, (PydanticSchemaGenerationError, PydanticUserError, UserError)):
        return attach_diagnostics(
            PydanticAIConfigurationError("pydantic-rejected"),
            ProcessingDiagnostics(
                stage=stage,
                reason=ProcessingFailureReason.CONFIGURATION,
                cause_exception_type=cause_type,
            ),
        )
    return None


def _looks_like_context_overflow(error: ModelHTTPError) -> bool:
    """Classify only when the provider's own response names a context limit."""

    body = getattr(error, "body", None)
    text = str(body if isinstance(body, (str, bytes)) else body or "").lower()
    return any(
        term in text
        for term in (
            "context length",
            "context window",
            "maximum context",
            "too many tokens",
            "prompt is too long",
            "input is too long",
            "token limit",
        )
    )


__all__ = [
    "InferenceLimits",
    "PydanticAIConfigurationError",
    "PydanticAIEmbeddingModel",
    "PydanticAIStructuredGenerator",
    "StreamingAggregateModel",
    "probe_pydantic_ai_model",
]
