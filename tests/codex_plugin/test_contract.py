from __future__ import annotations

import json
from pathlib import Path
from types import ModuleType

import pytest
from pydantic import ValidationError

PLUGIN_ROOT = Path(__file__).resolve().parents[2] / "integrations" / "codex" / "plugins" / "powercontext"


@pytest.mark.parametrize(
    ("remote", "expected"),
    [
        ("https://github.com/OceanBase/powercontext.git", "github.com/OceanBase/powercontext"),
        ("ssh://git@github.com/OceanBase/powercontext.git", "github.com/OceanBase/powercontext"),
        ("git@github.com:OceanBase/powercontext.git", "github.com/OceanBase/powercontext"),
    ],
)
def test_scope_normalizes_network_git_remotes(
    scope_module: ModuleType,
    remote: str,
    expected: str,
) -> None:
    assert scope_module.normalize_git_remote(remote) == expected


def test_scope_override_wins(scope_module: ModuleType, tmp_path: Path) -> None:
    assert (
        scope_module.derive_scope_id(
            str(tmp_path),
            configured_scope_id="project:explicit",
        )
        == "project:explicit"
    )


def test_codex_settings_precedence_and_validation(
    recall_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("POWERCONTEXT_CODEX_SERVER_URL", "https://environment.example/api/")
    monkeypatch.setenv("POWERCONTEXT_CODEX_CAPTURE_PROMPTS", "false")
    monkeypatch.setenv("POWERCONTEXT_CODEX_REQUEST_TIMEOUT_SECONDS", "4.5")

    environment = recall_module.CodexPluginSettings()
    explicit = recall_module.CodexPluginSettings(server_url="https://explicit.example/")

    assert environment.server_url == "http://127.0.0.1:8000"
    assert environment.capture_prompts is False
    assert environment.request_timeout_seconds == 4.5
    assert explicit.server_url == "http://127.0.0.1:8000"


def test_codex_settings_load_the_optional_mcp_authorization_environment(
    recall_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("POWERCONTEXT_CODEX_AUTHORIZATION", "Bearer secret-token")

    settings = recall_module.CodexPluginSettings()

    assert settings.authorization is not None
    assert settings.authorization.get_secret_value() == "Bearer secret-token"
    assert "secret-token" not in repr(settings)


def test_codex_mcp_uses_an_optional_authorization_environment() -> None:
    plugin_root = Path(__file__).resolve().parents[2] / "integrations" / "codex" / "plugins" / "powercontext"
    configuration = json.loads((plugin_root / ".mcp.json").read_text())

    assert configuration["mcpServers"]["powercontext"]["env_http_headers"] == {
        "Authorization": "POWERCONTEXT_CODEX_AUTHORIZATION"
    }
    assert "http_headers" not in configuration["mcpServers"]["powercontext"]


@pytest.mark.parametrize(
    "authorization",
    ["Basic secret-token", "Bearer ", "Bearer token with spaces", "Bearer token\nsecond-header"],
)
def test_codex_settings_reject_invalid_authorization_headers(
    recall_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    authorization: str,
) -> None:
    monkeypatch.setenv("POWERCONTEXT_CODEX_AUTHORIZATION", authorization)

    with pytest.raises(ValidationError):
        recall_module.CodexPluginSettings()


def test_codex_settings_ignore_unscoped_legacy_names(
    recall_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("POWERCONTEXT_HTTP_URL", "https://legacy.example")

    assert recall_module.CodexPluginSettings().server_url == "http://127.0.0.1:8000"


@pytest.mark.parametrize(
    "value",
    [
        "http://memory.example.com/mcp",
        "https://user:password@memory.example.com/mcp",
        "https://memory.example.com/mcp?token=secret",
        "https://memory.example.com/mcp#fragment",
        "https://memory.example.com/api",
        "file:///tmp/socket/mcp",
    ],
)
def test_codex_settings_reject_unsafe_or_ambiguous_mcp_urls(
    settings_module: ModuleType,
    value: str,
) -> None:
    with pytest.raises(ValueError):
        settings_module._http_base_url(value)


def test_codex_settings_normalize_the_mcp_path_to_http_base(
    settings_module: ModuleType,
) -> None:
    assert settings_module._http_base_url("https://memory.example/api/mcp/") == "https://memory.example/api"


def test_project_context_skill_preserves_the_explicit_handoff_boundary() -> None:
    content = (PLUGIN_ROOT / "skills" / "project-context" / "SKILL.md").read_text()

    assert "capture_content_source" in content
    assert "activate_handoff" in content
    assert "`boundary_source`" in content
    assert "canonical temporary" in content
    assert "finalize_handoff" in content
    assert 'selection: "prepared"' in content
    assert "Call `commit_handoff` only when" in content
