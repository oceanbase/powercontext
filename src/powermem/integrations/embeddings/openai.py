import os
import warnings
from typing import List, Literal, Optional

from openai import OpenAI

from powermem.integrations.embeddings.base import EmbeddingBase
from powermem.integrations.embeddings.config.base import BaseEmbedderConfig


class OpenAIEmbedding(EmbeddingBase):
    def __init__(self, config: Optional[BaseEmbedderConfig] = None):
        super().__init__(config)

        self.config.model = self.config.model or "text-embedding-3-small"
        self.config.embedding_dims = self.config.embedding_dims or 1536

        api_key = self.config.api_key or os.getenv("OPENAI_API_KEY")
        base_url = (
            getattr(self.config, "openai_base_url", None)
            or os.getenv("OPENAI_API_BASE")
            or os.getenv("OPENAI_BASE_URL")
            or "https://api.openai.com/v1"
        )
        if os.environ.get("OPENAI_API_BASE"):
            warnings.warn(
                "The environment variable 'OPENAI_API_BASE' is deprecated and will be removed in the 0.1.80. "
                "Please use 'OPENAI_BASE_URL' instead.",
                DeprecationWarning,
            )

        client_kwargs = {"api_key": api_key, "base_url": base_url}
        default_headers = getattr(self.config, "default_headers", None)
        if default_headers:
            client_kwargs["default_headers"] = default_headers
        self.client = OpenAI(**client_kwargs)

    def embed(self, text, memory_action: Optional[Literal["add", "search", "update"]] = None):
        """
        Get the embedding for the given text using OpenAI.

        Args:
            text (str): The text to embed.
            memory_action (optional): The type of embedding to use. Must be one of "add", "search", or "update". Defaults to None.
        Returns:
            list: The embedding vector.
        """
        text = text.replace("\n", " ")
        kwargs = {"input": [text], "model": self.config.model}
        pass_dims = getattr(self.config, "pass_dimensions", True)
        if pass_dims:
            kwargs["dimensions"] = self.config.embedding_dims
        return self.client.embeddings.create(**kwargs).data[0].embedding

    def embed_batch(self, texts: List[str], memory_action: Optional[Literal["add", "search", "update"]] = None) -> List[List[float]]:
        """Get embeddings for multiple texts in a single batch using OpenAI.

        Uses one API call for all texts, which is significantly faster than
        calling embed() sequentially.
        """
        cleaned = [t.replace("\n", " ") for t in texts]
        kwargs = {"input": cleaned, "model": self.config.model}
        pass_dims = getattr(self.config, "pass_dimensions", True)
        if pass_dims:
            kwargs["dimensions"] = self.config.embedding_dims
        response = self.client.embeddings.create(**kwargs)
        sorted_data = sorted(response.data, key=lambda x: x.index)
        return [item.embedding for item in sorted_data]
