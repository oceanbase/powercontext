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

from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from powercontext.builtin.artifacts.experience import (
    MAX_EXPERIENCE_FIELD_LENGTH,
    MAX_FAILURE_CUE_LENGTH,
    ExperienceContent,
    FailureRecord,
    FailureSignature,
    FailureVerification,
)

EXPERIENCE_FIELDS = ("situation", "action", "outcome", "lesson")


def _valid_experience() -> dict[str, str]:
    return {
        "situation": "The public OpenAPI contract changed.",
        "action": "Regenerate the checked-in Client and run contract tests.",
        "outcome": "The generated transport remained aligned with the server.",
        "lesson": "Regenerate the Client before validating public contract changes.",
    }


def _valid_failure(**overrides: object) -> dict[str, Any]:
    failure = {
        "signature": {
            "recall_cue": "OpenAPI contract changed without regenerating the Client",
            "symptom": "Contract tests report a missing generated operation.",
        },
        "repair_surface": "experience_content",
        "verification": {
            "condition": "The OpenAPI contract changed.",
            "check_subject": "Generated code matches the contract",
        },
    }
    failure.update(overrides)
    return failure


def test_experience_content_preserves_a_complete_reusable_judgment() -> None:
    values = _valid_experience()

    content = ExperienceContent.model_validate(values)

    assert content.model_dump() == {**values, "failure": None}


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


def test_experience_content_accepts_an_optional_failure_block() -> None:
    values = {**_valid_experience(), "failure": _valid_failure()}

    content = ExperienceContent.model_validate(values)

    assert content.failure is not None
    assert content.failure.signature.recall_cue == "OpenAPI contract changed without regenerating the Client"
    assert content.failure.repair_surface == "experience_content"
    assert content.failure.verification.check_subject == "Generated code matches the contract"


def test_experience_content_accepts_a_failure_block_without_a_symptom() -> None:
    failure = _valid_failure()
    del failure["signature"]["symptom"]

    content = ExperienceContent.model_validate({**_valid_experience(), "failure": failure})

    assert content.failure is not None
    assert content.failure.signature.symptom is None


def test_experience_content_rejects_a_blank_failure_cue() -> None:
    failure = _valid_failure()
    failure["signature"]["recall_cue"] = "   \n\t"

    with pytest.raises(ValidationError):
        ExperienceContent.model_validate({**_valid_experience(), "failure": failure})


def test_experience_content_bounds_the_failure_cue() -> None:
    failure = _valid_failure()
    failure["signature"]["recall_cue"] = "x" * (MAX_FAILURE_CUE_LENGTH + 1)

    with pytest.raises(ValidationError):
        ExperienceContent.model_validate({**_valid_experience(), "failure": failure})


def test_experience_content_rejects_a_blank_check_subject() -> None:
    failure = _valid_failure()
    failure["verification"]["check_subject"] = "   "

    with pytest.raises(ValidationError):
        ExperienceContent.model_validate({**_valid_experience(), "failure": failure})


@pytest.mark.parametrize("field", ["signature", "repair_surface", "verification"])
def test_failure_record_requires_every_part(field: str) -> None:
    failure = _valid_failure()
    del failure[field]

    with pytest.raises(ValidationError):
        FailureRecord.model_validate(failure)


@pytest.mark.parametrize(
    "surface",
    ["experience_content", "working_state", "recall_policy", "acceptance_check"],
)
def test_failure_record_accepts_every_repair_surface(surface: str) -> None:
    failure = _valid_failure(repair_surface=surface)

    assert FailureRecord.model_validate(failure).repair_surface == surface


def test_failure_record_rejects_an_unknown_repair_surface() -> None:
    with pytest.raises(ValidationError):
        FailureRecord.model_validate(_valid_failure(repair_surface="somewhere_else"))


def test_experience_content_rejects_in_place_field_assignment() -> None:
    content = ExperienceContent.model_validate(_valid_experience())

    with pytest.raises(ValidationError):
        content.lesson = "Rewrite the lesson in place."


def test_failure_signature_rejects_in_place_field_assignment() -> None:
    signature = FailureSignature.model_validate({"recall_cue": "cue"})

    with pytest.raises(ValidationError):
        signature.recall_cue = "changed"


def test_failure_verification_rejects_in_place_field_assignment() -> None:
    verification = FailureVerification.model_validate({"condition": "condition", "check_subject": "check"})

    with pytest.raises(ValidationError):
        verification.check_subject = "changed"


def test_failure_record_rejects_in_place_field_assignment() -> None:
    record = FailureRecord.model_validate(_valid_failure())

    with pytest.raises(ValidationError):
        record.repair_surface = "recall_policy"


@pytest.mark.parametrize(
    ("model", "payload"),
    [
        (FailureSignature, {"recall_cue": "cue", "unexpected": "value"}),
        (FailureVerification, {"condition": "condition", "check_subject": "check", "unexpected": "value"}),
        (FailureRecord, {**_valid_failure(), "unexpected": "value"}),
        (ExperienceContent, {**_valid_experience(), "unexpected": "value"}),
    ],
)
def test_experience_values_reject_unknown_fields(model: type[BaseModel], payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        model.model_validate(payload)


def test_experience_content_without_a_failure_block_round_trips_through_json() -> None:
    stored = ExperienceContent.model_validate(_valid_experience()).model_dump_json()

    assert ExperienceContent.model_validate_json(stored).failure is None
