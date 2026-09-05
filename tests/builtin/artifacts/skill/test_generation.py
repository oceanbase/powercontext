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
import json

from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel

from powercontext.builtin.artifacts.generation import ArtifactGenerationInput
from powercontext.builtin.artifacts.skill import SkillGenerationOutput
from powercontext.builtin.artifacts.skill.package import build_instruction_skill_package
from powercontext.builtin.inference.pydantic_ai import PydanticAIStructuredGenerator


def test_invalid_generated_package_is_retried_before_any_write() -> None:
    observed: list[str] = []

    async def respond(messages, info) -> ModelResponse:
        name = "Invalid Package Name" if not observed else "port-preflight"
        observed.append(name)
        return ModelResponse(
            parts=[
                TextPart(
                    json.dumps({
                        "proposal": {
                            "name": name,
                            "description": "Check ports before a release.",
                            "instructions": "Identify the port owner and rerun the smoke tests.",
                            "validation": ["The smoke tests pass."],
                        }
                    })
                )
            ]
        )

    async def scenario() -> None:
        generator = PydanticAIStructuredGenerator(
            model=FunctionModel(respond),
            instructions="Generate one standard Skill package.",
            input_type=ArtifactGenerationInput,
            output_type=SkillGenerationOutput,
        )
        result = await generator.generate(
            ArtifactGenerationInput.model_validate_json(
                json.dumps({
                    "evidence": [{"evidence_id": "source:1", "kind": "source", "content": "Verified port preflight."}],
                })
            )
        )
        assert result.output.proposal is not None
        assert result.output.proposal.name == "port-preflight"
        build_instruction_skill_package(result.output.proposal)
        assert result.usage.requests == 2

    asyncio.run(scenario())
