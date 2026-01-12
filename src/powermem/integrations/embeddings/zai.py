import os
from typing import Literal, Optional

from zai import ZhipuAiClient

from powermem.integrations.embeddings.base import EmbeddingBase
from powermem.integrations.embeddings.config.base import BaseEmbedderConfig


class ZaiEmbedding(EmbeddingBase):
    """
    Zhipu AI (Z.ai) Embedding implementation.
    
    Reference: https://docs.bigmodel.cn/cn/guide/develop/python/introduction
    
    Supported models:
    - embedding-3: Latest embedding model
    - embedding-2: Standard embedding model
    """

    def __init__(self, config: Optional[BaseEmbedderConfig] = None):
        super().__init__(config)

        # Set default model and embedding dimensions
        self.config.model = self.config.model or "embedding-3"
        self.config.embedding_dims = self.config.embedding_dims or 2048

        # Get API key from config or environment
        api_key = self.config.api_key or os.getenv("ZAI_API_KEY")

        # Initialize Zhipu AI client
        self.client = ZhipuAiClient(api_key=api_key)

    def embed(self, text, memory_action: Optional[Literal["add", "search", "update"]] = None):
        """
        Get the embedding for the given text using Zhipu AI.

        Args:
            text (str): The text to embed.
            memory_action (optional): The type of embedding to use. Must be one of "add", "search", or "update". Defaults to None.
        Returns:
            list: The embedding vector.
        """
        text = text.replace("\n", " ")
        response = self.client.embeddings.create(
            input=[text],
            model=self.config.model
        )
        return response.data[0].embedding
