"""
powermem - Intelligent Memory System

An AI-powered intelligent memory management system that provides a persistent memory layer for LLM applications.
"""

import importlib.metadata
import warnings
from typing import Any

__version__ = importlib.metadata.version("powermem")

# Import core classes
from .core.memory import Memory, _auto_convert_config
from .core.async_memory import AsyncMemory
from .core.base import MemoryBase
from .user_memory import UserMemory

# Import configuration loader
from .config_loader import load_config_from_env, create_config, validate_config, auto_config


def create_memory(
    config: Any = None,
    **kwargs
):
    """
    Create a Memory instance with automatic configuration loading.
    
    This is the simplest way to create a Memory instance. It automatically:
    1. Loads configuration from .env file if no config is provided
    2. Falls back to defaults if .env is not available
    3. Uses mock providers if API keys are not provided
    
    Args:
        config: Optional configuration dictionary. If None, loads from .env
        **kwargs: Additional parameters to pass to Memory
    
    Returns:
        Memory instance

    Preferred entrypoint for creating Memory instances.
        
    Example:
        ```python
        from powermem import create_memory
        
        # Simplest usage - auto loads from .env
        # No API keys needed - will use mock providers
        memory = create_memory()
        
        # With custom config
        memory = create_memory(config=my_config)
        
        # With parameters
        memory = create_memory(agent_id="my_agent")
        ```
    """
    if config is None:
        config = auto_config()
    
    return Memory(config=config, **kwargs)


def from_config(config: Any = None, **kwargs):
    """
    Create Memory instance from configuration

    Deprecated: prefer `create_memory()` or `auto_config()`.
    
    Args:
        config: Optional configuration dictionary. If None, auto-loads from .env file.
               The configuration dictionary can contain the following fields:
               
               **Required/Common Fields:**
               
               - **llm** (Dict[str, Any]): LLM provider configuration
                 - provider (str): LLM provider name (e.g., 'openai', 'qwen', 'ollama', 'anthropic', 'gemini', 'deepseek', 'vllm', 'langchain')
                 - config (Dict[str, Any]): Provider-specific configuration
                   - api_key (str, optional): API key for the LLM provider
                   - model (str, optional): Model name (e.g., 'gpt-4o-mini', 'qwen-plus')
                   - temperature (float, optional): Sampling temperature (default: 0.7)
                   - max_tokens (int, optional): Maximum tokens to generate (default: 1000)
                   - top_p (float, optional): Top-p sampling parameter
                   - top_k (int, optional): Top-k sampling parameter
                   - openai_base_url (str, optional): Custom base URL for OpenAI-compatible APIs
                   - dashscope_base_url (str, optional): Custom base URL for Qwen/DashScope
                   - enable_search (bool, optional): Enable web search for Qwen
               
               - **embedder** (Dict[str, Any]): Embedding model configuration
                 - provider (str): Embedding provider name (e.g., 'openai', 'qwen', 'ollama', 'huggingface', 'azure_openai', 'gemini', 'vertexai', 'together', 'lmstudio', 'langchain', 'aws_bedrock')
                 - config (Dict[str, Any]): Provider-specific configuration
                   - api_key (str, optional): API key for the embedding provider
                   - model (str, optional): Embedding model name (e.g., 'text-embedding-v4', 'text-embedding-ada-002')
                   - embedding_dims (int, optional): Embedding dimensions
                   - ollama_base_url (str, optional): Base URL for Ollama
                   - openai_base_url (str, optional): Base URL for OpenAI-compatible APIs
                   - huggingface_base_url (str, optional): Base URL for HuggingFace
                   - model_kwargs (Dict, optional): Additional model arguments for HuggingFace
                   - azure_deployment (str, optional): Azure OpenAI deployment name
                   - azure_endpoint (str, optional): Azure OpenAI endpoint URL
                   - api_version (str, optional): Azure OpenAI API version
                   - default_headers (Dict, optional): Default headers for Azure OpenAI requests
                   - vertex_credentials_json (str, optional): Path to Vertex AI credentials JSON
                   - lmstudio_base_url (str, optional): Base URL for LM Studio
                   - aws_access_key_id (str, optional): AWS access key for Bedrock
                   - aws_secret_access_key (str, optional): AWS secret key for Bedrock
                   - aws_region (str, optional): AWS region for Bedrock
                 
                 Note:
                 When using `auto_config()` or `load_config_from_env()`, provider-specific settings are
                 loaded via the provider's pydantic-settings class. Common `EMBEDDING_` fields only
                 override provider settings if explicitly set.
               
               - **vector_store** (Dict[str, Any]): Vector store configuration
                 - provider (str): Vector store provider (e.g., 'oceanbase', 'pgvector', 'sqlite', 'postgres')
                 - config (Dict[str, Any]): Provider-specific configuration
                   For OceanBase:
                     - collection_name (str): Collection/table name
                     - connection_args (Dict): Database connection arguments
                       - host (str): Database host
                       - port (str/int): Database port
                       - user (str): Database user
                       - password (str): Database password
                       - db_name (str): Database name
                     - vidx_metric_type (str): Vector index metric type (e.g., 'cosine', 'l2')
                     - index_type (str): Index type (e.g., 'IVF_FLAT')
                     - embedding_model_dims (int): Embedding dimensions
                     - primary_field (str): Primary key field name
                     - vector_field (str): Vector field name
                     - text_field (str): Text field name
                     - metadata_field (str): Metadata field name
                     - vidx_name (str): Vector index name
                   For PostgreSQL (pgvector):
                     - collection_name (str): Collection/table name
                     - dbname (str): Database name
                     - host (str): Database host
                     - port (int): Database port
                     - user (str): Database user
                     - password (str): Database password
                     - embedding_model_dims (int): Embedding dimensions
                     - diskann (bool): Enable DiskANN index
                     - hnsw (bool): Enable HNSW index
                   For SQLite:
                     - database_path (str): Path to SQLite database file
                     - collection_name (str): Collection/table name
                     - enable_wal (bool): Enable Write-Ahead Logging
                     - timeout (int): Connection timeout in seconds
               
               **Optional Fields:**
               
               - **graph_store** (Dict[str, Any], optional): Graph store configuration
                 - enabled (bool): Whether to enable graph store (default: False)
                 - provider (str): Graph store provider (e.g., 'oceanbase')
                 - config (Dict[str, Any]): Provider-specific configuration
                 - llm (Dict[str, Any], optional): LLM configuration for graph queries
                 - custom_prompt (str, optional): Custom prompt for entity extraction
                 - custom_extract_relations_prompt (str, optional): Custom prompt for relation extraction
                 - custom_update_graph_prompt (str, optional): Custom prompt for graph updates
                 - custom_delete_relations_prompt (str, optional): Custom prompt for deleting relations
               
               - **reranker** (Dict[str, Any], optional): Reranker configuration
                 - enabled (bool): Whether to enable reranker (default: False)
                 - provider (str): Reranker provider (e.g., 'qwen', 'cohere')
                 - config (Dict[str, Any]): Provider-specific configuration
                   - model (str, optional): Reranker model name
                   - api_key (str, optional): API key for reranker
               
               - **intelligent_memory** (Dict[str, Any], optional): Intelligent memory management configuration (Ebbinghaus algorithm)
                 - enabled (bool): Whether to enable intelligent memory (default: True)
                 - initial_retention (float): Initial retention strength for new memories (default: 1.0)
                 - decay_rate (float): Rate at which memories decay over time (default: 0.1)
                 - reinforcement_factor (float): Factor by which memories are reinforced when accessed (default: 0.3)
                 - working_threshold (float): Threshold for working memory classification (default: 0.3)
                 - short_term_threshold (float): Threshold for short-term memory classification (default: 0.6)
                 - long_term_threshold (float): Threshold for long-term memory classification (default: 0.8)
               
               - **agent_memory** (Dict[str, Any], optional): Agent memory management configuration
                 - enabled (bool): Whether to enable agent memory (default: True)
                 - mode (str): Agent memory mode: 'multi_agent', 'multi_user', 'hybrid', or 'auto' (default: 'auto')
                 - default_scope (str): Default scope for memories: 'private', 'agent_group', 'user_group', 'public', 'restricted' (default: 'private')
                 - default_privacy_level (str): Default privacy level: 'standard', 'sensitive', 'confidential' (default: 'standard')
                 - default_collaboration_level (str): Default collaboration level: 'isolated', 'collaborative' (default: 'isolated')
                 - default_access_permission (str): Default access permission: 'read', 'write', 'delete', 'admin' (default: 'read')
                 - enable_collaboration (bool): Whether to enable collaboration features (default: True)
               
               - **telemetry** (Dict[str, Any], optional): Telemetry and monitoring configuration
                 - enable_telemetry (bool): Whether to enable telemetry (default: False)
                 - telemetry_endpoint (str): Endpoint URL for telemetry data (default: 'https://telemetry.powermem.ai')
                 - telemetry_api_key (str, optional): API key for telemetry service
                 - batch_size (int): Number of events to batch before sending (default: 100)
                 - flush_interval (int): Interval in seconds to flush telemetry data (default: 30)
               
               - **audit** (Dict[str, Any], optional): Audit logging configuration
                 - enabled (bool): Whether to enable audit logging (default: True)
                 - log_file (str): Path to the audit log file (default: './logs/audit.log')
                 - log_level (str): Logging level for audit logs (default: 'INFO')
                 - retention_days (int): Number of days to retain audit logs (default: 90)
               
               - **logging** (Dict[str, Any], optional): Application logging configuration
                 - level (str): Logging level (default: 'DEBUG')
                 - format (str): Log message format (default: '%(asctime)s - %(name)s - %(levelname)s - %(message)s')
                 - file (str): Path to the log file (default: './logs/powermem.log')
               
               - **version** (str, optional): API version (default: 'v1.1')
               
               - **custom_fact_extraction_prompt** (str, optional): Custom prompt for fact extraction
               
               - **custom_update_memory_prompt** (str, optional): Custom prompt for memory updates
               
               - **custom_importance_evaluation_prompt** (str, optional): Custom prompt for importance evaluation
               
               - **audio_llm** (Dict[str, Any], optional): Audio language model configuration (same structure as 'llm')
        
        **kwargs: Additional parameters to pass to Memory constructor
    
    Returns:
        Memory instance
        
    Example:
        ```python
        from powermem import from_config
        
        # Full configuration example
        memory = from_config({
            "llm": {
                "provider": "openai",
                "config": {
                    "api_key": "...",
                    "model": "gpt-4o-mini",
                    "temperature": 0.7
                }
            },
            "embedder": {
                "provider": "openai",
                "config": {
                    "api_key": "...",
                    "model": "text-embedding-ada-002",
                    "embedding_dims": 1536
                }
            },
            "vector_store": {
                "provider": "oceanbase",
                "config": {
                    "collection_name": "memories",
                    "connection_args": {
                        "host": "127.0.0.1",
                        "port": "2881",
                        "user": "root@sys",
                        "password": "password",
                        "db_name": "powermem"
                    },
                    "embedding_model_dims": 1536
                }
            },
            "intelligent_memory": {
                "enabled": True,
                "decay_rate": 0.1
            }
        })
        
        # Or auto-load from .env file
        memory = from_config()
        ```
    """
    warnings.warn(
        "from_config is deprecated; prefer create_memory() or auto_config().",
        DeprecationWarning,
        stacklevel=2,
    )
    from .core.setup import from_config as _from_config
    return _from_config(config=config, **kwargs)


def _deprecated_memory_from_config(cls, config=None, **kwargs):
    warnings.warn(
        "Memory.from_config is deprecated; prefer create_memory() or auto_config().",
        DeprecationWarning,
        stacklevel=2,
    )
    return create_memory(config, **kwargs)


Memory.from_config = classmethod(_deprecated_memory_from_config)


__all__ = [
    "Memory",
    "AsyncMemory", 
    "MemoryBase",
    "UserMemory",
    "load_config_from_env",
    "create_config",
    "validate_config",
    "create_memory",
    "from_config",
    "auto_config",
]
