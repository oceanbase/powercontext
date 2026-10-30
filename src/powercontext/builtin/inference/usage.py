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

"""Scope-bound model usage reporting for PowerContext-owned adapters."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager, suppress
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Generic, TypeVar

from powercontext.builtin.inference.errors import InferenceError
from powercontext.builtin.inference.models import (
    MAX_INPUT_ENCODINGS,
    EmbeddingResult,
    GenerationResult,
    InferenceUsage,
    InputEncodingReport,
    combine_inference_usage,
)
from powercontext.builtin.inference.protocols import EmbeddingModel, StructuredGenerator, embed_query
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

_APPROVED_USAGE: ContextVar[list[InferenceUsage] | None] = ContextVar("protected_approved_usage", default=None)
_INPUT_ENCODINGS: ContextVar[list[InputEncodingReport] | None] = ContextVar("inference_input_encodings", default=None)
_CALL_INPUT_ENCODINGS: ContextVar[list[InputEncodingReport] | None] = ContextVar(
    "inference_call_input_encodings", default=None
)


@contextmanager
def collect_approved_usage() -> Iterator[list[InferenceUsage]]:
    """Keep exact optional provider counts for one adapter operation, task-locally."""

    values: list[InferenceUsage] = []
    token = _APPROVED_USAGE.set(values)
    try:
        yield values
    finally:
        _APPROVED_USAGE.reset(token)


def record_approved_usage(usage: InferenceUsage) -> None:
    """Record one approved provider response, without synthesizing missing counts."""

    values = _APPROVED_USAGE.get()
    if values is not None:
        values.append(usage)


def approved_usage(values: list[InferenceUsage]) -> InferenceUsage:
    result = InferenceUsage(requests=0)
    for value in values:
        result = combine_inference_usage(result, value)
    return result


@contextmanager
def collect_input_encodings() -> Iterator[list[InputEncodingReport]]:
    """Observe at most 128 actual preparations without logging or persistence.

    Nested observers are independent. Child tasks inherit an observer unless
    they establish their own. The usage wrapper also associates preparations
    with its individual call, independently of this explicit diagnostic scope.
    Encoding and budget probes never record automatically.
    """

    reports: list[InputEncodingReport] = []
    token = _INPUT_ENCODINGS.set(reports)
    try:
        yield reports
    finally:
        _INPUT_ENCODINGS.reset(token)


def record_input_encoding(report: InputEncodingReport) -> None:
    """Record one actual adapter preparation in the observer and owning call."""

    for reports in (_INPUT_ENCODINGS.get(), _CALL_INPUT_ENCODINGS.get()):
        if reports is not None and len(reports) < MAX_INPUT_ENCODINGS:
            reports.append(report)


def _with_input_encodings(usage: InferenceUsage, reports: list[InputEncodingReport]) -> InferenceUsage:
    if not reports:
        return usage
    return usage.model_copy(update={"input_encodings": (usage.input_encodings + tuple(reports))[:MAX_INPUT_ENCODINGS]})


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
    """Report scoped generation usage, including known usage on failed attempts."""

    def __init__(self, delegate: StructuredGenerator[InputT, OutputT]) -> None:
        # Reapplying this instrumentation must not report the same call twice.
        # A distinct nested model call still establishes its own attribution.
        self._delegate = delegate._delegate if type(delegate) is UsageReportingStructuredGenerator else delegate

    async def generate(self, value: InputT, /) -> GenerationResult[OutputT]:
        reports: list[InputEncodingReport] = []
        token = _CALL_INPUT_ENCODINGS.set(reports)
        try:
            try:
                result = await self._delegate.generate(value)
            except InferenceError as error:
                # No usage means unknown, even for a before-send rejection after
                # an earlier attempt. Never fabricate a zero-request event.
                if error.usage is not None:
                    error.usage = _with_input_encodings(error.usage, reports)
                    with suppress(Exception):
                        await _report(ModelUsageOperation.GENERATION, error.usage)
                raise
            usage = _with_input_encodings(result.usage, reports)
            await _report(ModelUsageOperation.GENERATION, usage)
            return result.model_copy(update={"usage": usage}) if reports else result
        finally:
            _CALL_INPUT_ENCODINGS.reset(token)


class UsageReportingEmbeddingModel:
    """Report scoped embedding usage, including known usage on failed attempts."""

    def __init__(self, delegate: EmbeddingModel) -> None:
        self._delegate = delegate
        self.profile = delegate.profile

    async def embed(self, texts: tuple[str, ...], /) -> EmbeddingResult:
        try:
            result = await self._delegate.embed(texts)
        except InferenceError as error:
            if error.usage is not None:
                with suppress(Exception):
                    await _report(ModelUsageOperation.EMBEDDING, error.usage)
            raise
        await _report(ModelUsageOperation.EMBEDDING, result.usage)
        return result

    async def embed_query(self, texts: tuple[str, ...], /) -> EmbeddingResult:
        try:
            result = await embed_query(self._delegate, texts)
        except InferenceError as error:
            if error.usage is not None:
                with suppress(Exception):
                    await _report(ModelUsageOperation.EMBEDDING, error.usage)
            raise
        await _report(ModelUsageOperation.EMBEDDING, result.usage)
        return result


__all__ = [
    "UsageReportingEmbeddingModel",
    "UsageReportingStructuredGenerator",
    "approved_usage",
    "bind_usage_reporter",
    "collect_approved_usage",
    "collect_input_encodings",
    "record_approved_usage",
    "record_input_encoding",
]
