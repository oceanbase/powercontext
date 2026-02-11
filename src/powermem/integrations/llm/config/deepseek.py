from typing import Optional

from pydantic import AliasChoices, Field

from powermem.integrations.llm.config.base import BaseLLMConfig
from powermem.settings import settings_config


class DeepSeekConfig(BaseLLMConfig):
    """
    Configuration class for DeepSeek-specific parameters.
    Inherits from BaseLLMConfig and adds DeepSeek-specific settings.
    """

    _provider_name = "deepseek"
    _class_path = "powermem.integrations.llm.deepseek.DeepSeekLLM"

    model_config = settings_config("LLM_", extra="forbid", env_file=None)

    # Override base fields with DeepSeek-specific validation_alias
    api_key: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices(
            "api_key",
            "LLM_API_KEY",
            "DEEPSEEK_API_KEY",
        ),
        description="DeepSeek API key"
    )

    # DeepSeek-specific fields
    deepseek_base_url: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices(
            "deepseek_base_url",
            "DEEPSEEK_LLM_BASE_URL",
        ),
        description="DeepSeek API base URL"
    )
