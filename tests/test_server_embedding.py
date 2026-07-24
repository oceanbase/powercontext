from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from powercontext.inference import EmbeddingResult
from powercontext.memory import EmbeddingProfile
from powercontext.server.runtime import ServerRuntimeConfigurationError, create_runtime_app
from powercontext.server.settings import (
    InferenceSettings,
    ServerSettings,
    SQLiteStorageSettings,
)


class StubEmbeddingModel:
    def __init__(self, profile: EmbeddingProfile) -> None:
        self.profile = profile

    async def embed(self, texts: tuple[str, ...], /) -> EmbeddingResult:
        return EmbeddingResult(vectors=tuple((1.0,) * self.profile.dimension for _ in texts))


def test_embedding_settings_load_one_complete_environment_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_MODEL", " test-provider:test-model ")
    monkeypatch.setenv("POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_PROFILE_ID", " test-profile-v1 ")
    monkeypatch.setenv("POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_DIMENSION", "3")
    monkeypatch.setenv("POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_NORMALIZATION", " unit ")
    monkeypatch.setenv("POWERCONTEXT_SERVER_STORAGE_VEC1_EXTENSION", "/opt/powercontext/vec1")

    settings = ServerSettings()

    assert settings.inference.embedding_model == "test-provider:test-model"
    assert settings.inference.embedding_profile_id == "test-profile-v1"
    assert settings.inference.embedding_dimension == 3
    assert settings.inference.embedding_normalization == "unit"
    assert settings.storage.vec1_extension == Path("/opt/powercontext/vec1")


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
        InferenceSettings.model_validate(values)


def test_default_runtime_capabilities_report_auto_and_fts(tmp_path: Path) -> None:
    app = create_runtime_app(
        settings=ServerSettings(
            storage=SQLiteStorageSettings(path=tmp_path / "runtime.db"),
        ),
    )

    with TestClient(app) as transport:
        capabilities = transport.get("/v1/capabilities")

    assert capabilities.status_code == 200
    assert capabilities.json()["search_modes"] == ["auto", "fts"]


@pytest.mark.parametrize(
    ("storage", "inference"),
    [
        (
            SQLiteStorageSettings(path=Path("runtime.db"), vec1_extension=Path("vec1")),
            InferenceSettings(),
        ),
        (
            SQLiteStorageSettings(path=Path("runtime.db")),
            InferenceSettings(
                embedding_model="provider:model",
                embedding_profile_id="profile-v1",
                embedding_dimension=3,
            ),
        ),
    ],
)
def test_sqlite_vector_settings_are_validated_at_composition(
    storage: SQLiteStorageSettings,
    inference: InferenceSettings,
) -> None:
    app = create_runtime_app(
        settings=ServerSettings(
            storage=storage,
            inference=inference,
        )
    )

    with pytest.raises(ServerRuntimeConfigurationError, match="requires both"), TestClient(app):
        pass


def test_injected_embedding_model_must_match_configured_profile(
    tmp_path: Path,
) -> None:
    settings = ServerSettings(
        storage=SQLiteStorageSettings(
            path=tmp_path / "runtime.db",
            vec1_extension=tmp_path / "vec1",
        ),
        inference=InferenceSettings(
            embedding_model="provider:model",
            embedding_profile_id="profile-v1",
            embedding_dimension=3,
        ),
    )
    embedding_model = StubEmbeddingModel(
        EmbeddingProfile(
            profile_id="different-profile",
            model="provider:model",
            dimension=3,
        )
    )
    app = create_runtime_app(settings=settings, embedding_model=embedding_model)

    with pytest.raises(ServerRuntimeConfigurationError, match="does not match"), TestClient(app):
        pass
