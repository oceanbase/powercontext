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

"""Typed generation owned by the managed Skill Artifact Family."""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, ValidationError

from powercontext.builtin.artifacts.generation import ArtifactGenerationInput
from powercontext.builtin.artifacts.skill.models import SkillContent
from powercontext.builtin.inference import GenerationResult, InvalidInferenceOutputError, StructuredGenerator


class _GeneratedSkillContent(SkillContent):
    """Model-authored instruction content cannot claim an existing package snapshot."""

    package: None = None


class SkillGenerationOutput(BaseModel):
    """A typed managed Skill proposal or an explicit no-op."""

    proposal: _GeneratedSkillContent | None = None


class SkillGenerator(Protocol):
    """Generate at most one complete managed Skill proposal."""

    async def generate(self, value: ArtifactGenerationInput, /) -> SkillContent | None: ...


class LLMSkillGenerator:
    """Validate schema-bound managed Skill model output."""

    def __init__(
        self,
        generator: StructuredGenerator[ArtifactGenerationInput, SkillGenerationOutput],
    ) -> None:
        self._generator = generator

    async def generate(self, value: ArtifactGenerationInput, /) -> SkillContent | None:
        result = await self._generator.generate(value)
        return _validated_output(result).proposal


def _validated_output(result: GenerationResult[SkillGenerationOutput]) -> SkillGenerationOutput:
    if not isinstance(result, GenerationResult):
        raise InvalidInferenceOutputError("skill-generate", "generator returned the wrong output type")
    try:
        return SkillGenerationOutput.model_validate(result.output)
    except ValidationError as error:
        raise InvalidInferenceOutputError("skill-generate", "generator returned invalid typed content") from error


__all__ = ["LLMSkillGenerator", "SkillGenerationOutput", "SkillGenerator"]
