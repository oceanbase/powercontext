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
