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

"""Real Source replay and Pipeline execution with a simulated provider transport."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from types import SimpleNamespace

import httpx
import pytest
from openai import AsyncOpenAI
from powercontext.builtin.inference import GenerationResult, character_token_estimator
from powercontext.builtin.inference.pydantic_ai import InferenceLimits, PydanticAIStructuredGenerator
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.profiles.openai import OpenAIModelProfile
from pydantic_ai.providers.openai import OpenAIProvider

from powercontext_eval_evidence_admission.manifests import EvaluationError, load_suite
from powercontext_eval_evidence_admission.models import canonical
from powercontext_eval_evidence_admission.runner import run_sample
from powercontext_eval_evidence_admission.v6.workflow import InputProbe
from powercontext_eval_evidence_admission.v9.codec import TemplateOutput
from powercontext_eval_evidence_admission.v9_preflight.workflow import mock_response
from powercontext_eval_evidence_admission.v10.protocol import INSTRUCTIONS, bind, pack, pipeline


async def prepared(case_id="exact-literals", budget=4096, arm="bounded-fragments-v3"):
    suite = load_suite(experiment_version="v5")
    case = next(c for c in suite.cases if c.case_id == case_id)
    probe = InputProbe()
    sample = await run_sample(suite, case, arm, budget, 0, generator=probe)
    return case, sample, probe.inputs


def test_persisted_source_compact_sdk_pipeline_preserves_unicode_and_copy_bytes():
    async def scenario():
        _, sample, inputs = await prepared("unicode-boundaries")
        request = bind(inputs[0], sample.supports)
        quote = next(q for q in request.encoded.quotes if "👩🏽‍💻" in q.value and "\r\n" in q.value)
        alias = f"e{quote.evidence_index}"
        start = quote.value.index("用户要求")
        text = quote.value[start : quote.value.index("\r\n", start) + 2]
        left = len(quote.value[:start].encode())
        output = {
            "candidates": [
                {
                    "kind": "constraint",
                    "description": "Synthetic protocol candidate",
                    "evidence_ids": [alias],
                    "parts": [
                        {
                            "type": "copy",
                            "quote_id": quote.quote_id,
                            "byte_start": left,
                            "byte_end": left + len(text.encode()),
                        },
                    ],
                }
            ]
        }
        seen = []

        def handler(http_request):
            body = json.loads(http_request.content)
            seen.append(body)
            response = mock_response()
            response["choices"][0]["message"]["content"] = json.dumps(output, ensure_ascii=False)
            return httpx.Response(200, json=response)

        async with (
            httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client,
            AsyncOpenAI(
                api_key="offline-placeholder", base_url="https://offline.invalid", http_client=client, max_retries=0
            ) as sdk,
        ):
            model = OpenAIChatModel(
                "offline",
                provider=OpenAIProvider(openai_client=sdk),
                profile=OpenAIModelProfile(openai_chat_supports_max_completion_tokens=False),
            )
            generator = PydanticAIStructuredGenerator(
                model=model,
                instructions=INSTRUCTIONS,
                input_type=dict,
                output_type=TemplateOutput,
                limits=InferenceLimits(max_requests=1, max_output_tokens_per_request=2048, allow_continuations=False),
                model_settings={"temperature": 0},
            )
            candidates = await pipeline(request, generator).extract(request.encoded.value)
        assert len(seen) == 1
        assert seen[0]["messages"][0]["content"] == INSTRUCTIONS
        assert json.loads(seen[0]["messages"][-1]["content"]) == request.encoded.payload
        assert candidates[0].text.encode() == text.encode()
        assert candidates[0].evidence_ids == (quote.evidence_id,)
        assert sample.materializations and sample.projections

    asyncio.run(scenario())


def test_wrong_origin_and_binding_are_rejected_before_generator():
    async def scenario():
        _, sample, inputs = await prepared("small-complete")
        request = bind(inputs[0], sample.supports)

        class NeverGenerate:
            async def generate(self, value, /):
                raise AssertionError("stale requests must not reach a provider")

        with pytest.raises(EvaluationError, match="v10_request_binding_mismatch"):
            await pipeline(replace(request, binding_hash="stale"), NeverGenerate()).extract(inputs[0])
        with pytest.raises(EvaluationError, match="v10_request_binding_mismatch"):
            await pipeline(request, NeverGenerate()).extract(inputs[0].model_copy(update={"evidence": ()}))

    asyncio.run(scenario())


def test_pipeline_cancellation_propagates_without_a_completed_output():
    async def scenario():
        _, sample, inputs = await prepared("small-complete")
        entered = asyncio.Event()

        class Waiting:
            async def generate(self, value, /):
                entered.set()
                await asyncio.Event().wait()

        task = asyncio.create_task(pipeline(bind(inputs[0], sample.supports), Waiting()).extract(inputs[0]))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())


def test_fragment_cost_includes_new_instructions_and_exhaustion_remains_rejected():
    async def scenario():
        _, sample, inputs = await prepared("many-source-overflow", 2048)
        request = bind(inputs[0], sample.supports)
        exact = character_token_estimator().estimate(
            canonical(
                {
                    "instructions": INSTRUCTIONS,
                    "input": request.encoded.payload,
                    "output_schema": TemplateOutput.model_json_schema(),
                }
            ).decode()
        )
        assert exact == request.cost["single_estimated_input_tokens"]
        plan = pack(
            inputs,
            sample.supports,
            2048,
            SimpleNamespace(max_calls_per_sample=1, total_input_budget_multiplier=8),
            allow_repacking=True,
        )
        assert not plan.accepted and plan.overflow["call_count"]

    asyncio.run(scenario())


def test_original_window_control_is_not_silently_split_to_fit_protocol():
    async def scenario():
        _, sample, inputs = await prepared("small-complete")
        request = bind(inputs[0], sample.supports)
        budget = request.cost["single_estimated_input_tokens"] - 1
        plan = pack(
            inputs,
            sample.supports,
            budget,
            SimpleNamespace(max_calls_per_sample=16, total_input_budget_multiplier=8),
            allow_repacking=False,
        )
        assert not plan.accepted and plan.overflow["per_call"]
        assert len(plan.requests) == len(inputs)
        assert plan.requests[0].encoded.value == inputs[0]

    asyncio.run(scenario())


def test_recorded_compound_output_is_not_split_by_pipeline():
    async def scenario():
        _, sample, inputs = await prepared()
        request = bind(inputs[0], sample.supports)

        class Compound:
            async def generate(self, value, /):
                return GenerationResult(
                    output=TemplateOutput.model_validate(
                        {
                            "candidates": [
                                {
                                    "kind": "fact",
                                    "description": "Synthetic protocol candidate",
                                    "evidence_ids": ["e0"],
                                    "parts": [
                                        {
                                            "type": "text",
                                            "text": "Deployment job is `batch_A-17` with limit `0.075`.",
                                        }
                                    ],
                                }
                            ]
                        }
                    )
                )

        candidates = await pipeline(request, Compound()).extract(inputs[0])
        assert len(candidates) == 1
        assert candidates[0].text == "Deployment job is `batch_A-17` with limit `0.075`."

    asyncio.run(scenario())
