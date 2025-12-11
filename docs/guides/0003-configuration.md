# Configuration Reference

Complete reference guide for all PowerMem configuration options. This document provides detailed explanations for every configuration parameter in `env.example`.

## Configuration Methods

PowerMem supports two configuration methods:

1. **Environment Variables (`.env` file)** - Recommended for most use cases
2. **JSON/Dictionary Configuration** - Useful for programmatic configuration

### Method 1: Environment Variables

Create a `.env` file in your project root and configure using environment variables. See the examples in each section below.

```python
from powermem import Memory, auto_config

# Load configuration (auto-loads from .env or uses defaults)
config = auto_config()

# Create memory instance
memory = Memory(config=config)
```

### Method 2: JSON/Dictionary Configuration

Pass configuration as a Python dictionary (JSON-like format). This is useful when:
- Loading configuration from a JSON file
- Programmatically generating configuration
- Embedding configuration in application code

```python
from powermem import Memory

config = {
    'vector_store': {
        'provider': 'sqlite',
        'config': {
            'database_path': './data/powermem_dev.db'
        }
    },
    'llm': {
        'provider': 'qwen',
        'config': {
            'api_key': 'your_api_key',
            'model': 'qwen-plus'
        }
    },
    'embedder': {
        'provider': 'qwen',
        'config': {
            'api_key': 'your_api_key',
            'model': 'text-embedding-v4'
        }
    }
}

memory = Memory(config=config)
```

### Loading from JSON File

You can also load configuration from a JSON file:

```python
import json
from powermem import Memory

# Load from JSON file
with open('config.json', 'r') as f:
    config = json.load(f)

memory = Memory(config=config)
```

## Table of Contents

1. [Database Configuration](#1-database-configuration-required)
2. [LLM Configuration](#2-llm-configuration-required)
3. [Embedding Configuration](#3-embedding-configuration-required)
4. [Agent Configuration](#4-agent-configuration-optional)
5. [Intelligent Memory Configuration](#5-intelligent-memory-configuration-optional)
6. [Performance Configuration](#6-performance-configuration-optional)
7. [Security Configuration](#7-security-configuration-optional)
8. [Telemetry Configuration](#8-telemetry-configuration-optional)
9. [Audit Configuration](#9-audit-configuration-optional)
10. [Logging Configuration](#10-logging-configuration-optional)

---

## 1. Database Configuration (Required)

PowerMem requires a database provider to store memories and vectors. Choose one of the supported providers: SQLite (development), OceanBase (production), or PostgreSQL.

### Common Database Settings

| Configuration | Type | Required | Default | Description |
|--------------|------|----------|---------|-------------|
| `DATABASE_PROVIDER` | string | Yes | `sqlite` | Database provider to use. Options: `sqlite`, `oceanbase`, `postgres` |

### SQLite Configuration

SQLite is the default database provider, recommended for development and single-user applications.

| Configuration | Type | Required | Default | Description |
|--------------|------|----------|---------|-------------|
| `SQLITE_PATH` | string | Yes* | `./data/powermem_dev.db` | Path to the SQLite database file. Required when `DATABASE_PROVIDER=sqlite` |
| `SQLITE_ENABLE_WAL` | boolean | No | `true` | Enable Write-Ahead Logging (WAL) mode for better concurrency |
| `SQLITE_TIMEOUT` | integer | No | `30` | Connection timeout in seconds |

**Environment Variables Example:**
```env
DATABASE_PROVIDER=sqlite
SQLITE_PATH=./data/powermem_dev.db
SQLITE_ENABLE_WAL=true
SQLITE_TIMEOUT=30
```

**JSON Configuration Example:**
```json
{
  "vector_store": {
    "provider": "sqlite",
    "config": {
      "database_path": "./data/powermem_dev.db",
      "enable_wal": true,
      "timeout": 30
    }
  }
}
```

**Python Dictionary Example:**
```python
config = {
    'vector_store': {
        'provider': 'sqlite',
        'config': {
            'database_path': './data/powermem_dev.db',
            'enable_wal': True,
            'timeout': 30
        }
    }
}
```

### OceanBase Configuration

OceanBase is recommended for production deployments and enterprise applications with high-scale requirements.

| Configuration | Type | Required | Default | Description |
|--------------|------|----------|---------|-------------|
| `OCEANBASE_HOST` | string | Yes* | `127.0.0.1` | OceanBase server hostname or IP address. Required when `DATABASE_PROVIDER=oceanbase` |
| `OCEANBASE_PORT` | integer | Yes* | `2881` | OceanBase server port. Required when `DATABASE_PROVIDER=oceanbase` |
| `OCEANBASE_USER` | string | Yes* | `root` | Database username. Required when `DATABASE_PROVIDER=oceanbase` |
| `OCEANBASE_PASSWORD` | string | Yes* | - | Database password. Required when `DATABASE_PROVIDER=oceanbase` |
| `OCEANBASE_DATABASE` | string | Yes* | `powermem` | Database name. Required when `DATABASE_PROVIDER=oceanbase` |
| `OCEANBASE_COLLECTION` | string | No | `memories` | Collection/table name for storing memories |
| `OCEANBASE_INDEX_TYPE` | string | No | `IVF_FLAT` | Vector index type. Options: `IVF_FLAT`, `HNSW`, etc. |
| `OCEANBASE_VECTOR_METRIC_TYPE` | string | No | `cosine` | Vector similarity metric. Options: `cosine`, `euclidean`, `dot_product` |
| `OCEANBASE_TEXT_FIELD` | string | No | `document` | Field name for storing text content |
| `OCEANBASE_VECTOR_FIELD` | string | No | `embedding` | Field name for storing vector embeddings |
| `OCEANBASE_EMBEDDING_MODEL_DIMS` | integer | Yes* | `1536` | Vector dimensions. Must match your embedding model dimensions. Required when `DATABASE_PROVIDER=oceanbase` |
| `OCEANBASE_PRIMARY_FIELD` | string | No | `id` | Primary key field name |
| `OCEANBASE_METADATA_FIELD` | string | No | `metadata` | Field name for storing metadata |
| `OCEANBASE_VIDX_NAME` | string | No | `memories_vidx` | Vector index name |

**Environment Variables Example:**
```env
DATABASE_PROVIDER=oceanbase
OCEANBASE_HOST=localhost
OCEANBASE_PORT=2881
OCEANBASE_USER=root
OCEANBASE_PASSWORD=your_password
OCEANBASE_DATABASE=powermem
OCEANBASE_COLLECTION=memories
OCEANBASE_INDEX_TYPE=IVF_FLAT
OCEANBASE_VECTOR_METRIC_TYPE=cosine
OCEANBASE_EMBEDDING_MODEL_DIMS=1536
```

**JSON Configuration Example:**
```json
{
  "vector_store": {
    "provider": "oceanbase",
    "config": {
      "collection_name": "memories",
      "connection_args": {
        "host": "localhost",
        "port": 2881,
        "user": "root",
        "password": "your_password",
        "db_name": "powermem"
      },
      "vidx_metric_type": "cosine",
      "index_type": "IVF_FLAT",
      "embedding_model_dims": 1536,
      "primary_field": "id",
      "vector_field": "embedding",
      "text_field": "document",
      "metadata_field": "metadata",
      "vidx_name": "memories_vidx"
    }
  }
}
```

**Python Dictionary Example:**
```python
config = {
    'vector_store': {
        'provider': 'oceanbase',
        'config': {
            'collection_name': 'memories',
            'connection_args': {
                'host': 'localhost',
                'port': 2881,
                'user': 'root',
                'password': 'your_password',
                'db_name': 'powermem'
            },
            'vidx_metric_type': 'cosine',
            'index_type': 'IVF_FLAT',
            'embedding_model_dims': 1536
        }
    }
}
```

### PostgreSQL Configuration

PostgreSQL with pgvector extension is supported for vector storage.

| Configuration | Type | Required | Default | Description |
|--------------|------|----------|---------|-------------|
| `POSTGRES_HOST` | string | Yes* | `127.0.0.1` | PostgreSQL server hostname or IP address. Required when `DATABASE_PROVIDER=postgres` |
| `POSTGRES_PORT` | integer | Yes* | `5432` | PostgreSQL server port. Required when `DATABASE_PROVIDER=postgres` |
| `POSTGRES_USER` | string | Yes* | `postgres` | Database username. Required when `DATABASE_PROVIDER=postgres` |
| `POSTGRES_PASSWORD` | string | Yes* | - | Database password. Required when `DATABASE_PROVIDER=postgres` |
| `POSTGRES_DATABASE` | string | Yes* | `powermem` | Database name. Required when `DATABASE_PROVIDER=postgres` |
| `DATABASE_SSLMODE` | string | No | `prefer` | SSL connection mode. Options: `disable`, `allow`, `prefer`, `require`, `verify-ca`, `verify-full` |
| `DATABASE_POOL_SIZE` | integer | No | `10` | Connection pool size |
| `DATABASE_MAX_OVERFLOW` | integer | No | `20` | Maximum overflow connections in the pool |

**Environment Variables Example:**
```env
DATABASE_PROVIDER=postgres
POSTGRES_HOST=127.0.0.1
POSTGRES_PORT=5432
POSTGRES_USER=postgres
POSTGRES_PASSWORD=your_password
POSTGRES_DATABASE=powermem
POSTGRES_COLLECTION=memories
DATABASE_SSLMODE=prefer
DATABASE_POOL_SIZE=10
DATABASE_MAX_OVERFLOW=20
```

**JSON Configuration Example:**
```json
{
  "vector_store": {
    "provider": "postgres",
    "config": {
      "collection_name": "memories",
      "dbname": "powermem",
      "host": "localhost",
      "port": 5432,
      "user": "postgres",
      "password": "your_password",
      "embedding_model_dims": 1536,
      "diskann": true,
      "hnsw": true
    }
  }
}
```

**Python Dictionary Example:**
```python
config = {
    'vector_store': {
        'provider': 'postgres',
        'config': {
            'collection_name': 'memories',
            'dbname': 'powermem',
            'host': 'localhost',
            'port': 5432,
            'user': 'postgres',
            'password': 'your_password',
            'embedding_model_dims': 1536
        }
    }
}
```

---

## 2. LLM Configuration (Required)

PowerMem requires an LLM provider for memory generation and retrieval. Choose from Qwen, OpenAI, or Mock (for testing).

### Common LLM Settings

| Configuration | Type | Required | Default | Description |
|--------------|------|----------|---------|-------------|
| `LLM_PROVIDER` | string | Yes | `qwen` | LLM provider to use. Options: `qwen`, `openai`, `mock` |

### Qwen Configuration (Default)

Qwen is the default LLM provider, powered by Alibaba Cloud DashScope.

| Configuration | Type | Required | Default | Description |
|--------------|------|----------|---------|-------------|
| `LLM_API_KEY` | string | Yes* | - | DashScope API key. Required when `LLM_PROVIDER=qwen` |
| `LLM_MODEL` | string | No | `qwen-plus` | Qwen model name. Options: `qwen-plus`, `qwen-max`, `qwen-turbo`, `qwen-long`, etc. |
| `LLM_BASE_URL` | string | No | `https://dashscope.aliyuncs.com/api/v1` | API base URL for DashScope |
| `LLM_TEMPERATURE` | float | No | `0.7` | Sampling temperature (0.0-2.0). Higher values make output more random |
| `LLM_MAX_TOKENS` | integer | No | `1000` | Maximum number of tokens to generate |
| `LLM_TOP_P` | float | No | `0.8` | Nucleus sampling parameter (0.0-1.0). Controls diversity of output |
| `LLM_TOP_K` | integer | No | `50` | Top-K sampling parameter. Limits sampling to top K tokens |
| `LLM_ENABLE_SEARCH` | boolean | No | `false` | Enable web search capability (if supported by model) |

**Environment Variables Example:**
```env
LLM_PROVIDER=qwen
LLM_API_KEY=your_api_key_here
LLM_MODEL=qwen-plus
LLM_BASE_URL=https://dashscope.aliyuncs.com/api/v1
LLM_TEMPERATURE=0.7
LLM_MAX_TOKENS=1000
LLM_TOP_P=0.8
LLM_TOP_K=50
LLM_ENABLE_SEARCH=false
```

**JSON Configuration Example:**
```json
{
  "llm": {
    "provider": "qwen",
    "config": {
      "api_key": "your_api_key_here",
      "model": "qwen-plus",
      "dashscope_base_url": "https://dashscope.aliyuncs.com/api/v1",
      "temperature": 0.7,
      "max_tokens": 1000,
      "top_p": 0.8,
      "top_k": 50,
      "enable_search": false
    }
  }
}
```

**Python Dictionary Example:**
```python
config = {
    'llm': {
        'provider': 'qwen',
        'config': {
            'api_key': 'your_api_key_here',
            'model': 'qwen-plus',
            'dashscope_base_url': 'https://dashscope.aliyuncs.com/api/v1',
            'temperature': 0.7,
            'max_tokens': 1000,
            'top_p': 0.8,
            'top_k': 50,
            'enable_search': False
        }
    }
}
```

### OpenAI Configuration

OpenAI GPT models are supported.

| Configuration | Type | Required | Default | Description |
|--------------|------|----------|---------|-------------|
| `LLM_API_KEY` | string | Yes* | - | OpenAI API key. Required when `LLM_PROVIDER=openai` |
| `LLM_MODEL` | string | No | `gpt-4` | OpenAI model name. Options: `gpt-4`, `gpt-4-turbo`, `gpt-3.5-turbo`, etc. |
| `LLM_BASE_URL` | string | No | `https://api.openai.com/v1` | API base URL for OpenAI |
| `LLM_TEMPERATURE` | float | No | `0.7` | Sampling temperature (0.0-2.0) |
| `LLM_MAX_TOKENS` | integer | No | `1000` | Maximum number of tokens to generate |
| `LLM_TOP_P` | float | No | `1.0` | Nucleus sampling parameter (0.0-1.0) |

**Environment Variables Example:**
```env
LLM_PROVIDER=openai
LLM_API_KEY=your-openai-api-key
LLM_MODEL=gpt-4
LLM_BASE_URL=https://api.openai.com/v1
LLM_TEMPERATURE=0.7
LLM_MAX_TOKENS=1000
LLM_TOP_P=1.0
```

**JSON Configuration Example:**
```json
{
  "llm": {
    "provider": "openai",
    "config": {
      "api_key": "your-openai-api-key",
      "model": "gpt-4",
      "openai_base_url": "https://api.openai.com/v1",
      "temperature": 0.7,
      "max_tokens": 1000,
      "top_p": 1.0
    }
  }
}
```

**Python Dictionary Example:**
```python
config = {
    'llm': {
        'provider': 'openai',
        'config': {
            'api_key': 'your-openai-api-key',
            'model': 'gpt-4',
            'openai_base_url': 'https://api.openai.com/v1',
            'temperature': 0.7,
            'max_tokens': 1000,
            'top_p': 1.0
        }
    }
}
```

---

## 3. Embedding Configuration (Required)

PowerMem requires an embedding provider to convert text into vector embeddings for similarity search.

### Common Embedding Settings

| Configuration | Type | Required | Default | Description |
|--------------|------|----------|---------|-------------|
| `EMBEDDING_PROVIDER` | string | Yes | `qwen` | Embedding provider to use. Options: `qwen`, `openai`, `mock` |

### Qwen Embedding Configuration (Default)

Qwen embeddings are provided by Alibaba Cloud DashScope.

| Configuration | Type | Required | Default | Description |
|--------------|------|----------|---------|-------------|
| `EMBEDDING_API_KEY` | string | Yes* | - | DashScope API key. Required when `EMBEDDING_PROVIDER=qwen` |
| `EMBEDDING_MODEL` | string | No | `text-embedding-v4` | Qwen embedding model name |
| `EMBEDDING_DIMS` | integer | Yes* | `1536` | Vector dimensions. Must match `DATABASE_EMBEDDING_MODEL_DIMS` if using OceanBase. Required when `EMBEDDING_PROVIDER=qwen` |
| `EMBEDDING_BASE_URL` | string | No | `https://dashscope.aliyuncs.com/api/v1` | API base URL for DashScope |

**Environment Variables Example:**
```env
EMBEDDING_PROVIDER=qwen
EMBEDDING_API_KEY=your_api_key_here
EMBEDDING_MODEL=text-embedding-v4
EMBEDDING_DIMS=1536
EMBEDDING_BASE_URL=https://dashscope.aliyuncs.com/api/v1
```

**JSON Configuration Example:**
```json
{
  "embedder": {
    "provider": "qwen",
    "config": {
      "api_key": "your_api_key_here",
      "model": "text-embedding-v4",
      "embedding_dims": 1536
    }
  }
}
```

**Python Dictionary Example:**
```python
config = {
    'embedder': {
        'provider': 'qwen',
        'config': {
            'api_key': 'your_api_key_here',
            'model': 'text-embedding-v4',
            'embedding_dims': 1536
        }
    }
}
```

### OpenAI Embedding Configuration

OpenAI provides text embedding models.

| Configuration | Type | Required | Default | Description |
|--------------|------|----------|---------|-------------|
| `EMBEDDING_API_KEY` | string | Yes* | - | OpenAI API key. Required when `EMBEDDING_PROVIDER=openai` |
| `EMBEDDING_MODEL` | string | No | `text-embedding-ada-002` | OpenAI embedding model name. Options: `text-embedding-ada-002`, `text-embedding-3-small`, `text-embedding-3-large` |
| `EMBEDDING_DIMS` | integer | Yes* | `1536` | Vector dimensions. Varies by model (ada-002: 1536, 3-small: 1536, 3-large: 3072). Required when `EMBEDDING_PROVIDER=openai` |
| `EMBEDDING_BASE_URL` | string | No | `https://api.openai.com/v1` | API base URL for OpenAI |

**Environment Variables Example:**
```env
EMBEDDING_PROVIDER=openai
EMBEDDING_API_KEY=your-openai-api-key
EMBEDDING_MODEL=text-embedding-ada-002
EMBEDDING_DIMS=1536
EMBEDDING_BASE_URL=https://api.openai.com/v1
```

**JSON Configuration Example:**
```json
{
  "embedder": {
    "provider": "openai",
    "config": {
      "api_key": "your-openai-api-key",
      "model": "text-embedding-ada-002",
      "embedding_dims": 1536
    }
  }
}
```

**Python Dictionary Example:**
```python
config = {
    'embedder': {
        'provider': 'openai',
        'config': {
            'api_key': 'your-openai-api-key',
            'model': 'text-embedding-ada-002',
            'embedding_dims': 1536
        }
    }
}
```

---

## 4. Agent Configuration (Optional)

Agent configuration controls how PowerMem manages memory for AI agents.

| Configuration | Type | Required | Default | Description |
|--------------|------|----------|---------|-------------|
| `AGENT_ENABLED` | boolean | No | `true` | Enable agent memory management |
| `AGENT_DEFAULT_SCOPE` | string | No | `AGENT` | Default scope for agent memories. Options: `AGENT`, `USER`, `GLOBAL` |
| `AGENT_DEFAULT_PRIVACY_LEVEL` | string | No | `PRIVATE` | Default privacy level. Options: `PRIVATE`, `PUBLIC`, `SHARED` |
| `AGENT_DEFAULT_COLLABORATION_LEVEL` | string | No | `READ_ONLY` | Default collaboration level. Options: `READ_ONLY`, `READ_WRITE`, `FULL` |
| `AGENT_DEFAULT_ACCESS_PERMISSION` | string | No | `OWNER_ONLY` | Default access permission. Options: `OWNER_ONLY`, `AUTHORIZED`, `PUBLIC` |
| `AGENT_MEMORY_MODE` | string | No | `auto` | Agent memory mode. Options: `auto`, `multi_agent`, `multi_user`, `hybrid` |

**Environment Variables Example:**
```env
AGENT_ENABLED=true
AGENT_DEFAULT_SCOPE=AGENT
AGENT_DEFAULT_PRIVACY_LEVEL=PRIVATE
AGENT_DEFAULT_COLLABORATION_LEVEL=READ_ONLY
AGENT_DEFAULT_ACCESS_PERMISSION=OWNER_ONLY
AGENT_MEMORY_MODE=auto
```

**JSON Configuration Example:**
```json
{
  "agent_memory": {
    "enabled": true,
    "mode": "auto",
    "default_scope": "AGENT",
    "default_privacy_level": "PRIVATE",
    "default_collaboration_level": "READ_ONLY",
    "default_access_permission": "OWNER_ONLY"
  }
}
```

**Python Dictionary Example:**
```python
config = {
    'agent_memory': {
        'enabled': True,
        'mode': 'auto',
        'default_scope': 'AGENT',
        'default_privacy_level': 'PRIVATE',
        'default_collaboration_level': 'READ_ONLY',
        'default_access_permission': 'OWNER_ONLY'
    }
}
```

---

## 5. Intelligent Memory Configuration (Optional)

Intelligent memory uses the Ebbinghaus forgetting curve to manage memory retention and decay.

### Ebbinghaus Forgetting Curve Settings

| Configuration | Type | Required | Default | Description |
|--------------|------|----------|---------|-------------|
| `INTELLIGENT_MEMORY_ENABLED` | boolean | No | `true` | Enable intelligent memory management |
| `INTELLIGENT_MEMORY_INITIAL_RETENTION` | float | No | `1.0` | Initial retention score (0.0-1.0). Starting memory strength |
| `INTELLIGENT_MEMORY_DECAY_RATE` | float | No | `0.1` | Memory decay rate (0.0-1.0). Higher values mean faster forgetting |
| `INTELLIGENT_MEMORY_REINFORCEMENT_FACTOR` | float | No | `0.3` | Reinforcement factor (0.0-1.0). How much memory strengthens when accessed |
| `INTELLIGENT_MEMORY_WORKING_THRESHOLD` | float | No | `0.3` | Working memory threshold (0.0-1.0). Memories below this are in working memory |
| `INTELLIGENT_MEMORY_SHORT_TERM_THRESHOLD` | float | No | `0.6` | Short-term memory threshold (0.0-1.0). Memories between working and this are short-term |
| `INTELLIGENT_MEMORY_LONG_TERM_THRESHOLD` | float | No | `0.8` | Long-term memory threshold (0.0-1.0). Memories above this are long-term |

### Memory Decay Calculation Settings

| Configuration | Type | Required | Default | Description |
|--------------|------|----------|---------|-------------|
| `MEMORY_DECAY_ENABLED` | boolean | No | `true` | Enable memory decay calculations |
| `MEMORY_DECAY_ALGORITHM` | string | No | `ebbinghaus` | Decay algorithm to use. Options: `ebbinghaus` |
| `MEMORY_DECAY_BASE_RETENTION` | float | No | `1.0` | Base retention score (0.0-1.0) |
| `MEMORY_DECAY_FORGETTING_RATE` | float | No | `0.1` | Forgetting rate (0.0-1.0) |
| `MEMORY_DECAY_REINFORCEMENT_FACTOR` | float | No | `0.3` | Reinforcement factor for decay calculations (0.0-1.0) |

**Environment Variables Example:**
```env
INTELLIGENT_MEMORY_ENABLED=true
INTELLIGENT_MEMORY_INITIAL_RETENTION=1.0
INTELLIGENT_MEMORY_DECAY_RATE=0.1
INTELLIGENT_MEMORY_REINFORCEMENT_FACTOR=0.3
INTELLIGENT_MEMORY_WORKING_THRESHOLD=0.3
INTELLIGENT_MEMORY_SHORT_TERM_THRESHOLD=0.6
INTELLIGENT_MEMORY_LONG_TERM_THRESHOLD=0.8
MEMORY_DECAY_ENABLED=true
MEMORY_DECAY_ALGORITHM=ebbinghaus
MEMORY_DECAY_BASE_RETENTION=1.0
MEMORY_DECAY_FORGETTING_RATE=0.1
MEMORY_DECAY_REINFORCEMENT_FACTOR=0.3
```

**JSON Configuration Example:**
```json
{
  "intelligent_memory": {
    "enabled": true,
    "initial_retention": 1.0,
    "decay_rate": 0.1,
    "reinforcement_factor": 0.3,
    "working_threshold": 0.3,
    "short_term_threshold": 0.6,
    "long_term_threshold": 0.8
  }
}
```

**Python Dictionary Example:**
```python
config = {
    'intelligent_memory': {
        'enabled': True,
        'initial_retention': 1.0,
        'decay_rate': 0.1,
        'reinforcement_factor': 0.3,
        'working_threshold': 0.3,
        'short_term_threshold': 0.6,
        'long_term_threshold': 0.8
    }
}
```

---

## 6. Performance Configuration (Optional)

Performance settings control batch sizes, caching, and search parameters.

### Memory Management Settings

| Configuration | Type | Required | Default | Description |
|--------------|------|----------|---------|-------------|
| `MEMORY_BATCH_SIZE` | integer | No | `100` | Number of memories to process in a single batch |
| `MEMORY_CACHE_SIZE` | integer | No | `1000` | Maximum number of memories to cache in memory |
| `MEMORY_CACHE_TTL` | integer | No | `3600` | Cache time-to-live in seconds |
| `MEMORY_SEARCH_LIMIT` | integer | No | `10` | Maximum number of results to return from memory search |
| `MEMORY_SEARCH_THRESHOLD` | float | No | `0.7` | Minimum similarity threshold for memory search (0.0-1.0) |

### Vector Store Settings

| Configuration | Type | Required | Default | Description |
|--------------|------|----------|---------|-------------|
| `VECTOR_STORE_BATCH_SIZE` | integer | No | `50` | Number of vectors to process in a single batch |
| `VECTOR_STORE_CACHE_SIZE` | integer | No | `500` | Maximum number of vectors to cache |
| `VECTOR_STORE_INDEX_REBUILD_INTERVAL` | integer | No | `86400` | Vector index rebuild interval in seconds (24 hours) |

**Environment Variables Example:**
```env
MEMORY_BATCH_SIZE=100
MEMORY_CACHE_SIZE=1000
MEMORY_CACHE_TTL=3600
MEMORY_SEARCH_LIMIT=10
MEMORY_SEARCH_THRESHOLD=0.7
VECTOR_STORE_BATCH_SIZE=50
VECTOR_STORE_CACHE_SIZE=500
VECTOR_STORE_INDEX_REBUILD_INTERVAL=86400
```

**Note:** Performance settings are typically configured through environment variables. JSON configuration for these settings may vary based on implementation. Check the specific API documentation for programmatic configuration options.

---

## 7. Security Configuration (Optional)

Security settings control encryption and access control.

### Encryption Settings

| Configuration | Type | Required | Default | Description |
|--------------|------|----------|---------|-------------|
| `ENCRYPTION_ENABLED` | boolean | No | `false` | Enable encryption for stored memories |
| `ENCRYPTION_KEY` | string | Yes* | - | Encryption key. Required when `ENCRYPTION_ENABLED=true`. Should be a secure random string |
| `ENCRYPTION_ALGORITHM` | string | No | `AES-256-GCM` | Encryption algorithm to use. Options: `AES-256-GCM` |

### Access Control Settings

| Configuration | Type | Required | Default | Description |
|--------------|----------|---------|---------|-------------|
| `ACCESS_CONTROL_ENABLED` | boolean | No | `true` | Enable access control for memories |
| `ACCESS_CONTROL_DEFAULT_PERMISSION` | string | No | `READ_ONLY` | Default permission level. Options: `READ_ONLY`, `READ_WRITE`, `FULL` |
| `ACCESS_CONTROL_ADMIN_USERS` | string | No | `admin,root` | Comma-separated list of admin usernames |

**Environment Variables Example:**
```env
ENCRYPTION_ENABLED=false
ENCRYPTION_KEY=
ENCRYPTION_ALGORITHM=AES-256-GCM
ACCESS_CONTROL_ENABLED=true
ACCESS_CONTROL_DEFAULT_PERMISSION=READ_ONLY
ACCESS_CONTROL_ADMIN_USERS=admin,root
```

**Note:** Security settings are typically configured through environment variables. JSON configuration for these settings may vary based on implementation.

---

## 8. Telemetry Configuration (Optional)

Telemetry settings control usage analytics and monitoring.

| Configuration | Type | Required | Default | Description |
|--------------|------|----------|---------|-------------|
| `TELEMETRY_ENABLED` | boolean | No | `false` | Enable telemetry data collection |
| `TELEMETRY_ENDPOINT` | string | No | `https://telemetry.powermem.ai` | Telemetry endpoint URL |
| `TELEMETRY_API_KEY` | string | Yes* | - | API key for telemetry endpoint. Required when `TELEMETRY_ENABLED=true` |
| `TELEMETRY_BATCH_SIZE` | integer | No | `100` | Number of telemetry events to batch before sending |
| `TELEMETRY_FLUSH_INTERVAL` | integer | No | `30` | Telemetry flush interval in seconds |
| `TELEMETRY_RETENTION_DAYS` | integer | No | `30` | Number of days to retain telemetry data |

**Environment Variables Example:**
```env
TELEMETRY_ENABLED=false
TELEMETRY_ENDPOINT=https://telemetry.powermem.ai
TELEMETRY_API_KEY=
TELEMETRY_BATCH_SIZE=100
TELEMETRY_FLUSH_INTERVAL=30
TELEMETRY_RETENTION_DAYS=30
```

**JSON Configuration Example:**
```json
{
  "telemetry": {
    "enable_telemetry": false,
    "telemetry_endpoint": "https://telemetry.powermem.ai",
    "telemetry_api_key": "",
    "telemetry_batch_size": 100,
    "telemetry_flush_interval": 30
  }
}
```

**Python Dictionary Example:**
```python
config = {
    'telemetry': {
        'enable_telemetry': False,
        'telemetry_endpoint': 'https://telemetry.powermem.ai',
        'telemetry_api_key': '',
        'telemetry_batch_size': 100,
        'telemetry_flush_interval': 30
    }
}
```

---

## 9. Audit Configuration (Optional)

Audit settings control audit logging for compliance and security.

| Configuration | Type | Required | Default | Description |
|--------------|------|----------|---------|-------------|
| `AUDIT_ENABLED` | boolean | No | `true` | Enable audit logging |
| `AUDIT_LOG_FILE` | string | No | `./logs/audit.log` | Path to audit log file |
| `AUDIT_LOG_LEVEL` | string | No | `INFO` | Audit log level. Options: `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL` |
| `AUDIT_RETENTION_DAYS` | integer | No | `90` | Number of days to retain audit logs |
| `AUDIT_COMPRESS_LOGS` | boolean | No | `true` | Compress old audit log files |
| `AUDIT_LOG_ROTATION_SIZE` | string | No | `100MB` | Maximum size of audit log file before rotation (e.g., `100MB`, `1GB`) |

**Environment Variables Example:**
```env
AUDIT_ENABLED=true
AUDIT_LOG_FILE=./logs/audit.log
AUDIT_LOG_LEVEL=INFO
AUDIT_RETENTION_DAYS=90
AUDIT_COMPRESS_LOGS=true
AUDIT_LOG_ROTATION_SIZE=100MB
```

**JSON Configuration Example:**
```json
{
  "audit": {
    "enabled": true,
    "log_file": "./logs/audit.log",
    "log_level": "INFO",
    "retention_days": 90
  }
}
```

**Python Dictionary Example:**
```python
config = {
    'audit': {
        'enabled': True,
        'log_file': './logs/audit.log',
        'log_level': 'INFO',
        'retention_days': 90
    }
}
```

---

## 10. Logging Configuration (Optional)

Logging settings control general application logging.

### General Logging Settings

| Configuration | Type | Required | Default | Description |
|--------------|------|----------|---------|-------------|
| `LOGGING_LEVEL` | string | No | `DEBUG` | Logging level. Options: `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL` |
| `LOGGING_FORMAT` | string | No | `%(asctime)s - %(name)s - %(levelname)s - %(message)s` | Log message format (Python logging format) |
| `LOGGING_FILE` | string | No | `./logs/powermem.log` | Path to log file |
| `LOGGING_MAX_SIZE` | string | No | `100MB` | Maximum size of log file before rotation |
| `LOGGING_BACKUP_COUNT` | integer | No | `5` | Number of backup log files to keep |
| `LOGGING_COMPRESS_BACKUPS` | boolean | No | `true` | Compress old log files |

### Console Logging Settings

| Configuration | Type | Required | Default | Description |
|--------------|------|----------|---------|-------------|
| `LOGGING_CONSOLE_ENABLED` | boolean | No | `true` | Enable console logging |
| `LOGGING_CONSOLE_LEVEL` | string | No | `INFO` | Console logging level. Options: `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL` |
| `LOGGING_CONSOLE_FORMAT` | string | No | `%(levelname)s - %(message)s` | Console log message format |

**Environment Variables Example:**
```env
LOGGING_LEVEL=DEBUG
LOGGING_FORMAT=%(asctime)s - %(name)s - %(levelname)s - %(message)s
LOGGING_FILE=./logs/powermem.log
LOGGING_MAX_SIZE=100MB
LOGGING_BACKUP_COUNT=5
LOGGING_COMPRESS_BACKUPS=true
LOGGING_CONSOLE_ENABLED=true
LOGGING_CONSOLE_LEVEL=INFO
LOGGING_CONSOLE_FORMAT=%(levelname)s - %(message)s
```

**JSON Configuration Example:**
```json
{
  "logging": {
    "level": "DEBUG",
    "format": "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    "file": "./logs/powermem.log"
  }
}
```

**Python Dictionary Example:**
```python
config = {
    'logging': {
        'level': 'DEBUG',
        'format': '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        'file': './logs/powermem.log'
    }
}
```

---

## Quick Start Examples

### Minimal Development Configuration

**Environment Variables:**
```env
# Required: Database
DATABASE_PROVIDER=sqlite
SQLITE_PATH=./data/powermem_dev.db

# Required: LLM
LLM_PROVIDER=qwen
LLM_API_KEY=your_api_key_here
LLM_MODEL=qwen-plus

# Required: Embedding
EMBEDDING_PROVIDER=qwen
EMBEDDING_API_KEY=your_api_key_here
EMBEDDING_MODEL=text-embedding-v4
EMBEDDING_DIMS=1536
```

**JSON Configuration:**
```json
{
  "vector_store": {
    "provider": "sqlite",
    "config": {
      "database_path": "./data/powermem_dev.db"
    }
  },
  "llm": {
    "provider": "qwen",
    "config": {
      "api_key": "your_api_key_here",
      "model": "qwen-plus"
    }
  },
  "embedder": {
    "provider": "qwen",
    "config": {
      "api_key": "your_api_key_here",
      "model": "text-embedding-v4",
      "embedding_dims": 1536
    }
  }
}
```

**Python Dictionary:**
```python
config = {
    'vector_store': {
        'provider': 'sqlite',
        'config': {
            'database_path': './data/powermem_dev.db'
        }
    },
    'llm': {
        'provider': 'qwen',
        'config': {
            'api_key': 'your_api_key_here',
            'model': 'qwen-plus'
        }
    },
    'embedder': {
        'provider': 'qwen',
        'config': {
            'api_key': 'your_api_key_here',
            'model': 'text-embedding-v4',
            'embedding_dims': 1536
        }
    }
}

from powermem import Memory
memory = Memory(config=config)
```

### Production Configuration with OceanBase

**Environment Variables:**
```env
# Database
DATABASE_PROVIDER=oceanbase
OCEANBASE_HOST=prod-db.example.com
OCEANBASE_PORT=2881
OCEANBASE_USER=prod_user
OCEANBASE_PASSWORD=secure_password
OCEANBASE_DATABASE=powermem_prod
OCEANBASE_EMBEDDING_MODEL_DIMS=1536

# LLM
LLM_PROVIDER=qwen
LLM_API_KEY=production_key
LLM_MODEL=qwen-plus

# Embedding
EMBEDDING_PROVIDER=qwen
EMBEDDING_API_KEY=production_key
EMBEDDING_MODEL=text-embedding-v4
EMBEDDING_DIMS=1536

# Optional: Enable intelligent memory and audit
INTELLIGENT_MEMORY_ENABLED=true
AUDIT_ENABLED=true
```

**JSON Configuration:**
```json
{
  "vector_store": {
    "provider": "oceanbase",
    "config": {
      "collection_name": "memories",
      "connection_args": {
        "host": "prod-db.example.com",
        "port": 2881,
        "user": "prod_user",
        "password": "secure_password",
        "db_name": "powermem_prod"
      },
      "embedding_model_dims": 1536,
      "vidx_metric_type": "cosine",
      "index_type": "IVF_FLAT"
    }
  },
  "llm": {
    "provider": "qwen",
    "config": {
      "api_key": "production_key",
      "model": "qwen-plus"
    }
  },
  "embedder": {
    "provider": "qwen",
    "config": {
      "api_key": "production_key",
      "model": "text-embedding-v4",
      "embedding_dims": 1536
    }
  },
  "intelligent_memory": {
    "enabled": true,
    "initial_retention": 1.0,
    "decay_rate": 0.1,
    "reinforcement_factor": 0.3
  },
  "audit": {
    "enabled": true,
    "log_file": "./logs/audit.log",
    "log_level": "INFO"
  }
}
```

**Python Dictionary:**
```python
config = {
    'vector_store': {
        'provider': 'oceanbase',
        'config': {
            'collection_name': 'memories',
            'connection_args': {
                'host': 'prod-db.example.com',
                'port': 2881,
                'user': 'prod_user',
                'password': 'secure_password',
                'db_name': 'powermem_prod'
            },
            'embedding_model_dims': 1536,
            'vidx_metric_type': 'cosine',
            'index_type': 'IVF_FLAT'
        }
    },
    'llm': {
        'provider': 'qwen',
        'config': {
            'api_key': 'production_key',
            'model': 'qwen-plus'
        }
    },
    'embedder': {
        'provider': 'qwen',
        'config': {
            'api_key': 'production_key',
            'model': 'text-embedding-v4',
            'embedding_dims': 1536
        }
    },
    'intelligent_memory': {
        'enabled': True,
        'initial_retention': 1.0,
        'decay_rate': 0.1,
        'reinforcement_factor': 0.3
    },
    'audit': {
        'enabled': True,
        'log_file': './logs/audit.log',
        'log_level': 'INFO'
    }
}

from powermem import Memory
memory = Memory(config=config)
```

### Complete Configuration Example (JSON)

Here's a complete JSON configuration file example (`config.json`) with all optional settings:

```json
{
  "vector_store": {
    "provider": "sqlite",
    "config": {
      "database_path": "./data/powermem_dev.db",
      "enable_wal": true,
      "timeout": 30
    }
  },
  "llm": {
    "provider": "qwen",
    "config": {
      "api_key": "your_api_key_here",
      "model": "qwen-plus",
      "dashscope_base_url": "https://dashscope.aliyuncs.com/api/v1",
      "temperature": 0.7,
      "max_tokens": 1000,
      "top_p": 0.8,
      "top_k": 50,
      "enable_search": false
    }
  },
  "embedder": {
    "provider": "qwen",
    "config": {
      "api_key": "your_api_key_here",
      "model": "text-embedding-v4",
      "embedding_dims": 1536
    }
  },
  "agent_memory": {
    "enabled": true,
    "mode": "auto",
    "default_scope": "AGENT",
    "default_privacy_level": "PRIVATE",
    "default_collaboration_level": "READ_ONLY",
    "default_access_permission": "OWNER_ONLY"
  },
  "intelligent_memory": {
    "enabled": true,
    "initial_retention": 1.0,
    "decay_rate": 0.1,
    "reinforcement_factor": 0.3,
    "working_threshold": 0.3,
    "short_term_threshold": 0.6,
    "long_term_threshold": 0.8
  },
  "telemetry": {
    "enable_telemetry": false,
    "telemetry_endpoint": "https://telemetry.powermem.ai",
    "telemetry_api_key": "",
    "telemetry_batch_size": 100,
    "telemetry_flush_interval": 30
  },
  "audit": {
    "enabled": true,
    "log_file": "./logs/audit.log",
    "log_level": "INFO",
    "retention_days": 90
  },
  "logging": {
    "level": "DEBUG",
    "format": "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    "file": "./logs/powermem.log"
  }
}
```

**Loading from JSON file:**
```python
import json
from powermem import Memory

# Load configuration from JSON file
with open('config.json', 'r') as f:
    config = json.load(f)

# Create memory instance
memory = Memory(config=config)
```

---
