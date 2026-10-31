# Copyright (c) 2026 OceanBase.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
# http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Durable facts about one Atomic Memory Source-window attempt."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from powercontext.artifacts import ArtifactRef
from powercontext.sources import SourceRef


class MemoryProcessingDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    proposal_id: str
    action: Literal["create", "revise", "merge", "noop"]
    target_ids: tuple[str, ...] = ()
    source_refs: tuple[SourceRef, ...] = ()
    reason: str
    reason_truncated: bool = False


class MemoryProcessingResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    action: Literal["create", "revise", "merge"]
    artifact: ArtifactRef
    source_refs: tuple[SourceRef, ...] = ()


class MemoryProcessingRecord(BaseModel):
    """Started means completion is unconfirmed; it is never a liveness claim."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    attempt_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    scope_id: str = Field(min_length=1, max_length=256)
    previous_attempt_id: str | None = None
    source_after: int = Field(ge=0)
    source_through: int = Field(ge=1)
    sources: tuple[SourceRef, ...]
    eligible_sources: tuple[SourceRef, ...]
    started_at: datetime
    completed_at: datetime | None = None
    status: Literal["started", "succeeded", "failed", "cancelled", "conflict"] = "started"
    outcome: Literal["changed", "no_change", "no_candidates", "held", "ineligible"] | None = None
    stage: str = "select"
    error_code: str | None = None
    decisions: tuple[MemoryProcessingDecision, ...] = ()
    decisions_truncated: bool = False
    results: tuple[MemoryProcessingResult, ...] = ()
    retained_artifacts: tuple[ArtifactRef, ...] = ()
    read_artifacts: tuple[ArtifactRef, ...] = Field(default=(), exclude=True)


MAX_PROCESSING_DECISIONS = 100
MAX_PROCESSING_REASON_CHARS = 2048
