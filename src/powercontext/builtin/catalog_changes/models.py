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

"""Typed Tag proposals separate from immutable Artifact candidates."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from powercontext.artifacts import ArtifactRef, MemoryCitation
from powercontext.builtin.evidence.models import ResolvedEvidence
from powercontext.builtin.review.models import CandidateAudit
from powercontext.builtin.tags import ArtifactTagSet, ArtifactTagTarget, TagTarget, normalize_tags
from powercontext.sources import SourceRef


class CatalogValue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TagDreamTarget(CatalogValue):
    target: TagTarget
    expected_etag: str = Field(min_length=1, max_length=256)
    basis_ref: ArtifactRef | None = None
    basis_citation: MemoryCitation | None = None

    @model_validator(mode="after")
    def validate_basis(self):
        if isinstance(self.target, ArtifactTagTarget):
            if self.basis_ref is None or self.basis_citation is not None:
                raise ValueError("Artifact tags require an exact Artifact basis")  # noqa: TRY003
            ref = self.basis_ref
        else:
            if self.basis_citation is None or self.basis_ref is not None:
                raise ValueError("Memory entry tags require an exact entry basis")  # noqa: TRY003
            if self.basis_citation.entry_id != self.target.entry_id:
                raise ValueError("Tag target and entry basis must match")  # noqa: TRY003
            ref = self.basis_citation.memory_ref
        if (ref.family, ref.artifact_id) != (self.target.family, self.target.artifact_id):
            raise ValueError("Tag target and content basis must match")  # noqa: TRY003
        return self


class TagChangeProposal(CatalogValue):
    after_tags: tuple[str, ...] = Field(max_length=32)

    @field_validator("after_tags")
    @classmethod
    def valid_tags(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(normalize_tags(value).values())


class CatalogChangeProposal(TagDreamTarget, TagChangeProposal):
    before_tags: tuple[str, ...] = Field(max_length=32)

    @field_validator("before_tags")
    @classmethod
    def valid_before_tags(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(normalize_tags(value).values())


class CatalogChangeCandidate(CatalogValue):
    candidate_id: str = Field(min_length=1, max_length=128)
    version: int = Field(ge=1)
    status: Literal["pending", "approved", "rejected"] = "pending"
    operation: Literal["revise_tags"] = "revise_tags"
    origin: Literal["dream", "manual"] = "dream"
    proposal: CatalogChangeProposal
    sources: tuple[SourceRef, ...] = Field(default=(), max_length=32)
    artifacts: tuple[ArtifactRef, ...] = Field(default=(), max_length=32)
    memory_citations: tuple[MemoryCitation, ...] = Field(default=(), max_length=32)
    reason: str = Field(min_length=1, max_length=2000)
    dream_run_id: str | None = None
    audit: CandidateAudit | None = None
    result: ArtifactTagSet | None = None
    decision_reason: str | None = Field(default=None, min_length=1, max_length=2000)

    @model_validator(mode="after")
    def valid_state(self):
        if not 1 <= len(self.sources) + len(self.artifacts) + len(self.memory_citations) <= 32:
            raise ValueError("Catalog candidates require 1 to 32 evidence references")  # noqa: TRY003
        if (self.status == "approved") != (self.result is not None):
            raise ValueError("Only an approved Catalog candidate has a tag result")  # noqa: TRY003
        if (self.status == "rejected") != (self.decision_reason is not None):
            raise ValueError("Only a rejected Catalog candidate has a rejection reason")  # noqa: TRY003
        if (self.origin == "dream") != (self.dream_run_id is not None):
            raise ValueError("Dream Catalog candidates identify their run")  # noqa: TRY003
        return self


class CatalogCandidatePage(CatalogValue):
    candidates: tuple[CatalogChangeCandidate, ...]
    next_cursor: str | None = None


class CatalogCandidateEvidence(CatalogValue):
    candidate_id: str
    version: int
    resolved: ResolvedEvidence | None = None
    unavailable: str | None = None
