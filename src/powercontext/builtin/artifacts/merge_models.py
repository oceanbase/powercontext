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

"""In-memory plans for shared Artifact merge and whole-group restoration."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt

from powercontext.artifacts import Artifact, ArtifactDraft, ArtifactRef
from powercontext.builtin.persistence.artifact_governance import ArtifactGovernance, ArtifactLifecycleState
from powercontext.errors import PowerContextError


class ArtifactMergeConflictError(PowerContextError):
    code = "artifact_changed"


class ArtifactMergeRelationError(PowerContextError):
    code = "invalid_merge_relation"


class InvalidArtifactMergeStateError(PowerContextError, ValueError):
    code = "invalid_artifact_state"


class InvalidArtifactMergePreviewError(PowerContextError, ValueError):
    code = "invalid_preview"


class ArtifactMergePreviewExpiredError(PowerContextError):
    code = "preview_expired"


class ArtifactMergePreviewStaleError(ArtifactMergeConflictError):
    code = "preview_stale"


@dataclass(frozen=True)
class ArtifactMergeErrors:
    conflict: type[Exception] = ArtifactMergeConflictError
    relation: type[Exception] = ArtifactMergeRelationError
    invalid_state: type[Exception] = InvalidArtifactMergeStateError
    invalid_preview: type[Exception] = InvalidArtifactMergePreviewError
    preview_expired: type[Exception] = ArtifactMergePreviewExpiredError
    preview_stale: type[Exception] = ArtifactMergePreviewStaleError


class ArtifactMergeRead(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    ref: ArtifactRef
    lifecycle_state: ArtifactLifecycleState
    state_version: StrictInt = Field(ge=0)
    merged_into_id: str | None = None
    replacement_artifact_id: str | None = None


@dataclass(frozen=True)
class ArtifactMergeRecord:
    artifact: Artifact[Any]
    state: ArtifactGovernance

    def as_read(self) -> ArtifactMergeRead:
        return ArtifactMergeRead(
            ref=self.ref,
            lifecycle_state=self.state.lifecycle_state,
            state_version=self.state.governance_generation,
            merged_into_id=self.state.merged_into_id,
            replacement_artifact_id=self.state.replacement_artifact_id,
        )

    @property
    def ref(self) -> ArtifactRef:
        return self.artifact.as_ref()


class ArtifactMergeRestoreItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    artifact_id: str
    source_revision: StrictInt = Field(ge=1)
    creates_revision: bool


class ArtifactMergeRestorationPreview(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    preview_token: str
    expires_at: datetime
    endpoint: ArtifactMergeRead
    restore: tuple[ArtifactMergeRestoreItem, ...]
    retire: tuple[ArtifactRef, ...]
    undo_merge_results: tuple[str, ...]


@dataclass(frozen=True)
class ArtifactMergeWrite:
    artifact_id: str
    state: ArtifactLifecycleState
    draft: ArtifactDraft[Any] | None = None
    merged_into_id: str | None = None
    current: ArtifactMergeRecord | None = None
    merge_input_ids: tuple[str, ...] = ()
    replacement_artifact_id: str | None = None


@dataclass(frozen=True)
class ArtifactMergePlan:
    scope_id: str
    subject: str
    reads: tuple[ArtifactMergeRead, ...]
    writes: tuple[ArtifactMergeWrite, ...]
    primary_artifact_id: str
    operation: Literal["change", "merge", "forget", "restore", "undo_merge"]
    endpoint: ArtifactMergeRead | None = None
    target_revision: int | None = None
    undo_merge_results: tuple[str, ...] = ()
    restore: tuple[ArtifactMergeRestoreItem, ...] = ()
    preview_token: str | None = None


@dataclass(frozen=True)
class PreparedArtifactMerge:
    plan: ArtifactMergePlan
    projections: tuple[tuple[str, Any], ...]


@dataclass(frozen=True)
class ArtifactMergeMutationResult:
    changed: bool
    records: tuple[ArtifactMergeRecord, ...]
    primary_artifact_id: str
    retired: tuple[ArtifactRef, ...] = ()
    undo_merge_results: tuple[str, ...] = ()

    @property
    def primary(self) -> ArtifactMergeRecord:
        return next(record for record in self.records if record.ref.artifact_id == self.primary_artifact_id)

    @property
    def restored(self) -> tuple[ArtifactRef, ...]:
        return tuple(
            record.ref for record in self.records if record.state.lifecycle_state is ArtifactLifecycleState.ACTIVE
        )


ArtifactMergeRestoreOperation = Literal["restore", "undo_merge"]
