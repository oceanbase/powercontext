from typing import Any, Dict, Optional, Union

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings

from powermem.settings import settings_config

from powermem.integrations.embeddings.config.base import BaseEmbedderConfig
from powermem.integrations.embeddings.config.providers import (
    CustomEmbeddingConfig,
)


class EmbedderConfig(BaseSettings):
    model_config = settings_config()

    provider: str = Field(
        description="Provider of the embedding model (e.g., 'ollama', 'openai')",
        default="openai",
    )
    config: Optional[Union[Dict[str, Any], BaseEmbedderConfig]] = Field(
        description="Configuration for the specific embedding model",
        default_factory=dict,
    )

    @field_validator("config")
    def validate_config(cls, v, info):
        provider = (info.data.get("provider") or "").lower()
        if v is None:
            return v
        if isinstance(v, BaseEmbedderConfig):
            return v
        if not isinstance(v, dict):
            raise ValueError("config must be a dict or BaseEmbedderConfig")
        initialized_providers = [
            "openai",
            "ollama",
            "huggingface",
            "azure_openai",
            "gemini",
            "vertexai",
            "together",
            "lmstudio",
            "langchain",
            "aws_bedrock",
            "qwen",
            "siliconflow",
            "zai",
        ]
        if provider in initialized_providers or BaseEmbedderConfig.has_provider(provider) or provider == "mock":
            config_cls = (
                BaseEmbedderConfig.get_provider_config_cls(provider)
                or CustomEmbeddingConfig
            )
            return config_cls(**v)
        raise ValueError(f"Unsupported embedding provider: {provider}")
