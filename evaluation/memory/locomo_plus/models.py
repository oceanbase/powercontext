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

"""Bounded benchmark model calls honoring the Server's endpoint configuration."""

from __future__ import annotations

import asyncio
import os
from contextlib import AsyncExitStack
from typing import Any, cast

from pydantic_ai import Agent
from pydantic_ai.models import Model, infer_model
from pydantic_ai.providers import Provider, infer_provider
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import UsageLimits

from powercontext.builtin.runtime import InferenceConfig


async def open_model(name: str, settings: InferenceConfig, resources: AsyncExitStack) -> Model:
    """Use public provider APIs; credentials never enter saved model inputs."""

    def provider_factory(provider_name: str) -> Provider[Any]:
        headers = {key: value.get_secret_value() for key, value in settings.generation_headers.items()}
        base_url = None if settings.generation_base_url is None else str(settings.generation_base_url)
        if provider_name in {"openai", "openai-chat", "openai-responses"}:
            from openai import AsyncOpenAI
            from pydantic_ai.providers.openai import OpenAIProvider

            client = AsyncOpenAI(
                base_url=base_url,
                api_key=os.getenv("OPENAI_API_KEY") or "api-key-not-set",
                default_headers=headers,
                max_retries=0,
            )
            resources.push_async_callback(client.close)
            return OpenAIProvider(openai_client=client)
        if provider_name == "anthropic":
            from anthropic import AsyncAnthropic
            from pydantic_ai.providers.anthropic import AnthropicProvider

            key = next((headers.pop(key) for key in tuple(headers) if key.lower() == "x-api-key"), None)
            client = AsyncAnthropic(
                base_url=base_url,
                api_key=key or os.getenv("ANTHROPIC_API_KEY") or "api-key-not-set",
                default_headers=headers,
                max_retries=0,
            )
            resources.push_async_callback(client.close)
            return AnthropicProvider(anthropic_client=client)
        if base_url or headers:
            raise ValueError("endpoint overrides require an OpenAI or Anthropic compatible provider")  # noqa: TRY003
        return infer_provider(provider_name)

    return await resources.enter_async_context(infer_model(name, provider_factory=provider_factory))


async def generate(
    model: Model,
    *,
    prompt: str,
    instructions: str,
    settings: InferenceConfig,
    max_tokens: int,
) -> tuple[str, dict[str, Any]]:
    """Make one bounded request, retaining provider usage even for plain-text output."""

    model_settings = cast(
        ModelSettings,
        {**settings.generation_model_settings, "temperature": 0.0, "max_tokens": max_tokens},
    )
    agent = Agent(model, output_type=str, instructions=instructions, retries=0, model_settings=model_settings)
    result = await asyncio.wait_for(
        agent.run(prompt, usage_limits=UsageLimits(request_limit=1)),
        timeout=settings.generation_timeout_seconds,
    )
    usage = result.usage
    return result.output, {
        "requests": usage.requests,
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "messages": result.all_messages_json().decode("utf-8"),
    }
