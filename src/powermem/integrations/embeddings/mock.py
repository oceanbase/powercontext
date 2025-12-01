from typing import Literal, Optional

from powermem.integrations.embeddings.base import EmbeddingBase


class MockEmbeddings(EmbeddingBase):
    def __init__(self, dimension: int = 1536):
        """
        Initialize MockEmbeddings with specified dimension.
        
        Args:
            dimension: Dimension of the mock embedding vector. Defaults to 1536 to match
                      common embedding models and OceanBase default.
        """
        self.dimension = dimension
    
    def embed(self, text, memory_action: Optional[Literal["add", "search", "update"]] = None):
        """
        Generate a mock embedding with the configured dimension.
        
        Returns a vector with values [0.1, 0.2, 0.3, ...] repeated to fill the dimension.
        """
        # Generate a simple pattern that repeats to fill the dimension
        base_values = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]
        # Repeat the pattern to fill the required dimension
        result = []
        for i in range(self.dimension):
            result.append(base_values[i % len(base_values)])
        return result
