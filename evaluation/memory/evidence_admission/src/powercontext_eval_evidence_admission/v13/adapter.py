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
import json
from time import perf_counter
from typing import Any, cast

import httpx
from openai import AsyncOpenAI
from powercontext.builtin.artifacts.atomic_memory.extraction import AtomicMemoryExtractionOutput
from powercontext.builtin.inference.pydantic_ai import InferenceLimits, PydanticAIStructuredGenerator
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.profiles.openai import OpenAIModelProfile
from pydantic_ai.providers.openai import OpenAIProvider

from ..manifests import EvaluationError
from ..models import canonical, digest
from ..v6.audit import _CURRENT, AuditTransport, ResponseCaptureModel, _write_new
from ..v9.codec import TemplateOutput
from ..v9_live.runner import ResumableJournal as PreviousJournal
from ..v9_preflight.adapter import _EXPECTED, ExpectedRequest, request_facts
from ..v9_preflight.adapter import failure as inherited_failure
from ..v10.protocol import INSTRUCTIONS, pipeline


def failure(error, facts):
    if isinstance(error, EvaluationError) and str(error) == "v13_thinking_response_mismatch":
        return "response", "thinking_response_mismatch"
    return inherited_failure(error, facts)


class RecordingGenerator:
    def __init__(self, generator):
        self.generator = generator
        self.template = None

    async def generate(self, value, /):
        result = await self.generator.generate(value)
        self.template = result.output
        attempt = _CURRENT.get()
        if attempt is not None:
            attempt.facts["structured_output_validated"] = True
        return result


class ResumableJournal(PreviousJournal):
    def response(self, attempt, body, status):
        super().response(attempt, body, status)
        attempt.facts["provider_response_received"] = True
        try:
            response = json.loads(body)
        except (ValueError, UnicodeError):
            return
        if not isinstance(response, dict):
            return
        usage = response.get("usage")
        if isinstance(usage, dict):
            details = usage.get("completion_tokens_details")
            if isinstance(details, dict) and type(details.get("reasoning_tokens")) is int:
                attempt.facts["reasoning_tokens"] = details["reasoning_tokens"]
        choices = response.get("choices")
        if isinstance(choices, list) and choices and isinstance(choices[0], dict):
            message = choices[0].get("message")
            if isinstance(message, dict) and isinstance(message.get("content"), str):
                attempt.facts["final_content_characters"] = len(message["content"])
        attempt.event(
            "response_state",
            **{
                key: attempt.facts.get(key)
                for key in ("provider_response_received", "reasoning_tokens", "final_content_characters")
            },
        )


class Transport(AuditTransport):
    def __init__(self, wrapped, expected_http_request_hash=None):
        super().__init__(wrapped)
        self.expected_http_request_hash = expected_http_request_hash

    async def handle_async_request(self, request):
        attempt = _CURRENT.get()
        expected = _EXPECTED.get()
        if attempt is None or expected is None:
            raise EvaluationError("missing_expected_request")
        if attempt.facts.get("http_requests", 0):
            raise EvaluationError("preflight_request_limit_exceeded")
        if request.method != "POST" or str(request.url) != expected.config.base_url.rstrip("/") + "/chat/completions":
            raise EvaluationError("sdk_endpoint_changed")
        body = json.loads(await request.aread())
        if body.get("thinking") != {"type": "disabled"}:
            raise EvaluationError("sdk_thinking_disabled_required")
        facts = request_facts(
            {k: v for k, v in body.items() if k != "thinking"},
            expected.payload,
            expected.instructions,
            expected.output_type,
            expected.config,
        )
        facts.update(http_request_hash=digest(body), http_request_bytes=len(canonical(body)))
        from powercontext.builtin.inference import character_token_estimator

        facts["http_json_estimated_tokens"] = character_token_estimator().estimate(canonical(body).decode())
        if (
            self.expected_http_request_hash is not None
            and facts["http_request_hash"] != self.expected_http_request_hash
        ):
            raise EvaluationError("sdk_preflight_request_hash_changed")
        facts["thinking_disabled_verified"] = True
        attempt.facts.update(http_requests=1, provider_request_started=True, **facts)
        _write_new(attempt.journal.directory / f"{attempt.attempt_id}.request.json", canonical(body))
        attempt.event("http_request", **facts)
        return await super().handle_async_request(request)


async def extract(request, config, journal, transport, *, api_key, sample_id, expected_http_request_hash=None):
    attempt = journal.begin(digest({"sample_id": sample_id, "protocol": "v13", "call_index": 0}), request.binding_hash)
    attempt.output_type = TemplateOutput
    attempt.facts.update(
        protocol="v13",
        pipeline_entered=False,
        pipeline_returned_successfully=False,
        provider_request_started=False,
        provider_response_received=False,
        structured_output_validated=False,
        protocol_output_validated=False,
        quality_review="not_run",
    )
    started = perf_counter()
    current_token = _CURRENT.set(attempt)
    expected_token = _EXPECTED.set(ExpectedRequest(request.encoded.payload, INSTRUCTIONS, TemplateOutput, config))
    try:
        async with (
            httpx.AsyncClient(
                transport=Transport(transport, expected_http_request_hash),
                follow_redirects=False,
                timeout=config.timeout_seconds,
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
                    model_settings={"temperature": 0, "extra_body": {"thinking": {"type": "disabled"}}},
                )
            )
            attempt.facts["pipeline_entered"] = True
            attempt.event("pipeline_entered", pipeline_entered=True)
            candidates = await pipeline(request, generator).extract(request.encoded.value)
            attempt.facts["pipeline_returned_successfully"] = True
            attempt.event("pipeline_returned", pipeline_returned_successfully=True)
        if attempt.facts.get("response_capture_complete") is not True:
            raise EvaluationError("response_capture_incomplete")
        if attempt.facts.get("output_tokens", 0) is not None and attempt.facts["output_tokens"] > 2048:
            raise EvaluationError("output_usage_limit_exceeded")
        if attempt.facts.get("reasoning_tokens") not in (None, 0):
            raise EvaluationError("v13_thinking_response_mismatch")
        template = TemplateOutput.model_validate(generator.template)
        attempt.facts["copy_parts"] = sum(p.type == "copy" for c in template.candidates for p in c.parts)
        attempt.facts["structured_output_validated"] = True
        attempt.facts["protocol_output_validated"] = True
        output = AtomicMemoryExtractionOutput(candidates=candidates)
    except asyncio.CancelledError as error:
        attempt.facts["failure_stage"], category = failure(error, attempt.facts)
        try:
            attempt.facts["generation_elapsed_seconds"] = perf_counter() - started
            attempt.finish("cancelled", category)
        finally:
            raise error
    except Exception as error:
        attempt.facts["failure_stage"], category = failure(error, attempt.facts)
        attempt.facts["generation_elapsed_seconds"] = perf_counter() - started
        attempt.finish("failed", category)
        raise
    else:
        attempt.facts["generation_elapsed_seconds"] = perf_counter() - started
        attempt.finish("success", None)
        return output
    finally:
        _EXPECTED.reset(expected_token)
        _CURRENT.reset(current_token)
