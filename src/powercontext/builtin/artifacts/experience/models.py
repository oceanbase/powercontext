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

"""Typed content for the built-in Experience Artifact Family."""

from __future__ import annotations

from typing import Annotated, ClassVar

from pydantic import BaseModel, Field, field_validator

from powercontext.artifacts import Artifact, ArtifactDraft

MAX_EXPERIENCE_FIELD_LENGTH = 8_000
ExperienceText = Annotated[str, Field(min_length=1, max_length=MAX_EXPERIENCE_FIELD_LENGTH)]


class ExperienceContent(BaseModel):
    """A reusable judgment grounded in exact task evidence."""

    situation: ExperienceText
    action: ExperienceText
    outcome: ExperienceText
    lesson: ExperienceText

    @field_validator("situation", "action", "outcome", "lesson")
    @classmethod
    def reject_blank_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Experience fields must not be blank")  # noqa: TRY003
        return value


class Experience(Artifact[ExperienceContent]):
    """An approved immutable Experience revision."""

    family: ClassVar[str] = "experience"


class ExperienceDraft(ArtifactDraft[ExperienceContent]):
    """Complete Experience content and evidence ready for Artifact commit."""

    family: ClassVar[str] = "experience"


__all__ = [
    "MAX_EXPERIENCE_FIELD_LENGTH",
    "Experience",
    "ExperienceContent",
    "ExperienceDraft",
]
