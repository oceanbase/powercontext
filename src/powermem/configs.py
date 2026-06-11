"""
Configuration classes for the memory system.

This module provides configuration classes for different components
of the memory system.
"""

from typing import Optional, Dict, Any
from pydantic import BaseModel, Field, field_validator

from powermem.integrations.embeddings.config.base import BaseEmbedderConfig
from powermem.integrations.embeddings.config.providers import (
    PyseekdbDefaultEmbeddingConfig,
)
from powermem.integrations.embeddings.config.sparse_base import BaseSparseEmbedderConfig
import powermem.integrations.embeddings.config.sparse_providers  # noqa: F401 — ensures sparse provider registry is populated
from powermem.integrations.llm.config.base import BaseLLMConfig
from powermem.integrations.llm.config.qwen import QwenConfig
from powermem.storage.config.base import BaseVectorStoreConfig, BaseGraphStoreConfig
from powermem.storage.config.sqlite import SQLiteConfig  # noqa: F401 — keeps SQLite provider registered
from powermem.storage.config.oceanbase import (
    OceanBaseConfig,
    OceanBaseGraphConfig,  # noqa: F401 — keeps OceanBase graph provider registered
)
from powermem.integrations.rerank.config.base import BaseRerankConfig


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
        default=1.5,
        description="Base memory decay strength; larger values decay slower"
    )
    decay_rate_multipliers: Dict[str, float] = Field(
        default_factory=lambda: {
            "working": 1,
            "short_term": 7,
            "long_term": 60,
        },
        description=(
            "Per memory_type multiplier on base decay_rate. Larger multipliers "
            "decay slower and should satisfy working < short_term < long_term."
        ),
    )
    reinforcement_factor: float = Field(
        default=0.3,
        description="Factor by which memories are reinforced when accessed"
    )
    forgotten_score_multiplier: float = Field(
        default=0.1,
        description=(
            "Multiplier applied to search ranking scores for memories "
            "marked should_forget"
        )
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
    review_adjustment_factor: float = Field(
        default=0.3,
        description="How strongly importance shortens review intervals (0-1 scale)",
    )
    review_interval_min_hours: float = Field(
        default=0.5,
        description="Minimum hours between scheduled reviews",
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


class SkillStoreConfig(BaseModel):
    """Configuration for skill sub-store."""
    enabled: bool = Field(default=False, description="Enable skill storage")
    collection_name: Optional[str] = Field(default=None, description="Table name; auto-generated if None")
    similarity_threshold: float = Field(default=0.03, description="Dedup RRF score threshold")
    index_type: Optional[str] = Field(default=None, description="Vector index type (hnsw, ivf, etc.). Falls back to vector_store.config.index_type if not set")


class SourceStoreConfig(BaseModel):
    """Configuration for source store (fact-source linking).

    When enabled, a sources table is created alongside the memory table
    so that each memory record can be linked back to its origin
    (conversation, file upload, API call, etc.).
    """
    enabled: bool = Field(default=False, description="Enable source linking")
    collection_name: Optional[str] = Field(default=None, description="Table name; auto-generated if None")


class MemoryConfig(BaseModel):
    """Main memory configuration class."""

    vector_store: BaseVectorStoreConfig = Field(
        description=(
            "Configuration for the vector store. Defaults to the OceanBase "
            "provider with an empty host, which boots embedded seekdb on "
            "disk (no separate server) so PowerMem starts with zero ops; "
            "set OCEANBASE_HOST to point at a remote OceanBase cluster, or "
            "switch the provider to sqlite / postgres."
        ),
        default_factory=OceanBaseConfig,
    )
    llm: BaseLLMConfig = Field(
        description="Configuration for the language model",
        default_factory=QwenConfig,
    )
    embedder: BaseEmbedderConfig = Field(
        description=(
            "Configuration for the embedding model. Defaults to the built-in local "
            "all-MiniLM-L6-v2 model (384 dims) so PowerMem can start with zero "
            "configuration; override to use OpenAI/Qwen/SiliconFlow/etc."
        ),
        default_factory=PyseekdbDefaultEmbeddingConfig,
    )
    graph_store: Optional[BaseGraphStoreConfig] = Field(
        description="Configuration for the graph store (None means disabled)",
        default=None,
    )
    reranker: Optional[BaseRerankConfig] = Field(
        description="Configuration for the reranker",
        default=None,
    )
    sparse_embedder: Optional[BaseSparseEmbedderConfig] = Field(
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
    audio_llm: Optional[BaseLLMConfig] = Field(
        description="Configuration for audio language model",
        default=None,
    )
    query_rewrite: Optional[QueryRewriteConfig] = Field(
        description="Configuration for query rewrite module",
        default=None,
    )
    skill_store: Optional[SkillStoreConfig] = Field(
        description="Configuration for skill storage (None means disabled)",
        default=None,
    )
    source_store: Optional[SourceStoreConfig] = Field(
        description="Configuration for source store / fact-source linking (None means disabled)",
        default=None,
    )

    @field_validator('sparse_embedder', mode='before')
    @classmethod
    def resolve_sparse_embedder(cls, v):
        if isinstance(v, dict) and 'provider' in v:
            provider = v['provider'].lower()
            config_dict = v.get('config', {})
            config_cls = (
                BaseSparseEmbedderConfig.get_provider_config_cls(provider)
                or BaseSparseEmbedderConfig
            )
            return config_cls(**config_dict)
        return v

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
            self.reranker = BaseRerankConfig()
        if self.query_rewrite is None:
            self.query_rewrite = QueryRewriteConfig()
        
        # Sync embedding_model_dims from embedder if not set in vector_store/graph_store
        embedder_dims = getattr(self.embedder, 'embedding_dims', None)
        if embedder_dims is not None:
            # Sync to vector_store if not set
            if hasattr(self.vector_store, 'embedding_model_dims') and self.vector_store.embedding_model_dims is None:
                self.vector_store.embedding_model_dims = embedder_dims
            # Sync to graph_store if not set
            if self.graph_store is not None:
                if hasattr(self.graph_store, 'embedding_model_dims') and self.graph_store.embedding_model_dims is None:
                    self.graph_store.embedding_model_dims = embedder_dims

    def to_dict(self) -> Dict[str, Any]:
        result = self.model_dump(exclude_none=True)

        for field in ['embedder', 'llm', 'vector_store', 'reranker', 'graph_store', 'sparse_embedder']:
            obj = getattr(self, field, None)
            if obj and hasattr(obj, 'to_component_dict'):
                result[field] = obj.to_component_dict()

        return result
