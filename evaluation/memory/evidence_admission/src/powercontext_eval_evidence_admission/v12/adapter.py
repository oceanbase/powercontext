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

"""Actual SDK transport audit around the real Atomic Memory extraction Pipeline."""

from __future__ import annotations

import asyncio
from typing import Any, cast

import httpx
from openai import AsyncOpenAI
from powercontext.builtin.artifacts.atomic_memory.extraction import AtomicMemoryExtractionOutput
from powercontext.builtin.inference.pydantic_ai import InferenceLimits, PydanticAIStructuredGenerator
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.profiles.openai import OpenAIModelProfile
from pydantic_ai.providers.openai import OpenAIProvider

from ..manifests import EvaluationError
from ..models import digest
from ..v6.audit import _CURRENT, ResponseCaptureModel
from ..v9.codec import TemplateOutput
from ..v9_preflight.adapter import _EXPECTED, ExpectedRequest, Transport, failure
from ..v10.protocol import INSTRUCTIONS, pipeline


class RecordingGenerator:
    def __init__(self, generator):
        self.generator = generator
        self.template = None

    async def generate(self, value, /):
        result = await self.generator.generate(value)
        self.template = result.output
        return result


async def extract(request, config, journal, transport, *, api_key, sample_id):
    attempt = journal.begin(digest({"sample_id": sample_id, "protocol": "v12", "call_index": 0}), request.binding_hash)
    attempt.output_type = TemplateOutput
    attempt.facts.update(protocol="v12", pipeline_executed=False, quality_review="not_run")
    current_token = _CURRENT.set(attempt)
    expected_token = _EXPECTED.set(ExpectedRequest(request.encoded.payload, INSTRUCTIONS, TemplateOutput, config))
    try:
        async with (
            httpx.AsyncClient(
                transport=Transport(transport), follow_redirects=False, timeout=config.timeout_seconds
            ) as client,
            AsyncOpenAI(api_key=api_key, base_url=config.base_url, http_client=cast(Any, client), max_retries=0) as sdk,
        ):
            model = ResponseCaptureModel(
                OpenAIChatModel(
                    config.model,
                    provider=OpenAIProvider(openai_client=sdk),
                    profile=OpenAIModelProfile(openai_chat_supports_max_completion_tokens=False),
                )
            )
            generator = RecordingGenerator(
                PydanticAIStructuredGenerator(
                    model=model,
                    instructions=INSTRUCTIONS,
                    input_type=dict,
                    output_type=TemplateOutput,
                    limits=InferenceLimits(
                        max_requests=1,
                        max_output_tokens_per_request=2048,
                        output_tokens_limit=2048,
                        timeout_seconds=config.timeout_seconds,
                        allow_continuations=False,
                    ),
                    model_settings={"temperature": 0},
                )
            )
            candidates = await pipeline(request, generator).extract(request.encoded.value)
        attempt.facts["pipeline_executed"] = True
        if attempt.facts.get("response_capture_complete") is not True:
            raise EvaluationError("response_capture_incomplete")
        if attempt.facts.get("output_tokens", 0) is not None and attempt.facts["output_tokens"] > 2048:
            raise EvaluationError("output_usage_limit_exceeded")
        template = TemplateOutput.model_validate(generator.template)
        attempt.facts["copy_parts"] = sum(p.type == "copy" for c in template.candidates for p in c.parts)
        attempt.facts["structured_output_validated"] = True
        attempt.facts["protocol_output_validated"] = True
        output = AtomicMemoryExtractionOutput(candidates=candidates)
    except asyncio.CancelledError as error:
        attempt.facts["failure_stage"], category = failure(error, attempt.facts)
        try:
            attempt.finish("cancelled", category)
        finally:
            raise error
    except Exception as error:
        attempt.facts["failure_stage"], category = failure(error, attempt.facts)
        attempt.finish("failed", category)
        raise
    else:
        attempt.finish("success", None)
        return output
    finally:
        _EXPECTED.reset(expected_token)
        _CURRENT.reset(current_token)
