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
