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

from powercontext.builtin.runtime import BuiltinConfig, InferenceConfig, RuntimeConfig
from powercontext.server.settings import ServerSettings


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


@pytest.fixture
def gateway_memory_environment(monkeypatch) -> None:
    monkeypatch.setenv("POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL", "provider:model")
    monkeypatch.setenv("POWERCONTEXT_SERVER_INFERENCE_PREFIX_EXTRACTION_GATEWAY_URL", "http://127.0.0.1:8787")


def test_non_gateway_server_and_sdk_keep_per_candidate_memory_default() -> None:
    assert ServerSettings().runtime.atomic_memory_processing_mode == "per_candidate"
    inference = InferenceConfig.model_validate({
        "generation_model": "provider:model",
        "prefix_extraction_gateway_url": "http://127.0.0.1:8787",
    })
    assert BuiltinConfig(inference=inference).runtime.atomic_memory_processing_mode == "per_candidate"


@pytest.mark.parametrize("source", ["default", "model", "mapping", "environment"])
def test_gateway_server_defaults_omitted_memory_mode_to_one_pass(
    gateway_memory_environment, monkeypatch, source
) -> None:
    original = RuntimeConfig(source_window_limit=7)
    if source == "model":
        settings = ServerSettings(runtime=original)
    elif source == "mapping":
        settings = ServerSettings(runtime={"source_window_limit": 7})  # ty: ignore[invalid-argument-type]
    else:
        if source == "environment":
            monkeypatch.setenv("POWERCONTEXT_SERVER_RUNTIME_SOURCE_WINDOW_LIMIT", "7")
        settings = ServerSettings()

    assert settings.runtime.atomic_memory_processing_mode == "one_pass"
    if source != "default":
        assert settings.runtime.source_window_limit == 7
    assert original.atomic_memory_processing_mode == "per_candidate"
    assert original.model_fields_set == {"source_window_limit"}


@pytest.mark.parametrize("source", ["model", "mapping", "environment"])
@pytest.mark.parametrize("mode", ["per_candidate", "one_pass"])
def test_gateway_server_preserves_explicit_memory_mode(gateway_memory_environment, monkeypatch, source, mode) -> None:
    if source == "model":
        settings = ServerSettings(runtime=RuntimeConfig(atomic_memory_processing_mode=mode))
    elif source == "mapping":
        settings = ServerSettings(runtime={"atomic_memory_processing_mode": mode})  # ty: ignore[invalid-argument-type]
    else:
        monkeypatch.setenv("POWERCONTEXT_SERVER_RUNTIME_ATOMIC_MEMORY_PROCESSING_MODE", mode)
        settings = ServerSettings()
    assert settings.runtime.atomic_memory_processing_mode == mode


@pytest.mark.parametrize("explicit_mode", [None, "one_pass"])
def test_gateway_server_merges_runtime_model_with_environment(
    gateway_memory_environment, monkeypatch, explicit_mode
) -> None:
    monkeypatch.setenv("POWERCONTEXT_SERVER_RUNTIME_ATOMIC_MEMORY_PROCESSING_MODE", "per_candidate")
    monkeypatch.setenv("POWERCONTEXT_SERVER_RUNTIME_SOURCE_WINDOW_LIMIT", "99")
    original = (
        RuntimeConfig(source_window_limit=7)
        if explicit_mode is None
        else RuntimeConfig(source_window_limit=7, atomic_memory_processing_mode=explicit_mode)
    )

    settings = ServerSettings(runtime=original)

    assert settings.runtime.atomic_memory_processing_mode == (explicit_mode or "per_candidate")
    assert settings.runtime.source_window_limit == 7


@pytest.mark.parametrize("exclude_unset", [False, True])
def test_gateway_server_respects_serialized_runtime_memory_mode(gateway_memory_environment, exclude_unset) -> None:
    runtime = RuntimeConfig(source_window_limit=7).model_dump(exclude_unset=exclude_unset)
    settings = ServerSettings(runtime=runtime)
    # A full dump supplies a concrete mode; an unset-only dump still asks for the deployment default.
    expected = "one_pass" if exclude_unset else "per_candidate"
    assert settings.runtime.atomic_memory_processing_mode == expected
    restored = ServerSettings(runtime=settings.runtime.model_dump())
    assert restored.runtime.atomic_memory_processing_mode == expected
