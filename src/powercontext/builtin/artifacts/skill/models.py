"""Typed content for the built-in managed Skill Artifact Family."""

from __future__ import annotations

from typing import Annotated, ClassVar

from pydantic import BaseModel, Field, field_validator

from powercontext.artifacts import Artifact, ArtifactDraft

MAX_SKILL_NAME_LENGTH = 128
MAX_SKILL_DESCRIPTION_LENGTH = 2_000
MAX_SKILL_INSTRUCTIONS_LENGTH = 32_000
MAX_SKILL_VALIDATION_ITEMS = 32
MAX_SKILL_VALIDATION_ITEM_LENGTH = 2_000

SkillName = Annotated[str, Field(min_length=1, max_length=MAX_SKILL_NAME_LENGTH)]
SkillDescription = Annotated[str, Field(min_length=1, max_length=MAX_SKILL_DESCRIPTION_LENGTH)]
SkillInstructions = Annotated[str, Field(min_length=1, max_length=MAX_SKILL_INSTRUCTIONS_LENGTH)]
SkillValidationItem = Annotated[str, Field(min_length=1, max_length=MAX_SKILL_VALIDATION_ITEM_LENGTH)]


class SkillContent(BaseModel):
    """A portable instruction core governed as an immutable Artifact."""

    name: SkillName
    description: SkillDescription
    instructions: SkillInstructions
    validation: tuple[SkillValidationItem, ...] = Field(min_length=1, max_length=MAX_SKILL_VALIDATION_ITEMS)

    @field_validator("name", "description")
    @classmethod
    def reject_untrimmed_text(cls, value: str) -> str:
        if not value.strip() or value != value.strip():
            raise ValueError("Skill name and description must be non-empty and trimmed")  # noqa: TRY003
        return value

    @field_validator("instructions")
    @classmethod
    def reject_blank_instructions(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Skill instructions must not be blank")  # noqa: TRY003
        return value

    @field_validator("validation")
    @classmethod
    def reject_blank_validation(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if any(not value.strip() or value != value.strip() for value in values):
            raise ValueError("Skill validation items must be non-empty and trimmed")  # noqa: TRY003
        return values


class Skill(Artifact[SkillContent]):
    """An approved immutable managed Skill revision."""

    family: ClassVar[str] = "skill"


class SkillDraft(ArtifactDraft[SkillContent]):
    """Complete managed Skill content and evidence ready for Artifact commit."""

    family: ClassVar[str] = "skill"


__all__ = [
    "MAX_SKILL_DESCRIPTION_LENGTH",
    "MAX_SKILL_INSTRUCTIONS_LENGTH",
    "MAX_SKILL_NAME_LENGTH",
    "MAX_SKILL_VALIDATION_ITEMS",
    "MAX_SKILL_VALIDATION_ITEM_LENGTH",
    "Skill",
    "SkillContent",
    "SkillDraft",
]
