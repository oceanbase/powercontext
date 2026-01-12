"""
Configuration for rerank models
"""
from typing import Optional, Dict, Any
from pydantic import BaseModel, Field


class RerankConfig(BaseModel):
    """Configuration for rerank functionality."""
    
    enabled: bool = Field(
        description="Whether to enable reranker",
        default=False,
    )
    provider: str = Field(
        description="Reranker provider (e.g., 'qwen', 'zai', 'jina')",
        default="qwen",
    )
    config: Optional[Dict[str, Any]] = Field(
        description="Configuration for the specific reranker provider",
        default=None
    )

