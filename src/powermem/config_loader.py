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
        default="oceanbase",
        validation_alias=AliasChoices("DATABASE_PROVIDER"),
    )
    database_sslmode: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("DATABASE_SSLMODE"),
    )
    database_pool_size: Optional[int] = Field(
        default=None,
        validation_alias=AliasChoices("DATABASE_POOL_SIZE"),
    )
    database_max_overflow: Optional[int] = Field(
        default=None,
        validation_alias=AliasChoices("DATABASE_MAX_OVERFLOW"),
    )
    sqlite_path: str = Field(
        default="./data/powermem_dev.db",
        validation_alias=AliasChoices("SQLITE_PATH"),
    )
    sqlite_collection: str = Field(
        default="memories",
        validation_alias=AliasChoices("SQLITE_COLLECTION"),
    )
    sqlite_enable_wal: bool = Field(
        default=True,
        validation_alias=AliasChoices("SQLITE_ENABLE_WAL"),
    )
    sqlite_timeout: int = Field(
        default=30,
        validation_alias=AliasChoices("SQLITE_TIMEOUT"),
    )
    oceanbase_host: str = Field(
        default="127.0.0.1",
        validation_alias=AliasChoices("OCEANBASE_HOST"),
    )
    oceanbase_port: int = Field(
        default=2881,
        validation_alias=AliasChoices("OCEANBASE_PORT"),
    )
    oceanbase_user: str = Field(
        default="root@sys",
        validation_alias=AliasChoices("OCEANBASE_USER"),
    )
    oceanbase_password: str = Field(
        default="password",
        validation_alias=AliasChoices("OCEANBASE_PASSWORD"),
    )
    oceanbase_database: str = Field(
        default="powermem",
        validation_alias=AliasChoices("OCEANBASE_DATABASE"),
    )
    oceanbase_collection: str = Field(
        default="memories",
        validation_alias=AliasChoices("OCEANBASE_COLLECTION"),
    )
    oceanbase_vector_metric_type: str = Field(
        default="cosine",
        validation_alias=AliasChoices("OCEANBASE_VECTOR_METRIC_TYPE"),
    )
    oceanbase_index_type: str = Field(
        default="IVF_FLAT",
        validation_alias=AliasChoices("OCEANBASE_INDEX_TYPE"),
    )
    oceanbase_embedding_model_dims: int = Field(
        default=1536,
        validation_alias=AliasChoices("OCEANBASE_EMBEDDING_MODEL_DIMS"),
    )
    oceanbase_primary_field: str = Field(
        default="id",
        validation_alias=AliasChoices("OCEANBASE_PRIMARY_FIELD"),
    )
    oceanbase_vector_field: str = Field(
        default="embedding",
        validation_alias=AliasChoices("OCEANBASE_VECTOR_FIELD"),
    )
    oceanbase_text_field: str = Field(
        default="document",
        validation_alias=AliasChoices("OCEANBASE_TEXT_FIELD"),
    )
    oceanbase_metadata_field: str = Field(
        default="metadata",
        validation_alias=AliasChoices("OCEANBASE_METADATA_FIELD"),
    )
    oceanbase_vidx_name: str = Field(
        default="memories_vidx",
        validation_alias=AliasChoices("OCEANBASE_VIDX_NAME"),
    )
    oceanbase_include_sparse: bool = Field(
        default=False,
        validation_alias=AliasChoices("SPARSE_VECTOR_ENABLE"),
    )
    postgres_collection: str = Field(
        default="memories",
        validation_alias=AliasChoices("POSTGRES_COLLECTION"),
    )
    postgres_database: str = Field(
        default="powermem",
        validation_alias=AliasChoices("POSTGRES_DATABASE"),
    )
    postgres_host: str = Field(
        default="127.0.0.1",
        validation_alias=AliasChoices("POSTGRES_HOST"),
    )
    postgres_port: int = Field(
        default=5432,
        validation_alias=AliasChoices("POSTGRES_PORT"),
    )
    postgres_user: str = Field(
        default="postgres",
        validation_alias=AliasChoices("POSTGRES_USER"),
    )
    postgres_password: str = Field(
        default="password",
        validation_alias=AliasChoices("POSTGRES_PASSWORD"),
    )
    postgres_embedding_model_dims: int = Field(
        default=1536,
        validation_alias=AliasChoices("POSTGRES_EMBEDDING_MODEL_DIMS"),
    )
    postgres_diskann: bool = Field(
        default=True,
        validation_alias=AliasChoices("POSTGRES_DISKANN"),
    )
    postgres_hnsw: bool = Field(
        default=True,
        validation_alias=AliasChoices("POSTGRES_HNSW"),
    )

    def _build_oceanbase_config(self) -> Dict[str, Any]:
        connection_args = {
            "host": self.oceanbase_host,
            "port": self.oceanbase_port,
            "user": self.oceanbase_user,
            "password": self.oceanbase_password,
            "db_name": self.oceanbase_database,
        }
        return {
            "collection_name": self.oceanbase_collection,
            "connection_args": connection_args,
            "vidx_metric_type": self.oceanbase_vector_metric_type,
            "index_type": self.oceanbase_index_type,
            "embedding_model_dims": self.oceanbase_embedding_model_dims,
            "primary_field": self.oceanbase_primary_field,
            "vector_field": self.oceanbase_vector_field,
            "text_field": self.oceanbase_text_field,
            "metadata_field": self.oceanbase_metadata_field,
            "vidx_name": self.oceanbase_vidx_name,
            "include_sparse": self.oceanbase_include_sparse,
        }

    def _build_postgres_config(self) -> Dict[str, Any]:
        return {
            "collection_name": self.postgres_collection,
            "dbname": self.postgres_database,
            "host": self.postgres_host,
            "port": self.postgres_port,
            "user": self.postgres_user,
            "password": self.postgres_password,
            "embedding_model_dims": self.postgres_embedding_model_dims,
            "diskann": self.postgres_diskann,
            "hnsw": self.postgres_hnsw,
        }

    def _build_sqlite_config(self) -> Dict[str, Any]:
        return {
            "database_path": self.sqlite_path,
            "collection_name": self.sqlite_collection,
            "enable_wal": self.sqlite_enable_wal,
            "timeout": self.sqlite_timeout,
        }

    def to_config(self) -> Dict[str, Any]:
        db_provider = self.provider.lower()
        builder = getattr(self, f"_build_{db_provider}_config", None)
        if not callable(builder):
            builder = self._build_sqlite_config
        return {"provider": db_provider, "config": builder()}


class LLMSettings(_BasePowermemSettings):
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
    enable_search: bool = Field(default=False)
    qwen_base_url: str = Field(
        default="https://dashscope.aliyuncs.com/api/v1",
        validation_alias=AliasChoices("QWEN_LLM_BASE_URL"),
    )
    openai_base_url: str = Field(
        default="https://api.openai.com/v1",
        validation_alias=AliasChoices("OPENAI_LLM_BASE_URL"),
    )
    siliconflow_base_url: str = Field(
        default="https://api.siliconflow.cn/v1",
        validation_alias=AliasChoices("SILICONFLOW_LLM_BASE_URL"),
    )
    ollama_base_url: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("OLLAMA_LLM_BASE_URL"),
    )
    vllm_base_url: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("VLLM_LLM_BASE_URL"),
    )
    anthropic_base_url: str = Field(
        default="https://api.anthropic.com",
        validation_alias=AliasChoices("ANTHROPIC_LLM_BASE_URL"),
    )
    deepseek_base_url: str = Field(
        default="https://api.deepseek.com",
        validation_alias=AliasChoices("DEEPSEEK_LLM_BASE_URL"),
    )

    def _apply_provider_config(
        self, provider: str, config: Dict[str, Any]
    ) -> None:
        configurer = getattr(self, f"_configure_{provider}", None)
        if callable(configurer):
            configurer(config)

    def _configure_qwen(self, config: Dict[str, Any]) -> None:
        config["dashscope_base_url"] = self.qwen_base_url
        config["enable_search"] = self.enable_search

    def _configure_openai(self, config: Dict[str, Any]) -> None:
        config["openai_base_url"] = self.openai_base_url

    def _configure_siliconflow(self, config: Dict[str, Any]) -> None:
        config["openai_base_url"] = self.siliconflow_base_url

    def _configure_ollama(self, config: Dict[str, Any]) -> None:
        if self.ollama_base_url is not None:
            config["ollama_base_url"] = self.ollama_base_url

    def _configure_vllm(self, config: Dict[str, Any]) -> None:
        if self.vllm_base_url is not None:
            config["vllm_base_url"] = self.vllm_base_url

    def _configure_anthropic(self, config: Dict[str, Any]) -> None:
        config["anthropic_base_url"] = self.anthropic_base_url

    def _configure_deepseek(self, config: Dict[str, Any]) -> None:
        config["deepseek_base_url"] = self.deepseek_base_url

    def to_config(self) -> Dict[str, Any]:
        llm_provider = self.provider.lower()
        llm_model = self.model
        if llm_model is None:
            llm_model = "qwen-plus" if llm_provider == "qwen" else "gpt-4o-mini"

        llm_config = {
            "api_key": self.api_key,
            "model": llm_model,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "top_p": self.top_p,
            "top_k": self.top_k,
        }

        self._apply_provider_config(llm_provider, llm_config)

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
    model_config = settings_config()

    enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices("MEMORY_DECAY_ENABLED"),
    )
    algorithm: str = Field(
        default="ebbinghaus",
        validation_alias=AliasChoices("MEMORY_DECAY_ALGORITHM"),
    )
    base_retention: float = Field(
        default=1.0,
        validation_alias=AliasChoices("MEMORY_DECAY_BASE_RETENTION"),
    )
    forgetting_rate: float = Field(
        default=0.1,
        validation_alias=AliasChoices("MEMORY_DECAY_FORGETTING_RATE"),
    )
    reinforcement_factor: float = Field(
        default=0.3,
        validation_alias=AliasChoices("MEMORY_DECAY_REINFORCEMENT_FACTOR"),
    )


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

    def to_config(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "provider": self.provider,
            "config": {
                "model": self.model,
                "api_key": self.api_key,
            },
        }


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
        config = {
            "api_key": self.api_key,
            "model": self.model,
            "base_url": self.base_url,
            "embedding_dims": self.embedding_dims,
        }
        config = {key: value for key, value in config.items() if value is not None}
        return {"provider": self.provider.lower(), "config": config}


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


class GraphStoreSettings(_BasePowermemSettings):
    model_config = settings_config("GRAPH_STORE_")

    enabled: bool = Field(default=False)
    provider: str = Field(default="oceanbase")
    host: Optional[str] = Field(default=None)
    port: Optional[int] = Field(default=None)
    user: Optional[str] = Field(default=None)
    password: Optional[str] = Field(default=None)
    db_name: Optional[str] = Field(default=None)
    vector_metric_type: Optional[str] = Field(default=None)
    index_type: Optional[str] = Field(default=None)
    embedding_model_dims: Optional[int] = Field(default=None)
    max_hops: Optional[int] = Field(default=None)
    custom_prompt: Optional[str] = Field(default=None)
    custom_extract_relations_prompt: Optional[str] = Field(default=None)
    custom_update_graph_prompt: Optional[str] = Field(default=None)
    custom_delete_relations_prompt: Optional[str] = Field(default=None)

    def _build_oceanbase_config(
        self, database_settings: "DatabaseSettings"
    ) -> Dict[str, Any]:
        graph_connection_args = {
            "host": _get_graph_value(
                self,
                "host",
                _get_db_value(
                    database_settings,
                    "oceanbase_host",
                ),
                "127.0.0.1",
            ),
            "port": _get_graph_value(
                self,
                "port",
                _get_db_value(
                    database_settings,
                    "oceanbase_port",
                ),
                2881,
            ),
            "user": _get_graph_value(
                self,
                "user",
                _get_db_value(
                    database_settings,
                    "oceanbase_user",
                ),
                "root@sys",
            ),
            "password": _get_graph_value(
                self,
                "password",
                _get_db_value(
                    database_settings,
                    "oceanbase_password",
                ),
                "password",
            ),
            "db_name": _get_graph_value(
                self,
                "db_name",
                _get_db_value(
                    database_settings,
                    "oceanbase_database",
                ),
                "powermem",
            ),
        }
        return {
            "host": graph_connection_args["host"],
            "port": graph_connection_args["port"],
            "user": graph_connection_args["user"],
            "password": graph_connection_args["password"],
            "db_name": graph_connection_args["db_name"],
            "vidx_metric_type": _get_graph_value_with_database(
                self,
                "vector_metric_type",
                database_settings,
                "oceanbase_vector_metric_type",
                "l2",
            ),
            "index_type": _get_graph_value_with_database(
                self,
                "index_type",
                database_settings,
                "oceanbase_index_type",
                "HNSW",
            ),
            "embedding_model_dims": _get_graph_value_with_database(
                self,
                "embedding_model_dims",
                database_settings,
                "oceanbase_embedding_model_dims",
                1536,
            ),
            "max_hops": _get_graph_value(
                self,
                "max_hops",
                None,
                3,
            ),
        }

    def to_config(
        self,
        database_settings: "DatabaseSettings",
    ) -> Optional[Dict[str, Any]]:
        if not self.enabled:
            return None

        graph_store_provider = self.provider.lower()
        builder = getattr(self, f"_build_{graph_store_provider}_config", None)
        graph_config = builder(database_settings) if callable(builder) else {}

        graph_store_config = {
            "enabled": True,
            "provider": graph_store_provider,
            "config": graph_config,
        }

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


def _get_graph_value(
    settings: GraphStoreSettings,
    field: str,
    fallback: Optional[Any],
    default: Any,
) -> Any:
    if field in settings.model_fields_set:
        return getattr(settings, field)
    if fallback is not None:
        return fallback
    return default


def _get_db_value(
    settings: DatabaseSettings,
    field: str,
) -> Optional[Any]:
    if field in settings.model_fields_set:
        return getattr(settings, field)
    return None


def _get_graph_value_with_database(
    settings: GraphStoreSettings,
    field: str,
    database_settings: DatabaseSettings,
    database_field: str,
    default: Any,
) -> Any:
    if field in settings.model_fields_set:
        return getattr(settings, field)
    if database_field in database_settings.model_fields_set:
        return getattr(database_settings, database_field)
    return default


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
    }

    def __init__(self) -> None:
        for _, (attr_name, component_cls) in self._COMPONENTS.items():
            setattr(self, attr_name, component_cls())
        self.graph_store = GraphStoreSettings()
        self.memory_decay = MemoryDecaySettings()
        self.performance = PerformanceSettings()
        self.security = SecuritySettings()
        self.sparse_embedder = SparseEmbedderSettings()

    def to_config(self) -> Dict[str, Any]:
        config = {}
        for output_key, (attr_name, _) in self._COMPONENTS.items():
            component_config = getattr(self, attr_name).to_config()
            if component_config is not None:
                config[output_key] = component_config

        graph_store_config = self.graph_store.to_config(self.database)
        if graph_store_config:
            config["graph_store"] = graph_store_config

        sparse_embedder_config = self.sparse_embedder.to_config()
        if sparse_embedder_config:
            config["sparse_embedder"] = sparse_embedder_config

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
