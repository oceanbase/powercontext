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

import hashlib
import json

import pytest

from powercontext.artifacts import ArtifactRef
from powercontext.client.projections.codex import project_skill
from powercontext.http import SkillProposal, SkillValidationItem


def test_exact_managed_skill_projects_to_a_new_codex_skill_directory(tmp_path) -> None:
    artifact = ArtifactRef(family="skill", artifact_id="skill-123", revision=2)
    content = SkillProposal(
        name="powercontext-openapi-change",
        description="Use when changing PowerContext's public HTTP contract.",
        instructions="Regenerate clients, inspect the diff, and run contract tests.",
        validation=[
            SkillValidationItem("make api-generate-check passes"),
            SkillValidationItem("make contract-test passes"),
        ],
    )
    destination = tmp_path / ".agents" / "skills" / content.name

    projected = project_skill(artifact, content, destination)

    skill_text = (projected / "SKILL.md").read_text(encoding="utf-8")
    manifest = json.loads((projected / "powercontext.json").read_text(encoding="utf-8"))
    assert 'name: "powercontext-openapi-change"' in skill_text
    assert "Generated from artifact:skill/skill-123@2" in skill_text
    assert "- make contract-test passes" in skill_text
    assert manifest == {
        "schema": "powercontext.codex-skill-projection.v1",
        "artifact": {"family": "skill", "artifact_id": "skill-123", "revision": 2},
        "skill_sha256": hashlib.sha256(skill_text.encode()).hexdigest(),
    }


def test_projection_never_overwrites_an_existing_directory(tmp_path) -> None:
    destination = tmp_path / "safe-skill"
    destination.mkdir()

    with pytest.raises(FileExistsError):
        project_skill(
            ArtifactRef(family="skill", artifact_id="skill-123", revision=1),
            SkillProposal(
                name="safe-skill",
                description="Use for a bounded task.",
                instructions="Perform the bounded task.",
                validation=[SkillValidationItem("The expected result exists.")],
            ),
            destination,
        )


@pytest.mark.parametrize(
    ("name", "description"),
    [
        ("Not-Hyphen-Case", "Use for a bounded task."),
        ("safe-skill", "Do <anything> for a bounded task."),
    ],
)
def test_projection_rejects_managed_content_that_is_not_a_valid_codex_skill(
    tmp_path,
    name: str,
    description: str,
) -> None:
    with pytest.raises(ValueError):
        project_skill(
            ArtifactRef(family="skill", artifact_id="skill-123", revision=1),
            SkillProposal(
                name=name,
                description=description,
                instructions="Perform the bounded task.",
                validation=[SkillValidationItem("The expected result exists.")],
            ),
            tmp_path / name,
        )
