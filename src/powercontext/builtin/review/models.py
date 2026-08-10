"""Family-neutral Candidate and Review Inbox domain values."""

from __future__ import annotations

from enum import StrEnum
from typing import Generic, TypeVar

from pydantic import BaseModel, Field, StrictInt, field_validator, model_validator

from powercontext.artifacts import ArtifactRef
from powercontext.limits import MAX_ARTIFACT_ID_LENGTH
from powercontext.sources import SourceRef

MAX_CANDIDATE_EVIDENCE = 32
MAX_CANDIDATE_PAGE_SIZE = 100
DEFAULT_CANDIDATE_PAGE_SIZE = 50
MAX_CANDIDATE_REASON_LENGTH = 2_000

ProposalT = TypeVar("ProposalT", bound=BaseModel)


class CandidateStatus(StrEnum):
    """The complete first-version Candidate lifecycle."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class ArtifactCandidate(BaseModel, Generic[ProposalT]):
    """One current Candidate head with its immutable proposal version."""

    candidate_id: str = Field(min_length=1, max_length=MAX_ARTIFACT_ID_LENGTH)
    version: StrictInt = Field(ge=1)
    family: str
    status: CandidateStatus
    proposal: ProposalT
    sources: tuple[SourceRef, ...] = Field(default=(), max_length=MAX_CANDIDATE_EVIDENCE)
    artifacts: tuple[ArtifactRef, ...] = Field(default=(), max_length=MAX_CANDIDATE_EVIDENCE)
    target: ArtifactRef | None = None
    reason: str | None = Field(default=None, min_length=1, max_length=MAX_CANDIDATE_REASON_LENGTH)
    result_artifact: ArtifactRef | None = None
    decision_reason: str | None = Field(default=None, min_length=1, max_length=MAX_CANDIDATE_REASON_LENGTH)

    @field_validator("candidate_id", "family", "reason", "decision_reason")
    @classmethod
    def reject_blank_text(cls, value: str | None) -> str | None:
        if value is not None and (not value.strip() or value != value.strip()):
            raise ValueError("Candidate text values must be non-empty and trimmed")  # noqa: TRY003
        return value

    @model_validator(mode="after")
    def validate_lifecycle(self):
        if not self.sources and not self.artifacts:
            raise ValueError("Candidate evidence must include a Source or Artifact reference")  # noqa: TRY003
        if len(self.sources) + len(self.artifacts) > MAX_CANDIDATE_EVIDENCE:
            raise ValueError(f"Candidate evidence must not exceed {MAX_CANDIDATE_EVIDENCE} references")  # noqa: TRY003
        if self.target is not None and self.target.family != self.family:
            raise ValueError("Candidate target must belong to the proposed family")  # noqa: TRY003
        if self.status is CandidateStatus.APPROVED:
            if self.result_artifact is None:
                raise ValueError("approved Candidate must identify its result Artifact")  # noqa: TRY003
        elif self.result_artifact is not None:
            raise ValueError("only an approved Candidate may identify a result Artifact")  # noqa: TRY003
        if self.status is CandidateStatus.REJECTED:
            if self.decision_reason is None:
                raise ValueError("rejected Candidate must include a decision reason")  # noqa: TRY003
        elif self.decision_reason is not None:
            raise ValueError("only a rejected Candidate may include a decision reason")  # noqa: TRY003
        return self


class ArtifactCandidatePage(BaseModel, Generic[ProposalT]):
    """A stable, cursor-based Review Inbox page."""

    candidates: tuple[ArtifactCandidate[ProposalT], ...]
    next_cursor: str | None = None


__all__ = [
    "DEFAULT_CANDIDATE_PAGE_SIZE",
    "MAX_CANDIDATE_EVIDENCE",
    "MAX_CANDIDATE_PAGE_SIZE",
    "MAX_CANDIDATE_REASON_LENGTH",
    "ArtifactCandidate",
    "ArtifactCandidatePage",
    "CandidateStatus",
]
