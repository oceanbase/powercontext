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

"""Run paired Atomic Memory extraction on synthetic labeled conversations."""

from __future__ import annotations

import argparse
import asyncio
import json
from contextlib import AsyncExitStack
from hashlib import sha256
from pathlib import Path

from pydantic_ai.settings import ModelSettings

from powercontext.builtin.artifacts.atomic_memory.extraction import (
    AtomicMemoryEvidence,
    AtomicMemoryExtractionInput,
    AtomicMemoryExtractionOutput,
    atomic_memory_extraction_instructions,
    atomic_memory_extraction_instructions_version,
    validate_extraction_output,
    with_session_evidence_instructions,
)
from powercontext.builtin.artifacts.memory.prompts import MemoryExtractionProfile
from powercontext.builtin.inference.pydantic_ai import InferenceLimits, PydanticAIStructuredGenerator
from powercontext.builtin.runtime.composition import _open_pydantic_ai_model
from powercontext.builtin.sources.session import SESSION_EVIDENCE_INSTRUCTIONS, SESSION_TYPE
from powercontext.server.configuration import server_settings_context
from powercontext.sources import SourceRef, SourceSequenceLocator


async def compare(env_file: Path, output: Path) -> None:
    cases = json.loads(Path(__file__).with_name("cases.json").read_text(encoding="utf-8"))
    settings: ModelSettings = {"temperature": 0, "max_tokens": 512, "extra_body": {"thinking": {"type": "disabled"}}}
    async with AsyncExitStack() as stack:
        settings_context = stack.enter_context(server_settings_context(env_file=env_file))
        inference = settings_context.inference
        if inference.generation_model is None:
            raise SystemExit("Configure POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL.")  # noqa: TRY003
        _, model = await _open_pydantic_ai_model(
            inference.generation_model,
            base_url=inference.generation_base_url,
            headers=inference.generation_headers,
            resources=stack,
            instrumentation=None,
            disable_provider_retries=True,
        )
        profile = MemoryExtractionProfile.CODING
        instructions = atomic_memory_extraction_instructions(profile)
        extractor = PydanticAIStructuredGenerator[AtomicMemoryExtractionInput, AtomicMemoryExtractionOutput](
            model=model,
            instructions=instructions,
            input_type=AtomicMemoryExtractionInput,
            output_type=AtomicMemoryExtractionOutput,
            limits=InferenceLimits(timeout_seconds=30, max_requests=1, allow_continuations=False),
            model_settings=settings,
            output_check=validate_extraction_output,
            input_renderer=lambda value: with_session_evidence_instructions(value, value.model_dump_json()),
        )
        report = {
            "model": inference.generation_model,
            "settings": settings,
            "prompt": {
                "extraction_version": atomic_memory_extraction_instructions_version(profile),
                "extraction_sha256": sha256(instructions.encode("utf-8")).hexdigest(),
                "session_guidance_sha256": sha256(SESSION_EVIDENCE_INSTRUCTIONS.encode("utf-8")).hexdigest(),
            },
            "rows": [],
        }
        for case in cases:
            for arm in ("user-only", "user-assistant"):
                messages = [
                    message for message in case["messages"] if arm == "user-assistant" or message["role"] == "user"
                ]
                row = {"case": case["id"], "arm": arm}
                if not messages:
                    row["status"] = "no_input"
                else:
                    content = {"type": SESSION_TYPE, "session_id": case["id"], "messages": messages}
                    evidence = AtomicMemoryEvidence(
                        evidence_id="source:1",
                        source_ref=SourceRef(
                            source_type="content", source_id=case["id"], locator=SourceSequenceLocator(sequence=1)
                        ),
                        journal_position=1,
                        content=content,
                        source_metadata={"session_input": True, "metadata": {}},
                    )
                    try:
                        result = await extractor.generate(AtomicMemoryExtractionInput(evidence=(evidence,)))
                    except Exception as error:  # Record an incomplete arm without exposing provider error details.
                        row.update(status="failed", error=type(error).__name__)
                    else:
                        row.update(
                            status="generated",
                            candidates=result.output.model_dump(mode="json")["candidates"],
                            usage=result.usage.model_dump(
                                mode="json", include={"requests", "input_tokens", "output_tokens"}
                            ),
                        )
                report["rows"].append(row)
                await asyncio.to_thread(
                    output.write_text, json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
                )
                print(case["id"], arm, row["status"], flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    options = parser.parse_args()
    asyncio.run(compare(options.env_file, options.output))


if __name__ == "__main__":
    main()
