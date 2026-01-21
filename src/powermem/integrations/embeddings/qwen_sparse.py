import os
from typing import Literal, Optional

from src.powermem.integrations.embeddings.config.sparse_base import BaseSparseEmbedderConfig
from src.powermem.integrations.embeddings.sparse_base import SparseEmbeddingBase

try:
    from dashscope import TextEmbedding
    from dashscope.api_entities.dashscope_response import DashScopeAPIResponse
except ImportError:
    TextEmbedding = None
    DashScopeAPIResponse = None
import dashscope


class QwenSparseEmbedding(SparseEmbeddingBase):
    def __init__(self, config: Optional[BaseSparseEmbedderConfig] = None):
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
            self.config.base_url
            or os.getenv("DASHSCOPE_BASE_URL")
            or "https://dashscope.aliyuncs.com/api/v1"
        )
        if base_url:
            os.environ["DASHSCOPE_BASE_URL"] = base_url

    def embed_sparse(self, text: str, memory_action: Optional[Literal["add", "search", "update"]] = None) -> dict[int, float]:
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
        if memory_action == "add":
            embedding_type = "document"
        elif memory_action == "search":
            embedding_type =  "query"
        elif memory_action == "update":
            embedding_type = "document"
        else:
            # Default to "document" if memory_action is None or unknown
            embedding_type = "document"

        try:
            params = {
                "model": self.config.model,
                "input": text,
                "output_type": "sparse",
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

            # Extract sparse embedding vector
            # response.output is a dict, not an object with attributes
            if isinstance(response.output, dict) and 'embeddings' in response.output:
                sparse_embedding_list = response.output['embeddings'][0].get('sparse_embedding', [])
                # Convert sparse_embedding list to dict format: {index: value}
                embedding = {item['index']: item['value'] for item in sparse_embedding_list}
            else:
                # Fallback for different response structures
                sparse_embedding_list = response.output.get('embeddings', [{}])[0].get('sparse_embedding', [])
                embedding = {item.get('index', 0): item.get('value', 0.0) for item in sparse_embedding_list if 'index' in item and 'value' in item}

            return embedding

        except Exception as e:
            raise Exception(f"Failed to generate embedding: {e}")
