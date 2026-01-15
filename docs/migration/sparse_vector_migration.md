# Sparse Vector Migration Guide

This guide provides detailed instructions on how to add sparse vector support to existing tables and migrate historical data.

## Prerequisites

- Python 3.10+
- powermem installed (`pip install powermem`)
- Database requirements: **seekdb** or **OceanBase >= 4.5.0**
- Existing table without sparse vector support

> **Note**: For new tables, simply enable `include_sparse=True` in the configuration. No upgrade or migration operations are required.

## Migration Workflow Overview

```
Existing Table (without sparse vector)
    ↓
1. Configure sparse vector
    ↓
2. Run Schema upgrade script (required)
    ↓
3. Run data migration script (optional, but recommended)
    ↓
4. Verify migration results
```

## Step 1: Configure Sparse Vector

Before running the upgrade script, you need to configure sparse vector. Add the following configuration to your `.env` file:

```env
# Enable sparse vector
SPARSE_VECTOR_ENABLE=true

# Sparse vector embedding configuration
SPARSE_EMBEDDER_PROVIDER=qwen
SPARSE_EMBEDDER_API_KEY=your_api_key
SPARSE_EMBEDDER_MODEL=text-embedding-v4
SPARSE_EMBEDDER_DIMS=1536
```

Or use dictionary configuration:

```python
config = {
    # ... other configurations ...
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
            'include_sparse': True,  # Enable sparse vector
            # ... other configurations ...
        }
    }
}
```

## Step 2: Schema Upgrade (Required)

### List Available Scripts

```python
from script import ScriptManager

# List all available scripts
ScriptManager.list_scripts()
```

Example output:
```
======================================================================
PowerMem Available Scripts
======================================================================

【Upgrade Scripts - Add new features or upgrade existing features】
----------------------------------------------------------------------
  • upgrade-sparse-vector
    Add sparse vector support to OceanBase table (add sparse_embedding column and index) (requires: dict)

======================================================================
```

### View Script Details

```python
from script import ScriptManager

# View upgrade script details
ScriptManager.info('upgrade-sparse-vector')
```

Example output:
```
======================================================================
Script: upgrade-sparse-vector
======================================================================
Category: upgrade
Description: Add sparse vector support to OceanBase table (add sparse_embedding column and index)

----------------------------------------------------------------------
Parameters:
----------------------------------------------------------------------
  config (dict) (required)
```

### Execute Upgrade Script

```python
from powermem import auto_config
from script import ScriptManager

# Load configuration
config = auto_config()

# Run upgrade script
ScriptManager.run('upgrade-sparse-vector', config)
```

**Expected output:**
```
Preparing to execute script: upgrade-sparse-vector
Description: Add sparse vector support to OceanBase table (add sparse_embedding column and index)
Loading module: script.scripts.upgrade_sparse_vector
Executing script function: upgrade_sparse_vector
Starting sparse vector upgrade for table 'memories'
Adding sparse_embedding column to table 'memories'
sparse_embedding column added successfully
Creating sparse vector index on table 'memories'
sparse_embedding_idx created successfully
Sparse vector upgrade completed successfully for table 'memories'

✓ Script 'upgrade-sparse-vector' executed successfully!
```

### Operations Performed by Upgrade Script

The upgrade script performs the following operations:
1. Check if the database version supports sparse vector
2. Add `sparse_embedding` column (SPARSE_VECTOR type)
3. Create `sparse_embedding_idx` index

> **Note**: The upgrade script is idempotent and can be safely executed multiple times.

## Step 3: Historical Data Migration (Optional, but Recommended)

After schema upgrade, the `sparse_embedding` column for historical data is empty. **Historical data migration is not required**, but it is strongly recommended to run the migration script for the following reasons:

- **Only migrated data will participate in sparse vector retrieval**: Unmigrated historical data will not use sparse vector during search. Only newly added data and migrated data will participate in sparse vector search
- **More accurate results after migration**: Sparse vector search provides more precise semantic matching. After migrating historical data, all data can benefit from the improved search accuracy brought by sparse vector
- **New data automatically generated**: Even without migrating historical data, newly added data will automatically generate sparse vectors and participate in search

### View Migration Script Details

```python
from script import ScriptManager

# View migration script details
ScriptManager.info('migrate-sparse-vector')
```

### Migration Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `batch_size` | int | `100` | Number of records processed per batch |
| `workers` | int | `1` | Number of concurrent threads, increasing can improve migration speed |
| `delay` | float | `0.1` | Delay between batches (seconds) |
| `dry_run` | bool | `False` | Test mode, only processes 100 records and does not write to database |

### Test with dry-run Mode

Before formal migration, it is recommended to test with dry-run mode first:

```python
from powermem import Memory, auto_config
from script import ScriptManager

# Load configuration
config = auto_config()

# Create Memory instance (migration script requires Memory instance)
memory = Memory(config=config)

# Test mode (only processes 100 records, does not write to database)
print("Test mode (dry-run):")
ScriptManager.run('migrate-sparse-vector', memory, dry_run=True)
```

**Expected output:**
```
Test mode (dry-run):
Preparing to execute script: migrate-sparse-vector
...
[DRY RUN] Mode enabled - will only test with 100 records

Total: [██████████████]  100.0% | 100/100
  ✓ Migrated: 100 | ✗ Failed: 0
  ⏱ Elapsed: 5.2s | Remaining: ~0s | 📊 19.2 rec/s

✓ Script 'migrate-sparse-vector' executed successfully!
```

### Execute Formal Migration

```python
from powermem import Memory, auto_config
from script import ScriptManager

# Load configuration
config = auto_config()

# Create Memory instance
memory = Memory(config=config)

# Formal migration (recommended to configure concurrent threads for better speed)
print("Formal migration:")
ScriptManager.run('migrate-sparse-vector', memory, batch_size=100, workers=3)
```

**Expected output:**
```
Formal migration:
Preparing to execute script: migrate-sparse-vector
...
Total records to migrate: 10000
Batch size: 100
Thread pool size: 3

Total: [████████████░░]  85.0% | 8,500/10,000
  ✓ Migrated: 8,500 | ✗ Failed: 0
  ⏱ Elapsed: 3m 42s | Remaining: ~39s | 📊 38.3 rec/s

Workers (3):
  Worker 0: ✓ 2,834 | ✗ 0
  Worker 1: ✓ 2,833 | ✗ 0
  Worker 2: ✓ 2,833 | ✗ 0
```

### Migration Progress

Real-time progress will be displayed during migration:

```
Total: [████████░░░░░░] 57.1% | 5,710/10,000
  ✓ Migrated: 5,710 | ✗ Failed: 0
  ⏱ Elapsed: 2m 30s | Remaining: ~1m 52s | 📊 38.1 rec/s

Workers (3):
  Worker 0: ✓ 1,903 | ✗ 0
  Worker 1: ✓ 1,904 | ✗ 0
  Worker 2: ✓ 1,903 | ✗ 0
```

Progress information includes:
- **Progress bar**: Shows completion percentage and count
- **Migrated/Failed**: Number of successful and failed records
- **Elapsed/Remaining**: Time elapsed and estimated remaining time
- **Speed**: Records processed per second
- **Workers**: Processing status of each thread

## Step 4: Verify Migration Results

After migration is complete, verify if sparse vector is working:

```python
from powermem import Memory, auto_config
import logging

# Load configuration
config = auto_config()
memory = Memory(config=config)

# Enable DEBUG logging to view search details
logging.getLogger().setLevel(logging.DEBUG)

# Execute search
print("Executing verification search...")
result = memory.search(query="test query", limit=10)

print(f"\n✓ Search returned {len(result.get('results', []))} results")
print("  Sparse vector search is active (check DEBUG logs to confirm)")
```

**Expected output:**
```
Executing verification search...
DEBUG:powermem.storage.oceanbase.oceanbase:Executing sparse vector search query with sparse_vector: ...
DEBUG:powermem.storage.oceanbase.oceanbase:_sparse_search results, len : 10

✓ Search returned 10 results
  Sparse vector search is active (check DEBUG logs to confirm)
```

You can see sparse vector search related information in the DEBUG logs.

## Complete Migration Example

```python
#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Complete Sparse Vector Migration Example
Demonstrates how to upgrade existing tables and migrate historical data
"""
from powermem import Memory, auto_config
from script import ScriptManager
import logging

def main():
    # 1. List available scripts
    print("=" * 60)
    print("Step 1: List Available Scripts")
    print("=" * 60)
    ScriptManager.list_scripts()

    # 2. View script details
    print("\n" + "=" * 60)
    print("Step 2: View Script Details")
    print("=" * 60)
    ScriptManager.info("upgrade-sparse-vector")
    ScriptManager.info("migrate-sparse-vector")

    # 3. Load configuration
    config = auto_config()

    # 4. Run upgrade script (add sparse vector support to existing table)
    print("\n" + "=" * 60)
    print("Step 3: Run Schema Upgrade Script")
    print("=" * 60)
    ScriptManager.run('upgrade-sparse-vector', config)

    # 5. Create Memory instance
    memory = Memory(config=config)

    # 6. Test migration (dry-run mode)
    print("\n" + "=" * 60)
    print("Step 4: Test Migration (dry-run)")
    print("=" * 60)
    ScriptManager.run('migrate-sparse-vector', memory, dry_run=True)

    # 7. Formal migration (optional: generate sparse vectors for historical data)
    # Note: Only migrated data will participate in sparse vector retrieval, results are more accurate after migration
    print("\n" + "=" * 60)
    print("Step 5: Run Data Migration Script (Optional)")
    print("=" * 60)
    user_input = input("Execute formal migration? (y/N): ")
    if user_input.lower() == 'y':
        ScriptManager.run('migrate-sparse-vector', memory, batch_size=100, workers=3)

    # 8. Verify search
    print("\n" + "=" * 60)
    print("Step 6: Verify Search")
    print("=" * 60)
    logging.getLogger().setLevel(logging.DEBUG)
    result = memory.search(query="test query", limit=10)
    print(f"Search returned {len(result.get('results', []))} results")

if __name__ == "__main__":
    main()
```

## Rollback (Optional)

If you need to remove sparse vector support, you can run the downgrade script:

```python
from powermem import auto_config
from script import ScriptManager

config = auto_config()

# Run downgrade script (will delete all sparse vector data)
ScriptManager.run('downgrade-sparse-vector', config)
```

> **Warning**: The downgrade script will delete the `sparse_embedding` column and index. All sparse vector data will be permanently deleted!

## Frequently Asked Questions

### 1. Is historical data migration required?

Not required, but strongly recommended. Unmigrated historical data:
- Will not participate in sparse vector retrieval
- Can still be found through vector search and full-text search
- Newly added data will automatically generate sparse vectors

### 2. How to improve migration speed?

- Increase the `workers` parameter value (number of concurrent threads)
- Adjust `batch_size` (batch size)
- Reduce `delay` (delay between batches)

### 3. What to do if migration fails?

- Check network connection and API keys
- View detailed error logs
- You can re-run the migration script. The script will automatically skip already migrated data

### 4. Can the upgrade script be executed multiple times?

Yes. The upgrade script is idempotent. Repeated execution will not cause problems.

## Related Documentation

- [Sparse Vector Guide](../guides/0011-sparse_vector.md) - Detailed sparse vector configuration guide
- [Configuration Guide](../guides/0003-configuration.md) - Complete configuration reference
- [Getting Started](../guides/0001-getting_started.md) - Quick start guide
