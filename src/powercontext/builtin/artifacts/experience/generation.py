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

"""Typed generation owned by the Experience Artifact Family."""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, ValidationError

from powercontext.builtin.artifacts.experience.models import ExperienceContent
from powercontext.builtin.artifacts.generation import ArtifactGenerationInput
from powercontext.builtin.inference import GenerationResult, InvalidInferenceOutputError, StructuredGenerator


class ExperienceGenerationOutput(BaseModel):
    """A typed Experience proposal or an explicit no-op."""

    proposal: ExperienceContent | None = None


class ExperienceGenerator(Protocol):
    """Generate at most one complete Experience proposal."""

    async def generate(self, value: ArtifactGenerationInput, /) -> ExperienceContent | None: ...


class LLMExperienceGenerator:
    """Validate schema-bound Experience model output."""

    def __init__(
        self,
        generator: StructuredGenerator[ArtifactGenerationInput, ExperienceGenerationOutput],
    ) -> None:
        self._generator = generator

    async def generate(self, value: ArtifactGenerationInput, /) -> ExperienceContent | None:
        result = await self._generator.generate(value)
        return _validated_output(result).proposal


def _validated_output(result: GenerationResult[ExperienceGenerationOutput]) -> ExperienceGenerationOutput:
    if not isinstance(result, GenerationResult):
        raise InvalidInferenceOutputError("experience-generate", "generator returned the wrong output type")
    try:
        return ExperienceGenerationOutput.model_validate(result.output)
    except ValidationError as error:
        raise InvalidInferenceOutputError("experience-generate", "generator returned invalid typed content") from error


__all__ = ["ExperienceGenerationOutput", "ExperienceGenerator", "LLMExperienceGenerator"]
