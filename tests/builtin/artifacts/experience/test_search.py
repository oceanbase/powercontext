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

from powercontext.builtin.artifacts.experience import ExperienceContent
from powercontext.builtin.artifacts.experience.search import (
    experience_search_text,
    experience_searchable_text,
    render_experience,
)

CUE = "OpenAPI contract changed without regenerating the Client"
SYMPTOM = "Contract tests report a missing generated operation."


def _valid_experience() -> dict[str, str]:
    return {
        "situation": "The public OpenAPI contract changed.",
        "action": "Regenerate the checked-in Client and run contract tests.",
        "outcome": "The generated transport remained aligned with the server.",
        "lesson": "Regenerate the Client before validating public contract changes.",
    }


def _failure_payload(*, with_symptom: bool = True) -> dict[str, object]:
    signature: dict[str, str] = {"recall_cue": CUE}
    if with_symptom:
        signature["symptom"] = SYMPTOM
    return {
        "signature": signature,
        "repair_surface": "experience_content",
        "verification": {
            "condition": "The OpenAPI contract changed.",
            "check_subject": "Generated code matches the contract",
        },
    }


def _content(*, with_failure: bool = True, with_symptom: bool = True) -> ExperienceContent:
    payload: dict[str, object] = dict(_valid_experience())
    if with_failure:
        payload["failure"] = _failure_payload(with_symptom=with_symptom)
    return ExperienceContent.model_validate(payload)


def test_search_text_matches_the_failure_cue() -> None:
    text = experience_search_text(_content())

    assert CUE in text
    assert SYMPTOM in text


def test_searchable_text_exposes_the_cue_to_lexical_search() -> None:
    assert "regenerating" in experience_searchable_text(_content())


def test_search_text_without_a_failure_block_only_projects_the_judgment() -> None:
    assert experience_search_text(_content(with_failure=False)) == "\n".join(_valid_experience().values())


def test_render_experience_includes_the_cue_and_symptom() -> None:
    rendered = render_experience(_content())

    assert f"Failure cue: {CUE}" in rendered
    assert f"Symptom: {SYMPTOM}" in rendered
    assert "Repair surface: experience_content" in rendered
    assert "Verification condition: The OpenAPI contract changed." in rendered
    assert "Verification check: Generated code matches the contract" in rendered


def test_render_experience_without_a_failure_block_is_unchanged() -> None:
    assert render_experience(_content(with_failure=False)) == (
        "Situation: The public OpenAPI contract changed.\n"
        "Action: Regenerate the checked-in Client and run contract tests.\n"
        "Outcome: The generated transport remained aligned with the server.\n"
        "Lesson: Regenerate the Client before validating public contract changes."
    )


def test_render_experience_omits_a_missing_symptom_line() -> None:
    rendered = render_experience(_content(with_symptom=False))

    assert f"Failure cue: {CUE}" in rendered
    assert "Symptom:" not in rendered
    assert rendered.splitlines()[-1] == "Verification check: Generated code matches the contract"
