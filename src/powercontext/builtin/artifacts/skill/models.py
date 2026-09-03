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

"""Typed content for the built-in managed Skill Artifact Family."""

from __future__ import annotations

from typing import Annotated, ClassVar

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from powercontext.artifacts import Artifact, ArtifactDraft

MAX_SKILL_NAME_LENGTH = 128
MAX_SKILL_DESCRIPTION_LENGTH = 2_000
MAX_SKILL_INSTRUCTIONS_LENGTH = 128 * 1024
MAX_SKILL_VALIDATION_ITEMS = 32
MAX_SKILL_VALIDATION_ITEM_LENGTH = 2_000
MAX_SKILL_COMPATIBILITY_LENGTH = 500

SkillName = Annotated[str, Field(min_length=1, max_length=MAX_SKILL_NAME_LENGTH)]
SkillDescription = Annotated[str, Field(min_length=1, max_length=MAX_SKILL_DESCRIPTION_LENGTH)]
SkillInstructions = Annotated[str, Field(max_length=MAX_SKILL_INSTRUCTIONS_LENGTH)]
SkillValidationItem = Annotated[str, Field(min_length=1, max_length=MAX_SKILL_VALIDATION_ITEM_LENGTH)]


class SkillPackageRef(BaseModel):
    """Content-addressed reference to one canonical Agent Skill package."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    tree_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    archive_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    file_count: int = Field(ge=1, le=256)
    uncompressed_size: int = Field(ge=1, le=4 * 1024 * 1024)
    archive_size: int = Field(ge=1, le=5 * 1024 * 1024)


class SkillContent(BaseModel):
    """A legacy instruction core or a standard package-backed managed Skill."""

    name: SkillName
    description: SkillDescription
    instructions: SkillInstructions
    validation: tuple[SkillValidationItem, ...] = Field(default=(), max_length=MAX_SKILL_VALIDATION_ITEMS)
    package: SkillPackageRef | None = None
    license: str | None = Field(default=None, min_length=1, max_length=512)
    compatibility: str | None = Field(default=None, min_length=1, max_length=MAX_SKILL_COMPATIBILITY_LENGTH)
    metadata: dict[str, str] = Field(default_factory=dict)
    allowed_tools: str | None = Field(default=None, min_length=1, max_length=2_000)

    @field_validator("name", "description")
    @classmethod
    def reject_untrimmed_text(cls, value: str) -> str:
        if not value.strip() or value != value.strip():
            raise ValueError("Skill name and description must be non-empty and trimmed")  # noqa: TRY003
        return value

    @field_validator("instructions")
    @classmethod
    def reject_blank_instructions(cls, value: str) -> str:
        if value and value != value.rstrip():
            raise ValueError("Skill instructions must not have trailing whitespace")  # noqa: TRY003
        return value

    @field_validator("validation")
    @classmethod
    def reject_blank_validation(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if any(not value.strip() or value != value.strip() for value in values):
            raise ValueError("Skill validation items must be non-empty and trimmed")  # noqa: TRY003
        return values

    @field_validator("license", "compatibility", "allowed_tools")
    @classmethod
    def reject_untrimmed_optional_text(cls, value: str | None) -> str | None:
        if value is not None and value != value.strip():
            raise ValueError("optional Skill metadata must be trimmed")  # noqa: TRY003
        return value

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, str]) -> dict[str, str]:
        if len(value) > 64:
            raise ValueError("Skill metadata must not exceed 64 entries")  # noqa: TRY003
        if any(not key.strip() or key != key.strip() or len(key) > 128 for key in value):
            raise ValueError("Skill metadata keys must be non-empty trimmed strings of at most 128 characters")  # noqa: TRY003
        if any(item != item.strip() or len(item) > 2_000 for item in value.values()):
            raise ValueError("Skill metadata values must be trimmed strings of at most 2000 characters")  # noqa: TRY003
        return value

    @model_validator(mode="after")
    def validate_content_kind(self) -> SkillContent:
        if self.package is None:
            if not self.instructions.strip():
                raise ValueError("legacy Skill instructions must not be blank")  # noqa: TRY003
            if not self.validation:
                raise ValueError("legacy Skill validation must not be empty")  # noqa: TRY003
        return self

    @property
    def package_backed(self) -> bool:
        """Return whether the exact standard package is the content authority."""

        return self.package is not None


class Skill(Artifact[SkillContent]):
    """An approved immutable managed Skill revision."""

    family: ClassVar[str] = "skill"


class SkillDraft(ArtifactDraft[SkillContent]):
    """Complete managed Skill content and evidence ready for Artifact commit."""

    family: ClassVar[str] = "skill"


__all__ = [
    "MAX_SKILL_COMPATIBILITY_LENGTH",
    "MAX_SKILL_DESCRIPTION_LENGTH",
    "MAX_SKILL_INSTRUCTIONS_LENGTH",
    "MAX_SKILL_NAME_LENGTH",
    "MAX_SKILL_VALIDATION_ITEMS",
    "MAX_SKILL_VALIDATION_ITEM_LENGTH",
    "Skill",
    "SkillContent",
    "SkillDraft",
    "SkillPackageRef",
]
