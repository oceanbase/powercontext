from typing import Optional

from pydantic import AliasChoices, Field

from powermem.integrations.llm.config.base import BaseLLMConfig
from powermem.settings import settings_config


class GeminiConfig(BaseLLMConfig):
    """
    Configuration class for Google Gemini-specific parameters.
    Inherits from BaseLLMConfig.
    """

    _provider_name = "gemini"
    _class_path = "powermem.integrations.llm.gemini.GeminiLLM"

    model_config = settings_config("LLM_", extra="forbid", env_file=None)

    # Override base fields with Gemini-specific validation_alias
    api_key: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices(
            "api_key",
            "LLM_API_KEY",
            "GEMINI_API_KEY",
            "GOOGLE_API_KEY",
        ),
        description="Google Gemini API key"
    )
