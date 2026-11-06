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

"""Native SDK extraction with raw-response capture before parsing, one HTTP attempt only."""

from __future__ import annotations

import json
from time import perf_counter
from typing import Any, cast

import httpx
from openai import AsyncOpenAI
from powercontext.builtin.artifacts.atomic_memory.extraction import (
    AtomicMemoryExtractionInput,
    AtomicMemoryExtractionOutput,
)
from powercontext.builtin.inference.pydantic_ai import InferenceLimits, PydanticAIStructuredGenerator
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.profiles.openai import OpenAIModelProfile
from pydantic_ai.providers.openai import OpenAIProvider

from ..manifests import EvaluationError
from ..models import digest
from ..runner import INSTRUCTIONS, pipeline_for
from ..v9_preflight.adapter import Config, request_facts
from .core import contract


class Transport(httpx.AsyncBaseTransport):
    def __init__(self, wrapped, value, record, save, expected_hash):
        self.wrapped, self.value, self.record, self.save = wrapped, value, record, save
        self.expected_hash, self.sent = expected_hash, False

    async def handle_async_request(self, request):
        policy = contract()
        if self.sent or request.method != "POST" or str(request.url) != policy["base_url"] + "/chat/completions":
            raise EvaluationError("v18_endpoint_or_request_limit_changed")
        body = json.loads(await request.aread())
        if body.get("thinking") != {"type": "disabled"}:
            raise EvaluationError("v18_thinking_changed")
        facts = request_facts(
            {k: v for k, v in body.items() if k != "thinking"},
            self.value.model_dump(mode="json"),
            INSTRUCTIONS,
            AtomicMemoryExtractionOutput,
            Config(policy["model"], policy["base_url"]),
        )
        facts["http_request_hash"] = digest(body)
        if self.expected_hash is not None and self.expected_hash != digest(body):
            raise EvaluationError("v18_preflight_body_changed")
        self.save("request", body)
        self.record.update(request_facts=facts, provider_request_started=True)
        self.save("attempt", self.record)
        self.sent = True
        response = await self.wrapped.handle_async_request(request)
        raw = await response.aread()
        self.save("response-bytes", raw)
        self.record.update(http_status=response.status_code, response_hash=digest(raw.hex()))
        try:
            data = json.loads(raw)
        except (ValueError, UnicodeError):
            data = {}
        if isinstance(data, dict):
            self.record["provider_usage"] = data.get("usage")
            model = data.get("model")
            self.record["response_model"] = model if model in policy["allowed_response_models"] else "unrecognized"
            self.record["fingerprint_hash"] = digest(data.get("system_fingerprint"))
            choices = data.get("choices")
            if isinstance(choices, list) and choices and isinstance(choices[0], dict):
                reason = choices[0].get("finish_reason")
                self.record["finish_reason"] = reason if reason in {"stop", "length", "tool_calls"} else "other"
        self.save("attempt", self.record)
        return httpx.Response(response.status_code, headers={"content-type": "application/json"}, content=raw)

    async def aclose(self):
        await self.wrapped.aclose()


async def extract(value, *, api_key, transport, record, save, expected_hash=None):
    policy = contract()
    started = perf_counter()
    async with (
        httpx.AsyncClient(
            transport=Transport(transport, value, record, save, expected_hash),
            timeout=policy["timeout_seconds"],
            follow_redirects=False,
        ) as client,
        AsyncOpenAI(api_key=api_key, base_url=policy["base_url"], max_retries=0, http_client=cast(Any, client)) as sdk,
    ):
        generator = PydanticAIStructuredGenerator(
            model=OpenAIChatModel(
                policy["model"],
                provider=OpenAIProvider(openai_client=sdk),
                profile=OpenAIModelProfile(openai_chat_supports_max_completion_tokens=False),
            ),
            instructions=INSTRUCTIONS,
            input_type=AtomicMemoryExtractionInput,
            output_type=AtomicMemoryExtractionOutput,
            limits=InferenceLimits(
                max_requests=1,
                max_output_tokens_per_request=policy["max_output_tokens"],
                output_tokens_limit=policy["max_output_tokens"],
                timeout_seconds=policy["timeout_seconds"],
                allow_continuations=False,
            ),
            model_settings={"temperature": 0, "extra_body": {"thinking": {"type": "disabled"}}},
        )

        class Captured:
            async def generate(self, value, /):
                generated = await generator.generate(value)
                # Capture schema-valid output even when Pipeline citation/text checks reject it.
                save("parsed", generated.output.model_dump(mode="json"))
                return generated

        record["pipeline_entered"] = True
        save("attempt", record)
        candidates = await pipeline_for(Captured()).extract(value)
        output = AtomicMemoryExtractionOutput(candidates=candidates)
        save("output", output.model_dump(mode="json"))
        record.update(
            pipeline_returned=True,
            output_hash=digest(output.model_dump(mode="json")),
            elapsed_seconds=perf_counter() - started,
        )
        save("attempt", record)
        return output
