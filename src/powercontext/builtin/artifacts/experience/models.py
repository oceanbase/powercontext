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

from typing import Annotated, ClassVar, Literal, TypeAlias

from pydantic import Field, field_validator

from powercontext.artifacts import Artifact, ArtifactDraft
from powercontext.artifacts.models import _ArtifactValue

MAX_EXPERIENCE_FIELD_LENGTH = 8_000
MAX_FAILURE_CUE_LENGTH = 512
ExperienceText = Annotated[str, Field(min_length=1, max_length=MAX_EXPERIENCE_FIELD_LENGTH)]
# A matching key must stay far shorter than prose: it is compared, indexed, and replayed.
FailureCueText = Annotated[str, Field(min_length=1, max_length=MAX_FAILURE_CUE_LENGTH)]

RepairSurface: TypeAlias = Literal["experience_content", "working_state", "recall_policy", "acceptance_check"]


class _ExperienceValue(_ArtifactValue):
    """Shared immutable configuration for Experience-family values."""


class FailureSignature(_ExperienceValue):
    """Machine-matchable identity for one recurring failure."""

    recall_cue: FailureCueText
    symptom: ExperienceText | None = None

    @field_validator("recall_cue")
    @classmethod
    def reject_blank_cue(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("failure recall_cue must not be blank")  # noqa: TRY003
        return value


class FailureVerification(_ExperienceValue):
    """Normalized strict bindings that make `avoided` decidable."""

    # Bound to a verified WorkClaim.text by normalized strict equality.
    condition: ExperienceText
    # Bound to a verified TaskCheck.name by normalized strict equality.
    check_subject: FailureCueText

    @field_validator("check_subject")
    @classmethod
    def reject_blank_subject(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("failure check_subject must not be blank")  # noqa: TRY003
        return value


class FailureRecord(_ExperienceValue):
    """Optional failure block carried by one Experience revision."""

    signature: FailureSignature
    repair_surface: RepairSurface
    verification: FailureVerification


class ExperienceContent(_ExperienceValue):
    """A reusable judgment grounded in exact task evidence."""

    situation: ExperienceText
    action: ExperienceText
    outcome: ExperienceText
    lesson: ExperienceText
    failure: FailureRecord | None = None

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
    "MAX_FAILURE_CUE_LENGTH",
    "Experience",
    "ExperienceContent",
    "ExperienceDraft",
    "FailureRecord",
    "FailureSignature",
    "FailureVerification",
    "RepairSurface",
]
