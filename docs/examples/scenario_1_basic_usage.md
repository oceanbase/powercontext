# Scenario 1: Basic Usage

This scenario guides you through the basics of powermem - storing, retrieving, and managing memories.

## Prerequisites

- Python 3.10+
- powermem installed (`pip install powermem`)

## Configuration

Powermem can automatically load configuration from a `.env` file in your project directory. This is the recommended way to configure powermem for your use case.

### Creating a `.env` File

1. Copy the example configuration file:
   ```bash
   cp .env.example .env
   ```

2. Edit the `.env` file and configure
   ```

> **Note:** When you call `auto_config()`, powermem will automatically:
> - Look for a `.env` file in the current directory
> - Load configuration from environment variables

For more configuration options, see the full example in `.env.example` or refer to the [Configuration Guide](docs/guides/0003-configuration.md).

## Step 1: Setup

First, let's create a simple Python script and import powermem:

```python
# basic_usage_example.py
from powermem import Memory, auto_config

# Load configuration (auto-loads from .env or uses defaults)
config = auto_config()

# Create memory instance
memory = Memory(config=config)

print("✓ Memory initialized successfully!")
```

**Run this code:**
```bash
python basic_usage_example.py
```

**Expected output:**
```
✓ Memory initialized successfully!
```

## Step 2: Add Your First Memory

Now let's add a simple memory:

```python
# basic_usage_example.py
from powermem import Memory, auto_config

config = auto_config()
memory = Memory(config=config)

# Add a memory
result = memory.add(
    messages="User likes Python programming",
    user_id="user123"
)

# Get memory ID from result
results_list = result.get('results', [])
memory_id = results_list[0].get('id', 'N/A') if results_list else 'N/A'
print(f"✓ Memory added! ID: {memory_id}")
```

**Run this code:**
```bash
python basic_usage_example.py
```

**Expected output:**
```
✓ Memory added! ID: xxxxxx
```

## Step 3: Add Multiple Memories

Let's add several memories for a user:

```python
# basic_usage_example.py
from powermem import Memory, auto_config

config = auto_config()
memory = Memory(config=config)
user_id = "user123"

# Add multiple memories
memories = [
    "User likes Python programming",
    "User prefers email support over phone calls",
    "User works as a software engineer",
    "User favorite color is blue"
]

for mem in memories:
    result = memory.add(messages=mem, user_id=user_id)
    print(f"✓ Added: {mem}")

print(f"\n✓ All memories added for user {user_id}")
```

**Run this code:**
```bash
python basic_usage_example.py
```

**Expected output:**
```
✓ Added: User likes Python programming
✓ Added: User prefers email support over phone calls
✓ Added: User works as a software engineer
✓ Added: User favorite color is blue

✓ All memories added for user user123
```

## Step 4: Search Memories

Now let's search for memories:

```python
# basic_usage_example.py
from powermem import Memory, auto_config

config = auto_config()
memory = Memory(config=config)
user_id = "user123"

# Add some memories first
memory.add("User likes Python programming", user_id=user_id)
memory.add("User prefers email support", user_id=user_id)
memory.add("User works as a software engineer", user_id=user_id)

# Search for memories
print("Searching for 'user preferences'...")
results = memory.search(
    query="user preferences",
    user_id=user_id,
    limit=5
)

print(f"\nFound {len(results.get('results', []))} memories:")
for i, result in enumerate(results.get('results', []), 1):
    print(f"  {i}. {result['memory']}")
```

**Run this code:**
```bash
python basic_usage_example.py
```

**Expected output:**
```
Searching for 'user preferences'...

Found 3 memories:
  1. Prefers email support
  2. Likes Python programming
  3. Works as a software engineer
```

## Step 5: Add Metadata

Let's add memories with metadata for better organization:

```python
# basic_usage_example.py
from powermem import Memory, auto_config

config = auto_config()
memory = Memory(config=config)
user_id = "user123"

# Add memories with metadata
memory.add(
    messages="User likes Python programming",
    user_id=user_id,
    metadata={
        "category": "preference",
        "importance": "high",
        "source": "conversation"
    }
)

memory.add(
    messages="User prefers email support",
    user_id=user_id,
    metadata={
        "category": "communication",
        "importance": "medium"
    }
)

print("✓ Memories added with metadata")
```

## Step 6: Search with Metadata Filters

Search memories using metadata filters:

```python
# basic_usage_example.py
from powermem import Memory, auto_config

config = auto_config()
memory = Memory(config=config)
user_id = "user123"

# Add memories with metadata
memory.add(
    messages="User likes Python programming",
    user_id=user_id,
    metadata={"category": "preference"}
)

memory.add(
    messages="User prefers email support",
    user_id=user_id,
    metadata={"category": "communication"}
)

# Search with metadata filter
# Note: category is extracted from metadata and stored as a top-level field
print("Searching with metadata filter...")
results = memory.search(
    query="user preferences",
    user_id=user_id,
    filters={"category": "preference"}
)

print(f"\nFound {len(results.get('results', []))} memories:")
for result in results.get('results', []):
    print(f"  - {result['memory']}")
    print(f"    Metadata: {result.get('metadata', {})}")
```

**Run this code:**
```bash
python basic_usage_example.py
```

**Expected output:**
```
Searching with metadata filter...

Found 1 memories:
  - Likes Python programming
    Metadata: {'last_searched_at': datetime.datetime(2025, 11, 6, 13, 9, 32, 250703), 'search_count': 4, 'category': 'preference', 'fulltext_content': 'Likes Python programming', 'access_count': 1, 'search_relevance_score': 0.25}
```

## Step 7: Get All Memories

Retrieve all memories for a user:

```python
# basic_usage_example.py
from powermem import Memory, auto_config

config = auto_config()
memory = Memory(config=config)
user_id = "user123"

# Add some memories
memory.add("User likes Python", user_id=user_id)
memory.add("User prefers email", user_id=user_id)
memory.add("User works as engineer", user_id=user_id)

# Get all memories
all_memories = memory.get_all(user_id=user_id)

print(f"\nTotal memories for {user_id}: {len(all_memories.get('results', []))}")
print("\nAll memories:")
for i, mem in enumerate(all_memories.get('results', []), 1):
    print(f"  {i}. {mem['memory']}")
```

**Run this code:**
```bash
python basic_usage_example.py
```

**Expected output:**
```
Total memories for user123: 3

All memories:
  1. Likes Python programming
  2. Prefers email support
  3. Works as engineer
```

## Step 8: Update a Memory

Update an existing memory:

```python
# basic_usage_example.py
from powermem import Memory, auto_config

config = auto_config()
memory = Memory(config=config)
user_id = "user123"

# Add a memory (using infer=False for predictable behavior)
result = memory.add(
    messages="User likes Python programming",
    user_id=user_id,
    infer=False  # Disable intelligent mode for predictable behavior
)

# Get memory ID from result
results_list = result.get('results', [])
if not results_list:
    raise ValueError("No memory was added. Check the result: " + str(result))
memory_id = results_list[0].get('id')
if not memory_id:
    raise ValueError("Memory ID not found in result")

# Update the memory
updated = memory.update(
    memory_id=memory_id,
    content="User loves Python programming, especially for data science"
)

print(f"✓ Memory updated!")
print(f"  Old: User likes Python programming")
print(f"  New: {updated.get('data', 'N/A')}")
```

**Run this code:**
```bash
python basic_usage_example.py
```

**Expected output:**
```
✓ Memory updated!
  Old: User likes Python programming
  New: User loves Python programming, especially for data science
```

## Step 9: Delete a Memory

Delete a memory:

```python
# basic_usage_example.py
from powermem import Memory, auto_config

config = auto_config()
memory = Memory(config=config)
user_id = "user123"

# Add a memory (using infer=False for predictable behavior)
result = memory.add(
    messages="User likes Python programming",
    user_id=user_id,
    infer=False  # Disable intelligent mode for predictable behavior
)

# Get memory ID from result
results_list = result.get('results', [])
if not results_list:
    raise ValueError("No memory was added. Check the result: " + str(result))
memory_id = results_list[0].get('id')
if not memory_id:
    raise ValueError("Memory ID not found in result")

# Delete the memory
success = memory.delete(memory_id)

if success:
    print(f"✓ Memory {memory_id} deleted successfully!")
else:
    print(f"✗ Failed to delete memory")
```

**Run this code:**
```bash
python basic_usage_example.py
```

**Expected output:**
```
✓ Memory xxx deleted successfully!
```

## Step 10: Delete All Memories

Delete all memories for a user:

```python
# basic_usage_example.py
from powermem import Memory, auto_config

config = auto_config()
memory = Memory(config=config)
user_id = "user123"

# Add some memories
memory.add("Memory 1", user_id=user_id)
memory.add("Memory 2", user_id=user_id)
memory.add("Memory 3", user_id=user_id)

# Get count before deletion
all_memories = memory.get_all(user_id=user_id)
count_before = len(all_memories.get('results', []))

# Delete all memories (returns True/False)
success = memory.delete_all(user_id=user_id)

if success:
    print(f"✓ Deleted {count_before} memories for {user_id}")
else:
    print(f"✗ Failed to delete memories")
```

**Run this code:**
```bash
python basic_usage_example.py
```

**Expected output:**
```
✓ Deleted x memories for user123
```

## Complete Example

Here's a complete example combining all the steps:

```python
# complete_basic_example.py
from powermem import Memory, auto_config

def main():
    # Load configuration
    config = auto_config()
    
    # Initialize memory
    memory = Memory(config=config)
    user_id = "demo_user"
    
    print("=" * 60)
    print("Powermem Basic Usage Example")
    print("=" * 60)
    
    # Step 1: Add memories
    print("\n1. Adding memories...")
    memories = [
        "User likes Python programming",
        "User prefers email support",
        "User works as a software engineer",
        "User favorite color is blue"
    ]
    
    for mem in memories:
        memory.add(messages=mem, user_id=user_id, metadata={"source": "demo"})
        print(f"   ✓ Added: {mem}")
    
    # Step 2: Search memories
    print("\n2. Searching memories...")
    results = memory.search(
        query="user preferences",
        user_id=user_id,
        limit=5
    )
    
    print(f"   Found {len(results.get('results', []))} memories:")
    for result in results.get('results', []):
        print(f"     - {result['memory']}")
    
    # Step 3: Get all memories
    print("\n3. Getting all memories...")
    all_memories = memory.get_all(user_id=user_id)
    print(f"   Total: {len(all_memories.get('results', []))} memories")
    
    # Step 4: Cleanup
    print("\n4. Cleaning up...")
    # Get count before deletion
    all_memories_before = memory.get_all(user_id=user_id)
    count_before = len(all_memories_before.get('results', []))
    
    # Delete all memories (returns True/False)
    delete_success = memory.delete_all(user_id=user_id)
    
    if delete_success:
        print(f"   ✓ Deleted {count_before} memories")
    else:
        print("   ✗ Failed to delete memories")
    
    print("\n" + "=" * 60)
    print("Example completed successfully!")
    print("=" * 60)

if __name__ == "__main__":
    main()
```

**Run this code:**
```bash
python complete_basic_example.py
```

## Extension Exercises

### Exercise 1: Multiple Users

Try managing memories for multiple users:

```python
from powermem import Memory, auto_config

config = auto_config()
memory = Memory(config=config)

# Add memories for different users
memory.add("User 1 likes Python", user_id="user1")
memory.add("User 2 likes Java", user_id="user2")

# Search for each user
results1 = memory.search("preferences", user_id="user1")
results2 = memory.search("preferences", user_id="user2")

memory.add(
    messages="User preference",
    user_id="user123",
    metadata={
        "category": "preference",
        "importance": "high",
        "source": "conversation",
        "timestamp": "2024-01-01",
        "tags": ["python", "programming"]
    }
)

# Search by category
results = memory.search(
    query="programming languages",
    user_id="user123"
)

print(results)

# Search with different limits
results = memory.search(
    query="user information",
    user_id="user123",
    limit=10
)

print(results)
```
