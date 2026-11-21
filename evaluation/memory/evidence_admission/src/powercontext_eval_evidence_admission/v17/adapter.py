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

"""One audited SDK request through the actual Pipeline; raw bodies stay private."""

from __future__ import annotations

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
from ..models import digest
from ..v9_preflight.adapter import Config, request_facts
from ..v16.codec import INSTRUCTIONS, TemplateOutput
from ..v16.live import Extractor, pipeline
from .freeze import contract


class Transport(httpx.AsyncBaseTransport):
    def __init__(self, wrapped, request, record, save, expected_hash=None):
        self.wrapped, self.request, self.record, self.save = wrapped, request, record, save
        self.expected_hash = expected_hash
        self.sent = False

    async def handle_async_request(self, request):
        policy = contract()
        if self.sent or request.method != "POST" or str(request.url) != policy["base_url"] + "/chat/completions":
            raise EvaluationError("v17_request_count_or_endpoint_changed")
        body = json.loads(await request.aread())
        if body.get("thinking") != policy["thinking"]:
            raise EvaluationError("v17_thinking_setting_changed")
        facts = request_facts(
            {k: v for k, v in body.items() if k != "thinking"},
            self.request.payload,
            INSTRUCTIONS,
            TemplateOutput,
            Config(policy["model"], policy["base_url"]),
        )
        facts["http_request_hash"] = digest(body)
        if facts["model_visible_text_estimated_tokens"] > policy["budget"]:
            raise EvaluationError("v17_actual_sdk_text_budget_exceeded")
        if self.expected_hash is not None and facts["http_request_hash"] != self.expected_hash:
            raise EvaluationError("v17_preflight_request_hash_changed")
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
            self.record["response_category"] = "invalid_json"
        else:
            if isinstance(data, dict):
                self.record["provider_usage"] = data.get("usage")
                model = data.get("model")
                self.record["response_model"] = (
                    model if model in {"deepseek-flash", "deepseek-v4-flash"} else "unrecognized"
                )
                self.record["fingerprint_hash"] = digest(data.get("system_fingerprint"))
                choices = data.get("choices")
                if isinstance(choices, list) and choices and isinstance(choices[0], dict):
                    reason = choices[0].get("finish_reason")
                    self.record["finish_reason"] = (
                        reason
                        if reason
                        in {"stop", "length", "tool_calls", "content_filter", "insufficient_system_resource", "aborted"}
                        else "unknown"
                    )
                    message = choices[0].get("message")
                    self.record["reasoning_content_present"] = (
                        bool(message.get("reasoning_content")) if isinstance(message, dict) else False
                    )
        self.save("attempt", self.record)
        return httpx.Response(response.status_code, headers={"content-type": "application/json"}, content=raw)

    async def aclose(self):
        await self.wrapped.aclose()


async def extract(request, *, api_key, transport, record, save, expected_hash=None):
    policy = contract()
    started = perf_counter()
    async with (
        httpx.AsyncClient(
            transport=Transport(transport, request, record, save, expected_hash),
            timeout=policy["timeout_seconds"],
            follow_redirects=False,
        ) as client,
        AsyncOpenAI(api_key=api_key, base_url=policy["base_url"], max_retries=0, http_client=cast(Any, client)) as sdk,
    ):
        model = OpenAIChatModel(
            policy["model"],
            provider=OpenAIProvider(openai_client=sdk),
            profile=OpenAIModelProfile(openai_chat_supports_max_completion_tokens=False),
        )
        generator = PydanticAIStructuredGenerator(
            model=model,
            instructions=INSTRUCTIONS,
            input_type=dict,
            output_type=TemplateOutput,
            limits=InferenceLimits(
                max_requests=1,
                max_output_tokens_per_request=policy["max_output_tokens"],
                output_tokens_limit=policy["max_output_tokens"],
                timeout_seconds=policy["timeout_seconds"],
                allow_continuations=False,
            ),
            model_settings={"temperature": 0, "extra_body": {"thinking": policy["thinking"]}},
        )
        extractor = Extractor(request, generator)
        record["pipeline_entered"] = True
        save("attempt", record)
        candidates = await pipeline(extractor).extract(request.value)
        record.update(pipeline_returned=True, elapsed_seconds=perf_counter() - started)
        output = AtomicMemoryExtractionOutput(candidates=candidates)
        template = extractor.raw_template
        if template is None:
            raise EvaluationError("v17_template_not_captured")
        save("template", template.model_dump(mode="json"))
        save("output", output.model_dump(mode="json"))
        record.update(
            output_hash=digest(output.model_dump(mode="json")), template_hash=digest(template.model_dump(mode="json"))
        )
        save("attempt", record)
        return output
