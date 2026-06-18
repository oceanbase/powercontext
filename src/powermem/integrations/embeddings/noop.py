from typing import List, Literal, Optional

from powermem.integrations.embeddings.base import EmbeddingBase
from powermem.integrations.embeddings.config.base import BaseEmbedderConfig


class NoopEmbedding(EmbeddingBase):
    """Embedding implementation used when embedding is explicitly disabled (provider='none')."""

    is_noop = True

    def __init__(self, config: Optional[BaseEmbedderConfig] = None):
        super().__init__(config)

    def embed(
        self,
        text,
        memory_action: Optional[Literal["add", "search", "update"]] = None,
    ) -> List[float]:
        return []

    def embed_batch(
        self,
        texts: List[str],
        memory_action: Optional[Literal["add", "search", "update"]] = None,
    ) -> List[List[float]]:
        return [[] for _ in texts]
