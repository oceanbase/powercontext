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
