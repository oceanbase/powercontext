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
from powercontext.server.settings import ServerSettings


def test_embedding_settings_load_one_complete_environment_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_MODEL", " test-provider:test-model ")
    monkeypatch.setenv("POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_PROFILE_ID", " test-profile-v1 ")
    monkeypatch.setenv("POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_DIMENSION", "3")
    monkeypatch.setenv("POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_NORMALIZATION", " unit ")
    monkeypatch.setenv("POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_BATCH_SIZE", "7")

    settings = ServerSettings()

    assert settings.inference.embedding_model == "test-provider:test-model"
    assert settings.inference.embedding_profile_id == "test-profile-v1"
    assert settings.inference.embedding_dimension == 3
    assert settings.inference.embedding_normalization == "unit"
    assert settings.inference.embedding_batch_size == 7


def test_embedding_normalization_defaults_to_unit() -> None:
    assert InferenceConfig().embedding_normalization == "unit"
    assert InferenceConfig().embedding_batch_size == 10


def test_embedding_settings_reject_unknown_normalization() -> None:
    with pytest.raises(ValidationError, match=r"none.*unit"):
        InferenceConfig.model_validate({"embedding_normalization": "provider-default"})


@pytest.mark.parametrize(
    "values",
    [
        {"embedding_model": "provider:model"},
        {
            "embedding_model": "provider:model",
            "embedding_profile_id": "profile-v1",
        },
    ],
)
def test_embedding_settings_reject_partial_profiles(values: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        InferenceConfig.model_validate(values)
