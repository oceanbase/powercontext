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
