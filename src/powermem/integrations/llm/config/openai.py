from typing import Any, Callable, List, Optional

from pydantic import AliasChoices, Field

from powermem.integrations.llm.config.base import BaseLLMConfig
from powermem.settings import settings_config


class OpenAIConfig(BaseLLMConfig):
    """
    Configuration class for OpenAI and OpenRouter-specific parameters.
    Inherits from BaseLLMConfig and adds OpenAI-specific settings.
    """

    _provider_name = "openai"
    _class_path = "powermem.integrations.llm.openai.OpenAILLM"

    model_config = settings_config("LLM_", extra="forbid", env_file=None)

    # Override base fields with OpenAI-specific validation_alias
    api_key: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices(
            "api_key",
            "LLM_API_KEY",
            "OPENAI_API_KEY",
        ),
        description="OpenAI API key"
    )

    # OpenAI-specific fields
    openai_base_url: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices(
            "openai_base_url",
            "OPENAI_LLM_BASE_URL",
        ),
        description="OpenAI API base URL"
    )
    
    models: Optional[List[str]] = Field(
        default=None,
        description="List of models for OpenRouter"
    )
    
    route: Optional[str] = Field(
        default="fallback",
        description="OpenRouter route strategy"
    )
    
    openrouter_base_url: Optional[str] = Field(
        default=None,
        description="OpenRouter base URL"
    )
    
    site_url: Optional[str] = Field(
        default=None,
        description="Site URL for OpenRouter"
    )
    
    app_name: Optional[str] = Field(
        default=None,
        description="Application name for OpenRouter"
    )
    
    store: bool = Field(
        default=False,
        description="Whether to store conversations"
    )
    
    response_callback: Optional[Callable[[Any, dict, dict], None]] = Field(
        default=None,
        exclude=True,
        description="Optional callback for monitoring LLM responses"
    )
