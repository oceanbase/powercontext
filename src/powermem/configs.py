"""
Configuration classes for the memory system.

This module provides configuration classes for different components
of the memory system.
"""

from typing import Optional, Dict, Any
from pydantic import BaseModel, Field

from powermem.integrations.embeddings.config.base import BaseEmbedderConfig
from powermem.integrations.embeddings.config.providers import OpenAIEmbeddingConfig
from powermem.integrations.embeddings.config.sparse_base import SparseEmbedderConfig
from powermem.integrations.llm import LlmConfig
from powermem.storage.configs import VectorStoreConfig, GraphStoreConfig
from powermem.integrations.rerank.configs import RerankConfig


class IntelligentMemoryConfig(BaseModel):
    """Configuration for intelligent memory management with Ebbinghaus algorithm."""

    enabled: bool = Field(
        default=True,
        description="Whether to enable intelligent memory management"
    )
    initial_retention: float = Field(
        default=1.0,
        description="Initial retention strength for new memories"
    )
    decay_rate: float = Field(
        default=0.1,
        description="Rate at which memories decay over time"
    )
    reinforcement_factor: float = Field(
        default=0.3,
        description="Factor by which memories are reinforced when accessed"
    )
    working_threshold: float = Field(
        default=0.3,
        description="Threshold for working memory classification"
    )
    short_term_threshold: float = Field(
        default=0.6,
        description="Threshold for short-term memory classification"
    )
    long_term_threshold: float = Field(
        default=0.8,
        description="Threshold for long-term memory classification"
    )
    fallback_to_simple_add: bool = Field(
        default=False,
        description="Whether to fallback to simple add mode when intelligent processing fails (no facts extracted or no actions returned)"
    )


class TelemetryConfig(BaseModel):
    """Configuration for telemetry and monitoring."""

    enable_telemetry: bool = Field(
        default=False,
        description="Whether to enable telemetry"
    )
    telemetry_endpoint: str = Field(
        default="https://telemetry.powermem.ai",
        description="Endpoint URL for telemetry data"
    )
    telemetry_api_key: Optional[str] = Field(
        default=None,
        description="API key for telemetry service"
    )
    batch_size: int = Field(
        default=100,
        description="Number of events to batch before sending"
    )
    flush_interval: int = Field(
        default=30,
        description="Interval in seconds to flush telemetry data"
    )


class AuditConfig(BaseModel):
    """Configuration for audit logging."""

    enabled: bool = Field(
        default=True,
        description="Whether to enable audit logging"
    )
    log_file: str = Field(
        default="./logs/audit.log",
        description="Path to the audit log file"
    )
    log_level: str = Field(
        default="INFO",
        description="Logging level for audit logs"
    )
    retention_days: int = Field(
        default=90,
        description="Number of days to retain audit logs"
    )


class LoggingConfig(BaseModel):
    """Configuration for application logging."""

    level: str = Field(
        default="DEBUG",
        description="Logging level"
    )
    format: str = Field(
        default="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        description="Log message format"
    )
    file: str = Field(
        default="./logs/powermem.log",
        description="Path to the log file"
    )


class AgentMemoryConfig(BaseModel):
    """Configuration for agent memory management."""

    enabled: bool = Field(
        default=True,
        description="Whether to enable agent memory management"
    )
    mode: str = Field(
        default="multi_agent",
        description="Agent memory mode: 'multi_agent', 'multi_user', 'hybrid', or 'auto'"
    )
    default_scope: str = Field(
        default="private",
        description="Default scope for memories: 'private', 'agent_group', 'user_group', 'public', 'restricted' (from MemoryScope enum)"
    )
    default_privacy_level: str = Field(
        default="standard",
        description="Default privacy level: 'standard', 'sensitive', 'confidential' (from PrivacyLevel enum)"
    )
    default_collaboration_level: str = Field(
        default="isolated",
        description="Default collaboration level: 'isolated', 'collaborative' (from CollaborationLevel enum)"
    )
    default_access_permission: str = Field(
        default="read",
        description="Default access permission: 'read', 'write', 'delete', 'admin' (from AccessPermission enum)"
    )
    enable_collaboration: bool = Field(
        default=True,
        description="Whether to enable collaboration features"
    )


class MultiAgentMemoryConfig(BaseModel):
    """Configuration for multi-agent memory management."""
    
    enabled: bool = True
    default_scope: str = "private"
    enable_collaboration: bool = True


class MultiUserConfig(BaseModel):
    """Configuration for multi-user memory management."""
    
    enabled: bool = True
    default_scope: str = "private"
    enable_collaboration: bool = True


class HybridConfig(BaseModel):
    """Configuration for hybrid memory management."""
    
    enabled: bool = True
    primary_mode: str = "multi_user"
    fallback_mode: str = "multi_agent"
    auto_switch_threshold: float = 0.8


class QueryRewriteConfig(BaseModel):
    """Configuration for query rewrite module."""

    enabled: bool = Field(
        default=False,
        description="Whether to enable query rewrite functionality"
    )
    prompt: Optional[str] = Field(
        default=None,
        description="Custom rewrite prompt, uses default prompt if None"
    )
    model_override: Optional[str] = Field(
        default=None,
        description="Optional independent LLM model for rewrite (e.g., use a faster model)"
    )


class MemoryConfig(BaseModel):
    """Main memory configuration class."""

    vector_store: VectorStoreConfig = Field(
        description="Configuration for the vector store",
        default_factory=VectorStoreConfig,
    )
    llm: LlmConfig = Field(
        description="Configuration for the language model",
        default_factory=LlmConfig,
    )
    embedder: BaseEmbedderConfig = Field(
        description="Configuration for the embedding model",
        default_factory=OpenAIEmbeddingConfig,
    )
    graph_store: GraphStoreConfig = Field(
        description="Configuration for the graph",
        default_factory=GraphStoreConfig,
    )
    reranker: Optional[RerankConfig] = Field(
        description="Configuration for the reranker",
        default=None,
    )
    sparse_embedder: Optional[SparseEmbedderConfig] = Field(
        description="Configuration for the sparse embedder (only supported for OceanBase)",
        default=None,
    )
    version: str = Field(
        description="The version of the API",
        default="v1.1",
    )
    custom_fact_extraction_prompt: Optional[str] = Field(
        description="Custom prompt for the fact extraction",
        default=None,
    )
    custom_update_memory_prompt: Optional[str] = Field(
        description="Custom prompt for the update memory",
        default=None,
    )
    custom_importance_evaluation_prompt: Optional[str] = Field(
        description="Custom prompt for importance evaluation",
        default=None,
    )
    agent_memory: Optional[AgentMemoryConfig] = Field(
        description="Configuration for agent memory management",
        default=None,
    )
    intelligent_memory: Optional[IntelligentMemoryConfig] = Field(
        description="Configuration for intelligent memory management",
        default=None,
    )
    telemetry: Optional[TelemetryConfig] = Field(
        description="Configuration for telemetry and monitoring",
        default=None,
    )
    audit: Optional[AuditConfig] = Field(
        description="Configuration for audit logging",
        default=None,
    )
    logging: Optional[LoggingConfig] = Field(
        description="Configuration for application logging",
        default=None,
    )
    audio_llm: Optional[LlmConfig] = Field(
        description="Configuration for audio language model",
        default=None,
    )
    query_rewrite: Optional[QueryRewriteConfig] = Field(
        description="Configuration for query rewrite module",
        default=None,
    )

    def __init__(self, **data):
        super().__init__(**data)
        if self.agent_memory is None:
            self.agent_memory = AgentMemoryConfig()
        if self.intelligent_memory is None:
            self.intelligent_memory = IntelligentMemoryConfig()
        if self.telemetry is None:
            self.telemetry = TelemetryConfig()
        if self.audit is None:
            self.audit = AuditConfig()
        if self.logging is None:
            self.logging = LoggingConfig()
        if self.reranker is None:
            self.reranker = RerankConfig()
        if self.query_rewrite is None:
            self.query_rewrite = QueryRewriteConfig()

    def to_dict(self) -> Dict[str, Any]:
        result = self.model_dump(exclude_none=True)

        for field in ['embedder', 'llm', 'vector_store']:
            obj = getattr(self, field, None)
            if obj and hasattr(obj, 'to_component_dict'):
                result[field] = obj.to_component_dict()

        return result
