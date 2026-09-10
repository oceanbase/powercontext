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

"""Typed Dream requests, persisted runs, and private generation plans."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from powercontext.artifacts import ArtifactRef, MemoryCitation
from powercontext.builtin.artifacts.experience import ExperienceContent
from powercontext.builtin.artifacts.skill import SkillContent
from powercontext.builtin.evidence.models import (
    EvidenceLimits,
    EvidenceManifest,
    content_digest,
    reference_key,
    unique_references,
)
from powercontext.errors import PowerContextError
from powercontext.sources import SourceRef

DreamOperation = Literal["refine_experience", "derive_skill"]
DreamStatus = Literal["queued", "running", "succeeded", "failed"]
DreamOutcome = Literal["proposed", "no_change", "needs_evidence"]
DreamIntent = Literal["create", "corroborate", "refine", "correct", "derive"]
DREAM_PROMPT_VERSION = "powercontext.dream.v1"


class DreamError(PowerContextError, ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class CreateDreamRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    operation: DreamOperation
    artifacts: tuple[ArtifactRef, ...] = ()
    memory_citations: tuple[MemoryCitation, ...] = ()
    sources: tuple[SourceRef, ...] = ()
    target: ArtifactRef | None = None
    idempotency_key: str = Field(min_length=1, max_length=128)

    @field_validator("idempotency_key")
    @classmethod
    def validate_key(cls, value: str) -> str:
        if value != value.strip() or not value:
            raise DreamError("invalid_idempotency_key")
        return value

    @field_validator("artifacts", "memory_citations", "sources")
    @classmethod
    def normalize_references(cls, value):
        return tuple(sorted(unique_references(value), key=reference_key))

    @model_validator(mode="after")
    def validate_selection(self):
        selected = len(self.artifacts) + len(self.memory_citations)
        if not 1 <= selected <= 20 or selected + len(self.sources) > 32:
            raise DreamError("evidence_limit_exceeded")
        if any(ref.family != "experience" for ref in self.artifacts):
            raise DreamError("invalid_artifact_family")
        if any(
            ref.memory_ref.family != "memory"
            or not 1 <= len(ref.entry_id) <= 128
            or not 1 <= len(ref.entry_version_id) <= 128
            for ref in self.memory_citations
        ):
            raise DreamError("invalid_memory_citation")
        if self.target is not None and self.target not in self.artifacts:
            raise DreamError("invalid_target")
        if self.operation == "derive_skill" and (
            self.memory_citations or self.target is not None or not self.artifacts
        ):
            raise DreamError("invalid_dream_operation")
        return self

    def digest(self) -> str:
        return content_digest(self.model_dump_json(exclude={"idempotency_key"}).encode())


class DreamBudget(EvidenceLimits):
    max_output_tokens: int = Field(default=4096, ge=1, le=4096)
    max_model_calls: int = Field(default=2, ge=1, le=2)
    timeout_seconds: float = Field(default=120, gt=0, le=120)


class DreamUsage(BaseModel):
    model_calls: int = Field(default=0, ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)


class DreamCandidateRef(BaseModel):
    candidate_id: str
    version: int = Field(ge=1)


class DreamRun(BaseModel):
    model_config = ConfigDict(frozen=True)

    scope_id: str
    run_id: str
    operation: DreamOperation
    status: DreamStatus = "queued"
    outcome: DreamOutcome | None = None
    target: ArtifactRef | None = None
    candidate: DreamCandidateRef | None = None
    reason: str | None = None
    error: str | None = None
    accepted_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    attempt_count: int = 0
    input_manifest: EvidenceManifest | None = None
    usage: DreamUsage = Field(default_factory=DreamUsage)
    budget: DreamBudget = Field(default_factory=DreamBudget)
    prompt_version: str = DREAM_PROMPT_VERSION
    model_config_id: str | None = None

    @property
    def terminal(self) -> bool:
        return self.status in {"succeeded", "failed"}


class DreamRunPage(BaseModel):
    runs: tuple[DreamRun, ...]
    next_cursor: str | None = None


class ListDreamRunsRequest(BaseModel):
    status: DreamStatus | None = None
    operation: DreamOperation | None = None
    cursor: str | None = None
    limit: int = Field(default=20, ge=1, le=100)


class GetDreamRunRequest(BaseModel):
    run_id: str = Field(min_length=1, max_length=64)


class DreamRecord(BaseModel):
    """Private execution state; a principal identity is never a credential."""

    run: DreamRun
    request: CreateDreamRunRequest
    principal_id: str
    generation: int = 0
    request_generation: int = 0
    deadline_at: datetime | None = None


class DreamPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outcome: DreamOutcome
    reason: str = Field(min_length=1, max_length=2000)
    intent: DreamIntent | None = None
    proposal: ExperienceContent | SkillContent | None = None
    evidence_ids: tuple[str, ...] = Field(default=(), max_length=32)

    @model_validator(mode="after")
    def validate_result(self):
        if not self.reason.strip() or self.reason != self.reason.strip():
            raise DreamError("invalid_generation_output")
        if self.outcome == "proposed":
            if self.proposal is None or self.intent is None or not self.evidence_ids:
                raise DreamError("invalid_generation_output")
        elif self.proposal is not None or self.intent is not None or self.evidence_ids:
            raise DreamError("invalid_generation_output")
        return self

    def validate_operation(self, request: CreateDreamRunRequest) -> None:
        if self.outcome != "proposed":
            return
        if request.operation == "derive_skill":
            valid = (
                isinstance(self.proposal, SkillContent) and self.intent == "derive" and self.proposal.package is None
            )
        else:
            intents = {"create"} if request.target is None else {"corroborate", "refine", "correct"}
            valid = isinstance(self.proposal, ExperienceContent) and self.intent in intents
        if not valid:
            raise DreamError("invalid_generation_output")
