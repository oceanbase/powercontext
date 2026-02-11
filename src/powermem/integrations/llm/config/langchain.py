from typing import Any, Optional

from pydantic import Field

from powermem.integrations.llm.config.base import BaseLLMConfig
from powermem.settings import settings_config


class LangchainConfig(BaseLLMConfig):
    """
    Configuration class for Langchain LLM wrapper.
    Inherits from BaseLLMConfig.
    """

    _provider_name = "langchain"
    _class_path = "powermem.integrations.llm.langchain.LangchainLLM"

    model_config = settings_config("LLM_", extra="forbid", env_file=None)

    # Langchain uses a model object instead of string
    model: Optional[Any] = Field(
        default=None,
        description="Langchain LLM model object"
    )
