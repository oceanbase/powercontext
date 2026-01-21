# Sparse Vector Guide

This guide explains how to use the Sparse Vector feature in PowerMem, including configuration, query usage, schema upgrades, and historical data migration.

## Prerequisites

- Python 3.10+
- powermem installed (`pip install powermem`)
- Database requirements: **seekdb** or **OceanBase >= 4.5.0**

> **Note**: Sparse vector feature only supports OceanBase storage backend, SQLite does not support this feature.

## Configuring Sparse Vector

To enable sparse vector functionality, you need to configure two parts:

1. `vector_store.config.include_sparse = True` - Enable sparse vector support
2. `sparse_embedder` - Configure sparse vector embedding service

### Environment Variable Configuration

Add the following configuration to your `.env` file:

```env
# Database configuration
DATABASE_PROVIDER=oceanbase
OCEANBASE_HOST=127.0.0.1
OCEANBASE_PORT=2881
OCEANBASE_USER=root
OCEANBASE_PASSWORD=your_password
OCEANBASE_DATABASE=powermem
OCEANBASE_COLLECTION=memories
OCEANBASE_EMBEDDING_MODEL_DIMS=1536

# Enable sparse vector
SPARSE_VECTOR_ENABLE=true

# Sparse vector embedding configuration
SPARSE_EMBEDDER_PROVIDER=qwen
SPARSE_EMBEDDER_API_KEY=your_api_key
SPARSE_EMBEDDER_MODEL=text-embedding-v4
SPARSE_EMBEDDER_DIMS=1536
```

### Dictionary Configuration

Configure sparse vector using Python dictionary:

```python
from powermem import Memory

config = {
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
            'model': 'text-embedding-v4',
            'embedding_dims': 1536
        }
    },
    # Sparse vector embedding configuration
    'sparse_embedder': {
        'provider': 'qwen',
        'config': {
            'api_key': 'your_api_key',
            'model': 'text-embedding-v4'
        }
    },
    'vector_store': {
        'provider': 'oceanbase',
        'config': {
            'collection_name': 'memories',
            'embedding_model_dims': 1536,
            'include_sparse': True,  # Enable sparse vector
            'connection_args': {
                'host': '127.0.0.1',
                'port': 2881,
                'user': 'root',
                'password': 'your_password',
                'db_name': 'powermem'
            },
            # Optional: Configure search weights
            'vector_weight': 0.5,
            'fts_weight': 0.5,
            'sparse_weight': 0.25
        }
    }
}

memory = Memory(config=config)
```

### Configuration Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `include_sparse` | bool | `False` | Whether to enable sparse vector support |
| `sparse_embedder.provider` | string | - | Sparse vector embedding provider (currently supports `qwen`) |
| `sparse_embedder.config.api_key` | string | - | API key |
| `sparse_embedder.config.model` | string | - | Embedding model name |
| `vector_weight` | float | `0.5` | Vector search weight |
| `fts_weight` | float | `0.5` | Full-text search weight |
| `sparse_weight` | float | `0.25` | Sparse vector search weight |

## Query Usage

After configuring sparse vector, searches will automatically use sparse vector for hybrid search without any code changes.

### Basic Search

```python
from powermem import Memory, auto_config

# Load configuration (automatically loads from .env)
config = auto_config()
memory = Memory(config=config)

# Add memory (automatically generates sparse vector)
memory.add(
    messages="Machine learning is a branch of artificial intelligence, I love machine learning",
    user_id="user123"
)

# Search (automatically uses sparse vector for hybrid search)
results = memory.search(
    query="AI technology",
    user_id="user123",
    limit=10
)
```

### Search Weight Configuration

Search combines three methods: vector search, full-text search, and sparse vector search. You can adjust the influence of each search method by configuring weights:

- `vector_weight`: Vector search weight (default 0.5)
- `fts_weight`: Full-text search weight (default 0.5)
- `sparse_weight`: Sparse vector search weight (default 0.25)

## Schema Upgrade and Data Migration

If you already have a table without sparse vector support, you need to upgrade the schema and optionally migrate historical data.

For detailed instructions on upgrading existing tables and migrating historical data, please refer to:

**[Sparse Vector Migration Guide](../migration/sparse_vector_migration.md)**

The migration guide covers:
- Schema upgrade steps
- Migration parameters and options
- Progress monitoring
- Verification methods
- Rollback procedures

## Complete Usage Workflow

### New Table (Recommended)

If creating a new table, simply enable sparse vector in the configuration:

```python
from powermem import Memory, auto_config

config = auto_config()  # Ensure include_sparse=True in configuration
memory = Memory(config=config)

# Add memory (automatically generates sparse vector)
memory.add(messages="memory content", user_id="user123")

# Search (automatically uses sparse vector)
results = memory.search(query="query content", user_id="user123")
```

### Existing Table Upgrade

If upgrading an existing table, please refer to the [Sparse Vector Migration Guide](../migration/sparse_vector_migration.md) for detailed instructions.

