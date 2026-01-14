"""
Configuration loader for powermem

This module provides utilities for loading configuration from environment variables
or other sources. It simplifies the configuration setup process.
"""

from pathlib import Path
from typing import Any, Dict, Optional

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _get_default_env_file() -> Optional[str]:
    project_root = Path(__file__).resolve().parents[2]
    candidates = (
        Path.cwd() / ".env",
        project_root / ".env",
        project_root / "examples" / "configs" / ".env",
    )
    for path in candidates:
        if path.exists():
            return str(path)
    try:
        from dotenv import find_dotenv

        env_path = find_dotenv(usecwd=True)
        if env_path:
            return env_path
    except Exception:
        pass
    return None


_DEFAULT_ENV_FILE = _get_default_env_file()


def _settings_config(env_prefix: str = "") -> SettingsConfigDict:
    return SettingsConfigDict(
        case_sensitive=False,
        extra="ignore",
        env_prefix=env_prefix,
        env_file=_DEFAULT_ENV_FILE,
        env_file_encoding="utf-8",
    )


class _BasePowermemSettings(BaseSettings):
    model_config = _settings_config()


class TelemetrySettings(_BasePowermemSettings):
    model_config = _settings_config("TELEMETRY_")

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
    )
    flush_interval: int = Field(
        default=30,
        validation_alias=AliasChoices("FLUSH_INTERVAL", "TELEMETRY_FLUSH_INTERVAL"),
    )


class AuditSettings(_BasePowermemSettings):
    model_config = _settings_config("AUDIT_")

    enabled: bool = Field(default=True)
    log_file: str = Field(default="./logs/audit.log")
    log_level: str = Field(default="INFO")
    retention_days: int = Field(default=90)


class LoggingSettings(_BasePowermemSettings):
    model_config = _settings_config("LOGGING_")

    level: str = Field(default="DEBUG")
    format: str = Field(
        default="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    file: str = Field(default="./logs/powermem.log")


class DatabaseSettings(_BasePowermemSettings):
    model_config = _settings_config("DATABASE_")

    provider: str = Field(default="oceanbase")


class OceanBaseSettings(_BasePowermemSettings):
    model_config = _settings_config("OCEANBASE_")

    host: str = Field(default="127.0.0.1")
    port: int = Field(default=2881)
    user: str = Field(default="root@sys")
    password: str = Field(default="password")
    database: str = Field(default="powermem")
    collection: str = Field(default="memories")
    vector_metric_type: str = Field(default="cosine")
    index_type: str = Field(default="IVF_FLAT")
    embedding_model_dims: int = Field(default=1536)
    primary_field: str = Field(default="id")
    vector_field: str = Field(default="embedding")
    text_field: str = Field(default="document")
    metadata_field: str = Field(default="metadata")
    vidx_name: str = Field(default="memories_vidx")


class PostgresSettings(_BasePowermemSettings):
    model_config = _settings_config("POSTGRES_")

    collection: str = Field(default="memories")
    database: str = Field(default="powermem")
    host: str = Field(default="127.0.0.1")
    port: int = Field(default=5432)
    user: str = Field(default="postgres")
    password: str = Field(default="password")
    embedding_model_dims: int = Field(default=1536)
    diskann: bool = Field(default=True)
    hnsw: bool = Field(default=True)


class SQLiteSettings(_BasePowermemSettings):
    model_config = _settings_config("SQLITE_")

    path: str = Field(default="./data/powermem_dev.db")
    collection: str = Field(default="memories")
    enable_wal: bool = Field(default=True)
    timeout: int = Field(default=30)


class LLMSettings(_BasePowermemSettings):
    model_config = _settings_config("LLM_")

    provider: str = Field(default="qwen")
    api_key: Optional[str] = Field(default=None)
    model: Optional[str] = Field(default=None)
    temperature: float = Field(default=0.7)
    max_tokens: int = Field(default=1000)
    top_p: float = Field(default=0.8)
    top_k: int = Field(default=50)
    enable_search: bool = Field(default=False)


class QwenLLMSettings(_BasePowermemSettings):
    model_config = _settings_config("QWEN_LLM_")

    base_url: str = Field(default="https://dashscope.aliyuncs.com/api/v1")


class OpenAILLMSettings(_BasePowermemSettings):
    model_config = _settings_config("OPENAI_LLM_")

    base_url: str = Field(default="https://api.openai.com/v1")


class SiliconflowLLMSettings(_BasePowermemSettings):
    model_config = _settings_config("SILICONFLOW_LLM_")

    base_url: str = Field(default="https://api.siliconflow.cn/v1")


class OllamaLLMSettings(_BasePowermemSettings):
    model_config = _settings_config("OLLAMA_LLM_")

    base_url: Optional[str] = Field(default=None)


class VllmLLMSettings(_BasePowermemSettings):
    model_config = _settings_config("VLLM_LLM_")

    base_url: Optional[str] = Field(default=None)


class AnthropicLLMSettings(_BasePowermemSettings):
    model_config = _settings_config("ANTHROPIC_LLM_")

    base_url: str = Field(default="https://api.anthropic.com")


class DeepSeekLLMSettings(_BasePowermemSettings):
    model_config = _settings_config("DEEPSEEK_LLM_")

    base_url: str = Field(default="https://api.deepseek.com")


class EmbeddingSettings(_BasePowermemSettings):
    model_config = _settings_config("EMBEDDING_")

    provider: str = Field(default="qwen")
    api_key: Optional[str] = Field(default=None)
    model: Optional[str] = Field(default=None)
    dims: int = Field(default=1536)


class QwenEmbeddingSettings(_BasePowermemSettings):
    model_config = _settings_config("QWEN_EMBEDDING_")

    base_url: Optional[str] = Field(default=None)


class OpenAIEmbeddingSettings(_BasePowermemSettings):
    model_config = _settings_config("OPENAI_EMBEDDING_")

    base_url: Optional[str] = Field(default=None)


class SiliconflowEmbeddingSettings(_BasePowermemSettings):
    model_config = _settings_config("SILICONFLOW_EMBEDDING_")

    base_url: Optional[str] = Field(default=None)


class HuggingfaceEmbeddingSettings(_BasePowermemSettings):
    model_config = _settings_config("HUGGINFACE_EMBEDDING_")

    base_url: Optional[str] = Field(default=None)


class LMStudioEmbeddingSettings(_BasePowermemSettings):
    model_config = _settings_config("LMSTUDIO_EMBEDDING_")

    base_url: Optional[str] = Field(default=None)


class OllamaEmbeddingSettings(_BasePowermemSettings):
    model_config = _settings_config("OLLAMA_EMBEDDING_")

    base_url: Optional[str] = Field(default=None)


class IntelligentMemorySettings(_BasePowermemSettings):
    model_config = _settings_config("INTELLIGENT_MEMORY_")

    enabled: bool = Field(default=True)
    initial_retention: float = Field(default=1.0)
    decay_rate: float = Field(default=0.1)
    reinforcement_factor: float = Field(default=0.3)
    working_threshold: float = Field(default=0.3)
    short_term_threshold: float = Field(default=0.6)
    long_term_threshold: float = Field(default=0.8)


class AgentMemorySettings(_BasePowermemSettings):
    model_config = _settings_config("AGENT_")

    enabled: bool = Field(default=True)
    memory_mode: str = Field(default="auto")
    default_scope: str = Field(default="AGENT")
    default_privacy_level: str = Field(default="PRIVATE")
    default_collaboration_level: str = Field(default="READ_ONLY")
    default_access_permission: str = Field(default="OWNER_ONLY")


class TimezoneSettings(_BasePowermemSettings):
    model_config = _settings_config()

    timezone: str = Field(default="UTC")


class RerankerSettings(_BasePowermemSettings):
    model_config = _settings_config("RERANKER_")

    enabled: bool = Field(default=False)
    provider: str = Field(default="qwen")
    model: Optional[str] = Field(default=None)
    api_key: Optional[str] = Field(default=None)


class GraphStoreSettings(_BasePowermemSettings):
    model_config = _settings_config("GRAPH_STORE_")

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


def _get_graph_value_with_oceanbase(
    settings: GraphStoreSettings,
    field: str,
    oceanbase_settings: OceanBaseSettings,
    default: Any,
) -> Any:
    if field in settings.model_fields_set:
        return getattr(settings, field)
    if field in oceanbase_settings.model_fields_set:
        return getattr(oceanbase_settings, field)
    return default


def load_config_from_env() -> Dict[str, Any]:
    """
    Load configuration from environment variables.
    
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
    db_settings = DatabaseSettings()
    oceanbase_settings = OceanBaseSettings()
    db_provider = db_settings.provider.lower()

    # Build database config based on provider
    if db_provider == 'oceanbase':
        connection_args = {
            "host": oceanbase_settings.host,
            "port": oceanbase_settings.port,
            "user": oceanbase_settings.user,
            "password": oceanbase_settings.password,
            "db_name": oceanbase_settings.database,
        }
        db_config = {
            'collection_name': oceanbase_settings.collection,
            'connection_args': connection_args,
            'vidx_metric_type': oceanbase_settings.vector_metric_type,
            'index_type': oceanbase_settings.index_type,
            'embedding_model_dims': oceanbase_settings.embedding_model_dims,
            'primary_field': oceanbase_settings.primary_field,
            'vector_field': oceanbase_settings.vector_field,
            'text_field': oceanbase_settings.text_field,
            'metadata_field': oceanbase_settings.metadata_field,
            'vidx_name': oceanbase_settings.vidx_name,
        }
    elif db_provider == 'postgres':
        postgres_settings = PostgresSettings()
        db_config = {
            'collection_name': postgres_settings.collection,
            'dbname': postgres_settings.database,
            'host': postgres_settings.host,
            'port': postgres_settings.port,
            'user': postgres_settings.user,
            'password': postgres_settings.password,
            'embedding_model_dims': postgres_settings.embedding_model_dims,
            'diskann': postgres_settings.diskann,
            'hnsw': postgres_settings.hnsw,
        }
    else:
        sqlite_settings = SQLiteSettings()
        db_config = {
            'database_path': sqlite_settings.path,
            'collection_name': sqlite_settings.collection,
            'enable_wal': sqlite_settings.enable_wal,
            'timeout': sqlite_settings.timeout,
        }

    llm_settings = LLMSettings()
    llm_provider = llm_settings.provider.lower()
    llm_model = llm_settings.model
    if llm_model is None:
        llm_model = 'qwen-plus' if llm_provider == 'qwen' else 'gpt-4o-mini'

    llm_config = {
        'api_key': llm_settings.api_key,
        'model': llm_model,
        'temperature': llm_settings.temperature,
        'max_tokens': llm_settings.max_tokens,
        'top_p': llm_settings.top_p,
        'top_k': llm_settings.top_k,
    }

    def _configure_qwen():
        qwen = QwenLLMSettings()
        llm_config['dashscope_base_url'] = qwen.base_url
        llm_config['enable_search'] = llm_settings.enable_search

    def _configure_openai():
        openai = OpenAILLMSettings()
        llm_config['openai_base_url'] = openai.base_url

    def _configure_siliconflow():
        siliconflow = SiliconflowLLMSettings()
        llm_config['openai_base_url'] = siliconflow.base_url

    def _configure_ollama():
        ollama = OllamaLLMSettings()
        llm_config['ollama_base_url'] = ollama.base_url

    def _configure_vllm():
        vllm = VllmLLMSettings()
        llm_config['vllm_base_url'] = vllm.base_url

    def _configure_anthropic():
        anthropic = AnthropicLLMSettings()
        llm_config['anthropic_base_url'] = anthropic.base_url

    def _configure_deepseek():
        deepseek = DeepSeekLLMSettings()
        llm_config['deepseek_base_url'] = deepseek.base_url

    provider_configs = {
        'qwen': _configure_qwen,
        'openai': _configure_openai,
        'siliconflow': _configure_siliconflow,
        'ollama': _configure_ollama,
        'vllm': _configure_vllm,
        'anthropic': _configure_anthropic,
        'deepseek': _configure_deepseek,
    }

    if llm_provider in provider_configs:
        provider_configs[llm_provider]()

    embedding_settings = EmbeddingSettings()
    embedding_provider = embedding_settings.provider.lower()
    embedding_config = {
        'api_key': embedding_settings.api_key,
        'model': embedding_settings.model,
        'embedding_dims': embedding_settings.dims,
    }

    if embedding_provider == 'qwen':
        qwen = QwenEmbeddingSettings()
        embedding_config['dashscope_base_url'] = qwen.base_url
    elif embedding_provider == 'openai':
        openai = OpenAIEmbeddingSettings()
        embedding_config['openai_base_url'] = openai.base_url
    elif embedding_provider == 'siliconflow':
        siliconflow = SiliconflowEmbeddingSettings()
        embedding_config['siliconflow_base_url'] = siliconflow.base_url
    elif embedding_provider == 'huggingface':
        huggingface = HuggingfaceEmbeddingSettings()
        embedding_config['huggingface_base_url'] = huggingface.base_url
    elif embedding_provider == 'lmstudio':
        lmstudio = LMStudioEmbeddingSettings()
        embedding_config['lmstudio_base_url'] = lmstudio.base_url
    elif embedding_provider == 'ollama':
        ollama = OllamaEmbeddingSettings()
        embedding_config['ollama_base_url'] = ollama.base_url

    intelligent_settings = IntelligentMemorySettings()
    agent_settings = AgentMemorySettings()
    telemetry_settings = TelemetrySettings()
    audit_settings = AuditSettings()
    logging_settings = LoggingSettings()
    timezone_settings = TimezoneSettings()
    reranker_settings = RerankerSettings()

    config = {
        'vector_store': {
            'provider': db_provider,
            'config': db_config,
        },
        'llm': {
            'provider': llm_provider,
            'config': llm_config,
        },
        'embedder': {
            'provider': embedding_provider,
            'config': embedding_config,
        },
        'intelligent_memory': intelligent_settings.model_dump(),
        'agent_memory': {
            'enabled': agent_settings.enabled,
            'mode': agent_settings.memory_mode,
            'default_scope': agent_settings.default_scope,
            'default_privacy_level': agent_settings.default_privacy_level,
            'default_collaboration_level': agent_settings.default_collaboration_level,
            'default_access_permission': agent_settings.default_access_permission,
        },
        'timezone': timezone_settings.model_dump(),
        'reranker': {
            'enabled': reranker_settings.enabled,
            'provider': reranker_settings.provider,
            'config': {
                'model': reranker_settings.model,
                'api_key': reranker_settings.api_key,
            },
        },
        'telemetry': telemetry_settings.model_dump(by_alias=True),
        'audit': audit_settings.model_dump(),
        'logging': logging_settings.model_dump(),
    }

    graph_store_settings = GraphStoreSettings()
    if graph_store_settings.enabled:
        graph_store_provider = graph_store_settings.provider.lower()

        if graph_store_provider == 'oceanbase':
            graph_connection_args = {
                "host": _get_graph_value(
                    graph_store_settings,
                    "host",
                    oceanbase_settings.host,
                    "127.0.0.1",
                ),
                "port": _get_graph_value(
                    graph_store_settings,
                    "port",
                    oceanbase_settings.port,
                    2881,
                ),
                "user": _get_graph_value(
                    graph_store_settings,
                    "user",
                    oceanbase_settings.user,
                    "root@sys",
                ),
                "password": _get_graph_value(
                    graph_store_settings,
                    "password",
                    oceanbase_settings.password,
                    "password",
                ),
                "db_name": _get_graph_value(
                    graph_store_settings,
                    "db_name",
                    oceanbase_settings.database,
                    "powermem",
                ),
            }
            graph_config = {
                'host': graph_connection_args['host'],
                'port': graph_connection_args['port'],
                'user': graph_connection_args['user'],
                'password': graph_connection_args['password'],
                'db_name': graph_connection_args['db_name'],
                'vidx_metric_type': _get_graph_value_with_oceanbase(
                    graph_store_settings,
                    "vector_metric_type",
                    oceanbase_settings,
                    "l2",
                ),
                'index_type': _get_graph_value_with_oceanbase(
                    graph_store_settings,
                    "index_type",
                    oceanbase_settings,
                    "HNSW",
                ),
                'embedding_model_dims': _get_graph_value_with_oceanbase(
                    graph_store_settings,
                    "embedding_model_dims",
                    oceanbase_settings,
                    1536,
                ),
                'max_hops': _get_graph_value(
                    graph_store_settings,
                    "max_hops",
                    None,
                    3,
                ),
            }
        else:
            graph_config = {}

        graph_store_config = {
            'enabled': True,
            'provider': graph_store_provider,
            'config': graph_config,
        }

        if graph_store_settings.custom_prompt:
            graph_store_config['custom_prompt'] = graph_store_settings.custom_prompt
        if graph_store_settings.custom_extract_relations_prompt:
            graph_store_config['custom_extract_relations_prompt'] = (
                graph_store_settings.custom_extract_relations_prompt
            )
        if graph_store_settings.custom_update_graph_prompt:
            graph_store_config['custom_update_graph_prompt'] = (
                graph_store_settings.custom_update_graph_prompt
            )
        if graph_store_settings.custom_delete_relations_prompt:
            graph_store_config['custom_delete_relations_prompt'] = (
                graph_store_settings.custom_delete_relations_prompt
            )

        config['graph_store'] = graph_store_config

    return config


def create_config(
    database_provider: str = 'sqlite',
    llm_provider: str = 'qwen',
    embedding_provider: str = 'qwen',
    **kwargs
) -> Dict[str, Any]:
    """
    Create a basic configuration dictionary with specified providers.
    
    Args:
        database_provider: Database provider ('sqlite', 'oceanbase', 'postgres')
        llm_provider: LLM provider ('qwen', 'openai', etc.)
        embedding_provider: Embedding provider ('qwen', 'openai', etc.)
        **kwargs: Additional configuration parameters
    
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
    config = {
        'vector_store': {
            'provider': database_provider,
            'config': kwargs.get('database_config', {})
        },
        'llm': {
            'provider': llm_provider,
            'config': {
                'api_key': kwargs.get('llm_api_key'),
                'model': kwargs.get('llm_model', 'qwen-plus'),
                'temperature': kwargs.get('llm_temperature', 0.7),
                'max_tokens': kwargs.get('llm_max_tokens', 1000),
                **{k: v for k, v in kwargs.items() if k.startswith('llm_') and k != 'llm_api_key' and k != 'llm_model' and k != 'llm_temperature' and k != 'llm_max_tokens'}
            }
        },
        'embedder': {
            'provider': embedding_provider,
            'config': {
                'api_key': kwargs.get('embedding_api_key'),
                'model': kwargs.get('embedding_model', 'text-embedding-v4'),
                'embedding_dims': kwargs.get('embedding_dims', 1536),
            }
        }
    }
    
    return config


def validate_config(config: Dict[str, Any]) -> bool:
    """
    Validate a configuration dictionary.
    
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
