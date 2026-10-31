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

"""Single-shot v14 live probe for the ID-reference hypothesis (exact-literals case)."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any, cast

import httpx
from openai import AsyncOpenAI
from powercontext.builtin.inference.pydantic_ai import InferenceLimits, PydanticAIStructuredGenerator
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.profiles.openai import OpenAIModelProfile
from pydantic_ai.providers.openai import OpenAIProvider

from ..manifests import EvaluationError, load_suite
from ..models import canonical, digest
from ..runner import run_sample
from ..v6.workflow import InputProbe
from .codec import INSTRUCTIONS, TemplateOutput, cell_binding, render
from .live import bind

BASE_URL = "https://api.deepseek.com"
MODEL = "deepseek-v4-flash"
TIMEOUT_SECONDS = 60
MAX_OUTPUT_TOKENS = 2048


def score_literals(case, rendered) -> dict[str, Any]:
    text = "\n".join(c.text for c in rendered.candidates)
    claims = case.required
    results = []
    for claim in claims:
        for literal in claim.exact_literals:
            results.append(
                {
                    "literal_sha256": digest(literal),
                    "present_byte_for_byte": literal.encode() in text.encode(),
                }
            )
    return {
        "candidate_count": len(rendered.candidates),
        "all_required_literals": len(results),
        "retained_required_literals": sum(1 for r in results if r["present_byte_for_byte"]),
        "literal_checks": results,
    }


async def run_once(output_dir: Path) -> dict[str, Any]:
    if output_dir.exists():
        raise EvaluationError("output_exists")
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key or not api_key.strip():
        raise EvaluationError("configured_api_key_unavailable")
    suite = load_suite(experiment_version="v5", dataset="synthetic-v1")
    cases = {c.case_id: c for c in suite.cases}
    case = cases["exact-literals"]
    probe = InputProbe()
    sample = await run_sample(suite, case, "bounded-fragments-v3", 4096, 0, generator=probe)
    if not probe.inputs:
        raise EvaluationError("no_admitted_input")
    request = bind(probe.inputs[0], sample.supports)
    payload = request.encoded.payload
    result: dict[str, Any] = {
        "schema": "evidence-admission.v14.live-probe.v1",
        "sample_id": sample.sample_id,
        "case": "exact-literals",
        "arm": "bounded-fragments-v3",
        "budget": 4096,
        "repetition": 0,
        "provider": "openai-compatible",
        "model_requested": MODEL,
        "base_url": BASE_URL,
        "payload_hash": digest(payload),
        "binding_hash": request.binding_hash,
        "cell_count": len(request.encoded.cells),
        "cells": [{"c": c.cell_id, "literal_sha256": digest(c.value)} for c in request.encoded.cells],
        "estimated_input_tokens": request.encoded.cost["single_estimated_input_tokens"],
    }
    async with (
        httpx.AsyncClient(
            transport=httpx.AsyncHTTPTransport(retries=0), follow_redirects=False, timeout=TIMEOUT_SECONDS
        ) as client,
        AsyncOpenAI(api_key=api_key, base_url=BASE_URL, http_client=cast(Any, client), max_retries=0) as sdk,
    ):
        model = OpenAIChatModel(
            MODEL,
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
                max_output_tokens_per_request=MAX_OUTPUT_TOKENS,
                output_tokens_limit=MAX_OUTPUT_TOKENS,
                timeout_seconds=TIMEOUT_SECONDS,
                allow_continuations=False,
            ),
            model_settings={"temperature": 0, "extra_body": {"thinking": {"type": "disabled"}}},
        )
        raw = await generator.generate(request.encoded.payload)
        template = TemplateOutput.model_validate(raw.output)
        result["provider_usage"] = raw.usage.model_dump(mode="json")
        result["raw_template_hash"] = digest(template.model_dump(mode="json"))
        rendered = render(template, request.encoded.cells, request.encoded.aliases, request_hash=cell_binding(request.encoded.cells))
    result["score"] = score_literals(case, rendered)
    result["rendered_candidates"] = [c.text for c in rendered.candidates]
    output_dir.mkdir(parents=True)
    (output_dir / "result.json").write_text(canonical(result).decode(), encoding="utf-8")
    return result


def main():
    import sys

    output_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".artifacts/evidence-admission/v14-live-probe")
    result = asyncio.run(run_once(output_dir))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
