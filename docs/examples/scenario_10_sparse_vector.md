# Scenario 10: Sparse Vector

This example demonstrates how to use the sparse vector feature, including configuration, adding memories, searching, and upgrading existing tables and migrating historical data.

## Prerequisites

- Python 3.10+
- powermem installed (`pip install powermem`)
- Database: **seekdb** or **OceanBase >= 4.5.0**

## Step 1: Configure Sparse Vector

Create configuration to enable sparse vector support:

```python
# sparse_vector_example.py
from powermem import Memory

config = {
    'llm': {
        'provider': 'qwen',
        'config': {
            'api_key': 'your_api_key',
            'model': 'qwen-plus',
            'temperature': 0.2
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
            'collection_name': 'sparse_demo',
            'embedding_model_dims': 1536,
            'include_sparse': True,  # Enable sparse vector
            'connection_args': {
                'host': '127.0.0.1',
                'port': 2881,
                'user': 'root',
                'password': 'your_password',
                'db_name': 'powermem'
            }
        }
    }
}

print("✓ Configuration created successfully")
```

**Run the code:**
```bash
python sparse_vector_example.py
```

**Expected output:**
```
✓ Configuration created successfully
```

## Step 2: Initialize Memory

Create a Memory instance using the configuration. For new tables, the system will automatically create a table structure with sparse vector support:

```python
# sparse_vector_example.py
from powermem import Memory

# ... configuration code (same as Step 1)

# Create Memory instance
memory = Memory(config=config)

print("✓ Memory initialized successfully")
print(f"  - sparse_embedder: {memory.sparse_embedder is not None}")
```

**Expected output:**
```
✓ Memory initialized successfully
  - sparse_embedder: True
```

## Step 3: Add Memories

When adding memories, the system will automatically generate sparse vectors:

```python
# sparse_vector_example.py
from powermem import Memory

# ... initialization code (same as Step 2)

# Add test memories
test_memories = [
    "Machine learning is a branch of artificial intelligence, focusing on algorithms and statistical models",
    "Natural language processing is an interdisciplinary field of computer science and artificial intelligence",
    "Vector search is an important technology for information retrieval, used for similarity matching",
    "Deep learning uses multi-layer neural networks for feature learning",
    "Knowledge graphs are graph-structured data used to represent entities and their relationships"
]

print("Adding test memories...")
for content in test_memories:
    memory.add(
        messages=content,
        user_id="user123",
        infer=False  # Do not use intelligent inference, store directly
    )

print(f"✓ Successfully added {len(test_memories)} memories")
```

**Expected output:**
```
Adding test memories...
✓ Successfully added 5 memories
```

## Step 4: Execute Search

Search will automatically use sparse vector for hybrid search:

```python
# sparse_vector_example.py
from powermem import Memory

# ... add memory code (same as Step 3)

# Execute search
query = "AI algorithms"
print(f"\nSearch query: '{query}'")

results = memory.search(
    query=query,
    user_id="user123",
    limit=5
)

# Display search results
print(f"Found {len(results.get('results', []))} results:\n")
for i, result in enumerate(results.get('results', []), 1):
    print(f"{i}. Score: {result['score']:.4f}")
    print(f"   Content: {result['memory'][:50]}...")
    print()
```

## Step 5: Upgrade Schema for Existing Table (If Needed)

If you already have a table without sparse vector support, you need to upgrade the schema and optionally migrate historical data.

For detailed instructions on upgrading existing tables and migrating historical data, please refer to:

**[Sparse Vector Migration Guide](../migration/sparse_vector_migration.md)**

The migration guide covers:
- Schema upgrade process
- Historical data migration
- Migration parameters and options
- Progress monitoring and verification
- Rollback procedures

Quick example:

```python
from powermem import auto_config
from script import ScriptManager

# Load configuration
config = auto_config()

# Run upgrade script
ScriptManager.run('upgrade-sparse-vector', config)
```

## Complete Example Code

Here is a complete usage example:

```python
#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Complete Sparse Vector Example
Demonstrates how to use sparse vector functionality
"""
from powermem import Memory, auto_config

def main():
    # Load configuration (with sparse vector enabled)
    config = auto_config()
    
    # Create Memory instance
    memory = Memory(config=config)
    
    # Add test memories (automatically generates sparse vectors)
    test_memories = [
        "Machine learning is a branch of artificial intelligence, focusing on algorithms and statistical models",
        "Natural language processing is an interdisciplinary field of computer science and artificial intelligence",
        "Vector search is an important technology for information retrieval, used for similarity matching",
    ]
    
    print("Adding test memories...")
    for content in test_memories:
        memory.add(
            messages=content,
            user_id="user123",
            infer=False
        )
    
    # Search (automatically uses sparse vector for hybrid search)
    print("\nSearching...")
    results = memory.search(
        query="AI technology",
        user_id="user123",
        limit=5
    )
    
    # Display results
    print(f"\nFound {len(results.get('results', []))} results:")
    for i, result in enumerate(results.get('results', []), 1):
        print(f"{i}. Score: {result['score']:.4f}")
        print(f"   Content: {result['memory'][:80]}...")
        print()

if __name__ == "__main__":
    main()
```

> **Note**: For upgrading existing tables and migrating historical data, please refer to the [Sparse Vector Migration Guide](../migration/sparse_vector_migration.md).

## Extended Exercises

1. **Try different search weights**: Modify `vector_weight`, `fts_weight`, and `sparse_weight` parameters to observe changes in search results.

2. **Compare search effectiveness**: Test search results with sparse vector enabled and disabled separately, and compare relevance.

## Related Documentation

- [Sparse Vector Guide](../guides/0011-sparse_vector.md) - Detailed sparse vector configuration guide
- [Sparse Vector Migration Guide](../migration/sparse_vector_migration.md) - Schema upgrade and data migration guide
- [Configuration Guide](../guides/0003-configuration.md) - Complete configuration reference
- [Getting Started](../guides/0001-getting_started.md) - Quick start guide

