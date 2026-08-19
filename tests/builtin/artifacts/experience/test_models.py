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
