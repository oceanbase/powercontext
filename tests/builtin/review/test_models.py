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

import pytest
from pydantic import ValidationError

from powercontext.builtin.artifacts.experience import ExperienceContent
from powercontext.builtin.review import ArtifactCandidate, CandidateStatus
from powercontext.sources import SourceRef


def _proposal() -> ExperienceContent:
    return ExperienceContent(situation="situation", action="action", outcome="outcome", lesson="lesson")


def test_candidate_requires_bounded_exact_evidence() -> None:
    with pytest.raises(ValidationError):
        ArtifactCandidate(
            candidate_id="candidate-1",
            version=1,
            family="experience",
            status=CandidateStatus.PENDING,
            proposal=_proposal(),
        )

    with pytest.raises(ValidationError):
        ArtifactCandidate(
            candidate_id="candidate-1",
            version=1,
            family="experience",
            status=CandidateStatus.PENDING,
            proposal=_proposal(),
            sources=tuple(SourceRef(source_type="content", source_id=f"source-{index}") for index in range(33)),
        )


def test_candidate_terminal_fields_match_status() -> None:
    with pytest.raises(ValidationError):
        ArtifactCandidate(
            candidate_id="candidate-1",
            version=1,
            family="experience",
            status=CandidateStatus.REJECTED,
            proposal=_proposal(),
            sources=(SourceRef(source_type="content", source_id="source-1"),),
        )
