"""Typed generation owned by the managed Skill Artifact Family."""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, ValidationError

from powercontext.builtin.artifacts.generation import ArtifactGenerationInput
from powercontext.builtin.artifacts.skill.models import SkillContent
from powercontext.builtin.inference import GenerationResult, InvalidInferenceOutputError, StructuredGenerator


class SkillGenerationOutput(BaseModel):
    """A typed managed Skill proposal or an explicit no-op."""

    proposal: SkillContent | None = None


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
