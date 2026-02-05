"""
Configuration loader for powermem

This module provides utilities for loading configuration from environment variables
or other sources. It simplifies the configuration setup process.
"""

from typing import Any, Dict, Optional
import warnings

from pydantic import AliasChoices, BaseModel, ConfigDict, Field
from pydantic_settings import BaseSettings

from powermem.integrations.embeddings.config.base import BaseEmbedderConfig
from powermem.integrations.embeddings.config.providers import CustomEmbeddingConfig
from powermem.integrations.embeddings.config.sparse_base import BaseSparseEmbedderConfig
from powermem.integrations.llm.config.base import BaseLLMConfig
from powermem.settings import _DEFAULT_ENV_FILE, settings_config


def _load_dotenv_if_available() -> None:
    if not _DEFAULT_ENV_FILE:
        return
    try:
        from dotenv import load_dotenv
    except Exception:
        return
    load_dotenv(_DEFAULT_ENV_FILE, override=False)


class _BasePowermemSettings(BaseSettings):
    model_config = settings_config()


class TelemetrySettings(_BasePowermemSettings):
    model_config = settings_config("TELEMETRY_")

    enabled: bool = Field(default=False, serialization_alias="enable_telemetry")
    endpoint: str = Field(
        default="https://telemetry.powermem.ai",
        serialization_alias="telemetry_endpoint",
    )
    api_key: Optional[str] = Field(
        default=None,
        serialization_alias="telemetry_api_key",
    )
    batch_size: int = Field(
        default=100,
        validation_alias=AliasChoices("BATCH_SIZE", "TELEMETRY_BATCH_SIZE"),
        serialization_alias="telemetry_batch_size",
    )
    flush_interval: int = Field(
        default=30,
        validation_alias=AliasChoices("FLUSH_INTERVAL", "TELEMETRY_FLUSH_INTERVAL"),
        serialization_alias="telemetry_flush_interval",
    )
    retention_days: int = Field(default=30)

    def to_config(self) -> Dict[str, Any]:
        config = self.model_dump(
            by_alias=True,
            include={
                "enabled",
                "endpoint",
                "api_key",
                "batch_size",
                "flush_interval",
            },
        )
        config["batch_size"] = self.batch_size
        config["flush_interval"] = self.flush_interval
        return config


class AuditSettings(_BasePowermemSettings):
    model_config = settings_config("AUDIT_")

    enabled: bool = Field(default=True)
    log_file: str = Field(default="./logs/audit.log")
    log_level: str = Field(default="INFO")
    retention_days: int = Field(default=90)
    compress_logs: bool = Field(default=True)
    log_rotation_size: Optional[str] = Field(default=None)

    def to_config(self) -> Dict[str, Any]:
        return self.model_dump(
            include={"enabled", "log_file", "log_level", "retention_days"}
        )


class LoggingSettings(_BasePowermemSettings):
    model_config = settings_config("LOGGING_")

    level: str = Field(default="DEBUG")
    format: str = Field(
        default="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    file: str = Field(default="./logs/powermem.log")
    max_size: str = Field(default="100MB")
    backup_count: int = Field(default=5)
    compress_backups: bool = Field(default=True)
    console_enabled: bool = Field(default=True)
    console_level: str = Field(default="INFO")
    console_format: str = Field(default="%(levelname)s - %(message)s")

    def to_config(self) -> Dict[str, Any]:
        return self.model_dump(include={"level", "format", "file"})


class DatabaseSettings(_BasePowermemSettings):
    model_config = settings_config()

    provider: str = Field(
        default="sqlite",
        validation_alias=AliasChoices("DATABASE_PROVIDER"),
    )

    def to_config(self) -> Dict[str, Any]:
        """
        Convert settings to VectorStore configuration dictionary.
        
        Provider-specific fields are automatically loaded from environment
        variables by the provider config class.
        """
        from powermem.storage.config.base import BaseVectorStoreConfig
        
        db_provider = self.provider.lower()
        
        # Handle postgres alias
        if db_provider == "postgres":
            db_provider = "pgvector"
        
        # 1. Get provider config class from registry
        config_cls = (
            BaseVectorStoreConfig.get_provider_config_cls(db_provider)
            or BaseVectorStoreConfig
        )
        
        # 2. Create provider settings from environment variables
        # All provider-specific fields are loaded here automatically
        provider_settings = config_cls()
        
        # 3. Export to dict
        vector_store_config = provider_settings.model_dump(exclude_none=True)
        
        # 4. For OceanBase, build connection_args for backward compatibility
        if db_provider == "oceanbase":
            connection_args = {}
            for key in ["host", "port", "user", "password", "db_name"]:
                if key in vector_store_config:
                    connection_args[key] = vector_store_config[key]
            
            # Only add connection_args if we have connection parameters
            if connection_args:
                vector_store_config["connection_args"] = connection_args
        
        return {"provider": db_provider, "config": vector_store_config}


class LLMSettings(_BasePowermemSettings):
    """
    Unified LLM configuration settings.
    
    This class provides a common interface for configuring LLM providers.
    It only contains fields that are common across all providers.
    Provider-specific fields (e.g., dashscope_base_url for Qwen) should be
    set via environment variables and will be loaded by the respective provider config classes.
    
    Design rationale: This follows the same pattern as EmbeddingSettings,
    keeping the unified settings simple and delegating provider-specific
    configuration to the provider config classes.
    """
    model_config = settings_config("LLM_")

    provider: str = Field(default="qwen")
    api_key: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices(
            "LLM_API_KEY",
            "QWEN_API_KEY",
            "DASHSCOPE_API_KEY",
        ),
    )
    model: Optional[str] = Field(default=None)
    temperature: float = Field(default=0.7)
    max_tokens: int = Field(default=1000)
    top_p: float = Field(default=0.8)
    top_k: int = Field(default=50)

    def to_config(self) -> Dict[str, Any]:
        """
        Convert settings to LLM configuration dictionary.
        
        This method:
        1. Gets the appropriate provider config class
        2. Creates an instance (loading provider-specific fields from environment)
        3. Overrides with explicitly set common fields from this settings object
        4. Returns the final configuration
        
        Provider-specific fields (e.g., dashscope_base_url, enable_search) are
        automatically loaded from environment variables by the provider config class.
        """
        llm_provider = self.provider.lower()

        # Determine model name
        llm_model = self.model
        if llm_model is None:
            llm_model = "qwen-plus" if llm_provider == "qwen" else "gpt-4o-mini"

        # 1. Get provider config class from registry
        config_cls = (
            BaseLLMConfig.get_provider_config_cls(llm_provider)
            or BaseLLMConfig  # fallback to base config
        )

        # 2. Create provider settings from environment variables
        # Provider-specific fields are automatically loaded here
        provider_settings = config_cls()

        # 3. Collect common fields to override
        overrides = {}
        for field in ("api_key", "temperature", "max_tokens", "top_p", "top_k"):
            if field in self.model_fields_set:
                value = getattr(self, field)
                if value is not None:
                    overrides[field] = value

        # Always set model
        overrides["model"] = llm_model

        # 4. Update configuration with overrides
        if overrides:
            provider_settings = provider_settings.model_copy(update=overrides)

        # 5. Export to dict
        llm_config = provider_settings.model_dump(exclude_none=True)

        return {"provider": llm_provider, "config": llm_config}


class EmbeddingSettings(_BasePowermemSettings):
    model_config = settings_config("EMBEDDING_")

    provider: str = Field(default="qwen")
    api_key: Optional[str] = Field(default=None)
    model: Optional[str] = Field(default=None)
    embedding_dims: Optional[int] = Field(
        default=None,
        validation_alias=AliasChoices("EMBEDDING_DIMS", "DIMS"),
    )

    def to_config(self) -> Dict[str, Any]:
        embedding_provider = self.provider.lower()
        config_cls = (
            BaseEmbedderConfig.get_provider_config_cls(embedding_provider)
            or CustomEmbeddingConfig
        )
        provider_settings = config_cls()
        overrides = {}
        for field in ("api_key", "model", "embedding_dims"):
            if field in self.model_fields_set:
                value = getattr(self, field)
                if value is not None:
                    overrides[field] = value
        if overrides:
            provider_settings = provider_settings.model_copy(update=overrides)
        embedding_config = provider_settings.model_dump(exclude_none=True)
        return {"provider": embedding_provider, "config": embedding_config}


class IntelligentMemorySettings(_BasePowermemSettings):
    model_config = settings_config("INTELLIGENT_MEMORY_")

    enabled: bool = Field(default=True)
    initial_retention: float = Field(default=1.0)
    decay_rate: float = Field(default=0.1)
    reinforcement_factor: float = Field(default=0.3)
    working_threshold: float = Field(default=0.3)
    short_term_threshold: float = Field(default=0.6)
    long_term_threshold: float = Field(default=0.8)
    fallback_to_simple_add: bool = Field(default=False)

    def to_config(self) -> Dict[str, Any]:
        return self.model_dump()


class MemoryDecaySettings(_BasePowermemSettings):
    model_config = settings_config("MEMORY_DECAY_")

    enabled: bool = Field(default=True)
    algorithm: str = Field(default="ebbinghaus")
    base_retention: float = Field(default=1.0)
    forgetting_rate: float = Field(default=0.1)
    reinforcement_factor: float = Field(default=0.3)

    def to_config(self) -> Dict[str, Any]:
        """Convert MemoryDecaySettings to config dict."""
        return self.model_dump()


class AgentMemorySettings(_BasePowermemSettings):
    model_config = settings_config("AGENT_")

    enabled: bool = Field(default=True)
    memory_mode: str = Field(default="auto", serialization_alias="mode")
    default_scope: str = Field(default="AGENT")
    default_privacy_level: str = Field(default="PRIVATE")
    default_collaboration_level: str = Field(default="READ_ONLY")
    default_access_permission: str = Field(default="OWNER_ONLY")

    def to_config(self) -> Dict[str, Any]:
        return self.model_dump(
            by_alias=True,
            include={
                "enabled",
                "memory_mode",
                "default_scope",
                "default_privacy_level",
                "default_collaboration_level",
                "default_access_permission",
            },
        )


class TimezoneSettings(_BasePowermemSettings):
    model_config = settings_config()

    timezone: str = Field(default="UTC")

    def to_config(self) -> Dict[str, Any]:
        return self.model_dump()


class RerankerSettings(_BasePowermemSettings):
    model_config = settings_config("RERANKER_")

    enabled: bool = Field(default=False)
    provider: str = Field(default="qwen")
    model: Optional[str] = Field(default=None)
    api_key: Optional[str] = Field(default=None)
    api_base_url: Optional[str] = Field(default=None)
    top_n: Optional[int] = Field(default=None)

    def to_config(self) -> Dict[str, Any]:
        """
        Convert settings to Rerank configuration dictionary.
        
        This method:
        1. Gets the appropriate provider config class
        2. Creates an instance (loading provider-specific fields from environment)
        3. Overrides with explicitly set fields from this settings object
        4. Returns the final configuration
        
        Provider-specific fields (e.g., api_base_url) are automatically loaded
        from environment variables by the provider config class.
        """
        from powermem.integrations.rerank.config.base import BaseRerankConfig
        
        rerank_provider = self.provider.lower()
        
        # 1. Get provider config class from registry
        config_cls = (
            BaseRerankConfig.get_provider_config_cls(rerank_provider)
            or BaseRerankConfig  # fallback to base config
        )
        
        # 2. Create provider settings from environment variables
        # Provider-specific fields are automatically loaded here
        provider_settings = config_cls()
        
        # 3. Collect fields to override
        overrides = {}
        for field in ("enabled", "model", "api_key", "api_base_url", "top_n"):
            if field in self.model_fields_set:
                value = getattr(self, field)
                if value is not None:
                    overrides[field] = value
        
        # 4. Update configuration with overrides
        if overrides:
            provider_settings = provider_settings.model_copy(update=overrides)
        
        # 5. Export using to_component_dict() to match RerankConfig structure
        return provider_settings.to_component_dict()


class QueryRewriteSettings(_BasePowermemSettings):
    model_config = settings_config("QUERY_REWRITE_")

    enabled: bool = Field(default=False)
    prompt: Optional[str] = Field(default=None)
    model_override: Optional[str] = Field(default=None)

    def to_config(self) -> Dict[str, Any]:
        return self.model_dump()


class SparseEmbedderSettings(_BasePowermemSettings):
    model_config = settings_config("SPARSE_EMBEDDER_")

    provider: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("SPARSE_EMBEDDER_PROVIDER"),
    )
    api_key: Optional[str] = Field(default=None)
    model: Optional[str] = Field(default=None)
    base_url: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("SPARSE_EMBEDDING_BASE_URL"),
    )
    embedding_dims: Optional[int] = Field(
        default=None,
        validation_alias=AliasChoices("SPARSE_EMBEDDER_DIMS"),
    )

    def to_config(self) -> Optional[Dict[str, Any]]:
        if not self.provider:
            return None
        provider = self.provider.lower()
        config_cls = (
            BaseSparseEmbedderConfig.get_provider_config_cls(provider)
            or BaseSparseEmbedderConfig
        )
        provider_settings = config_cls()
        overrides = {}
        for field in ("api_key", "model", "base_url", "embedding_dims"):
            if field in self.model_fields_set:
                value = getattr(self, field)
                if value is not None:
                    overrides[field] = value
        if overrides:
            provider_settings = provider_settings.model_copy(update=overrides)
        return provider_settings.to_component_dict()


class PerformanceSettings(_BasePowermemSettings):
    model_config = settings_config()

    memory_batch_size: int = Field(
        default=100,
        validation_alias=AliasChoices("MEMORY_BATCH_SIZE"),
    )
    memory_cache_size: int = Field(
        default=1000,
        validation_alias=AliasChoices("MEMORY_CACHE_SIZE"),
    )
    memory_cache_ttl: int = Field(
        default=3600,
        validation_alias=AliasChoices("MEMORY_CACHE_TTL"),
    )
    memory_search_limit: int = Field(
        default=10,
        validation_alias=AliasChoices("MEMORY_SEARCH_LIMIT"),
    )
    memory_search_threshold: float = Field(
        default=0.7,
        validation_alias=AliasChoices("MEMORY_SEARCH_THRESHOLD"),
    )
    vector_store_batch_size: int = Field(
        default=50,
        validation_alias=AliasChoices("VECTOR_STORE_BATCH_SIZE"),
    )
    vector_store_cache_size: int = Field(
        default=500,
        validation_alias=AliasChoices("VECTOR_STORE_CACHE_SIZE"),
    )
    vector_store_index_rebuild_interval: int = Field(
        default=86400,
        validation_alias=AliasChoices("VECTOR_STORE_INDEX_REBUILD_INTERVAL"),
    )

    def to_config(self) -> Dict[str, Any]:
        """Convert PerformanceSettings to config dict."""
        return self.model_dump()


class SecuritySettings(_BasePowermemSettings):
    model_config = settings_config()

    encryption_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices("ENCRYPTION_ENABLED"),
    )
    encryption_key: str = Field(
        default="",
        validation_alias=AliasChoices("ENCRYPTION_KEY"),
    )
    encryption_algorithm: str = Field(
        default="AES-256-GCM",
        validation_alias=AliasChoices("ENCRYPTION_ALGORITHM"),
    )
    access_control_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices("ACCESS_CONTROL_ENABLED"),
    )
    access_control_default_permission: str = Field(
        default="READ_ONLY",
        validation_alias=AliasChoices("ACCESS_CONTROL_DEFAULT_PERMISSION"),
    )
    access_control_admin_users: str = Field(
        default="admin,root",
        validation_alias=AliasChoices("ACCESS_CONTROL_ADMIN_USERS"),
    )

    def to_config(self) -> Dict[str, Any]:
        """Convert SecuritySettings to config dict."""
        return self.model_dump()


class GraphStoreSettings(_BasePowermemSettings):
    model_config = settings_config("GRAPH_STORE_")

    enabled: bool = Field(default=False)
    provider: str = Field(default="oceanbase")
    custom_prompt: Optional[str] = Field(default=None)
    custom_extract_relations_prompt: Optional[str] = Field(default=None)
    custom_update_graph_prompt: Optional[str] = Field(default=None)
    custom_delete_relations_prompt: Optional[str] = Field(default=None)

    def to_config(
        self,
    ) -> Optional[Dict[str, Any]]:
        """
        Convert settings to GraphStore configuration dictionary.
        
        Provider-specific fields are automatically loaded from environment
        variables by the provider config class (with fallback to VectorStore env vars).
        """
        if not self.enabled:
            return None
        
        from powermem.storage.config.base import BaseGraphStoreConfig
        
        graph_provider = self.provider.lower()
        
        # 1. Get provider config class from registry
        config_cls = (
            BaseGraphStoreConfig.get_provider_config_cls(graph_provider)
            or BaseGraphStoreConfig
        )
        
        # 2. Create provider settings from environment variables
        provider_settings = config_cls()
        
        # 3. Export to dict
        graph_config = provider_settings.model_dump(exclude_none=True)
        
        # 4. Build final config
        graph_store_config = {
            "enabled": True,
            "provider": graph_provider,
            "config": graph_config,
        }
        
        # 5. Add custom prompts if configured
        if self.custom_prompt:
            graph_store_config["custom_prompt"] = self.custom_prompt
        if self.custom_extract_relations_prompt:
            graph_store_config["custom_extract_relations_prompt"] = (
                self.custom_extract_relations_prompt
            )
        if self.custom_update_graph_prompt:
            graph_store_config["custom_update_graph_prompt"] = (
                self.custom_update_graph_prompt
            )
        if self.custom_delete_relations_prompt:
            graph_store_config["custom_delete_relations_prompt"] = (
                self.custom_delete_relations_prompt
            )

        return graph_store_config


class PowermemSettings:
    _COMPONENTS = {
        "vector_store": ("database", DatabaseSettings),
        "llm": ("llm", LLMSettings),
        "embedder": ("embedder", EmbeddingSettings),
        "intelligent_memory": ("intelligent_memory", IntelligentMemorySettings),
        "agent_memory": ("agent_memory", AgentMemorySettings),
        "timezone": ("timezone", TimezoneSettings),
        "reranker": ("reranker", RerankerSettings),
        "query_rewrite": ("query_rewrite", QueryRewriteSettings),
        "telemetry": ("telemetry", TelemetrySettings),
        "audit": ("audit", AuditSettings),
        "logging": ("logging", LoggingSettings),
        "performance": ("performance", PerformanceSettings),
        "security": ("security", SecuritySettings),
        "memory_decay": ("memory_decay", MemoryDecaySettings),
    }

    def __init__(self) -> None:
        for _, (attr_name, component_cls) in self._COMPONENTS.items():
            setattr(self, attr_name, component_cls())
        self.graph_store = GraphStoreSettings()
        self.sparse_embedder = SparseEmbedderSettings()

    def to_config(self) -> Dict[str, Any]:
        config = {}
        for output_key, (attr_name, _) in self._COMPONENTS.items():
            component_config = getattr(self, attr_name).to_config()
            if component_config is not None:
                config[output_key] = component_config

        graph_store_config = self.graph_store.to_config()
        if graph_store_config:
            config["graph_store"] = graph_store_config

        sparse_embedder_config = self.sparse_embedder.to_config()
        if sparse_embedder_config:
            config["sparse_embedder"] = sparse_embedder_config

        # Sync embedding_model_dims from embedder to vector_store and graph_store
        embedder_config = config.get("embedder", {})
        embedder_dims = embedder_config.get("config", {}).get("embedding_dims")
        
        if embedder_dims is not None:
            # Sync to vector_store if not set
            vector_store_config = config.get("vector_store", {})
            vector_store_inner_config = vector_store_config.get("config", {})
            if vector_store_inner_config.get("embedding_model_dims") is None:
                vector_store_inner_config["embedding_model_dims"] = embedder_dims
            
            # Sync to graph_store if not set
            if graph_store_config:
                graph_store_inner_config = graph_store_config.get("config", {})
                if graph_store_inner_config.get("embedding_model_dims") is None:
                    graph_store_inner_config["embedding_model_dims"] = embedder_dims

        return config


def load_config_from_env() -> Dict[str, Any]:
    """
    Load configuration from environment variables.

    Deprecated for direct use: prefer `auto_config()` or `create_memory()`.

    This function reads configuration from environment variables and builds a config dictionary.
    You can use this when you have .env file set up to avoid manually building config dict.
    
    It automatically detects the database provider (sqlite, oceanbase, postgres) and builds
    the appropriate configuration.
    
    Returns:
        Configuration dictionary built from environment variables
        
    Example:
        ```python
        from dotenv import load_dotenv
        from powermem.config_loader import load_config_from_env
        
        # Load .env file
        load_dotenv()
        
        # Get config
        config = load_config_from_env()
        
        # Use config
        from powermem import Memory
        memory = Memory(config=config)
        ```
    """
    _load_dotenv_if_available()
    return PowermemSettings().to_config()


class CreateConfigOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    database_provider: str = "sqlite"
    llm_provider: str = "qwen"
    embedding_provider: str = "qwen"
    database_config: Dict[str, Any] = Field(default_factory=dict)

    llm_api_key: Optional[str] = None
    llm_model: str = "qwen-plus"
    llm_temperature: float = 0.7
    llm_max_tokens: int = 1000
    llm_top_p: float = 0.8
    llm_top_k: int = 50
    llm_extra: Dict[str, Any] = Field(default_factory=dict)

    embedding_api_key: Optional[str] = None
    embedding_model: str = "text-embedding-v4"
    embedding_dims: int = 1536
    embedding_extra: Dict[str, Any] = Field(default_factory=dict)


def create_config(
    database_provider: str = "sqlite",
    llm_provider: str = "qwen",
    embedding_provider: str = "qwen",
    database_config: Optional[Dict[str, Any]] = None,
    llm_api_key: Optional[str] = None,
    llm_model: str = "qwen-plus",
    llm_temperature: float = 0.7,
    llm_max_tokens: int = 1000,
    llm_top_p: float = 0.8,
    llm_top_k: int = 50,
    llm_extra: Optional[Dict[str, Any]] = None,
    embedding_api_key: Optional[str] = None,
    embedding_model: str = "text-embedding-v4",
    embedding_dims: int = 1536,
    embedding_extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Create a basic configuration dictionary with specified providers.

    Deprecated: prefer `auto_config()` or `create_memory()` unless you
    need a minimal manual config.

    Args:
        database_provider: Database provider ('sqlite', 'oceanbase', 'postgres')
        llm_provider: LLM provider ('qwen', 'openai', etc.)
        embedding_provider: Embedding provider ('qwen', 'openai', etc.)
        database_config: Vector store configuration dictionary
        llm_api_key: API key for the LLM provider
        llm_model: LLM model name
        llm_temperature: LLM temperature
        llm_max_tokens: Max tokens
        llm_top_p: LLM top-p
        llm_top_k: LLM top-k
        llm_extra: Provider-specific LLM configuration fields
        embedding_api_key: API key for embedding provider
        embedding_model: Embedding model name
        embedding_dims: Embedding vector dimensions
        embedding_extra: Provider-specific embedding configuration fields
    
    Returns:
        Configuration dictionary
        
    Example:
        ```python
        from powermem.config_loader import create_config
        from powermem import Memory
        
        config = create_config(
            database_provider='sqlite',
            llm_provider='qwen',
            llm_api_key='your_key',
            llm_model='qwen-plus'
        )
        
        memory = Memory(config=config)
        ```
    """
    warnings.warn(
        "create_config is deprecated; prefer auto_config() or create_memory().",
        DeprecationWarning,
        stacklevel=2,
    )
    options = CreateConfigOptions(
        database_provider=database_provider,
        llm_provider=llm_provider,
        embedding_provider=embedding_provider,
        database_config=database_config or {},
        llm_api_key=llm_api_key,
        llm_model=llm_model,
        llm_temperature=llm_temperature,
        llm_max_tokens=llm_max_tokens,
        llm_top_p=llm_top_p,
        llm_top_k=llm_top_k,
        llm_extra=llm_extra or {},
        embedding_api_key=embedding_api_key,
        embedding_model=embedding_model,
        embedding_dims=embedding_dims,
        embedding_extra=embedding_extra or {},
    )
    config = {
        "vector_store": {
            "provider": options.database_provider,
            "config": options.database_config,
        },
        "llm": {
            "provider": options.llm_provider,
            "config": {
                "api_key": options.llm_api_key,
                "model": options.llm_model,
                "temperature": options.llm_temperature,
                "max_tokens": options.llm_max_tokens,
                "top_p": options.llm_top_p,
                "top_k": options.llm_top_k,
                **options.llm_extra,
            },
        },
        "embedder": {
            "provider": options.embedding_provider,
            "config": {
                "api_key": options.embedding_api_key,
                "model": options.embedding_model,
                "embedding_dims": options.embedding_dims,
                **options.embedding_extra,
            },
        },
    }
    
    # Sync embedding_model_dims from embedder to vector_store if not set
    if config["vector_store"]["config"].get("embedding_model_dims") is None:
        config["vector_store"]["config"]["embedding_model_dims"] = options.embedding_dims
    
    return config


def validate_config(config: Dict[str, Any]) -> bool:
    """
    Validate a configuration dictionary.

    Deprecated for new code paths: prefer `create_memory()` or `auto_config()`.

    Args:
        config: Configuration dictionary to validate
        
    Returns:
        True if valid, False otherwise
        
    Example:
        ```python
        from powermem.config_loader import load_config_from_env, validate_config
        
        config = load_config_from_env()
        if validate_config(config):
            print("Configuration is valid!")
        ```
    """
    required_sections = ['vector_store', 'llm', 'embedder']
    
    for section in required_sections:
        if section not in config:
            return False
        
        if 'provider' not in config[section]:
            return False
        
        if 'config' not in config[section]:
            return False
    
    return True


def auto_config() -> Dict[str, Any]:
    """
    Automatically load configuration from environment variables.
    
    This is the simplest way to get configuration.
    It automatically loads .env file and returns the config.

    Preferred entrypoint for configuration loading.

    Returns:
        Configuration dictionary from environment variables
        
    Example:
        ```python
        from powermem import Memory
        
        # Simplest way - just load from .env
        memory = Memory(config=auto_config())
        
        # Or even simpler with create_memory()
        from powermem import create_memory
        memory = create_memory()  # Auto loads from .env
        ```
    """
    return load_config_from_env()
