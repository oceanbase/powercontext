from typing import Optional

from pydantic import AliasChoices, Field

from powermem.integrations.llm.config.openai import OpenAIConfig
from powermem.settings import settings_config


class SiliconFlowConfig(OpenAIConfig):
    """
    Configuration class for SiliconFlow-specific parameters.
    SiliconFlow is OpenAI-compatible, so it inherits from OpenAIConfig.
    Only overrides provider-specific metadata and fields.
    """

    _provider_name = "siliconflow"
    _class_path = "powermem.integrations.llm.siliconflow.SiliconFlowLLM"

    model_config = settings_config("LLM_", extra="forbid", env_file=None)

    # Override api_key to add SiliconFlow-specific validation_alias
    api_key: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices(
            "SILICONFLOW_API_KEY",
            "api_key",
            "LLM_API_KEY",
            "OPENAI_API_KEY",
        ),
        description="SiliconFlow API key"
    )

    # Override openai_base_url with SiliconFlow default
    openai_base_url: Optional[str] = Field(
        default="https://api.siliconflow.cn/v1",
        validation_alias=AliasChoices(
            "SILICONFLOW_LLM_BASE_URL",
            "openai_base_url",
            "OPENAI_LLM_BASE_URL",
        ),
        description="SiliconFlow API base URL (OpenAI-compatible)"
    )