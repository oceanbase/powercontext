## Prerequisites

- Python 3.10+
- powermem installed (`pip install powermem`)

## Configuration

Before using powermem, you need to configure it. Powermem can automatically load configuration from a `.env` file in your project directory. This is the recommended way to configure powermem for your use case.

### Why Use a `.env` File?

Using a `.env` file allows you to:
- Keep configuration separate from your code
- Easily switch between different environments (dev, staging, prod)
- Protect sensitive credentials (API keys, database passwords)
- Share configuration templates without exposing secrets

### Creating a `.env` File

1. Copy the example configuration file:
   ```bash
   cp .env.example .env
   ```

2. Edit the `.env` file and configure your settings:
   - **LLM Provider**: Choose your language model provider (Qwen, OpenAI, Anthropic, etc.)
   - **Embedding Provider**: Select how text will be converted to vectors
   - **Vector Store**: Choose your database (SQLite for development, OceanBase for production)

> **Note:** When you call `auto_config()`, powermem will automatically:
> - Look for a `.env` file in the current directory
> - Load configuration from environment variables
> - Use sensible defaults if no configuration is found

For more configuration options, see the full example in `.env.example` or refer to the [Configuration Guide](docs/guides/0003-configuration.md).

## Initializing Memory

The first step in using powermem is to create a memory instance. This instance will handle all memory operations for your application.

### Understanding `Memory` and `auto_config()`

The `Memory` class is the core memory management class. To initialize it:
- Use `auto_config()` to automatically load configuration from your `.env` file
- Pass the config to `Memory` to create an instance with the appropriate settings
- The `Memory` class handles initialization of vector stores and embeddings

Let's create a simple Python script and initialize powermem:

```python
from powermem import Memory, auto_config

# Load configuration (auto-loads from .env or uses defaults)
config = auto_config()

# Create memory instance
memory = Memory(config=config)

print("✓ Memory initialized successfully!")
```

### Using JSON/Dictionary Configuration

Instead of using `.env` files, you can also pass configuration directly as a Python dictionary (JSON-like format). This is useful when:
- You want to load configuration from a JSON file
- You need to programmatically generate configuration
- You're embedding configuration in your application code

Here's an example using a dictionary configuration:

```python
from powermem import Memory

# Define configuration as a dictionary (JSON-like format)
config = {
    'llm': {
        'provider': 'qwen',
        'config': {
            'api_key': 'your_api_key_here',
            'model': 'qwen-plus',
            'temperature': 0.7
        }
    },
    'embedder': {
        'provider': 'qwen',
        'config': {
            'api_key': 'your_api_key_here',
            'model': 'text-embedding-v4'
        }
    },
    'vector_store': {
        'provider': 'oceanbase',
        'config': {
            'collection_name': 'memories',
            'connection_args': {
                'host': '127.0.0.1',
                'port': 2881,
                'user': 'root@sys',
                'password': 'password',
                'db_name': 'powermem'
            },
            'embedding_model_dims': 1536,
            'index_type': 'IVF_FLAT',
            'vidx_metric_type': 'cosine'
        }
    }
}

# Create memory instance with dictionary config
memory = Memory(config=config)

print("✓ Memory initialized with JSON config!")
```

## Add Your First Memory

Now that you have initialized powermem, let's add your first memory. Adding a memory stores information that can later be retrieved using semantic search.

### Understanding the `add()` Method

The `add()` method:
- Takes a text message describing the memory
- Associates it with a `user_id` to keep memories isolated per user
- Converts the text to a vector embedding for semantic search
- Stores it in your configured vector database
- Returns a result containing the memory ID and other metadata

### Important Parameters

- **`messages`**: The text content you want to store as a memory
- **`user_id`**: A unique identifier for the user (required for multi-user isolation)
- **`infer`**: Whether to use intelligent deduplication (default: `True`)

Let's add a simple memory:

```python
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

## Add Multiple Memories

In real applications, you'll often need to add multiple memories for a user. This is useful when:
- Importing historical data
- Processing batch conversations
- Initializing user profiles with known information

### Best Practices for Adding Multiple Memories

- **Use consistent user_id**: Always use the same `user_id` for the same user
- **Process sequentially**: For small batches, sequential processing is fine
- **Consider batch operations**: For large datasets, consider using async operations (see [Async Guide](docs/guides/0002-getting_started_async.md))
- **Handle errors**: Wrap operations in try-except blocks for production code

Let's add several memories for a user:

```python
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

## Search Memories

One of powermem's most powerful features is semantic search. Unlike traditional keyword search, semantic search finds memories based on meaning and context, not just exact word matches.

### How Semantic Search Works

1. **Query Embedding**: Your search query is converted to a vector embedding
2. **Similarity Calculation**: Powermem compares your query vector with all stored memory vectors
3. **Ranking**: Results are ranked by semantic similarity (cosine similarity)
4. **Filtering**: Only memories for the specified `user_id` are considered

### Understanding Search Parameters

- **`query`**: The text you're searching for (can be natural language)
- **`user_id`**: Limits search to memories for this specific user
- **`limit`**: Maximum number of results to return (default: 10)
- **`filters`**: Optional metadata filters (see next section)

### Why Semantic Search is Powerful

Semantic search allows you to find relevant memories even when:
- The query uses different words than the stored memory
- The query is phrased differently
- You're looking for conceptually similar information

Let's search for memories:

```python
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

## Add Metadata

Metadata is additional information you can attach to memories to help organize, filter, and categorize them. This is especially useful in production applications where you need to:

- **Categorize memories**: Group related memories together
- **Track sources**: Know where a memory came from (conversation, form, API, etc.)
- **Set importance levels**: Prioritize certain memories
- **Add timestamps**: Track when memories were created or updated
- **Store custom attributes**: Any additional information relevant to your use case

### Benefits of Using Metadata

- **Better organization**: Structure your memories logically
- **Efficient filtering**: Quickly find memories matching specific criteria
- **Rich context**: Store additional information without cluttering the memory text
- **Analytics**: Track patterns and usage statistics

### Metadata Best Practices

- **Use consistent keys**: Define a schema for your metadata keys
- **Keep it simple**: Don't overcomplicate metadata structures
- **Use meaningful values**: Make metadata values searchable and meaningful
- **Consider indexing**: Some vector stores support indexing on metadata fields

Let's add memories with metadata for better organization:

```python
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

> **Tip:** Metadata is stored alongside the memory and can be retrieved with search results. You can also use metadata for filtering searches, as shown in the next section.

## Search with Metadata Filters

Metadata filters allow you to narrow down search results based on metadata values. This is extremely useful when you have many memories and need to find specific subsets.

### How Metadata Filtering Works

When you provide a `filters` parameter to `search()`:
1. Powermem first performs semantic search to find relevant memories
2. Then it filters results to only include memories matching your metadata criteria
3. Results are still ranked by semantic similarity

### Filter Syntax

Filters use a dictionary where:
- **Keys** are metadata field names
- **Values** are the exact values to match

### Use Cases for Metadata Filters

- **Category filtering**: Find all "preference" memories
- **Date ranges**: Find memories from a specific time period
- **Source filtering**: Find memories from a specific source
- **Importance filtering**: Find only high-importance memories
- **Combined filters**: Use multiple filters together

Let's search memories using metadata filters:

```python
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

> **Note:** Metadata filters work in combination with semantic search. The results will be both semantically relevant to your query AND match your metadata criteria. This gives you precise control over what memories are returned.

## Get All Memories

Sometimes you need to retrieve all memories for a user without performing a search. The `get_all()` method returns all memories associated with a specific `user_id`.

### When to Use `get_all()`

- **User profile display**: Show all stored information about a user
- **Data export**: Export all memories for backup or migration
- **Debugging**: Inspect all memories to understand what's stored
- **Analytics**: Analyze all memories for patterns or statistics

### Important Considerations

- **Performance**: For users with many memories, `get_all()` can be slow
- **Memory usage**: Large result sets consume more memory
- **Pagination**: Consider implementing pagination for production use
- **Use search when possible**: If you need specific memories, use `search()` instead

### Return Format

The `get_all()` method returns the same format as `search()`, with a `results` list containing all memories for the user.

Let's retrieve all memories for a user:

```python
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

> **Tip:** For production applications with many memories per user, consider using `search()` with a broad query or implementing pagination to avoid loading all memories at once.

## Update a Memory

Over time, you may need to update existing memories when information changes or becomes more detailed. The `update()` method allows you to modify the content of a stored memory.

### When to Update Memories

- **Information changes**: User preferences or details have changed
- **Adding details**: Expanding a memory with more information
- **Corrections**: Fixing incorrect or outdated information
- **Refinement**: Making memories more specific or accurate

### Understanding Memory Updates

When you update a memory:
1. The memory content is replaced with the new content
2. A new vector embedding is generated for the updated content
3. The memory ID remains the same (useful for tracking)
4. Metadata can optionally be updated as well

### Getting the Memory ID

To update a memory, you need its ID. You can get the ID from:
- The result of `add()` when you first create the memory
- The result of `search()` or `get_all()` operations
- Storing IDs in your application's database

Let's update an existing memory:

```python
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

> **Note:** After updating, the memory's vector embedding is regenerated, so it will be found by semantic search using the new content. The old content is completely replaced.

## Delete a Memory

Sometimes you need to remove a memory that is no longer relevant or accurate. The `delete()` method permanently removes a memory from storage.

### When to Delete Memories

- **Outdated information**: Information that is no longer true
- **User requests**: User explicitly asks to remove a memory
- **Privacy compliance**: Removing data for GDPR or other privacy requirements
- **Data cleanup**: Removing test or temporary memories

### Understanding Deletion

- **Permanent**: Deletion is permanent and cannot be undone
- **Requires memory ID**: You must know the memory ID to delete it
- **Returns boolean**: The method returns `True` on success, `False` on failure
- **No cascading**: Deleting a memory doesn't affect other memories

### Best Practices

- **Confirm before deleting**: In production, consider asking for confirmation
- **Log deletions**: Keep audit logs of what was deleted and when
- **Handle errors**: Check the return value and handle failures appropriately
- **Backup important data**: Consider backing up before bulk deletions

Let's delete a memory:

```python
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

> **Warning:** Deletion is permanent. Make sure you really want to delete the memory before calling `delete()`. Consider implementing a soft-delete mechanism in production if you need to recover deleted memories.

## Delete All Memories

The `delete_all()` method removes all memories for a specific user. This is useful for:
- **Account deletion**: Removing all data when a user deletes their account
- **Data reset**: Clearing all memories for testing or resetting user state
- **Privacy compliance**: Complete data removal for privacy regulations

### Important Considerations

- **Irreversible**: All memories for the user are permanently deleted
- **User-specific**: Only affects memories for the specified `user_id`
- **No confirmation**: The method executes immediately without confirmation
- **Returns boolean**: Returns `True` on success, `False` on failure

### When to Use `delete_all()`

- **User account deletion**: When a user requests complete data removal
- **Testing**: Clearing test data between test runs
- **Data migration**: Removing old data before importing new data
- **Privacy compliance**: Meeting GDPR "right to be forgotten" requirements

### Production Best Practices

- **Require confirmation**: Always require explicit user confirmation
- **Log the action**: Record who deleted what and when
- **Backup first**: Consider backing up data before bulk deletion
- **Verify user identity**: Ensure the requester has permission to delete

Let's delete all memories for a user:

```python
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

> **Warning:** `delete_all()` permanently removes all memories for the specified user. This action cannot be undone. Use with extreme caution in production environments. Always implement proper authorization checks and user confirmation before allowing this operation.