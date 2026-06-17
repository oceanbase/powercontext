from typing import Dict, Optional, Union

from pydantic import Field

from powermem.integrations.llm.config.base import BaseLLMConfig
from powermem.settings import settings_config


class NoopConfig(BaseLLMConfig):
    """Configuration for the disabled LLM provider."""

    _provider_name = "noop"
    _class_path = "powermem.integrations.llm.noop.NoopLLM"

    model_config = settings_config("LLM_", extra="allow", env_file=None)

    model: Optional[Union[str, Dict]] = Field(
        default="noop",
        description="Placeholder model name used when LLM features are disabled.",
    )
