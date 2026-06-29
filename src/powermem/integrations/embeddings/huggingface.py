import logging
import threading
from typing import Literal, Optional

from openai import OpenAI

from powermem.integrations.embeddings._model_cache import (
    DEFAULT_MODEL_NAME,
    DEFAULT_MODEL_REPO_ID,
    load_sentence_transformer_with_fallback,
)
from powermem.integrations.embeddings.base import EmbeddingBase
from powermem.integrations.embeddings.config.base import BaseEmbedderConfig

logging.getLogger("transformers").setLevel(logging.WARNING)
logging.getLogger("sentence_transformers").setLevel(logging.WARNING)
logging.getLogger("huggingface_hub").setLevel(logging.WARNING)


class HuggingFaceEmbedding(EmbeddingBase):
    def __init__(self, config: Optional[BaseEmbedderConfig] = None):
        super().__init__(config)

        self._model = None
        self._model_lock = threading.Lock()

        base_url = getattr(self.config, "huggingface_base_url", None)
        if base_url:
            self.client = OpenAI(base_url=base_url)
        else:
            self.config.model = self.config.model or "multi-qa-MiniLM-L6-cos-v1"

    def _get_model(self):
        if self._model is not None:
            return self._model
        with self._model_lock:
            if self._model is None:
                model_name = self.config.model
                model_kwargs = getattr(self.config, "model_kwargs", {}) or {}

                # For the default model, use the CN-aware loader (cache-first,
                # ModelScope fallback for CN) to avoid huggingface_hub's buggy
                # httpx retry path on networks where huggingface.co is unreachable.
                if not model_kwargs and model_name in (DEFAULT_MODEL_NAME, DEFAULT_MODEL_REPO_ID):
                    self._model = load_sentence_transformer_with_fallback(
                        DEFAULT_MODEL_NAME, DEFAULT_MODEL_REPO_ID
                    )
                else:
                    from sentence_transformers import SentenceTransformer
                    self._model = SentenceTransformer(model_name, **model_kwargs)

                if self._model is not None:
                    self.config.embedding_dims = (
                        self.config.embedding_dims
                        or self._model.get_sentence_embedding_dimension()
                    )
        return self._model

    def embed(self, text, memory_action: Optional[Literal["add", "search", "update"]] = None):
        """
        Get the embedding for the given text using Hugging Face.

        Args:
            text (str): The text to embed.
            memory_action (optional): The type of embedding to use. Must be one of "add", "search", or "update". Defaults to None.
        Returns:
            list: The embedding vector.
        """
        if getattr(self.config, "huggingface_base_url", None):
            return self.client.embeddings.create(input=text, model="tei").data[0].embedding
        else:
            return self._get_model().encode(text, convert_to_numpy=True).tolist()
