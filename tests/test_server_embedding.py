from __future__ import annotations

import pytest
from pydantic import ValidationError

from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import InferenceConfig
from powercontext.server.settings import ServerSettings


def test_embedding_settings_load_one_complete_environment_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_MODEL", " test-provider:test-model ")
    monkeypatch.setenv("POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_PROFILE_ID", " test-profile-v1 ")
    monkeypatch.setenv("POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_DIMENSION", "3")
    monkeypatch.setenv("POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_NORMALIZATION", " unit ")

    settings = ServerSettings()

    assert settings.inference.embedding_model == "test-provider:test-model"
    assert settings.inference.embedding_profile_id == "test-profile-v1"
    assert settings.inference.embedding_dimension == 3
    assert settings.inference.embedding_normalization == "unit"


def test_embedding_normalization_defaults_to_unit() -> None:
    assert InferenceConfig().embedding_normalization == "unit"


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


def test_component_config_rejects_unknown_values() -> None:
    with pytest.raises(ValidationError):
        SQLiteConfig.model_validate({"legacy_path": "powercontext.db"})
