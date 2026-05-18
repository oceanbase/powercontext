import os
from typing import Literal, Optional

try:
    from dashscope import TextEmbedding, MultiModalEmbedding, MultiModalEmbeddingItemText
    from dashscope.api_entities.dashscope_response import DashScopeAPIResponse
except ImportError:
    TextEmbedding = None
    MultiModalEmbedding = None
    MultiModalEmbeddingItemText = None
    DashScopeAPIResponse = None

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

        # Store API key per-instance so multiple Qwen LLM/embedder instances do not overwrite each other
        self.api_key = self.config.api_key or os.getenv("DASHSCOPE_API_KEY")
        if not self.api_key:
            raise ValueError(
                "API key is required. Set DASHSCOPE_API_KEY environment variable or pass api_key in config.")

        # Set base URL (if needed)
        base_url = (
            getattr(self.config, "dashscope_base_url", None)
            or os.getenv("DASHSCOPE_BASE_URL")
            or "https://dashscope.aliyuncs.com/api/v1"
        )
        if base_url:
            os.environ["DASHSCOPE_BASE_URL"] = base_url

    def _is_vl_model(self) -> bool:
        """Check if the configured model is a VL (vision-language) multimodal model.

        If config.multimodal is explicitly set, use that value.
        Otherwise, auto-detect from model name (contains "vl").
        """
        multimodal = getattr(self.config, "multimodal", None)
        if multimodal is not None:
            return multimodal
        return "vl" in (self.config.model or "").lower()

    def embed(self, text: str, memory_action: Optional[Literal["add", "search", "update"]] = None):
        """
        Get the embedding for the given text using Qwen.

        Args:
            text (str): The text to embed.
            memory_action (optional): The type of embedding to use. Must be one of "add", "search", or "update". Defaults to None.
        Returns:
            list: The embedding vector.
        """
        text = text.replace("\n", " ").strip()

        if self._is_vl_model():
            return self._embed_multimodal(text)
        return self._embed_text(text, memory_action)

    def _extract_embedding(self, response):
        """Extract embedding vector from a DashScope API response."""
        if response.status_code != 200:
            raise Exception(f"API request failed with status {response.status_code}: {response.message}")

        if isinstance(response.output, dict) and 'embeddings' in response.output:
            return response.output['embeddings'][0]['embedding']
        return response.output.get('embeddings', [{}])[0].get('embedding', [])

    def _embed_text(self, text: str, memory_action: Optional[Literal["add", "search", "update"]] = None):
        """Embed text using the standard TextEmbedding API (text-only models)."""
        if memory_action == "add":
            embedding_type = getattr(self.config, "memory_add_embedding_type", None) or "document"
        elif memory_action == "search":
            embedding_type = getattr(self.config, "memory_search_embedding_type", None) or "query"
        elif memory_action == "update":
            embedding_type = getattr(self.config, "memory_update_embedding_type", None) or "document"
        else:
            embedding_type = "document"

        try:
            params = {
                "model": self.config.model,
                "input": text,
                "text_type": embedding_type,
            }
            if hasattr(self.config, 'embedding_dims') and self.config.embedding_dims:
                params["dimension"] = self.config.embedding_dims

            response = TextEmbedding.call(api_key=self.api_key, **params)
            return self._extract_embedding(response)
        except Exception as e:
            raise Exception(f"Failed to generate embedding: {e}")

    def _embed_multimodal(self, text: str):
        """Embed text using the MultiModalEmbedding API (VL models).

        VL models do not support text_type. Text input is wrapped in
        MultiModalEmbeddingItemText with factor=1.0.
        """
        if MultiModalEmbedding is None or MultiModalEmbeddingItemText is None:
            raise ImportError(
                "DashScope SDK does not support MultiModalEmbedding. "
                "Please upgrade: pip install dashscope>=1.14.0"
            )

        try:
            input_items = [MultiModalEmbeddingItemText(text=text, factor=1.0)]
            params = {
                "model": self.config.model,
                "input": input_items,
            }
            if hasattr(self.config, 'embedding_dims') and self.config.embedding_dims:
                params["dimension"] = self.config.embedding_dims

            response = MultiModalEmbedding.call(api_key=self.api_key, **params)
            return self._extract_embedding(response)
        except Exception as e:
            raise Exception(f"Failed to generate multimodal embedding: {e}")