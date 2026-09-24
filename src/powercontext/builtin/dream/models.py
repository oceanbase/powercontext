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
from powercontext.builtin.artifacts.handoff.models import HandoffContent
from powercontext.builtin.artifacts.memory.models import MemoryDreamWrite
from powercontext.builtin.artifacts.profile.models import ProfilePolicy, ProfileWriteContent
from powercontext.builtin.artifacts.prompt.models import PROMPT_KEYS, PromptContent
from powercontext.builtin.artifacts.skill import SkillContent
from powercontext.builtin.artifacts.topic_memory.models import TopicMemoryContent
from powercontext.builtin.catalog_changes.models import TagChangeProposal, TagDreamTarget
from powercontext.builtin.evidence.models import (
    EvidenceLimits,
    EvidenceManifest,
    content_digest,
    reference_key,
    unique_references,
)
from powercontext.errors import PowerContextError
from powercontext.sources import SourceRef

DreamOperation = Literal[
    "refine_experience",
    "derive_skill",
    "revise_skill",
    "revise_profile",
    "revise_memory",
    "revise_topic_memory",
    "refresh_handoff",
    "revise_prompt",
    "revise_tags",
]
DreamStatus = Literal["queued", "running", "succeeded", "failed"]
DreamOutcome = Literal["proposed", "no_change", "needs_evidence"]
DreamIntent = Literal["create", "corroborate", "refine", "correct", "derive"]
DREAM_PROMPT_VERSION = "powercontext.dream.v1.2"


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
    tag_target: TagDreamTarget | None = None
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
    def validate_selection(self):  # noqa: C901 - bounded validation for each registered operation
        if self.operation == "revise_tags":
            if self.target is not None or self.tag_target is None:
                raise DreamError("invalid_target")
            if not 1 <= len(self.sources) + len(self.artifacts) + len(self.memory_citations) <= 32:
                raise DreamError("evidence_limit_exceeded")
            return self
        if self.tag_target is not None:
            raise DreamError("invalid_target")
        selected = len(self.artifacts) + len(self.memory_citations)
        selected_limit = 21 if self.operation == "revise_memory" else 20
        if not 1 <= selected <= selected_limit or selected + len(self.sources) > 32:
            raise DreamError("evidence_limit_exceeded")
        allowed_families = {
            "refine_experience": {"experience"},
            "derive_skill": {"experience"},
            "revise_skill": {"experience", "skill"},
            "revise_profile": {"experience", "profile"},
            "revise_memory": {"experience", "memory"},
            "revise_topic_memory": {"experience", "topic-memory"},
            "refresh_handoff": {"experience", "handoff"},
            "revise_prompt": {"experience", "prompt"},
        }[self.operation]
        if any(ref.family not in allowed_families for ref in self.artifacts):
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
        if self.operation == "revise_skill" and (
            self.target is None
            or self.target.family != "skill"
            or any(ref.family == "skill" and ref != self.target for ref in self.artifacts)
            or not (self.sources or self.memory_citations or any(ref != self.target for ref in self.artifacts))
        ):
            raise DreamError("invalid_dream_operation")
        if self.operation == "revise_profile" and (
            self.target is None
            or self.target.family != "profile"
            or any(ref.family == "profile" and ref != self.target for ref in self.artifacts)
        ):
            raise DreamError("invalid_dream_operation")
        if self.operation == "revise_memory" and (
            self.target is None
            or self.target.family != "memory"
            or not self.memory_citations
            or any(citation.memory_ref != self.target for citation in self.memory_citations)
            or any(ref.family == "memory" and ref != self.target for ref in self.artifacts)
        ):
            raise DreamError("invalid_dream_operation")
        if self.operation == "revise_topic_memory" and (
            self.target is None
            or self.target.family != "topic-memory"
            or any(ref.family == "topic-memory" and ref != self.target for ref in self.artifacts)
        ):
            raise DreamError("invalid_dream_operation")
        if self.operation == "refresh_handoff" and (
            self.target is None
            or self.target.family != "handoff"
            or any(ref.family == "handoff" and ref != self.target for ref in self.artifacts)
        ):
            raise DreamError("invalid_dream_operation")
        if self.operation == "revise_prompt" and (
            self.target is None
            or self.target.family != "prompt"
            or self.target.artifact_id not in PROMPT_KEYS
            or any(ref.family == "prompt" and ref != self.target for ref in self.artifacts)
            or not self.sources
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
    kind: Literal["artifact", "tag"] = "artifact"
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
    tag_target: TagDreamTarget | None = None
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
    reused: bool = False

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
    profile_policy: ProfilePolicy | None = None
    proposal_fingerprint: str | None = None


class DreamPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outcome: DreamOutcome
    reason: str = Field(min_length=1, max_length=2000)
    intent: DreamIntent | None = None
    proposal: (
        ExperienceContent
        | SkillContent
        | ProfileWriteContent
        | MemoryDreamWrite
        | TopicMemoryContent
        | HandoffContent
        | PromptContent
        | TagChangeProposal
        | None
    ) = None
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

    def validate_operation(self, request: CreateDreamRunRequest) -> None:  # noqa: C901 - explicit operation contracts
        if self.outcome != "proposed":
            return
        if request.operation == "derive_skill":
            valid = (
                isinstance(self.proposal, SkillContent) and self.intent == "derive" and self.proposal.package is None
            )
        elif request.operation == "revise_skill":
            valid = (
                isinstance(self.proposal, SkillContent) and self.intent == "correct" and self.proposal.package is None
            )
        elif request.operation == "revise_profile":
            valid = (
                isinstance(self.proposal, ProfileWriteContent)
                and self.proposal.restored_from_revision is None
                and self.intent == "correct"
            )
        elif request.operation == "revise_memory":
            selected = {(citation.entry_id, citation.entry_version_id) for citation in request.memory_citations}
            valid = (
                isinstance(self.proposal, MemoryDreamWrite)
                and self.intent == "correct"
                and all((change.entry_id, change.entry_version_id) in selected for change in self.proposal.changes)
            )
        elif request.operation == "revise_topic_memory":
            valid = isinstance(self.proposal, TopicMemoryContent) and self.intent == "correct"
        elif request.operation == "refresh_handoff":
            valid = (
                isinstance(self.proposal, HandoffContent)
                and self.proposal.generation is None
                and self.intent == "correct"
            )
        elif request.operation == "revise_prompt":
            valid = (
                isinstance(self.proposal, PromptContent) and self.proposal.mode == "custom" and self.intent == "correct"
            )
        elif request.operation == "revise_tags":
            valid = isinstance(self.proposal, TagChangeProposal) and self.intent == "correct"
        else:
            intents = {"create"} if request.target is None else {"corroborate", "refine", "correct"}
            valid = isinstance(self.proposal, ExperienceContent) and self.intent in intents
        if not valid:
            raise DreamError("invalid_generation_output")
