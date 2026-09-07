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

"""Typed content and metadata for the built-in Profile Artifact Family."""

from __future__ import annotations

import unicodedata
from datetime import datetime
from typing import Annotated, ClassVar, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator

from powercontext.artifacts import Artifact, ArtifactDraft, ArtifactRef
from powercontext.builtin.sources import validate_scope_id
from powercontext.limits import MAX_SCOPE_BINDING_EXTERNAL_ID_LENGTH
from powercontext.sources import SourceRef

MAX_PROFILE_CONTENT_BYTES = 262_144
PROFILE_FAMILY = "profile"
USER_PROFILE_ARTIFACT_ID = "profile:user"
LOCAL_PROFILE_ARTIFACT_ID = "profile:local"
PROFILE_SOURCE_WINDOW_BINDING = "profile-source-window"

SubjectKey: TypeAlias = Annotated[
    str,
    Field(min_length=1, max_length=MAX_SCOPE_BINDING_EXTERNAL_ID_LENGTH, pattern=r".*\S.*"),
]
ProfileActivationMode: TypeAlias = Literal["automatic", "review_required"]
ProfileGenerationMode: TypeAlias = Literal[
    "automatic",
    "manual_create",
    "manual_replace",
    "review_approved",
    "rollback",
]


class _ProfileValue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


def normalize_profile_markdown(value: str) -> str:
    """Return the canonical Markdown representation used for equality and storage."""

    normalized = unicodedata.normalize("NFC", value.removeprefix("\ufeff")).replace("\r\n", "\n").replace("\r", "\n")
    normalized = normalized.rstrip("\n") + "\n"
    if not normalized.strip():
        raise ValueError("profile Markdown must not be blank")  # noqa: TRY003
    if len(normalized.encode("utf-8")) > MAX_PROFILE_CONTENT_BYTES:
        raise ValueError("profile Markdown exceeds 256 KiB")  # noqa: TRY003
    return normalized


class ProfileContent(_ProfileValue):
    """One complete, normalized Markdown Profile snapshot."""

    schema_: Literal["powercontext.profile.v1"] = Field(default="powercontext.profile.v1", alias="schema")
    media_type: Literal["text/markdown"] = "text/markdown"
    content: str

    @field_validator("content")
    @classmethod
    def validate_markdown(cls, value: str) -> str:
        return normalize_profile_markdown(value)


class Profile(Artifact[ProfileContent]):
    """An immutable Profile revision."""

    family: ClassVar[str] = PROFILE_FAMILY


class ProfileDraft(ArtifactDraft[ProfileContent]):
    """Complete Profile content and exact evidence ready for commit."""

    family: ClassVar[str] = PROFILE_FAMILY


class SubjectRoot(_ProfileValue):
    """Stable one-to-one mapping from caller-owned user identity to one Root Scope."""

    subject_key: SubjectKey
    root_scope_id: str
    created_at: datetime

    @field_validator("root_scope_id")
    @classmethod
    def validate_root_scope_id(cls, value: str) -> str:
        return validate_scope_id(value)


class SourceAddress(_ProfileValue):
    scope_id: str
    source_type: str
    source_id: str

    @field_validator("scope_id")
    @classmethod
    def validate_address_scope_id(cls, value: str) -> str:
        return validate_scope_id(value)


class SubjectSourceProjection(_ProfileValue):
    """Exact relation between an Origin Source and its Root-local copy."""

    subject_key: SubjectKey
    origin: SourceAddress
    projected: SourceAddress
    content_digest: str
    created_at: datetime

    @model_validator(mode="after")
    def require_distinct_scopes_and_stable_identity(self) -> SubjectSourceProjection:
        if self.origin.scope_id == self.projected.scope_id:
            raise ValueError("origin and projected Source scopes must differ")  # noqa: TRY003
        if (self.origin.source_type, self.origin.source_id) != (
            self.projected.source_type,
            self.projected.source_id,
        ):
            raise ValueError("the initial projection must preserve Source identity")  # noqa: TRY003
        return self


class SubjectSourceWrite(_ProfileValue):
    """Result of one atomic subject-keyed Source write."""

    subject: SubjectRoot
    origin_ref: SourceRef
    root_ref: SourceRef
    origin_position: StrictInt = Field(ge=1)
    root_position: StrictInt = Field(ge=1)
    status: Literal["committed", "already_in_root"]


class ProfilePolicy(_ProfileValue):
    scope_id: str
    generation_enabled: bool = True
    activation_mode: ProfileActivationMode = "automatic"
    pending_candidate_id: str | None = None
    version: StrictInt = Field(ge=1)
    updated_at: datetime

    @field_validator("scope_id")
    @classmethod
    def validate_policy_scope_id(cls, value: str) -> str:
        return validate_scope_id(value)


class ProfileRevisionMetadata(_ProfileValue):
    scope_id: str
    artifact_ref: ArtifactRef
    generation_mode: ProfileGenerationMode
    generator_id: str | None = None
    generator_version: str | None = None
    source_after: int | None = None
    source_through: int | None = None
    restored_from_revision: int | None = None
    operation_reason: str | None = None
    created_at: datetime

    @field_validator("scope_id")
    @classmethod
    def validate_metadata_scope_id(cls, value: str) -> str:
        return validate_scope_id(value)

    @model_validator(mode="after")
    def validate_generation_window(self) -> ProfileRevisionMetadata:
        if self.artifact_ref.family != PROFILE_FAMILY:
            raise ValueError("Profile revision metadata must reference the profile family")  # noqa: TRY003
        generated = self.generation_mode in {"automatic", "review_approved"}
        if generated != (self.source_after is not None and self.source_through is not None):
            raise ValueError("generated Profile revisions require a complete Source window")  # noqa: TRY003
        if self.source_after is not None and (
            self.source_after < 0 or self.source_through is None or self.source_through < self.source_after
        ):
            raise ValueError("Profile Source window is invalid")  # noqa: TRY003
        rollback = self.generation_mode == "rollback"
        if rollback != (self.restored_from_revision is not None):
            raise ValueError("only rollback metadata may name a restored revision")  # noqa: TRY003
        return self


class ResolvedProfileTarget(_ProfileValue):
    """Canonical Profile address after resolving either a Subject or a Scope."""

    scope_id: str
    artifact_id: Literal["profile:user", "profile:local"]
    subject_key: SubjectKey | None = None
    root_scope_id: str | None = None

    @field_validator("scope_id")
    @classmethod
    def validate_target_scope_id(cls, value: str) -> str:
        return validate_scope_id(value)

    @model_validator(mode="after")
    def require_consistent_subject_target(self) -> ResolvedProfileTarget:
        subject_target = self.artifact_id == USER_PROFILE_ARTIFACT_ID
        if subject_target != (self.subject_key is not None and self.root_scope_id == self.scope_id):
            raise ValueError("Profile target identity is inconsistent")  # noqa: TRY003
        return self


class ProfileRecord(_ProfileValue):
    """A resolved Profile revision with generation metadata and its ETag."""

    target: ResolvedProfileTarget
    profile: Profile
    generation: ProfileRevisionMetadata
    etag: str


__all__ = [
    "LOCAL_PROFILE_ARTIFACT_ID",
    "MAX_PROFILE_CONTENT_BYTES",
    "PROFILE_FAMILY",
    "PROFILE_SOURCE_WINDOW_BINDING",
    "USER_PROFILE_ARTIFACT_ID",
    "Profile",
    "ProfileActivationMode",
    "ProfileContent",
    "ProfileDraft",
    "ProfileGenerationMode",
    "ProfilePolicy",
    "ProfileRecord",
    "ProfileRevisionMetadata",
    "ResolvedProfileTarget",
    "SourceAddress",
    "SubjectKey",
    "SubjectRoot",
    "SubjectSourceProjection",
    "SubjectSourceWrite",
    "normalize_profile_markdown",
]
