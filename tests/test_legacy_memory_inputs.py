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

"""Embedded Python evidence inputs must not silently discard retired citations."""

from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from powercontext.artifacts import ArtifactLineage
from powercontext.builtin.artifacts.experience import Experience, ExperienceContent, ExperienceDraft
from powercontext.builtin.artifacts.skill import SkillDraft
from powercontext.builtin.review import ArtifactCandidate
from powercontext.builtin.runtime import (
    GenerateExperienceRequest,
    GenerateSkillRequest,
    ProposeExperienceRequest,
    ProposeSkillRequest,
    ReviseArtifactCandidateRequest,
)

EXPERIENCE = {"situation": "Situation", "action": "Action", "outcome": "Outcome", "lesson": "Lesson"}
SKILL = {"name": "Skill", "description": "Description", "instructions": "Steps", "validation": ["Check"]}
ARTIFACT = {"family": "atomic-memory", "artifact_id": "note", "revision": 1}
EVIDENCE = {"sources": [{"source_type": "content", "source_id": "source"}], "artifacts": [ARTIFACT]}
ENTRY = {
    "memory_ref": {"family": "memory", "artifact_id": "collection", "revision": 1},
    "entry_id": "entry",
    "entry_version_id": "entry-v1",
}
INPUTS = [
    (ArtifactLineage, {}),
    (ExperienceDraft, {"content": EXPERIENCE}),
    (SkillDraft, {"content": SKILL}),
    (ProposeExperienceRequest, {"proposal": EXPERIENCE}),
    (GenerateExperienceRequest, {}),
    (ProposeSkillRequest, {"proposal": SKILL}),
    (GenerateSkillRequest, {"origin": "source"}),
    (ReviseArtifactCandidateRequest, {"candidate_id": "candidate", "expected_version": 1, "proposal": EXPERIENCE}),
    (
        ArtifactCandidate[ExperienceContent],
        {
            "candidate_id": "candidate",
            "version": 1,
            "family": "experience",
            "status": "pending",
            "proposal": EXPERIENCE,
        },
    ),
]


@pytest.mark.parametrize(("model", "payload"), INPUTS, ids=lambda value: getattr(value, "__name__", None))
@pytest.mark.parametrize("citations", [[ENTRY], [], None], ids=["populated", "empty", "null"])
def test_embedded_evidence_rejects_removed_memory_citations(
    model: type[BaseModel], payload: dict[str, Any], citations: Any
) -> None:
    with pytest.raises(ValidationError, match="memory_citations"):
        model.model_validate({**payload, **EVIDENCE, "memory_citations": citations})


@pytest.mark.parametrize(("model", "payload"), INPUTS, ids=lambda value: getattr(value, "__name__", None))
def test_embedded_evidence_preserves_current_references_and_existing_extra_policy(
    model: type[BaseModel], payload: dict[str, Any]
) -> None:
    value = model.model_validate({**payload, **EVIDENCE, "unrelated_extra": "ignored"})
    serialized = value.model_dump(mode="json")
    assert serialized["sources"] == EVIDENCE["sources"]
    assert serialized["artifacts"] == EVIDENCE["artifacts"]
    assert "unrelated_extra" not in serialized
    assert model.model_validate_json(value.model_dump_json()) == value


def test_artifact_snapshot_rejects_removed_citations_in_nested_lineage() -> None:
    with pytest.raises(ValidationError, match="memory_citations"):
        Experience.model_validate({
            "artifact_id": "experience",
            "revision": 1,
            "content": EXPERIENCE,
            "lineage": {**EVIDENCE, "memory_citations": [ENTRY]},
        })
