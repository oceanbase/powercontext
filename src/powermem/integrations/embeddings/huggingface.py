import logging
from typing import Literal, Optional

from openai import OpenAI
from sentence_transformers import SentenceTransformer

from powermem.integrations.embeddings.base import EmbeddingBase
from powermem.integrations.embeddings.config.base import BaseEmbedderConfig

logging.getLogger("transformers").setLevel(logging.WARNING)
logging.getLogger("sentence_transformers").setLevel(logging.WARNING)
logging.getLogger("huggingface_hub").setLevel(logging.WARNING)


class HuggingFaceEmbedding(EmbeddingBase):
    def __init__(self, config: Optional[BaseEmbedderConfig] = None):
        super().__init__(config)

        base_url = getattr(self.config, "huggingface_base_url", None)
        if base_url:
            self.client = OpenAI(base_url=base_url)
        else:
            self.config.model = self.config.model or "multi-qa-MiniLM-L6-cos-v1"

            model_kwargs = getattr(self.config, "model_kwargs", {})
            self.model = SentenceTransformer(self.config.model, **model_kwargs)

            self.config.embedding_dims = self.config.embedding_dims or self.model.get_sentence_embedding_dimension()

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
            return self.model.encode(text, convert_to_numpy=True).tolist()
