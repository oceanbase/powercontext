from typing import Any, Callable, Optional

from pydantic import AliasChoices, Field

from powermem.integrations.llm.config.base import BaseLLMConfig
from powermem.settings import settings_config


class ZaiConfig(BaseLLMConfig):
    """
    Configuration class for Zhipu AI (Z.ai) specific parameters.
    Inherits from BaseLLMConfig and adds Zhipu AI-specific settings.
    
    Reference: https://docs.bigmodel.cn/cn/guide/develop/python/introduction
    """

    _provider_name = "zai"
    _class_path = "powermem.integrations.llm.zai.ZaiLLM"

    model_config = settings_config("LLM_", extra="forbid", env_file=None)

    # Override base fields with Zhipu AI-specific validation_alias
    api_key: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices(
            "api_key",
            "LLM_API_KEY",
            "ZAI_API_KEY",
            "ZHIPU_API_KEY",
        ),
        description="Zhipu AI API key"
    )

    # Zhipu AI-specific fields
    zai_base_url: Optional[str] = Field(
        default="https://open.bigmodel.cn/api/paas/v4/",
        validation_alias=AliasChoices(
            "zai_base_url",
            "ZAI_BASE_URL",
        ),
        description="Zhipu AI API base URL"
    )
    
    response_callback: Optional[Callable[[Any, dict, dict], None]] = Field(
        default=None,
        exclude=True,
        description="Optional callback for monitoring LLM responses"
    )
