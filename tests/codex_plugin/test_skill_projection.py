import hashlib
import json

import pytest

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.skill import SkillContent
from powercontext.client.projections.codex import project_skill


def test_exact_managed_skill_projects_to_a_new_codex_skill_directory(tmp_path) -> None:
    artifact = ArtifactRef(family="skill", artifact_id="skill-123", revision=2)
    content = SkillContent(
        name="powercontext-openapi-change",
        description="Use when changing PowerContext's public HTTP contract.",
        instructions="Regenerate clients, inspect the diff, and run contract tests.",
        validation=("make api-generate-check passes", "make contract-test passes"),
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
            SkillContent(
                name="safe-skill",
                description="Use for a bounded task.",
                instructions="Perform the bounded task.",
                validation=("The expected result exists.",),
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
            SkillContent(
                name=name,
                description=description,
                instructions="Perform the bounded task.",
                validation=("The expected result exists.",),
            ),
            tmp_path / name,
        )
