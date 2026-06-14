import pytest

pytest.importorskip("anthropic")

from powermem.integrations.llm.config.anthropic import AnthropicConfig
from powermem.integrations.llm.anthropic import AnthropicLLM


def test_anthropic_llm_base_url(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_LLM_BASE_URL", raising=False)
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)

    # case 1: config field takes precedence
    config_url = "https://via-config.example.com"
    llm = AnthropicLLM(AnthropicConfig(api_key="fake", anthropic_base_url=config_url))
    assert str(llm.client.base_url) == config_url

    # case 2: ANTHROPIC_LLM_BASE_URL env var (PowerMem convention, read by AnthropicConfig)
    env_url = "https://via-llm-base-url.example.com"
    monkeypatch.setenv("ANTHROPIC_LLM_BASE_URL", env_url)
    llm = AnthropicLLM(AnthropicConfig(api_key="fake"))
    assert str(llm.client.base_url) == env_url
    monkeypatch.delenv("ANTHROPIC_LLM_BASE_URL")

    # case 3: ANTHROPIC_BASE_URL env var (Claude Code convention, fallback in anthropic.py)
    compat_url = "https://via-anthropic-base-url.example.com"
    monkeypatch.setenv("ANTHROPIC_BASE_URL", compat_url)
    llm = AnthropicLLM(AnthropicConfig(api_key="fake"))
    assert str(llm.client.base_url) == compat_url


def test_anthropic_llm_api_key_auth_headers(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    llm = AnthropicLLM(AnthropicConfig(api_key="fake-key"))

    assert llm.client.auth_headers == {"X-Api-Key": "fake-key"}


def test_anthropic_llm_auth_token_headers(monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("LLM_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    llm = AnthropicLLM(
        AnthropicConfig(
            auth_token="gateway-token",
            anthropic_base_url="https://gateway.example.com",
        )
    )

    assert llm.client.auth_headers == {"Authorization": "Bearer gateway-token"}
    assert str(llm.client.base_url) == "https://gateway.example.com"


def test_anthropic_llm_auth_token_requires_base_url(monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("LLM_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    monkeypatch.delenv("ANTHROPIC_LLM_BASE_URL", raising=False)

    with pytest.raises(ValueError, match="ANTHROPIC_AUTH_TOKEN"):
        AnthropicLLM(AnthropicConfig(auth_token="gateway-token"))


def test_anthropic_llm_api_key_from_env_takes_precedence(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "env-token")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "env-api-key")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://env-gateway.example.com")

    llm = AnthropicLLM(AnthropicConfig())

    assert llm.client.auth_headers == {"X-Api-Key": "env-api-key"}
    assert str(llm.client.base_url) == "https://env-gateway.example.com"
