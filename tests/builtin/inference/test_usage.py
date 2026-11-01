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
from dataclasses import replace

import pytest
from pydantic import ValidationError

from powercontext.builtin.inference.errors import InferencePrivacyError, InferenceUnavailableError
from powercontext.builtin.inference.models import (
    GenerationResult,
    InferenceUsage,
    InputEncodingReport,
    combine_inference_usage,
)
from powercontext.builtin.inference.usage import (
    UsageReportingStructuredGenerator,
    approved_usage,
    bind_usage_reporter,
    collect_approved_usage,
    collect_input_encodings,
    record_approved_usage,
    record_input_encoding,
)
from powercontext.builtin.statistics import ModelUsageOperation, ModelUsagePurpose

_REPORT = InputEncodingReport(
    mode="active",
    stage="atomic_memory.extract",
    codec="shared-json-v1",
    reason="applied",
    original_bytes=2400,
    original_estimated_tokens=600,
    candidate_bytes=1600,
    candidate_estimated_tokens=400,
    selected_bytes=1600,
    selected_estimated_tokens=400,
    estimator_id="test",
    estimator_version="1",
    replacements=2,
    shared_values=1,
)
_PURPOSE = ModelUsagePurpose.MEMORY_EXTRACTION


class _Generator:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error

    async def generate(self, value: int) -> GenerationResult[int]:
        record_input_encoding(replace(_REPORT, original_bytes=value))
        await asyncio.sleep(0)
        if self.error is not None:
            raise self.error
        return GenerationResult(output=value, usage=InferenceUsage(requests=2, input_tokens=137, output_tokens=None))


def test_call_associates_estimates_with_actual_counts_and_keeps_explicit_observer() -> None:
    async def scenario() -> None:
        observed = []

        async def report(purpose, operation, usage):
            observed.append((purpose, operation, usage))

        with collect_input_encodings() as encodings, bind_usage_reporter(report, generation_purpose=_PURPOSE):
            result = await UsageReportingStructuredGenerator(_Generator()).generate(2400)
        assert observed == [(_PURPOSE, ModelUsageOperation.GENERATION, result.usage)]
        assert result.usage.requests == 2
        assert result.usage.input_tokens == 137
        assert result.usage.output_tokens is None
        assert result.usage.input_encodings == tuple(encodings) == (_REPORT,)
        assert InferenceUsage.model_validate_json(result.usage.model_dump_json()) == result.usage

    asyncio.run(scenario())


@pytest.mark.parametrize("requests", [0, 1])
def test_failure_with_known_usage_keeps_diagnostics_and_original_error(requests: int) -> None:
    async def scenario() -> None:
        original = InferencePrivacyError(
            "before_send" if requests == 0 else "before_release",
            "block",
            ("forbidden_content",),
            usage=InferenceUsage(requests=requests, input_tokens=80 if requests else None),
        )
        observed = []

        async def report(_purpose, _operation, usage):
            observed.append(usage)
            raise RuntimeError("reporting failed")  # noqa: TRY003

        with bind_usage_reporter(report, generation_purpose=_PURPOSE), pytest.raises(InferencePrivacyError) as raised:
            await UsageReportingStructuredGenerator(_Generator(error=original)).generate(2400)
        assert raised.value is original
        assert original.usage is not None
        assert observed == [original.usage]
        assert original.usage.requests == requests
        assert original.usage.input_tokens == (80 if requests else None)
        assert original.usage.output_tokens is None
        assert original.usage.input_encodings == (_REPORT,)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "original",
    [InferencePrivacyError("before_send", "block", ("forbidden_content",)), InferenceUnavailableError("generate")],
)
def test_unknown_failed_usage_does_not_fabricate_zero_requests(original) -> None:
    async def scenario() -> None:
        observed = []

        async def report(_purpose, _operation, usage):
            observed.append(usage)

        with (
            collect_input_encodings() as encodings,
            bind_usage_reporter(report, generation_purpose=_PURPOSE),
            pytest.raises(type(original)) as raised,
        ):
            await UsageReportingStructuredGenerator(_Generator(error=original)).generate(2400)
        assert raised.value is original
        assert original.usage is None
        assert observed == []
        assert encodings == [_REPORT]

    asyncio.run(scenario())


def test_concurrent_calls_keep_scope_binding_and_input_estimates_separate() -> None:
    async def scenario() -> None:
        generator = UsageReportingStructuredGenerator(_Generator())
        observed = {}

        async def run(scope: str, value: int) -> None:
            async def report(_purpose, _operation, usage):
                observed[scope] = usage

            with bind_usage_reporter(report, generation_purpose=_PURPOSE):
                result = await generator.generate(value)
            assert result.usage.input_encodings == (replace(_REPORT, original_bytes=value),)

        with collect_input_encodings() as encodings:
            await asyncio.gather(run("first", 2400), run("second", 4800))
        assert len(encodings) == 2
        assert observed["first"].input_encodings[0].original_bytes == 2400
        assert observed["second"].input_encodings[0].original_bytes == 4800
        assert observed["first"].requests == observed["second"].requests == 2

    asyncio.run(scenario())


def test_wrapping_instrumented_generator_again_does_not_double_count_usage() -> None:
    async def scenario() -> None:
        observed = []

        async def report(_purpose, _operation, usage):
            observed.append(usage)

        once = UsageReportingStructuredGenerator(_Generator())
        twice = UsageReportingStructuredGenerator(once)
        with collect_input_encodings() as encodings, bind_usage_reporter(report, generation_purpose=_PURPOSE):
            result = await twice.generate(2400)
        assert observed == [result.usage]
        assert result.usage.requests == 2
        assert result.usage.input_encodings == tuple(encodings) == (_REPORT,)

    asyncio.run(scenario())


def test_nested_calls_do_not_reattribute_inner_preparation_to_outer_usage() -> None:
    class Outer:
        async def generate(self, value):
            record_input_encoding(replace(_REPORT, original_bytes=1200))
            await UsageReportingStructuredGenerator(_Generator()).generate(value)
            return GenerationResult(output=value, usage=InferenceUsage(requests=1, input_tokens=50))

    async def scenario() -> None:
        observed = []

        async def report(_purpose, _operation, usage):
            observed.append(usage)

        with collect_input_encodings() as encodings, bind_usage_reporter(report, generation_purpose=_PURPOSE):
            result = await UsageReportingStructuredGenerator(Outer()).generate(2400)
        assert [usage.requests for usage in observed] == [2, 1]
        assert observed[0].input_encodings == (_REPORT,)
        assert result.usage.input_encodings == (replace(_REPORT, original_bytes=1200),)
        assert len(encodings) == 2

    asyncio.run(scenario())


def test_cancellation_resets_call_collection_without_reporting_unknown_usage() -> None:
    async def scenario() -> None:
        started = asyncio.Event()
        observed = []

        class Waiting:
            async def generate(self, value):
                record_input_encoding(replace(_REPORT, original_bytes=value))
                started.set()
                await asyncio.Event().wait()
                raise AssertionError("unreachable")

        async def report(_purpose, _operation, usage):
            observed.append(usage)

        with collect_input_encodings() as encodings, bind_usage_reporter(report, generation_purpose=_PURPOSE):
            task = asyncio.create_task(UsageReportingStructuredGenerator(Waiting()).generate(1200))
            await started.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            result = await UsageReportingStructuredGenerator(_Generator()).generate(2400)
        assert observed == [result.usage]
        assert result.usage.input_encodings == (_REPORT,)
        assert [report.original_bytes for report in encodings] == [1200, 2400]

    asyncio.run(scenario())


def test_combining_attempts_bounds_diagnostics_without_changing_unknown_counts() -> None:
    left = InferenceUsage(requests=2, input_tokens=137, output_tokens=None, input_encodings=(_REPORT,) * 100)
    right = InferenceUsage(requests=1, input_tokens=50, output_tokens=12, input_encodings=(_REPORT,) * 100)
    combined = combine_inference_usage(left, right)
    assert combined.requests == 3
    assert combined.input_tokens == 187
    assert combined.output_tokens is None
    assert len(combined.input_encodings) == 128
    with pytest.raises(ValidationError):
        InferenceUsage(requests=1, input_encodings=(_REPORT,) * 129)
    zero = InferenceUsage(requests=0, input_encodings=(_REPORT,), incomplete=True)
    assert combine_inference_usage(zero, right).input_encodings == (_REPORT,) * 101
    assert combine_inference_usage(left, zero).input_encodings == (_REPORT,) * 101
    assert combine_inference_usage(left, zero).incomplete


def test_provider_collections_keep_nested_attempts_isolated_and_unknown_tokens_unknown() -> None:
    first = InferenceUsage(requests=1, input_tokens=10, output_tokens=20)
    second = InferenceUsage(requests=1, input_tokens=None, output_tokens=30, incomplete=True)
    with collect_approved_usage() as outer:
        record_approved_usage(first)
        with collect_approved_usage() as inner:
            record_approved_usage(second)
        record_approved_usage(first)
    assert approved_usage(inner) == second
    assert approved_usage(outer).requests == 2
    assert approved_usage(outer).input_tokens == 20
    assert approved_usage([first, second]).input_tokens is None
    assert approved_usage([first, second]).output_tokens == 50
