import pytest
from pydantic import ValidationError

from powercontext.builtin.artifacts.experience import MAX_EXPERIENCE_FIELD_LENGTH, ExperienceContent

EXPERIENCE_FIELDS = ("situation", "action", "outcome", "lesson")


def _valid_experience() -> dict[str, str]:
    return {
        "situation": "The public OpenAPI contract changed.",
        "action": "Regenerate the checked-in Client and run contract tests.",
        "outcome": "The generated transport remained aligned with the server.",
        "lesson": "Regenerate the Client before validating public contract changes.",
    }


def test_experience_content_preserves_a_complete_reusable_judgment() -> None:
    values = _valid_experience()

    content = ExperienceContent.model_validate(values)

    assert content.model_dump() == values


@pytest.mark.parametrize("field", EXPERIENCE_FIELDS)
def test_experience_content_requires_every_judgment_part(field: str) -> None:
    values = _valid_experience()
    del values[field]

    with pytest.raises(ValidationError):
        ExperienceContent.model_validate(values)


@pytest.mark.parametrize("field", EXPERIENCE_FIELDS)
def test_experience_content_rejects_blank_judgment_parts(field: str) -> None:
    values = _valid_experience()
    values[field] = "\n\t"

    with pytest.raises(ValidationError):
        ExperienceContent.model_validate(values)


@pytest.mark.parametrize("field", EXPERIENCE_FIELDS)
def test_experience_content_bounds_each_judgment_part(field: str) -> None:
    values = _valid_experience()
    values[field] = "x" * (MAX_EXPERIENCE_FIELD_LENGTH + 1)

    with pytest.raises(ValidationError):
        ExperienceContent.model_validate(values)
