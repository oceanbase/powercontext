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

"""Scope-local labels for logical Artifacts and Memory entries.

Tags are discovery metadata, never Artifact content or authorization policy.
"""

from __future__ import annotations

import unicodedata
from hashlib import sha256
from typing import Annotated, Literal

import rfc8785
from pydantic import BaseModel, ConfigDict, Field, field_validator

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.records import BaseAccessError, BaseArtifactFamily, InvalidBaseAccessRequestError

TagMatch = Literal["all", "any"]
TagTargetType = Literal["artifact", "memory_entry"]


class _TagModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, hide_input_in_errors=True)


class ArtifactTagTarget(_TagModel):
    """A stable Artifact identity, independent of its current revision."""

    type: Literal["artifact"] = "artifact"
    family: BaseArtifactFamily
    artifact_id: str = Field(min_length=1, max_length=128)


class MemoryEntryTagTarget(_TagModel):
    """A logical entry in the current Memory manifest, including inactive entries."""

    type: Literal["memory_entry"] = "memory_entry"
    family: Literal["memory"] = "memory"
    artifact_id: str = Field(min_length=1, max_length=128)
    entry_id: str = Field(min_length=1, max_length=128)


TagTarget = Annotated[ArtifactTagTarget | MemoryEntryTagTarget, Field(discriminator="type")]


def normalize_tags(tags: tuple[str, ...], *, maximum: int = 32, allow_empty: bool = True) -> dict[str, str]:
    """Validate atomically and return display labels in UTF-8 key order.

    Invalid input is never included in an exception or diagnostic message.
    """

    if len(tags) > maximum or (not tags and not allow_empty):
        raise InvalidBaseAccessRequestError("tags", "has an invalid number of labels")
    normalized: dict[str, str] = {}
    for tag in tags:
        if (
            not isinstance(tag, str)
            or not 1 <= len(tag) <= 64
            or tag != tag.strip()
            or any(unicodedata.category(char) in {"Cc", "Cs", "Cn"} for char in tag)
        ):
            raise InvalidBaseAccessRequestError("tags", "contains an invalid label")
        key = unicodedata.normalize("NFC", tag).casefold()
        if len(key) > 128 or key in normalized:
            raise InvalidBaseAccessRequestError("tags", "contains an invalid or duplicate normalized label")
        normalized[key] = tag
    return dict(sorted(normalized.items(), key=lambda item: item[0].encode("utf-8")))


class TagFilter(_TagModel):
    """Exact label matching applied before candidate limits."""

    tags: tuple[str, ...]
    match: TagMatch = "all"

    @field_validator("tags")
    @classmethod
    def validate_tags(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalize_tags(value, maximum=16, allow_empty=False)
        return value

    @property
    def keys(self) -> tuple[str, ...]:
        return tuple(normalize_tags(self.tags, maximum=16, allow_empty=False))


class ArtifactTagSet(_TagModel):
    """The complete current tag set for one target."""

    scope_id: str
    target: TagTarget
    tags: tuple[str, ...]
    tag_digest: str

    @property
    def etag(self) -> str:
        payload = {"scope_id": self.scope_id, "target": self.target.model_dump(), "tag_digest": self.tag_digest}
        return '"tags:' + sha256(rfc8785.dumps(payload)).hexdigest() + '"'


def tag_set(scope_id: str, target: TagTarget, tags: tuple[str, ...]) -> ArtifactTagSet:
    ordered = tuple(normalize_tags(tags).values())
    digest = "sha256:" + sha256(rfc8785.dumps({"tags": list(ordered)})).hexdigest()
    return ArtifactTagSet(scope_id=scope_id, target=target, tags=ordered, tag_digest=digest)


class TagPreconditionError(BaseAccessError):
    """The supplied ETag does not name the current target's tag state."""


class TagQuery(TagFilter):
    """A bounded exact query within one authorized Scope."""

    families: tuple[BaseArtifactFamily, ...] = ("memory", "experience", "skill", "handoff")
    target_types: tuple[TagTargetType, ...] = ("artifact", "memory_entry")
    include_inactive: bool = False
    limit: int = Field(default=50, ge=1, le=100)
    cursor: str | None = None

    @field_validator("families", "target_types")
    @classmethod
    def validate_selection(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value or len(set(value)) != len(value):
            raise InvalidBaseAccessRequestError("filters", "must contain distinct nonempty selections")
        return value


class TaggedMemoryCitation(_TagModel):
    """The exact Memory citation accompanying a logical entry match."""

    memory_ref: ArtifactRef
    entry_id: str
    entry_version_id: str


class TaggedTarget(ArtifactTagSet):
    """A tag match pinned to the authoritative current content revision."""

    reference: ArtifactRef | TaggedMemoryCitation


class TagQueryPage(_TagModel):
    items: tuple[TaggedTarget, ...]
    next_cursor: str | None
