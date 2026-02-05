from typing import Any, Callable, Optional

from pydantic import AliasChoices, Field

from powermem.integrations.llm.config.base import BaseLLMConfig
from powermem.settings import settings_config


class QwenConfig(BaseLLMConfig):
    """
    Configuration class for Qwen-specific parameters.
    Inherits from BaseLLMConfig and adds Qwen-specific settings.
    """

    _provider_name = "qwen"
    _class_path = "powermem.integrations.llm.qwen.QwenLLM"

    model_config = settings_config("LLM_", extra="forbid", env_file=None)

    # Override base fields with Qwen-specific validation_alias
    api_key: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices(
            "api_key",
            "LLM_API_KEY",
            "QWEN_API_KEY",
            "DASHSCOPE_API_KEY",
        ),
        description="DashScope API key for Qwen models"
    )

    # Qwen-specific fields
    dashscope_base_url: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices(
            "dashscope_base_url",
            "QWEN_LLM_BASE_URL",
        ),
        description="DashScope API base URL"
    )
    
    enable_search: bool = Field(
        default=False,
        description="Enable web search capability for Qwen models"
    )
    
    search_params: Optional[dict] = Field(
        default_factory=dict,
        description="Parameters for web search functionality"
    )
    
    response_callback: Optional[Callable[[Any, dict, dict], None]] = Field(
        default=None,
        exclude=True,
        description="Optional callback for monitoring LLM responses"
    )
