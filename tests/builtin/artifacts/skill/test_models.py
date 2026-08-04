import pytest
from pydantic import ValidationError

from powercontext.builtin.artifacts.skill import SkillContent


def test_skill_content_requires_complete_portable_instructions() -> None:
    content = SkillContent(
        name="powercontext-openapi-change",
        description="Use when changing PowerContext's public HTTP contract.",
        instructions="Regenerate checked-in clients, inspect the diff, and run contract tests.",
        validation=("make api-generate-check passes", "make contract-test passes"),
    )

    assert content.validation == ("make api-generate-check passes", "make contract-test passes")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("name", " "),
        ("description", " trailing "),
        ("instructions", "\n\t"),
        ("validation", ("",)),
    ],
)
def test_skill_content_rejects_incomplete_or_ambiguous_text(field: str, value: object) -> None:
    payload = {
        "name": "powercontext-openapi-change",
        "description": "Use when changing PowerContext's public HTTP contract.",
        "instructions": "Regenerate clients and run contract tests.",
        "validation": ("make contract-test passes",),
    }
    payload[field] = value

    with pytest.raises(ValidationError):
        SkillContent.model_validate(payload)
