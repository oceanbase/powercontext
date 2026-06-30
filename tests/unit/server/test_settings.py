import pytest
from pydantic import ValidationError


def test_server_settings_parsing(monkeypatch):
    monkeypatch.setenv("POWERMEM_SERVER_AUTH_ENABLED", "false")
    monkeypatch.setenv("POWERMEM_SERVER_LOG_FILE", "")
    monkeypatch.setenv("POWERMEM_SERVER_API_KEYS", " a, b ,, c ")
    monkeypatch.setenv(
        "POWERMEM_SERVER_CORS_ORIGINS", "https://a.example, https://b.example"
    )
    monkeypatch.setenv("POWERMEM_SERVER_RELOAD", "enabled")

    from server.config import ServerSettings

    settings = ServerSettings(_env_file=None)

    assert settings.auth_enabled is False
    assert settings.log_file is None
    assert settings.get_api_keys_list() == ["a", "b", "c"]
    assert settings.get_cors_origins_list() == [
        "https://a.example",
        "https://b.example",
    ]


@pytest.mark.parametrize(
    "env_name",
    [
        "POWERMEM_SERVER_DEPENDENCY_CHECK_TIMEOUT_SECONDS",
        "POWERMEM_SERVER_DEPENDENCY_STATUS_CACHE_TTL_SECONDS",
    ],
)
def test_server_dependency_probe_settings_must_be_positive(monkeypatch, env_name):
    from server.config import ServerSettings

    monkeypatch.setenv(env_name, "0")

    with pytest.raises(ValidationError, match="greater than 0"):
        ServerSettings(_env_file=None)
