"""Typed failures for Candidate and Review lifecycle operations."""

from __future__ import annotations

from powercontext.artifacts import ArtifactRef
from powercontext.errors import PowerContextError


class CandidateError(PowerContextError):
    """Base failure for Candidate lifecycle operations."""


class CandidateNotFoundError(CandidateError, LookupError):
    def __init__(self, candidate_id: str) -> None:
        self.candidate_id = candidate_id
        super().__init__("Candidate was not found")


class CandidateConflictError(CandidateError, RuntimeError):
    def __init__(self, candidate_id: str, expected_version: int, current_version: int) -> None:
        self.candidate_id = candidate_id
        self.expected_version = expected_version
        self.current_version = current_version
        super().__init__("Candidate version is stale")


class CandidateTerminalError(CandidateError, RuntimeError):
    def __init__(self, candidate_id: str, status: str) -> None:
        self.candidate_id = candidate_id
        self.status = status
        super().__init__("Candidate is already terminal")


class InvalidCandidateError(CandidateError, ValueError):
    def __init__(self, field: str, detail: str) -> None:
        self.field = field
        self.detail = detail
        super().__init__(f"invalid Candidate {field}: {detail}")


class ArtifactTargetConflictError(CandidateError, RuntimeError):
    def __init__(self, target: ArtifactRef, current: ArtifactRef) -> None:
        self.target = target
        self.current = current
        super().__init__("Candidate target is not the current Artifact head")


__all__ = [
    "ArtifactTargetConflictError",
    "CandidateConflictError",
    "CandidateError",
    "CandidateNotFoundError",
    "CandidateTerminalError",
    "InvalidCandidateError",
]
