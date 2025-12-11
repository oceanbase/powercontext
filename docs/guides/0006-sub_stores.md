# Sub Stores Guide

Complete guide to using sub stores for advanced memory partitioning and optimization.

## Overview

Sub stores allow you to partition memory data into separate vector tables, each with:

- **Independent routing rules**: Automatically route data based on metadata
- **Different embedding services**: Use different models and dimensions
- **Custom storage configuration**: Different index types, database parameters, etc.
- **Independent performance optimization**: Optimize for different data characteristics

**Note**: Sub stores are currently only supported with OceanBase vector store.

## Quick Start

### Basic Configuration

```python
from powermem import Memory

config = {
    "vector_store": {
        "provider": "oceanbase",
        "config": {
            "collection_name": "main_memories",
            "embedding_model_dims": 1536,
            "host": "127.0.0.1",
            "port": "2881",
            "user": "root@test",
            "password": "password",
            "db_name": "test_db",
        }
    },
    "embedding": {
        "provider": "openai",
        "config": {
            "api_key": "your-api-key",
            "model": "text-embedding-3-large",
            "embedding_dims": 1536,
        }
    },
    # Configure sub stores
    "sub_stores": [
        {
            # Sub store 1: Inherits main config
            "routing_filter": {"type": "semantic"}
        },
        {
            # Sub store 2: Uses different embedding
            "routing_filter": {"type": "working"},
            "embedding_model_dims": 768,
            "embedding": {
                "provider": "qwen",
                "config": {
                    "model": "text-embedding-v4",
                    "embedding_dims": 768,
                    "api_key": "your-qwen-key",
                }
            }
        }
    ]
}

memory = Memory(config=config)
```

### Initialize Sub Store Routing

**Important**: Before using sub store routing, you must run migration at least once to activate the routing functionality, even if there's no data to migrate:

```python
# Initialize sub store routing (required before first use)
memory.migrate_all_sub_stores(delete_source=False)
```

This ensures that sub store migration status is properly initialized in the database, enabling automatic routing.

### Using Sub Stores

Once initialized, data is automatically routed to the appropriate sub store based on metadata:

```python
# Add data - automatically routed to sub stores
memory.add(
    "Python is a programming language",
    metadata={"type": "semantic"}  # Routes to sub store 1
)

memory.add(
    "Buy milk today",
    metadata={"type": "working"}  # Routes to sub store 2
)

# Search from specific sub store
results = memory.search(
    "programming concepts",
    filters={"type": "semantic"},  # Searches in sub store 1
    limit=5
)
```

## Configuration Options

### Required Parameters

#### routing_filter

Defines which data should be routed to this sub store:

```python
"routing_filter": {
    "type": "working",      # Single condition
    "priority": "high"      # Multiple conditions (AND logic)
}
```

### Optional Parameters

#### collection_name

Custom table name. Defaults to `{main_table_name}_sub_{index}`:

```python
"collection_name": "memories_important"
```

#### embedding_model_dims

Vector dimensions. Defaults to main store dimensions:

```python
"embedding_model_dims": 768
```

#### embedding

Sub store specific embedding service:

```python
"embedding": {
    "provider": "qwen",
    "config": {
        "model": "text-embedding-v4",
        "embedding_dims": 768,
        "api_key": "your-api-key",
    }
}
```

**Important**: `embedding.config.embedding_dims` must match `embedding_model_dims`.

#### vector_store

Override main store's vector_store configuration:

```python
"vector_store": {
    "index_type": "HNSW",           # Use HNSW index
    "vector_weight": 0.7,           # Vector search weight
    "fts_weight": 0.3,              # Full-text search weight
    "host": "different-host",       # Use different database
    # Any vector_store parameter can be overridden
}
```

#### index_type

Index type for this sub store:

```python
"index_type": "HNSW"  # Options: "HNSW", "IVF_FLAT", "IVF_PQ"
```

## Data Migration

### Migrate Data to Sub Store

```python
# Method 1: Migrate by index
count = memory.migrate_to_sub_store(
    sub_store_index=0,      # Sub store index
    delete_source=False     # Keep source data
)

# Method 2: Migrate by name
count = memory.migrate_to_sub_store(
    sub_store_name="memories_sub_0",
    delete_source=True      # Delete source after migration
)

# Method 3: Migrate all sub stores
count = memory.migrate_all_sub_stores(delete_source=False)
```

### Monitor Migration Status

```python
# Check migration status
status = memory.get_sub_store_migration_status("memories_sub_0")

print(f"Status: {status['status']}")           # PENDING/MIGRATING/COMPLETED/FAILED
print(f"Progress: {status['migrated_count']}/{status['total_count']}")
print(f"Percentage: {status['progress']}%")
```

### Migration Process

During migration:
1. Queries matching records based on `routing_filter`
2. **Re-generates vectors** using sub store's `embedding_service`
3. Inserts new vectors and payload into sub store
4. Optionally deletes source data

## Advanced Configuration

### Multiple Embedding Dimensions

```python
config = {
    "vector_store": {"config": {"embedding_model_dims": 1536}},
    "embedding": {
        "provider": "openai",
        "config": {"model": "text-embedding-3-large", "embedding_dims": 1536}
    },
    "sub_stores": [
        {
            "routing_filter": {"priority": "low"},
            "embedding_model_dims": 384,  # Smaller dimension
            "embedding": {
                "provider": "qwen",
                "config": {"model": "text-embedding-v4", "embedding_dims": 384}
            }
        },
        {
            "routing_filter": {"priority": "high"},
            "embedding_model_dims": 3072,  # Larger dimension
            "embedding": {
                "provider": "openai",
                "config": {"model": "text-embedding-3-large", "embedding_dims": 3072}
            }
        }
    ]
}
```

### Different Index Types

```python
config = {
    "vector_store": {
        "config": {"index_type": "IVF_FLAT"}  # Main store: balanced
    },
    "sub_stores": [
        {
            "routing_filter": {"access_pattern": "hot"},
            "vector_store": {
                "index_type": "HNSW",      # Fast retrieval
                "vector_weight": 0.8,
                "fts_weight": 0.2,
            }
        },
        {
            "routing_filter": {"access_pattern": "cold"},
            "vector_store": {
                "index_type": "IVF_PQ"     # Compressed storage
            }
        }
    ]
}
```

### Custom Database Configuration

```python
config = {
    "vector_store": {...},
    "sub_stores": [
        {
            "routing_filter": {"tenant": "enterprise_a"},
            "collection_name": "memories_enterprise_a",
            "vector_store": {
                "database": "tenant_a_db",     # Different database
                "host": "db-a.example.com",    # Different host
            }
        }
    ]
}
```

## Common Patterns

### Pattern 1: By Memory Type

```python
"sub_stores": [
    {
        "routing_filter": {"type": "episodic"},
        "embedding_model_dims": 1536,
    },
    {
        "routing_filter": {"type": "semantic"},
        "embedding_model_dims": 768,
        "embedding": {...}  # Different model
    }
]
```

### Pattern 2: By Priority Level

```python
"sub_stores": [
    {
        "routing_filter": {"priority": "high"},
        "vector_store": {
            "index_type": "HNSW"  # Fast queries
        }
    },
    {
        "routing_filter": {"priority": "low"},
        "vector_store": {
            "index_type": "IVF_PQ"  # Save resources
        }
    }
]
```

### Pattern 3: By Access Frequency

```python
"sub_stores": [
    {
        "routing_filter": {"frequency": "hot"},
        "vector_store": {
            "index_type": "HNSW",
            "vector_weight": 0.7,
            "fts_weight": 0.3
        }
    },
    {
        "routing_filter": {"frequency": "cold"},
        "vector_store": {
            "index_type": "IVF_FLAT"
        }
    }
]
```

## Important Notes

### Initialize Before Use

**Critical**: Before using sub store routing, you must initialize the migration system at least once:

```python
memory.migrate_all_sub_stores(delete_source=False)
```

This is required even if you have no existing data to migrate. It initializes the sub store status in the database, which enables the routing mechanism. Without this step, all data will continue to go to the main store.

### Dimension Consistency

Ensure both dimension parameters match:

```python
{
    "embedding_model_dims": 768,  # Database table structure
    "embedding": {
        "config": {
            "embedding_dims": 768,  # API returns this dimension
        }
    }
}
```

Mismatched dimensions will cause errors: `"expected 1536 dimensions, not 768"`

### Routing Rules

- Sub store `routing_filter` matches against memory `metadata`
- All filter conditions must match (AND logic)
- If no sub store matches, data goes to main store
- Only exact matches are supported (no OR/NOT logic)

## Related Documentation

- [Getting Started](./0001-getting_started.md) - Quick start
- [Configuration Guide](./0003-configuration.md) - Basic configuration
- [Integrations Guide](./0009-integrations.md) - Embedding services

## Examples

See complete examples:
- `examples/sub_store_simple.py` - Basic sub store usage
- `examples/multi_agent.py` - Multi-agent with sub stores
