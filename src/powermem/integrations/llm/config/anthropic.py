from typing import Optional

from pydantic import AliasChoices, Field

from powermem.integrations.llm.config.base import BaseLLMConfig
from powermem.settings import settings_config


class AnthropicConfig(BaseLLMConfig):
    """
    Configuration class for Anthropic-specific parameters.
    Inherits from BaseLLMConfig and adds Anthropic-specific settings.
    """

    _provider_name = "anthropic"
    _class_path = "powermem.integrations.llm.anthropic.AnthropicLLM"

    model_config = settings_config("LLM_", extra="forbid", env_file=None)

    # Override base fields with Anthropic-specific validation_alias
    api_key: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices(
            "api_key",
            "LLM_API_KEY",
            "ANTHROPIC_API_KEY",
        ),
        description="Anthropic API key"
    )

    # Anthropic-specific fields
    anthropic_base_url: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices(
            "anthropic_base_url",
            "ANTHROPIC_LLM_BASE_URL",
        ),
        description="Anthropic API base URL"
    )
