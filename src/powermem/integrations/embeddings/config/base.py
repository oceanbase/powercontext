from typing import Any, Optional

from pydantic import Field
from pydantic_settings import BaseSettings

from powermem.settings import settings_config


class BaseEmbedderConfig(BaseSettings):
    """Common embedding configuration shared by all providers."""

    model_config = settings_config("EMBEDDING_", extra="allow", env_file=None)

    model: Optional[Any] = Field(
        default=None,
        description="Embedding model name or provider-specific model object.",
    )
    api_key: Optional[str] = Field(
        default=None,
        description="API key used for provider authentication.",
    )
    embedding_dims: Optional[int] = Field(
        default=None,
        description="Embedding vector dimensions, when configurable by provider.",
    )
