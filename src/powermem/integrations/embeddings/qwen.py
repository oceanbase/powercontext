import os
from typing import Literal, Optional

try:
    from dashscope import TextEmbedding
    from dashscope.api_entities.dashscope_response import DashScopeAPIResponse
except ImportError:
    TextEmbedding = None
    DashScopeAPIResponse = None
import dashscope

from powermem.integrations.embeddings.base import EmbeddingBase
from powermem.integrations.embeddings.config.base import BaseEmbedderConfig


class QwenEmbedding(EmbeddingBase):
    def __init__(self, config: Optional[BaseEmbedderConfig] = None):
        super().__init__(config)

        # Set default model and dimensions
        self.config.model = self.config.model or "text-embedding-v4"
        self.config.embedding_dims = self.config.embedding_dims or 1536

        # Check if dashscope is available
        if TextEmbedding is None:
            raise ImportError(
                "DashScope SDK is not installed. Please install it with: pip install dashscope"
            )

        # Set API key
        api_key = self.config.api_key or os.getenv("DASHSCOPE_API_KEY")
        if not api_key:
            raise ValueError(
                "API key is required. Set DASHSCOPE_API_KEY environment variable or pass api_key in config.")

        # Set API key for DashScope SDK
        dashscope.api_key = api_key

        # Set base URL (if needed)
        base_url = (
            getattr(self.config, "dashscope_base_url", None)
            or os.getenv("DASHSCOPE_BASE_URL")
            or "https://dashscope.aliyuncs.com/api/v1"
        )
        if base_url:
            os.environ["DASHSCOPE_BASE_URL"] = base_url

    def embed(self, text: str, memory_action: Optional[Literal["add", "search", "update"]] = None):
        """
        Get the embedding for the given text using Qwen.

        Args:
            text (str): The text to embed.
            memory_action (optional): The type of embedding to use. Must be one of "add", "search", or "update". Defaults to None.
        Returns:
            list: The embedding vector.
        """
        # Clean text
        text = text.replace("\n", " ").strip()

        # Determine embedding type based on memory action
        # Default values for DashScope text-embedding-v4:
        # - "document" for add/update (RETRIEVAL_DOCUMENT equivalent)
        # - "query" for search (RETRIEVAL_QUERY equivalent)
        if memory_action == "add":
            embedding_type = getattr(self.config, "memory_add_embedding_type", None) or "document"
        elif memory_action == "search":
            embedding_type = getattr(self.config, "memory_search_embedding_type", None) or "query"
        elif memory_action == "update":
            embedding_type = getattr(self.config, "memory_update_embedding_type", None) or "document"
        else:
            # Default to "document" if memory_action is None or unknown
            embedding_type = "document"

        try:
            # Prepare parameters
            params = {
                "model": self.config.model,
                "input": text,
            }

            # Add dimension parameter if specified
            if hasattr(self.config, 'embedding_dims') and self.config.embedding_dims:
                params["dimension"] = self.config.embedding_dims

            # Add embedding type (always set, either from config or default)
            params["text_type"] = embedding_type

            # Call the API
            response = TextEmbedding.call(**params)

            if response.status_code != 200:
                raise Exception(f"API request failed with status {response.status_code}: {response.message}")

            # Extract embedding vector
            # response.output is a dict, not an object with attributes
            if isinstance(response.output, dict) and 'embeddings' in response.output:
                embedding = response.output['embeddings'][0]['embedding']
            else:
                # Fallback for different response structures
                embedding = response.output.get('embeddings', [{}])[0].get('embedding', [])

            return embedding

        except Exception as e:
            raise Exception(f"Failed to generate embedding: {e}")
