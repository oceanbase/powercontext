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

from __future__ import annotations

from typing import Any

import pytest

from examples.systemone.laya import LayaInputBudget
from powercontext.builtin.inference import InferenceConfigurationError


def _tokenize(text: str) -> tuple[int, ...]:
    return tuple(map(ord, text))


def _budget(**overrides: Any) -> LayaInputBudget:
    values: dict[str, Any] = {
        "tokenize": _tokenize,
        "max_length": 200,
        "head_max_length": 100,
        "mask_token": "[MASK]",
    }
    return LayaInputBudget(**(values | overrides))


def test_state_fills_the_remaining_sequence_without_truncation() -> None:
    # 19 head tokens, 8 choice tokens including the marker, four special tokens.
    budget = _budget(max_length=41)
    budget.validate("证" * 10, "ok", {"yes": "a"})

    with pytest.raises(InferenceConfigurationError, match="sequence budget"):
        budget.validate("证" * 11, "ok", {"yes": "a"})


def test_instructions_fit_the_head_but_reject_one_token_over() -> None:
    budget = _budget(head_max_length=27)
    budget.validate("", "ok", {"yes": "a"})

    with pytest.raises(InferenceConfigurationError, match="instructions"):
        budget.validate("", "ok!", {"yes": "a"})


def test_choice_text_cannot_exceed_its_individual_limit() -> None:
    budget = _budget()
    budget.validate("", "ok", {"yes": "a" * 42})

    with pytest.raises(InferenceConfigurationError, match="48-token"):
        budget.validate("", "ok", {"yes": "a" * 43})


def test_combined_choices_cannot_trigger_secondary_head_clipping() -> None:
    with pytest.raises(InferenceConfigurationError, match=r"choices.*head budget"):
        _budget(head_max_length=30).validate("", "ok", {"yes": "a" * 9, "no": "b" * 9})


def test_empty_choice_description_uses_the_bare_label() -> None:
    _budget(max_length=29).validate("x", "ok", {"yes": ""})


@pytest.mark.parametrize("field", ["state", "instructions", "criterion", "label"])
def test_literal_mask_token_is_rejected_before_server_replacement(field: str) -> None:
    state, instructions, criteria = "state", "ok", {"yes": "a"}
    if field == "state":
        state = "private [MASK] state"
    elif field == "instructions":
        instructions = "private [MASK] instructions"
    elif field == "criterion":
        criteria = {"yes": "private [MASK] criterion"}
    else:
        criteria = {"private [MASK] label": "a"}

    with pytest.raises(InferenceConfigurationError, match="mask token") as caught:
        _budget().validate(state, instructions, criteria)

    assert "private" not in str(caught.value)


@pytest.mark.parametrize(
    "overrides",
    [
        {"max_length": 0},
        {"max_length": True},
        {"head_max_length": -1},
        {"head_max_length": 1.5},
        {"mask_token": ""},
        {"tokenize": None},
    ],
)
def test_invalid_checkpoint_settings_are_rejected(overrides: dict[str, Any]) -> None:
    with pytest.raises(InferenceConfigurationError):
        _budget(**overrides)


def test_tokenizer_failure_does_not_expose_input_content() -> None:
    def failing_tokenizer(text: str) -> tuple[int, ...]:
        raise ValueError(f"private input: {text}")  # noqa: TRY003

    with pytest.raises(InferenceConfigurationError, match="could not tokenize") as caught:
        _budget(tokenize=failing_tokenizer).validate("private state", "ok", {"yes": "a"})

    assert "private" not in str(caught.value)


def test_empty_choices_are_rejected() -> None:
    with pytest.raises(InferenceConfigurationError, match="choices must not be empty"):
        _budget().validate("", "ok", {})
