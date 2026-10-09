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

"""Independent Atomic Memory content, state and publication plans."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator

from powercontext.artifacts import Artifact, ArtifactDraft, ArtifactRef
from powercontext.builtin.artifacts.memory.canonical import canonical_error_code, normalize_text


class AtomicMemoryCreation(BaseModel):
    """Select merge inputs from the exact lineage of revision one."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    type: Literal["merge"] = "merge"
    input_artifact_ids: tuple[str, ...] = Field(min_length=2)

    @field_validator("input_artifact_ids")
    @classmethod
    def distinct_inputs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("merge input IDs must be distinct")  # noqa: TRY003
        for artifact_id in value:
            ArtifactRef(family="atomic-memory", artifact_id=artifact_id, revision=1)
        return value


class AtomicMemoryContent(BaseModel):
    """One durable fact, preference or other atomic memory."""

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)
    schema_: Literal["powercontext.atomic-memory.v1"] = Field(default="powercontext.atomic-memory.v1", alias="schema")
    kind: str = Field(min_length=1, max_length=128)
    text: str = Field(min_length=1)
    creation: AtomicMemoryCreation | None = None

    @field_validator("kind")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("memory kind must not be blank")  # noqa: TRY003
        return value

    @field_validator("text")
    @classmethod
    def valid_text(cls, value: str) -> str:
        try:
            normalize_text(value)
        except ValueError as error:
            # Older revisions used the original UTF-8 byte bound. NFC can expand
            # that valid historical text past the current normalized byte bound.
            if canonical_error_code(error) != "text-too-long" or len(value.encode("utf-8")) > 8_192:
                raise
        # This model also decodes immutable revisions. Preserve their exact text;
        # the owning service normalizes new writes before preparing a draft.
        return value

    def without_creation(self) -> AtomicMemoryContent:
        return self.model_copy(update={"creation": None})


class AtomicMemory(Artifact[AtomicMemoryContent]):
    """One immutable content revision."""

    family: ClassVar[str] = "atomic-memory"


class AtomicMemoryDraft(ArtifactDraft[AtomicMemoryContent]):
    """Content and direct evidence for one write."""

    family: ClassVar[str] = "atomic-memory"


class AtomicMemoryStateValue(StrEnum):
    ACTIVE = "active"
    FORGOTTEN = "forgotten"
    MERGED = "merged"
    RETIRED = "retired"


class AtomicMemoryState(BaseModel):
    """Mutable Family state; content revision remains on the common head."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    state: AtomicMemoryStateValue = AtomicMemoryStateValue.ACTIVE
    state_version: StrictInt = Field(default=0, ge=0)
    merged_into_id: str | None = None

    @model_validator(mode="after")
    def validate_merge_target(self) -> AtomicMemoryState:
        if (self.state is AtomicMemoryStateValue.MERGED) != (self.merged_into_id is not None):
            raise ValueError("only merged memories must have a merge target")  # noqa: TRY003
        if self.merged_into_id is not None:
            ArtifactRef(family="atomic-memory", artifact_id=self.merged_into_id, revision=1)
        return self


class AtomicMemoryRead(BaseModel):
    """A current read dependency rechecked after head locks are acquired."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    ref: ArtifactRef
    state: AtomicMemoryStateValue
    state_version: StrictInt = Field(ge=0)
    merged_into_id: str | None = None

    @model_validator(mode="after")
    def atomic_ref(self) -> AtomicMemoryRead:
        if self.ref.family != AtomicMemory.family:
            raise ValueError("Atomic Memory read dependencies require the atomic-memory Family")  # noqa: TRY003
        AtomicMemoryState(state=self.state, state_version=self.state_version, merged_into_id=self.merged_into_id)
        return self


class AtomicMemoryRecord(BaseModel):
    """Exact current content and authoritative Family state."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    artifact: AtomicMemory
    state: AtomicMemoryState

    def as_read(self) -> AtomicMemoryRead:
        return AtomicMemoryRead(ref=self.artifact.as_ref(), **self.state.model_dump())

    @property
    def ref(self) -> ArtifactRef:
        return self.artifact.as_ref()


class AtomicMemoryRestoreItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    artifact_id: str
    source_revision: StrictInt = Field(ge=1)
    creates_revision: bool


class AtomicMemoryRestorationPreview(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    preview_token: str
    expires_at: datetime
    endpoint: AtomicMemoryRead
    restore: tuple[AtomicMemoryRestoreItem, ...]
    retire: tuple[ArtifactRef, ...]
    undo_merge_results: tuple[str, ...]


@dataclass(frozen=True)
class AtomicMemoryWrite:
    """Final state and optional new content for one logical identity."""

    artifact_id: str
    state: AtomicMemoryStateValue
    draft: AtomicMemoryDraft | None = None
    merged_into_id: str | None = None
    current: AtomicMemoryRecord | None = None
    merge_input_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class AtomicMemoryPlan:
    """Database inspection result, held in memory after its read connection closes."""

    scope_id: str
    subject: str
    reads: tuple[AtomicMemoryRead, ...]
    writes: tuple[AtomicMemoryWrite, ...]
    primary_artifact_id: str
    operation: Literal["change", "merge", "forget", "restore", "undo_merge"]
    endpoint: AtomicMemoryRead | None = None
    target_revision: int | None = None
    undo_merge_results: tuple[str, ...] = ()
    restore: tuple[AtomicMemoryRestoreItem, ...] = ()
    preview_token: str | None = None


@dataclass(frozen=True)
class PreparedAtomicMemory:
    """Final plan plus projection payloads prepared without a database connection."""

    plan: AtomicMemoryPlan
    projections: tuple[tuple[str, Any], ...]


class AtomicMemoryMutationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    changed: bool
    records: tuple[AtomicMemoryRecord, ...]
    primary_artifact_id: str
    retired: tuple[ArtifactRef, ...] = ()
    undo_merge_results: tuple[str, ...] = ()

    @property
    def primary(self) -> AtomicMemoryRecord:
        return next(record for record in self.records if record.artifact.artifact_id == self.primary_artifact_id)

    @property
    def restored(self) -> tuple[ArtifactRef, ...]:
        return tuple(record.ref for record in self.records if record.state.state is AtomicMemoryStateValue.ACTIVE)


AtomicMemoryRestoreOperation = Literal["restore", "undo_merge"]
