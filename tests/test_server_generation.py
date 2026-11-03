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

import pytest
from pydantic import ValidationError

from powercontext.builtin.runtime import InferenceConfig


def test_generation_model_settings_require_generation_model() -> None:
    with pytest.raises(ValidationError, match="generation_model_settings requires generation_model"):
        InferenceConfig(
            generation_model_settings={
                "extra_body": {"chat_template_kwargs": {"enable_thinking": False}},
            }
        )


def test_generation_model_settings_reject_static_headers() -> None:
    with pytest.raises(ValidationError, match="configure credentials and static headers"):
        InferenceConfig(
            generation_model="provider:model",
            generation_model_settings={"extra_headers": {"Authorization": "Bearer secret"}},
        )


def test_decision_overrides_require_decision_model() -> None:
    with pytest.raises(ValidationError, match="decision overrides require decision_model"):
        InferenceConfig(
            decision_model_settings={
                "temperature": 0,
            }
        )


def test_decision_base_url_requires_decision_model() -> None:
    with pytest.raises(ValidationError, match="decision_base_url requires decision_model"):
        InferenceConfig.model_validate({"decision_base_url": "https://decision.example/v1"})


def test_prefix_extraction_requires_normal_extraction_as_its_fallback() -> None:
    with pytest.raises(ValidationError, match="prefix extraction requires generation_model"):
        InferenceConfig.model_validate({"prefix_extraction_gateway_url": "http://127.0.0.1:8787"})
    with pytest.raises(ValidationError, match="requires prefix_extraction_gateway_url"):
        InferenceConfig.model_validate({
            "generation_model": "provider:model",
            "prefix_extraction_gateway_headers": {"Authorization": "Bearer gateway-key"},
        })
    configured = InferenceConfig.model_validate({
        "generation_model": "provider:model",
        "prefix_extraction_gateway_url": "http://127.0.0.1:8787",
        "prefix_extraction_gateway_headers": {"Authorization": "Bearer gateway-key"},
    })
    assert "gateway-key" not in repr(configured)


def test_prefix_extraction_cached_input_ratio_is_a_price_fraction() -> None:
    for ratio in (0, 1.5):
        with pytest.raises(ValidationError, match="prefix_extraction_cached_input_ratio"):
            InferenceConfig.model_validate({"prefix_extraction_cached_input_ratio": ratio})
    assert InferenceConfig().prefix_extraction_cached_input_ratio == 0.1
