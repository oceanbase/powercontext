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


"""Scope Profile snapshots and server-owned processing metadata."""

from __future__ import annotations

import unicodedata
from datetime import UTC, datetime
from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from powercontext.artifacts import Artifact, ArtifactDraft, ArtifactRef

MAX_PROFILE_CONTENT_BYTES = 262_144
PROFILE_FAMILY = "profile"
PROFILE_ARTIFACT_ID = "profile"
PROFILE_SOURCE_WINDOW_BINDING = "profile-source-window"
ProfileActivationMode = Literal["automatic", "review_required"]
ProfileGenerationMode = Literal["automatic", "manual_create", "manual_replace", "review_approved", "rollback"]


class _ProfileValue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


def normalize_profile_markdown(value: str) -> str:
    normalized = unicodedata.normalize("NFC", value.removeprefix("\ufeff")).replace("\r\n", "\n").replace("\r", "\n")
    normalized = normalized.rstrip("\n") + "\n"
    if not normalized.strip():
        raise ValueError("profile Markdown must not be blank")  # noqa: TRY003
    if len(normalized.encode("utf-8")) > MAX_PROFILE_CONTENT_BYTES:
        raise ValueError("profile Markdown exceeds 256 KiB")  # noqa: TRY003
    return normalized


class ProfileWriteContent(_ProfileValue):
    content: str
    restored_from_revision: int | None = Field(default=None, ge=1)

    @field_validator("content")
    @classmethod
    def validate_markdown(cls, value: str) -> str:
        return normalize_profile_markdown(value)


class SourceWindow(_ProfileValue):
    after: int = Field(ge=0)
    through: int = Field(ge=0)

    @model_validator(mode="after")
    def ordered(self):
        if self.through < self.after:
            raise ValueError("through must be >= after")  # noqa: TRY003
        return self


class ProfileGeneration(_ProfileValue):
    mode: ProfileGenerationMode
    created_at: datetime
    generator_id: str | None = None
    generator_version: str | None = None
    source_window: SourceWindow | None = None
    restored_from_revision: int | None = Field(default=None, ge=1)

    @field_validator("created_at")
    @classmethod
    def utc_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("created_at must include a timezone")  # noqa: TRY003
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def valid_generation(self):
        generated = self.mode in {"automatic", "review_approved"}
        if generated != (self.source_window is not None):
            raise ValueError("only generated profiles require a Source window")  # noqa: TRY003
        if generated and (not self.generator_id or not self.generator_version):
            raise ValueError("generated profiles require generator identity")  # noqa: TRY003
        if not generated and (self.generator_id is not None or self.generator_version is not None):
            raise ValueError("manual writes cannot set generator identity")  # noqa: TRY003
        if (self.mode == "rollback") != (self.restored_from_revision is not None):
            raise ValueError("only rollback names a restored revision")  # noqa: TRY003
        return self


class ProfileContent(_ProfileValue):
    schema_: Literal["powercontext.profile.v1"] = Field(default="powercontext.profile.v1", alias="schema")
    media_type: Literal["text/markdown"] = "text/markdown"
    content: str
    generation: ProfileGeneration

    @field_validator("content")
    @classmethod
    def validate_markdown(cls, value: str) -> str:
        return normalize_profile_markdown(value)


class ProfileCandidateProposal(_ProfileValue):
    schema_: Literal["powercontext.profile-candidate.v1"] = Field(
        default="powercontext.profile-candidate.v1", alias="schema"
    )
    content: str
    source_window: SourceWindow
    generator_id: str = Field(min_length=1)
    generator_version: str = Field(min_length=1)
    created_at: datetime

    @field_validator("content")
    @classmethod
    def validate_markdown(cls, value: str) -> str:
        return normalize_profile_markdown(value)

    @field_validator("created_at")
    @classmethod
    def utc_timestamp(cls, value: datetime) -> datetime:
        return ProfileGeneration.utc_timestamp(value)


class Profile(Artifact[ProfileContent]):
    family: ClassVar[str] = PROFILE_FAMILY


class ProfileDraft(ArtifactDraft[ProfileContent]):
    family: ClassVar[str] = PROFILE_FAMILY


class ProfilePolicy(_ProfileValue):
    scope_id: str
    generation_enabled: bool
    activation_mode: ProfileActivationMode = "automatic"
    pending_candidate_id: str | None = None
    version: int = Field(ge=1)
    updated_at: datetime


class ProfileFlushResult(_ProfileValue):
    status: Literal["updated", "noop", "review_pending", "disabled", "conflict"]
    previous_cursor: int = Field(ge=0)
    current_cursor: int = Field(ge=0)
    high_watermark: int = Field(ge=0)
    processed_source_count: int = Field(default=0, ge=0)
    artifact: ArtifactRef | None = None
    candidate_id: str | None = None
