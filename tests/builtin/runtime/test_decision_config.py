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
from pydantic import AnyHttpUrl, SecretStr, ValidationError

from powercontext.builtin.runtime import RuntimeConfig
from powercontext.builtin.runtime.config import InferenceConfig


def test_decision_assistance_is_disabled_by_default() -> None:
    assert RuntimeConfig().decision_assistance_enabled is False


def test_decision_inference_defaults_are_unset() -> None:
    config = InferenceConfig()

    assert config.decision_model is None
    assert config.decision_base_url is None
    assert config.decision_headers == {}
    assert config.decision_model_settings == {}
    assert config.decision_timeout_seconds is None
    assert config.decision_max_requests is None


@pytest.mark.parametrize(
    "overrides",
    [
        {"decision_timeout_seconds": 0},
        {"decision_timeout_seconds": -1},
        {"decision_max_requests": 0},
        {"decision_model": "   "},
        {"decision_headers": {"": SecretStr("value")}},
        {"decision_headers": {"X-Test": SecretStr("")}},
        {"decision_model_settings": {"extra_headers": {"X-Test": "value"}}},
    ],
)
def test_invalid_decision_values_are_rejected(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        InferenceConfig(**overrides)


def test_decision_base_url_requires_a_decision_model() -> None:
    with pytest.raises(ValidationError, match="decision_base_url requires decision_model"):
        InferenceConfig(decision_base_url=AnyHttpUrl("http://127.0.0.1:9/v1"))


def test_decision_overrides_require_a_model() -> None:
    with pytest.raises(ValidationError, match="decision overrides require decision_model or generation_model"):
        InferenceConfig(decision_headers={"X-Test": SecretStr("value")})


def test_decision_overrides_may_reuse_the_generation_model() -> None:
    config = InferenceConfig(generation_model="openai:gpt-4.1-mini", decision_headers={"X-Test": SecretStr("value")})

    assert config.decision_model is None
    assert config.decision_headers == {"X-Test": SecretStr("value")}


def test_dedicated_decision_model_accepts_endpoint_overrides() -> None:
    config = InferenceConfig(
        decision_model="openai-chat:decider",
        decision_base_url=AnyHttpUrl("http://127.0.0.1:9/v1"),
        decision_timeout_seconds=5,
        decision_max_requests=2,
    )

    assert config.decision_model == "openai-chat:decider"
    assert config.decision_timeout_seconds == 5
    assert config.decision_max_requests == 2
