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

"""Artifact Candidate and Review Inbox contracts."""

from powercontext.builtin.review.errors import (
    ArtifactTargetConflictError,
    CandidateConflictError,
    CandidateError,
    CandidateNotFoundError,
    CandidateTerminalError,
    InvalidCandidateError,
)
from powercontext.builtin.review.models import (
    DEFAULT_CANDIDATE_PAGE_SIZE,
    MAX_CANDIDATE_EVIDENCE,
    MAX_CANDIDATE_PAGE_SIZE,
    MAX_CANDIDATE_REASON_LENGTH,
    ArtifactCandidate,
    ArtifactCandidatePage,
    CandidateStatus,
)

__all__ = [
    "DEFAULT_CANDIDATE_PAGE_SIZE",
    "MAX_CANDIDATE_EVIDENCE",
    "MAX_CANDIDATE_PAGE_SIZE",
    "MAX_CANDIDATE_REASON_LENGTH",
    "ArtifactCandidate",
    "ArtifactCandidatePage",
    "ArtifactTargetConflictError",
    "CandidateConflictError",
    "CandidateError",
    "CandidateNotFoundError",
    "CandidateStatus",
    "CandidateTerminalError",
    "InvalidCandidateError",
]
