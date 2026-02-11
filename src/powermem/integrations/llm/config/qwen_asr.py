from typing import Optional

from pydantic import AliasChoices, Field

from powermem.integrations.llm.config.base import BaseLLMConfig
from powermem.settings import settings_config


class QwenASRConfig(BaseLLMConfig):
    """
    Configuration class for Qwen ASR-specific parameters.
    Inherits from BaseLLMConfig and adds ASR-specific settings.
    """

    _provider_name = "qwen_asr"
    _class_path = "powermem.integrations.llm.qwen_asr.QwenASR"

    model_config = settings_config("LLM_", extra="forbid", env_file=None)

    # Override base fields with ASR-specific validation_alias
    api_key: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices(
            "api_key",
            "LLM_API_KEY",
            "QWEN_API_KEY",
            "DASHSCOPE_API_KEY",
        ),
        description="DashScope API key for Qwen ASR"
    )

    # ASR-specific fields
    dashscope_base_url: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices(
            "dashscope_base_url",
            "QWEN_LLM_BASE_URL",
        ),
        description="DashScope API base URL"
    )
    
    asr_options: Optional[dict] = Field(
        default_factory=lambda: {"enable_itn": True},
        description="ASR-specific options (e.g., language, enable_itn)"
    )
    
    result_format: str = Field(
        default="message",
        description="Result format for ASR response"
    )
