from abc import ABC
from typing import Optional

from pydantic import BaseModel, Field, field_validator


class BaseSparseEmbedderConfig(ABC):
    """
    Base config for Sparse Embeddings.
    This is an abstract base class used by specific sparse embedding implementations.
    """

    def __init__(
        self,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        embedding_dims: Optional[int] = None,
        base_url: Optional[str] = None,
    ):
        """
        Initializes a configuration class instance for the Sparse Embeddings.

        :param model: Embedding model to use, defaults to None
        :type model: Optional[str], optional
        :param api_key: API key to use, defaults to None
        :type api_key: Optional[str], optional
        :param embedding_dims: The number of dimensions in the embedding, defaults to None
        :type embedding_dims: Optional[int], optional
        :param base_url: Base URL for the API, defaults to None
        :type base_url: Optional[str], optional
        """

        self.model = model
        self.api_key = api_key
        self.embedding_dims = embedding_dims
        self.base_url = base_url


class SparseEmbedderConfig(BaseModel):
    """
    Configuration for sparse embedder in MemoryConfig.
    This is a Pydantic model used in MemoryConfig, similar to EmbedderConfig.
    """

    provider: str = Field(
        description="Provider of the sparse embedding model (e.g., 'qwen')",
        default=None,
    )
    config: Optional[dict] = Field(
        description="Configuration for the specific sparse embedding model",
        default={}
    )

    @field_validator("config")
    def validate_config(cls, v, values):
        provider = values.data.get("provider")
        
        # Import here to avoid circular import
        from powermem.integrations.embeddings.sparse_factory import SparseEmbedderFactory
        
        if provider in SparseEmbedderFactory.provider_to_class:
            return v
        else:
            raise ValueError(f"Unsupported sparse embedding provider: {provider}")

