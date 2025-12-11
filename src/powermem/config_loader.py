"""
Configuration loader for powermem

This module provides utilities for loading configuration from environment variables
or other sources. It simplifies the configuration setup process.
"""

import os
from typing import Any, Dict, Optional


def _auto_load_env():
    """Automatically load .env file from common locations"""
    try:
        from dotenv import load_dotenv
        
        # Try to load .env from multiple possible locations
        possible_paths = [
            os.path.join(os.getcwd(), '.env'),  # Current directory
            os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), '.env'),  # Project root
            os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'examples', 'configs', '.env'),  # examples/configs
        ]
        
        for path in possible_paths:
            if os.path.exists(path):
                load_dotenv(path, override=False)  # Don't override existing env vars
                break
        else:
            # Fallback: try to load from default location
            load_dotenv()
            
    except ImportError:
        pass  # python-dotenv not installed
    except Exception:
        pass  # Silently fail if .env doesn't exist


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
    _auto_load_env()  # Auto-load .env if available
    
    db_provider = os.getenv('DATABASE_PROVIDER', 'oceanbase')
    
    # Build database config based on provider
    if db_provider == 'oceanbase':
        # OceanBase configuration
        connection_args = {
            "host": os.getenv('OCEANBASE_HOST', '127.0.0.1'),
            "port": int(os.getenv('OCEANBASE_PORT', '2881')),
            "user": os.getenv('OCEANBASE_USER', 'root@sys'),
            "password": os.getenv('OCEANBASE_PASSWORD', 'password'),
            "db_name": os.getenv('OCEANBASE_DATABASE', 'powermem')
        }
        db_config = {
            'collection_name': os.getenv('OCEANBASE_COLLECTION', 'memories'),
            'connection_args': connection_args,
            'vidx_metric_type': os.getenv('OCEANBASE_VECTOR_METRIC_TYPE', 'cosine'),
            'index_type': os.getenv('OCEANBASE_INDEX_TYPE', 'IVF_FLAT'),
            'embedding_model_dims': int(os.getenv('OCEANBASE_EMBEDDING_MODEL_DIMS', '1536')),
            'primary_field': os.getenv('OCEANBASE_PRIMARY_FIELD', 'id'),
            'vector_field': os.getenv('OCEANBASE_VECTOR_FIELD', 'embedding'),
            'text_field': os.getenv('OCEANBASE_TEXT_FIELD', 'document'),
            'metadata_field': os.getenv('OCEANBASE_METADATA_FIELD', 'metadata'),
            'vidx_name': os.getenv('OCEANBASE_VIDX_NAME', 'memories_vidx')
        }
    elif db_provider == 'postgres':
        # PostgreSQL configuration (pgvector)
        db_config = {
            'collection_name': os.getenv('POSTGRES_COLLECTION', 'memories'),
            'dbname': os.getenv('POSTGRES_DATABASE', 'powermem'),
            'host': os.getenv('POSTGRES_HOST', 'localhost'),    
            'port': int(os.getenv('POSTGRES_PORT', '5432')),
            'user': os.getenv('POSTGRES_USER', 'postgres'),
            'password': os.getenv('POSTGRES_PASSWORD', 'password'),
            'embedding_model_dims': int(os.getenv('POSTGRES_EMBEDDING_MODEL_DIMS', '1536')),
            'diskann': os.getenv('POSTGRES_DISKANN', 'true').lower() == 'true',
            'hnsw': os.getenv('POSTGRES_HNSW', 'true').lower() == 'true',
        }
    else:
        # SQLite configuration (default)
        db_config = {
            'database_path': os.getenv('SQLITE_PATH', './data/powermem_dev.db'),
            'collection_name': os.getenv('SQLITE_COLLECTION', 'memories'),
            'enable_wal': os.getenv('SQLITE_ENABLE_WAL', 'true').lower() == 'true',
            'timeout': int(os.getenv('SQLITE_TIMEOUT', '30'))
        }
    
    # Build LLM config based on provider
    llm_provider = os.getenv('LLM_PROVIDER', 'qwen')
    llm_config = {
        'api_key': os.getenv('LLM_API_KEY'),
        'model': os.getenv('LLM_MODEL', 'qwen-plus' if llm_provider == 'qwen' else 'gpt-4o-mini'),
        'temperature': float(os.getenv('LLM_TEMPERATURE', '0.7')),
        'max_tokens': int(os.getenv('LLM_MAX_TOKENS', '1000')),
        'top_p': float(os.getenv('LLM_TOP_P', '0.8')),
        'top_k': int(os.getenv('LLM_TOP_K', '50')),
    }
    
    # Add provider-specific config
    if llm_provider == 'qwen':
        llm_config['dashscope_base_url'] = os.getenv('LLM_BASE_URL', 'https://dashscope.aliyuncs.com/api/v1')
        llm_config['enable_search'] = os.getenv('LLM_ENABLE_SEARCH', 'false').lower() == 'true'
    elif llm_provider == 'openai':
        base_url = os.getenv('LLM_BASE_URL')
        if base_url:
            llm_config['openai_base_url'] = base_url
    elif llm_provider == 'siliconflow':
        # SiliconFlow uses OpenAI-compatible API
        base_url = os.getenv('LLM_BASE_URL', 'https://api.siliconflow.cn/v1')
        llm_config['openai_base_url'] = base_url


    # Build Embedding config based on provider
    embedding_provider = os.getenv('EMBEDDING_PROVIDER', 'qwen')
    embedding_config = {
        'api_key': os.getenv('EMBEDDING_API_KEY'),
        'model': os.getenv('EMBEDDING_MODEL'),
        'embedding_dims': int(os.getenv('EMBEDDING_DIMS', '1536'))
    }

    # Add provider-specific config
    provider_base_url_map = {
        'qwen': ('dashscope_base_url', 'https://dashscope.aliyuncs.com/api/v1'),
        'openai': ('openai_base_url', 'https://api.openai.com/v1'),
        'huggingface': ('huggingface_base_url', None),
        'lmstudio': ('lmstudio_base_url', None),
        'ollama': ('ollama_base_url', None),
    }
    
    if embedding_provider in provider_base_url_map:
        config_key, default_value = provider_base_url_map[embedding_provider]
        base_url = os.getenv('EMBEDDING_BASE_URL', default_value)
        if base_url:
            embedding_config[config_key] = base_url
    
    config = {
        'vector_store': {
            'provider': db_provider,
            'config': db_config
        },
        'llm': {
            'provider': llm_provider,
            'config': llm_config
        },
        'embedder': {
            'provider': embedding_provider,
            'config': embedding_config
        },
        'intelligent_memory': {
            'enabled': os.getenv('INTELLIGENT_MEMORY_ENABLED', 'true').lower() == 'true',
            'initial_retention': float(os.getenv('INTELLIGENT_MEMORY_INITIAL_RETENTION', '1.0')),
            'decay_rate': float(os.getenv('INTELLIGENT_MEMORY_DECAY_RATE', '0.1')),
            'reinforcement_factor': float(os.getenv('INTELLIGENT_MEMORY_REINFORCEMENT_FACTOR', '0.3')),
            'working_threshold': float(os.getenv('INTELLIGENT_MEMORY_WORKING_THRESHOLD', '0.3')),
            'short_term_threshold': float(os.getenv('INTELLIGENT_MEMORY_SHORT_TERM_THRESHOLD', '0.6')),
            'long_term_threshold': float(os.getenv('INTELLIGENT_MEMORY_LONG_TERM_THRESHOLD', '0.8'))
        },
        'agent_memory': {
            'enabled': os.getenv('AGENT_ENABLED', 'true').lower() == 'true',
            'mode': os.getenv('AGENT_MEMORY_MODE', 'auto'),
            'default_scope': os.getenv('AGENT_DEFAULT_SCOPE', 'AGENT'),
            'default_privacy_level': os.getenv('AGENT_DEFAULT_PRIVACY_LEVEL', 'PRIVATE'),
            'default_collaboration_level': os.getenv('AGENT_DEFAULT_COLLABORATION_LEVEL', 'READ_ONLY'),
            'default_access_permission': os.getenv('AGENT_DEFAULT_ACCESS_PERMISSION', 'OWNER_ONLY')
        },
        'telemetry': {
            'enable_telemetry': os.getenv('TELEMETRY_ENABLED', 'false').lower() == 'true',
            'telemetry_endpoint': os.getenv('TELEMETRY_ENDPOINT', 'https://telemetry.powermem.ai'),
            'telemetry_api_key': os.getenv('TELEMETRY_API_KEY'),
            'telemetry_batch_size': int(os.getenv('TELEMETRY_BATCH_SIZE', '100')),
            'telemetry_flush_interval': int(os.getenv('TELEMETRY_FLUSH_INTERVAL', '30'))
        },
        'audit': {
            'enabled': os.getenv('AUDIT_ENABLED', 'true').lower() == 'true',
            'log_file': os.getenv('AUDIT_LOG_FILE', './logs/audit.log'),
            'log_level': os.getenv('AUDIT_LOG_LEVEL', 'INFO'),
            'retention_days': int(os.getenv('AUDIT_RETENTION_DAYS', '90'))
        },
        'logging': {
            'level': os.getenv('LOGGING_LEVEL', 'DEBUG'),
            'format': os.getenv('LOGGING_FORMAT', '%(asctime)s - %(name)s - %(levelname)s - %(message)s'),
            'file': os.getenv('LOGGING_FILE', './logs/powermem.log')
        },
        'timezone': {
            'timezone': os.getenv('TIMEZONE', 'UTC')
        },
        'reranker': {
            'enabled': os.getenv('RERANKER_ENABLED', 'false').lower() == 'true',
            'provider': os.getenv('RERANKER_PROVIDER', 'qwen'),
            'config': {
                'model': os.getenv('RERANKER_MODEL'),
                'api_key': os.getenv('RERANKER_API_KEY'),
            }
        }
    }
    
    # Build graph_store config if enabled
    graph_store_enabled = os.getenv('GRAPH_STORE_ENABLED', 'false').lower() == 'true'
    if graph_store_enabled:
        graph_store_provider = os.getenv('GRAPH_STORE_PROVIDER', 'oceanbase')
        
        # Build graph store config based on provider
        if graph_store_provider == 'oceanbase':
            # OceanBase graph configuration
            graph_connection_args = {
                "host": os.getenv('GRAPH_STORE_HOST', os.getenv('OCEANBASE_HOST', '127.0.0.1')),
                "port": os.getenv('GRAPH_STORE_PORT', os.getenv('OCEANBASE_PORT', '2881')),
                "user": os.getenv('GRAPH_STORE_USER', os.getenv('OCEANBASE_USER', 'root@sys')),
                "password": os.getenv('GRAPH_STORE_PASSWORD', os.getenv('OCEANBASE_PASSWORD', 'password')),
                "db_name": os.getenv('GRAPH_STORE_DB_NAME', os.getenv('OCEANBASE_DATABASE', 'powermem'))
            }
            graph_config = {
                'host': graph_connection_args['host'],
                'port': graph_connection_args['port'],
                'user': graph_connection_args['user'],
                'password': graph_connection_args['password'],
                'db_name': graph_connection_args['db_name'],
                'vidx_metric_type': os.getenv('GRAPH_STORE_VECTOR_METRIC_TYPE', os.getenv('OCEANBASE_VECTOR_METRIC_TYPE', 'l2')),
                'index_type': os.getenv('GRAPH_STORE_INDEX_TYPE', os.getenv('OCEANBASE_INDEX_TYPE', 'HNSW')),
                'embedding_model_dims': int(os.getenv('GRAPH_STORE_EMBEDDING_MODEL_DIMS', os.getenv('OCEANBASE_EMBEDDING_MODEL_DIMS', '1536'))),
                'max_hops': int(os.getenv('GRAPH_STORE_MAX_HOPS', '3'))
            }
        else:
            # Default/fallback configuration
            graph_config = {}
        
        # Build graph_store config dict
        graph_store_config = {
            'enabled': True,
            'provider': graph_store_provider,
            'config': graph_config
        }

        # Add optional custom prompts
        custom_prompt = os.getenv('GRAPH_STORE_CUSTOM_PROMPT')
        if custom_prompt:
            graph_store_config['custom_prompt'] = custom_prompt
        
        custom_extract_relations_prompt = os.getenv('GRAPH_STORE_CUSTOM_EXTRACT_RELATIONS_PROMPT')
        if custom_extract_relations_prompt:
            graph_store_config['custom_extract_relations_prompt'] = custom_extract_relations_prompt
        
        custom_update_graph_prompt = os.getenv('GRAPH_STORE_CUSTOM_UPDATE_GRAPH_PROMPT')
        if custom_update_graph_prompt:
            graph_store_config['custom_update_graph_prompt'] = custom_update_graph_prompt
        
        custom_delete_relations_prompt = os.getenv('GRAPH_STORE_CUSTOM_DELETE_RELATIONS_PROMPT')
        if custom_delete_relations_prompt:
            graph_store_config['custom_delete_relations_prompt'] = custom_delete_relations_prompt
        
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

