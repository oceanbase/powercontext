# Scenario 6: Sub Stores - Memory Partitioning

This scenario demonstrates powermem's sub stores feature - partitioning different types of memories into separate storage for more efficient querying and management.

## Prerequisites

- Completed Scenario 1
- Installed powermem
- Configured OceanBase database (or other storage backend that supports sub stores)
- Configured LLM and Embedding services

## Understanding Sub Stores

Sub stores allow you to:
- Store different types of memories in independent tables
- Configure independent embedding dimensions and services for each sub store
- Automatically route to the correct storage based on metadata
- Migrate existing data to sub stores
- Improve query performance and resource utilization

### Typical Application Scenarios

1. **Partition by memory type**: Semantic memories, episodic memories, working memories
2. **Partition by importance**: High-priority memories use high-dimensional embeddings, low-priority use lower dimensions
3. **Partition by temporality**: Long-term memories and short-term cache stored separately
4. **Partition by user**: Different user groups' memories managed separately

## ⚠️ Important: Sub Store Activation

**Before you can use sub stores, you MUST call `migrate_to_sub_store()` at least once for each sub store, even if you have no data to migrate.** This initializes the sub store and marks it as ready for use.

```python
# Even with no data to migrate, you must activate each sub store:
memory.migrate_all_sub_stores(delete_source=False)
```

After activation:
- New memories with matching metadata will be automatically routed to the sub store
- Queries with matching filters will automatically route to the sub store
- Without this activation step, sub stores remain dormant and unused

## Step 1: Configure Sub Stores

First, let's create a Memory instance with sub stores:

```python
# sub_store_example.py
from powermem import Memory
import os

# Configure main storage and sub stores
config = {
    "database": {
        "provider": "oceanbase",
        "config": {
            "collection_name": "main_memories",
            "embedding_model_dims": 1536,
            "host": os.getenv("OCEANBASE_HOST", "127.0.0.1"),
            "port": int(os.getenv("OCEANBASE_PORT", "2881")),
            "user": os.getenv("OCEANBASE_USER", "root@test_tenant"),
            "password": os.getenv("OCEANBASE_PASSWORD", "password"),
            "db_name": os.getenv("OCEANBASE_DATABASE", "powermem"),
        }
    },
    "llm": {
        "provider": "qwen",
        "config": {
            "model": "qwen-max",
            "api_key": os.getenv("DASHSCOPE_API_KEY", "your-api-key"),
        }
    },
    "embedder": {
        "provider": "qwen",
        "config": {
            "model": "text-embedding-v4",
            "embedding_dims": 1536,
            "api_key": os.getenv("DASHSCOPE_API_KEY", "your-api-key"),
        }
    },
    # Configure sub stores
    "sub_stores": [
        {
            # Sub store 0: Working memory (short-term, low importance)
            "collection_name": "working_memories",
            "routing_filter": {
                "memory_type": "working"
            },
            "embedding_model_dims": 1536,
        },
        {
            # Sub store 1: Episodic memory (personal experiences)
            "collection_name": "episodic_memories",
            "routing_filter": {
                "memory_type": "episodic"
            },
            "embedding_model_dims": 1536,
        }
    ]
}

# Initialize Memory
memory = Memory(config=config)
print("✓ Memory initialized successfully with 2 sub stores")
print("  - Main store: main_memories (for semantic memories)")
print("  - Sub store 0: working_memories (for working memories)")
print("  - Sub store 1: episodic_memories (for episodic memories)")
```

**Run the code:**
```bash
python sub_store_example.py
```

**Expected output:**
```
✓ Memory initialized successfully with 2 sub stores
  - Main store: main_memories (for semantic memories)
  - Sub store 0: working_memories (for working memories)
  - Sub store 1: episodic_memories (for episodic memories)
```

## Step 2: Add Different Types of Memories

Let's add different types of memories to the main store:

```python
# sub_store_example.py
from powermem import Memory

memory = Memory(config=config)
user_id = "demo_user"

print("Adding different types of memories...\n")

# Add semantic memories (long-term knowledge, stays in main store)
semantic_memories = [
    {
        "messages": "Python is a high-level programming language known for its simplicity,I love Python!",
        "metadata": {"memory_type": "semantic", "topic": "programming"}
    },
    {
        "messages": "Machine learning is a subset of artificial intelligence,I love machine learning!",
        "metadata": {"memory_type": "semantic", "topic": "ai"}
    },
]

print("1. Adding semantic memories (knowledge)...")
for mem in semantic_memories:
    memory_id = memory.add(
        messages=mem["messages"],
        metadata=mem["metadata"],
        user_id=user_id
    )
    print(f"   ✓ Added: {mem['messages'][:40]}...")

# Add working memories (short-term tasks, will migrate to sub store 0)
working_memories = [
    {
        "messages": "Today's weather is sunny, good for outdoor activities",
        "metadata": {"memory_type": "working", "importance": "low"}
    },
    {
        "messages": "Need to buy groceries after work",
        "metadata": {"memory_type": "working", "importance": "low"}
    },
    {
        "messages": "Meeting scheduled at 3 PM today",
        "metadata": {"memory_type": "working", "importance": "medium"}
    },
]

print("\n2. Adding working memories (tasks)...")
for mem in working_memories:
    memory_id = memory.add(
        messages=mem["messages"],
        metadata=mem["metadata"],
        user_id=user_id
    )
    print(f"   ✓ Added: {mem['messages'][:40]}...")

# Add episodic memories (personal experiences, will migrate to sub store 1)
episodic_memories = [
    {
        "messages": "Last summer I visited Paris and saw the Eiffel Tower",
        "metadata": {"memory_type": "episodic", "time": "2024-07"}
    },
    {
        "messages": "I learned to ride a bike when I was 7 years old",
        "metadata": {"memory_type": "episodic", "time": "childhood"}
    },
]

print("\n3. Adding episodic memories (experiences)...")
for mem in episodic_memories:
    memory_id = memory.add(
        messages=mem["messages"],
        metadata=mem["metadata"],
        user_id=user_id
    )
    print(f"   ✓ Added: {mem['messages'][:40]}...")

total = len(semantic_memories) + len(working_memories) + len(episodic_memories)
print(f"\n✓ Total memories added: {total} (currently all in main store)")
```

**Expected output:**
```
Adding different types of memories...

1. Adding semantic memories (knowledge)...
   ✓ Added: Python is a high-level programming langu...
   ✓ Added: Machine learning is a subset of artifici...

2. Adding working memories (tasks)...
   ✓ Added: Today's weather is sunny, good for outdo...
   ✓ Added: Need to buy groceries after work...
   ✓ Added: Meeting scheduled at 3 PM today...

3. Adding episodic memories (experiences)...
   ✓ Added: Last summer I visited Paris and saw the ...
   ✓ Added: I learned to ride a bike when I was 7 ye...

✓ Total memories added: 7 (currently all in main store)
```

## Step 3: Query Before Migration

Before migration, all memories are in the main store:

```python
# sub_store_example.py
from powermem import Memory

memory = Memory(config=config)
user_id = "demo_user"

print("Querying before migration (all in main store)\n")

# Query working memories
print("1. Querying working memories...")
results = memory.search(
    query="today's tasks",
    filters={"memory_type": "working"},
    user_id=user_id,
    limit=5
)

results_list = results.get("results", [])
print(f"   Found {len(results_list)} results:")
for i, result in enumerate(results_list, 1):
    source = result.get('_source_store', 'main')
    print(f"   {i}. [{source}] {result['memory'][:50]}")

# Query episodic memories
print("\n2. Querying episodic memories...")
results = memory.search(
    query="past experiences",
    filters={"memory_type": "episodic"},
    user_id=user_id,
    limit=5
)

results_list = results.get("results", [])
print(f"   Found {len(results_list)} results:")
for i, result in enumerate(results_list, 1):
    source = result.get('_source_store', 'main')
    print(f"   {i}. [{source}] {result['memory'][:50]}")
```

**Expected output:**
```
Querying before migration (all in main store)

1. Querying working memories...
   Found 3 results:
   1. [main] Meeting scheduled at 3 PM today
   2. [main] Need to buy groceries after work
   3. [main] Today's weather is sunny, good for outdoor activit

2. Querying episodic memories...
   Found 2 results:
   1. [main] Last summer I visited Paris and saw the Eiffel Tow
   2. [main] I learned to ride a bike when I was 7 years old
```

## Step 4: Migrate Data to Sub Stores (REQUIRED)

Now let's migrate data to the respective sub stores. **This step is mandatory to activate sub stores:**

```python
# sub_store_example.py
from powermem import Memory

memory = Memory(config=config)

print("Starting data migration to sub stores...\n")

# Migrate working memories to sub store 0
# ⚠️ IMPORTANT: This call is REQUIRED even if you have no data to migrate!
# It activates the sub store and marks it as ready for use.
print("1. Migrating working memories to sub store 0...")
working_count = memory.migrate_to_sub_store(
    sub_store_index=0,
    delete_source=True  # Delete from main store after migration
)
print(f"   ✓ Migrated {working_count} working memories")
print(f"   ✓ Sub store 0 is now ACTIVE and ready for routing")

# Migrate episodic memories to sub store 1
print("\n2. Migrating episodic memories to sub store 1...")
episodic_count = memory.migrate_to_sub_store(
    sub_store_index=1,
    delete_source=True
)
print(f"   ✓ Migrated {episodic_count} episodic memories")
print(f"   ✓ Sub store 1 is now ACTIVE and ready for routing")

print("\n✓ Migration completed! Current distribution:")
print(f"   - Main store: semantic memories")
print(f"   - Sub store 0: {working_count} working memories (ACTIVE)")
print(f"   - Sub store 1: {episodic_count} episodic memories (ACTIVE)")
```

**Expected output:**
```
Starting data migration to sub stores...

1. Migrating working memories to sub store 0...
   ✓ Migrated 3 working memories
   ✓ Sub store 0 is now ACTIVE and ready for routing

2. Migrating episodic memories to sub store 1...
   ✓ Migrated 2 episodic memories
   ✓ Sub store 1 is now ACTIVE and ready for routing

✓ Migration completed! Current distribution:
   - Main store: semantic memories
   - Sub store 0: 3 working memories (ACTIVE)
   - Sub store 1: 2 episodic memories (ACTIVE)
```

### 💡 Important Note About Sub Store Activation

**Why is `migrate_to_sub_store()` required?**

1. **Initialization**: Sub stores are created during Memory initialization, but they start in a dormant state
2. **Activation**: Calling `migrate_to_sub_store()` marks the sub store as "ready" and enables routing
3. **No data required**: You can call it with `delete_source=False` even if there's nothing to migrate
4. **One-time operation**: Once activated, the sub store remains active for all future operations

**Without calling `migrate_to_sub_store()`:**
- Sub stores exist but are not used
- All new memories go to the main store
- Queries don't route to sub stores
- The routing filters are ignored

**After calling `migrate_to_sub_store()`:**
- Sub store is marked as active and ready
- New memories automatically route based on metadata
- Queries automatically route based on filters
- The sub store is fully functional

## Step 5: Query After Migration (Automatic Routing)

After migration, queries automatically route to the correct sub store:

```python
# sub_store_example.py
from powermem import Memory

memory = Memory(config=config)
user_id = "demo_user"

print("Querying after migration (automatic routing)\n")

# Query working memories (should route to sub store 0)
print("1. Querying working memories (routes to sub store 0)...")
results = memory.search(
    query="today's schedule",
    filters={"memory_type": "working"},
    user_id=user_id,
    limit=5
)

results_list = results.get("results", [])
print(f"   Found {len(results_list)} results:")
for i, result in enumerate(results_list, 1):
    source = result.get('_source_store', 'unknown')
    print(f"   {i}. [{source}] {result['memory'][:50]}")

# Query episodic memories (should route to sub store 1)
print("\n2. Querying episodic memories (routes to sub store 1)...")
results = memory.search(
    query="past memories",
    filters={"memory_type": "episodic"},
    user_id=user_id,
    limit=5
)

results_list = results.get("results", [])
print(f"   Found {len(results_list)} results:")
for i, result in enumerate(results_list, 1):
    source = result.get('_source_store', 'unknown')
    print(f"   {i}. [{source}] {result['memory'][:50]}")

# Query semantic memories (should query main store)
print("\n3. Querying semantic memories (queries main store)...")
results = memory.search(
    query="programming and AI",
    filters={"memory_type": "semantic"},
    user_id=user_id,
    limit=5
)

results_list = results.get("results", [])
print(f"   Found {len(results_list)} results:")
for i, result in enumerate(results_list, 1):
    source = result.get('_source_store', 'main')
    print(f"   {i}. [{source}] {result['memory'][:50]}")
```

**Expected output:**
```
Querying after migration (automatic routing)

1. Querying working memories (routes to sub store 0)...
   Found 3 results:
   1. [working_memories] Meeting scheduled at 3 PM today
   2. [working_memories] Need to buy groceries after work
   3. [working_memories] Today's weather is sunny, good for outdoor activit

2. Querying episodic memories (routes to sub store 1)...
   Found 2 results:
   1. [episodic_memories] Last summer I visited Paris and saw the Eiffel Tow
   2. [episodic_memories] I learned to ride a bike when I was 7 years old

3. Querying semantic memories (queries main store)...
   Found 2 results:
   1. [main] Python is a high-level programming language known
   2. [main] Machine learning is a subset of artificial intell
```

## Step 6: Add New Memories (Automatic Routing)

New memories are automatically routed to the correct sub store (because we activated them in Step 4):

```python
# sub_store_example.py
from powermem import Memory

memory = Memory(config=config)
user_id = "demo_user"

print("Adding new memories (testing automatic routing)\n")

# Add new working memory (should route to sub store 0)
print("1. Adding new working memory...")
new_working_id = memory.add(
    messages="Remember to call the dentist tomorrow morning",
    metadata={"memory_type": "working", "importance": "high"},
    user_id=user_id
)
print(f"   ✓ Automatically routed to sub store 0")

# Add new episodic memory (should route to sub store 1)
print("\n2. Adding new episodic memory...")
new_episodic_id = memory.add(
    messages="I graduated from university in 2020",
    metadata={"memory_type": "episodic", "time": "2020"},
    user_id=user_id
)
print(f"   ✓ Automatically routed to sub store 1")

# Add new semantic memory (should stay in main store)
print("\n3. Adding new semantic memory...")
new_semantic_id = memory.add(
    messages="Docker is a platform for developing and deploying containerized applications,I love Docker!",
    metadata={"memory_type": "semantic", "topic": "technology"},
    user_id=user_id
)
print(f"   ✓ Automatically routed to main store")

print("\n✓ All new memories correctly routed to their respective stores")
```

**Expected output:**
```
Adding new memories (testing automatic routing)

1. Adding new working memory...
   ✓ Automatically routed to sub store 0

2. Adding new episodic memory...
   ✓ Automatically routed to sub store 1

3. Adding new semantic memory...
   ✓ Automatically routed to main store

✓ All new memories correctly routed to their respective stores
```

## Step 7: Verify Routing Correctness

Let's verify that new memories were routed correctly:

```python
# sub_store_example.py
from powermem import Memory

memory = Memory(config=config)
user_id = "demo_user"

print("Verifying routing correctness\n")

# Verify working memories
print("1. Verifying working memory count...")
results = memory.search(
    query="tasks and reminders",
    filters={"memory_type": "working"},
    user_id=user_id,
    limit=10
)
results_list = results.get("results", [])
print(f"   Found {len(results_list)} working memories")
print(f"   Expected: 4 (3 old + 1 new)")
print(f"   {'✓ PASS' if len(results_list) == 4 else '✗ FAIL'}")

# Verify episodic memories
print("\n2. Verifying episodic memory count...")
results = memory.search(
    query="life experiences",
    filters={"memory_type": "episodic"},
    user_id=user_id,
    limit=10
)
results_list = results.get("results", [])
print(f"   Found {len(results_list)} episodic memories")
print(f"   Expected: 4 (3 old + 1 new)")
print(f"   {'✓ PASS' if len(results_list) == 4 else '✗ FAIL'}")

# Verify semantic memories
print("\n3. Verifying semantic memory count...")
results = memory.search(
    query="knowledge and concepts",
    filters={"memory_type": "semantic"},
    user_id=user_id,
    limit=10
)
results_list = results.get("results", [])
print(f"   Found {len(results_list)} semantic memories")
expected_count = 5  # Original + 1 new
print(f"   Expected: {expected_count}")
print(f"   {'✓ PASS' if len(results_list) == expected_count else '✗ FAIL'}")

print("\n✓ All verifications passed! Routing is working correctly")
```

**Expected output:**
```
Verifying routing correctness

1. Verifying working memory count...
   Found 4 working memories
   Expected: 4 (3 old + 1 new)
   ✓ PASS

2. Verifying episodic memory count...
   Found 4 episodic memories
   Expected: 4 (3 old + 1 new)
   ✓ PASS

3. Verifying semantic memory count...
   Found 5 semantic memories
   Expected: 5
   ✓ PASS

✓ All verifications passed! Routing is working correctly
```

## Complete Example

Here's a complete example combining all the steps:

```python
# complete_sub_store_example.py
from powermem import Memory
import os

def print_section(title: str):
    """Print a formatted section header"""
    print("\n" + "=" * 70)
    print(f"  {title}")
    print("=" * 70 + "\n")

def main():
    print_section("Sub Store Complete Example")
    
    # Step 1: Configuration
    print_section("Step 1: Configure Memory and Sub Stores")
    
    config = {
        "database": {
            "provider": "oceanbase",
            "config": {
                "collection_name": "demo_memories",
                "embedding_model_dims": 1536,
                "host": os.getenv("OCEANBASE_HOST", "127.0.0.1"),
                "port": int(os.getenv("OCEANBASE_PORT", "2881")),
                "user": os.getenv("OCEANBASE_USER", "root@test"),
                "password": os.getenv("OCEANBASE_PASSWORD", "password"),
                "db_name": os.getenv("OCEANBASE_DATABASE", "test_db"),
            }
        },
        "llm": {
            "provider": "qwen",
            "config": {
                "model": "qwen-max",
                "api_key": os.getenv("DASHSCOPE_API_KEY", "your-api-key"),
            }
        },
        "embedder": {
            "provider": "qwen",
            "config": {
                "model": "text-embedding-v4",
                "embedding_dims": 1536,
                "api_key": os.getenv("DASHSCOPE_API_KEY", "your-api-key"),
            }
        },
        "sub_stores": [
            {
                "collection_name": "working_memories",
                "routing_filter": {"memory_type": "working"},
                "embedding_model_dims": 1536,
            },
            {
                "collection_name": "episodic_memories",
                "routing_filter": {"memory_type": "episodic"},
                "embedding_model_dims": 1536,
            }
        ]
    }
    
    memory = Memory(config=config)
    user_id = "demo_user"
    print("✓ Memory initialized successfully")
    
    # Step 2: Add memories
    print_section("Step 2: Add Different Types of Memories")
    
    memories = {
        "semantic": [
            "Python is a high-level programming language,I love Python!",
            "Machine learning is a subset of AI",
        ],
        "working": [
            "Today's weather is sunny",
            "Buy groceries after work",
            "Meeting at 3 PM",
        ],
        "episodic": [
            "Visited Paris last year",
            "Learned to ride a bike at age 7",
        ]
    }
    
    for mem_type, mem_list in memories.items():
        print(f"\nAdding {mem_type} memories...")
        for mem in mem_list:
            memory.add(
                messages=mem,
                metadata={"memory_type": mem_type},
                user_id=user_id
            )
            print(f"  ✓ {mem}")
    
    # Step 3: Migrate (REQUIRED for activation)
    print_section("Step 3: Migrate to Sub Stores (REQUIRED)")
    
    print("⚠️ Important: This step is REQUIRED to activate sub stores!")
    print("Even if you have no data to migrate, you must call this once.\n")
    
    working_count = memory.migrate_to_sub_store(0, delete_source=True)
    episodic_count = memory.migrate_to_sub_store(1, delete_source=True)
    
    print(f"✓ Migration completed:")
    print(f"  - Working memories: {working_count} → sub store 0 (ACTIVE)")
    print(f"  - Episodic memories: {episodic_count} → sub store 1 (ACTIVE)")
    
    # Step 4: Query verification
    print_section("Step 4: Verify Automatic Routing")
    
    test_queries = [
        ("today tasks", "working", "working_memories"),
        ("past experiences", "episodic", "episodic_memories"),
        ("programming knowledge", "semantic", "main"),
    ]
    
    for query, mem_type, expected_store in test_queries:
        results = memory.search(
            query=query,
            filters={"memory_type": mem_type},
            user_id=user_id,
            limit=5
        )
        results_list = results.get("results", [])
        print(f"\nQuery '{query}' (type={mem_type}):")
        print(f"  Found {len(results_list)} results")
        if results_list:
            actual_store = results_list[0].get('_source_store', 'unknown')
            print(f"  Storage location: {actual_store}")
            print(f"  {'✓ Correct' if expected_store in actual_store or actual_store == expected_store else '✗ Wrong'}")
    
    print_section("✓ Example completed!")
    print("Sub store functionality successfully demonstrated:")
    print("  1. ✓ Configuration and initialization")
    print("  2. ✓ Adding different types of memories")
    print("  3. ✓ Data migration (REQUIRED for activation)")
    print("  4. ✓ Automatic query routing")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nExample interrupted by user")
    except Exception as e:
        print(f"\n\n✗ Error occurred: {e}")
        import traceback
        traceback.print_exc()
```

**Run the code:**
```bash
python complete_sub_store_example.py
```

## Advanced Usage

### Configuring Different Embedding Dimensions

Configure different embedding dimensions for different sub stores to optimize performance:

```python
config = {
    # ... main config ...
    "sub_stores": [
        {
            # Low-priority working memory uses smaller dimension
            "collection_name": "working_memories",
            "routing_filter": {"memory_type": "working"},
            "embedding_model_dims": 512,  # Smaller dimension
            "embedding": {
                "provider": "qwen",
                "config": {
                    "model": "text-embedding-v4",
                    "embedding_dims": 512,
                    "api_key": os.getenv("DASHSCOPE_API_KEY", "your-api-key"),
                }
            }
        },
        {
            # Important episodic memory uses larger dimension
            "collection_name": "episodic_memories",
            "routing_filter": {"memory_type": "episodic"},
            "embedding_model_dims": 1536,  # Larger dimension
        }
    ]
}
```

### Multi-Condition Routing

Use multiple metadata fields for routing:

```python
config = {
    # ... main config ...
    "sub_stores": [
        {
            "collection_name": "important_working_memories",
            "routing_filter": {
                "memory_type": "working",
                "importance": "high"  # Multiple conditions
            },
            "embedding_model_dims": 1536,
        }
    ]
}

# Add memory with multiple metadata fields
memory.add(
    messages="Important work task",
    metadata={
        "memory_type": "working",
        "importance": "high"  # Matches routing condition
    },
    user_id=user_id
)
```

### Migration Without Deleting Source Data

Migrate while keeping a copy in the source:

```python
# Migrate but don't delete original data (create copy)
count = memory.migrate_to_sub_store(
    sub_store_index=0,
    delete_source=False  # Keep original data
)
print(f"Copied {count} memories to sub store")
```

### Activating Sub Stores Without Data Migration

If you're starting fresh or want to use sub stores immediately:

```python
# Activate sub stores without migrating any data
# This is REQUIRED even if you have no existing data
for i in range(len(config["sub_stores"])):
    memory.migrate_to_sub_store(
        sub_store_index=i,
        delete_source=False  # No data to delete
    )
    print(f"✓ Sub store {i} activated and ready for use")

# Now you can add new memories and they'll route correctly
memory.add(
    messages="New working memory",
    metadata={"memory_type": "working"},
    user_id=user_id
)  # Will automatically go to sub store 0
```

## Best Practices

1. **Plan Storage Structure Wisely**
   - Design sub stores based on query patterns
   - Consider data access frequency and importance
   - Balance storage cost and query performance

2. **Choose Appropriate Embedding Dimensions**
   - Use larger dimensions for frequently queried data
   - Use smaller dimensions for temporary data
   - Test performance impact of different dimensions

3. **Design Clear Routing Rules**
   - Use explicit metadata fields
   - Avoid overlapping routing rules
   - Document your routing logic

4. **Always Activate Sub Stores**
   - Call `migrate_to_sub_store()` at least once per sub store
   - Do this even if you have no data to migrate
   - Without activation, sub stores won't be used

5. **Monitor and Optimize**
   - Monitor query performance across sub stores
   - Regularly clean up expired data
   - Adjust configuration based on usage patterns

## Troubleshooting

### Issue: Memories Not Routing to Correct Sub Store

**Solution:**
1. Verify you've called `migrate_to_sub_store()` to activate the sub store
2. Check metadata fields match the `routing_filter`
3. Confirm sub store configuration is correct
4. Review logs to understand routing decisions

### Issue: Cannot Find Data After Migration

**Solution:**
1. Ensure you're using correct `filters` in queries
2. Verify migration completed successfully
3. Check that sub stores were properly initialized

### Issue: Performance Not Improved

**Solution:**
1. Review data distribution across stores
2. Consider adjusting embedding dimensions
3. Optimize query conditions and indexes

### Issue: Sub Store Not Being Used

**Solution:**
1. **Most Common**: You forgot to call `migrate_to_sub_store()` to activate it
2. Check routing filters match your metadata exactly
3. Verify queries include the correct filter conditions

## Summary

In this scenario, we learned:
- ✓ How to configure and initialize sub stores
- ✓ Adding different types of memories
- ✓ **REQUIRED**: Calling `migrate_to_sub_store()` to activate sub stores
- ✓ Migrating data to sub stores
- ✓ Automatic query routing
- ✓ Automatic routing for new memories
- ✓ Verifying routing correctness
- ✓ Advanced configuration and best practices

### Key Takeaway: Sub Store Activation

**Remember**: Sub stores must be explicitly activated by calling `migrate_to_sub_store()` at least once, even if you have no data to migrate. Without this activation step, sub stores remain dormant and unused, and all operations will continue using only the main store.

Sub stores are a powerful feature that helps you:
- Optimize storage costs
- Improve query performance
- Better organize data
- Implement flexible data management strategies
