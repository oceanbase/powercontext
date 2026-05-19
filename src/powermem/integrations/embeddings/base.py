from abc import ABC, abstractmethod
from typing import List, Literal, Optional

from powermem.integrations.embeddings.config.base import BaseEmbedderConfig


class EmbeddingBase(ABC):
    """Initialized a base embedding class

    :param config: Embedding configuration option class, defaults to None
    :type config: Optional[BaseEmbedderConfig], optional
    """

    def __init__(self, config: Optional[BaseEmbedderConfig] = None):
        if config is None:
            self.config = BaseEmbedderConfig()
        else:
            self.config = config

    @abstractmethod
    def embed(self, text, memory_action: Optional[Literal["add", "search", "update"]]):
        """
        Get the embedding for the given text.

        Args:
            text (str): The text to embed.
            memory_action (optional): The type of embedding to use. Must be one of "add", "search", or "update". Defaults to None.
        Returns:
            list: The embedding vector.
        """
        pass

    def embed_batch(self, texts: List[str], memory_action: Optional[Literal["add", "search", "update"]] = None) -> List[List[float]]:
        """Get embeddings for multiple texts in a single batch.

        Default implementation calls embed() sequentially. Subclasses may
        override to use a true batch API for better performance.

        Args:
            texts: List of texts to embed.
            memory_action: The type of embedding action. Defaults to None.
        Returns:
            List of embedding vectors, one per input text.
        """
        return [self.embed(text, memory_action) for text in texts]
