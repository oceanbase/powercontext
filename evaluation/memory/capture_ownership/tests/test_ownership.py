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

import json

import pytest
from pydantic import ValidationError

from powercontext_eval_capture_ownership.host import current_turn, load_hook, repository_root
from powercontext_eval_capture_ownership.models import Manifest, RunConfig


def test_all_frozen_boundaries_agree_with_owning_parser(manifest):
    hook = load_hook(repository_root())
    for case in manifest.cases:
        turn = current_turn(case.prompt, hook)
        assert turn.status == case.expected_turn, case.case_id
        if turn.status == "eligible":
            assert turn.text(case.prompt) == hook._last_user_query(case.prompt)
            assert turn.text(case.prompt).strip()
        else:
            assert not turn.text(case.prompt).strip()


def test_ground_truth_rejects_incorrect_utf8_offsets(manifest):
    value = manifest.model_dump(mode="json")
    case = next(case for case in value["cases"] if case["case_id"] == "multilingual")
    case["spans"][-2]["byte_end"] -= 1
    with pytest.raises(ValidationError):
        Manifest.model_validate(value)


def test_ground_truth_rejects_historical_positive_control(manifest):
    value = manifest.model_dump(mode="json")
    value["cases"][0]["claims"][0]["required_current"] = True
    with pytest.raises(ValidationError):
        Manifest.model_validate(value)


def test_ground_truth_requires_a_complete_literal_value(manifest):
    value = manifest.model_dump(mode="json")
    value["cases"][0]["claims"][-1]["exact_literals"] = ["eu-west"]
    with pytest.raises(ValidationError, match="required literal is absent"):
        Manifest.model_validate(value)


@pytest.mark.parametrize(
    "changes",
    [
        {"base_url": "https://secret@example.test/v1"},
        {"base_url": "https://example.test/v1?api_key=secret"},
        {"model_settings": {"max_tokens": 1024, "headers": {"Authorization": "secret"}}},
        {"model_settings": {"max_tokens": 1024, "extra_body": {"enable_thinking": True}}},
        {"model_settings": {"temperature": 0}},
    ],
)
def test_frozen_config_excludes_secrets_and_unbounded_outputs(changes):
    with pytest.raises(ValidationError):
        RunConfig(schema_version="capture-ownership.config.v1", **changes)


def test_manifest_covers_required_ownership_classes(manifest):
    owners = {span.owner for case in manifest.cases for span in case.spans}
    assert owners == {"current", "history", "prepared_context", "scaffold", "skill", "quoted", "summary"}
    assert "\\u" not in json.dumps(manifest.model_dump(mode="json"), ensure_ascii=False)
