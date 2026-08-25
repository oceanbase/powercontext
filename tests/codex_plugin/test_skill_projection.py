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
from powercontext.client.projections.codex import (
    CodexSkillProjectionConflictError,
    CodexSkillProjectionState,
    inspect_skill_projection,
    project_skill,
    publish_skill_projection,
)
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
        "schema": "powercontext.agent-skill-projection.v1",
        "agent_kind": "codex",
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


def test_managed_projection_can_be_inspected_and_safely_updated(tmp_path) -> None:
    root = tmp_path / ".agents" / "skills"
    first = ArtifactRef(family="skill", artifact_id="skill-123", revision=1)
    second = ArtifactRef(family="skill", artifact_id="skill-123", revision=2)
    original = SkillProposal(
        name="safe-skill",
        description="Use for a bounded task.",
        instructions="Perform the bounded task.",
        validation=[SkillValidationItem("The expected result exists.")],
    )
    updated = original.model_copy(
        update={"name": "safe-skill-v2", "instructions": "Perform the bounded task and inspect the result."}
    )

    unpublished = inspect_skill_projection(first, original, root)
    published = publish_skill_projection(first, original, root, expected=unpublished)
    update_available = inspect_skill_projection(second, updated, root)
    current = publish_skill_projection(second, updated, root, expected=update_available)

    assert unpublished.state is CodexSkillProjectionState.UNPUBLISHED
    assert published.state is CodexSkillProjectionState.CURRENT
    assert update_available.state is CodexSkillProjectionState.UPDATE_AVAILABLE
    assert update_available.published_artifact == first
    assert current.state is CodexSkillProjectionState.CURRENT
    assert current.published_artifact == second
    assert not root.joinpath("safe-skill").exists()
    assert "inspect the result" in (current.destination / "SKILL.md").read_text(encoding="utf-8")


def test_managed_projection_refuses_to_replace_modified_or_foreign_content(tmp_path) -> None:
    root = tmp_path / ".agents" / "skills"
    artifact = ArtifactRef(family="skill", artifact_id="skill-123", revision=1)
    content = SkillProposal(
        name="safe-skill",
        description="Use for a bounded task.",
        instructions="Perform the bounded task.",
        validation=[SkillValidationItem("The expected result exists.")],
    )
    published = publish_skill_projection(artifact, content, root)
    (published.destination / "SKILL.md").write_text("locally edited\n", encoding="utf-8")

    drifted = inspect_skill_projection(artifact, content, root)

    assert drifted.state is CodexSkillProjectionState.DRIFTED
    with pytest.raises(CodexSkillProjectionConflictError):
        publish_skill_projection(artifact, content, root)

    foreign = content.model_copy(update={"name": "foreign-skill"})
    foreign_destination = root / foreign.name
    foreign_destination.mkdir()
    assert (
        inspect_skill_projection(
            ArtifactRef(family="skill", artifact_id="skill-456", revision=1),
            foreign,
            root,
        ).state
        is CodexSkillProjectionState.CONFLICT
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


def test_exact_managed_skill_projects_to_claude_code(tmp_path) -> None:
    from powercontext.client.projections.claude_code import project_skill as project_claude_code_skill

    content = SkillProposal(
        name="review-change",
        description="Use <carefully> when reviewing a bounded change.",
        instructions="Review the change and report concrete findings.",
        validation=[SkillValidationItem("The findings cite exact files.")],
    )

    projected = project_claude_code_skill(
        ArtifactRef(family="skill", artifact_id="skill-claude", revision=1),
        content,
        tmp_path / ".claude" / "skills" / content.name,
    )

    manifest = json.loads((projected / "powercontext.json").read_text(encoding="utf-8"))
    assert projected == tmp_path / ".claude" / "skills" / "review-change"
    assert manifest["agent_kind"] == "claude_code"
    assert manifest["schema"] == "powercontext.agent-skill-projection.v1"
