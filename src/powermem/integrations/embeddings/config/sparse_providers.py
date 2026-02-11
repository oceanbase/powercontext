from typing import Optional

from pydantic import AliasChoices, Field

from powermem.integrations.embeddings.config.sparse_base import BaseSparseEmbedderConfig
from powermem.settings import settings_config


class QwenSparseEmbeddingConfig(BaseSparseEmbedderConfig):
    _provider_name = "qwen"
    _class_path = "powermem.integrations.embeddings.qwen_sparse.QwenSparseEmbedding"

    model_config = settings_config("SPARSE_EMBEDDER_", extra="forbid", env_file=None)

    api_key: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices(
            "api_key",
            "SPARSE_EMBEDDER_API_KEY",
            "DASHSCOPE_API_KEY",
        ),
    )
    base_url: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices(
            "base_url",
            "SPARSE_EMBEDDING_BASE_URL",
            "DASHSCOPE_BASE_URL",
        ),
    )
    model: Optional[str] = Field(default=None)
    embedding_dims: Optional[int] = Field(default=None)
