"""Scope-bound model usage reporting for PowerContext-owned adapters."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Generic, TypeVar

from powercontext.builtin.inference.models import EmbeddingResult, GenerationResult, InferenceUsage
from powercontext.builtin.inference.protocols import EmbeddingModel, StructuredGenerator
from powercontext.builtin.statistics import ModelUsageOperation, ModelUsagePurpose

InputT = TypeVar("InputT")
OutputT = TypeVar("OutputT")
UsageReporter = Callable[[ModelUsagePurpose, ModelUsageOperation, InferenceUsage], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class _UsageBinding:
    reporter: UsageReporter
    generation_purpose: ModelUsagePurpose | None
    embedding_purpose: ModelUsagePurpose | None

    def purpose_for(self, operation: ModelUsageOperation) -> ModelUsagePurpose | None:
        if operation is ModelUsageOperation.GENERATION:
            return self.generation_purpose
        return self.embedding_purpose


_BINDING: ContextVar[_UsageBinding | None] = ContextVar("powercontext_usage_binding", default=None)


@contextmanager
def bind_usage_reporter(
    reporter: UsageReporter,
    *,
    generation_purpose: ModelUsagePurpose | None = None,
    embedding_purpose: ModelUsagePurpose | None = None,
) -> Iterator[None]:
    """Bind scoped usage attribution for nested inference calls."""

    token: Token[_UsageBinding | None] = _BINDING.set(
        _UsageBinding(
            reporter=reporter,
            generation_purpose=generation_purpose,
            embedding_purpose=embedding_purpose,
        )
    )
    try:
        yield
    finally:
        _BINDING.reset(token)


async def _report(operation: ModelUsageOperation, usage: InferenceUsage) -> None:
    binding = _BINDING.get()
    if binding is None:
        return
    purpose = binding.purpose_for(operation)
    if purpose is not None:
        await binding.reporter(purpose, operation, usage)


class UsageReportingStructuredGenerator(Generic[InputT, OutputT]):
    """Report successful structured-generation usage to the current scope."""

    def __init__(self, delegate: StructuredGenerator[InputT, OutputT]) -> None:
        self._delegate = delegate

    async def generate(self, value: InputT, /) -> GenerationResult[OutputT]:
        result = await self._delegate.generate(value)
        await _report(ModelUsageOperation.GENERATION, result.usage)
        return result


class UsageReportingEmbeddingModel:
    """Report successful embedding usage to the current scope."""

    def __init__(self, delegate: EmbeddingModel) -> None:
        self._delegate = delegate
        self.profile = delegate.profile

    async def embed(self, texts: tuple[str, ...], /) -> EmbeddingResult:
        result = await self._delegate.embed(texts)
        await _report(ModelUsageOperation.EMBEDDING, result.usage)
        return result


__all__ = ["UsageReportingEmbeddingModel", "UsageReportingStructuredGenerator", "bind_usage_reporter"]
