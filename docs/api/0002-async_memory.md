# AsyncMemory API Reference

`AsyncMemory` provides asynchronous memory operations for high-performance and concurrent scenarios.

## Class: `AsyncMemory`

```python
from powermem import AsyncMemory

async_memory = AsyncMemory(config=config)
await async_memory.initialize()
```

### Constructor

#### `AsyncMemory(config=None, storage_type=None, llm_provider=None, embedding_provider=None)`

Create a new AsyncMemory instance.

**Parameters:**
- `config` (dict, optional): Configuration dictionary.
- `storage_type` (str, optional): Storage backend type (deprecated).
- `llm_provider` (str, optional): LLM provider name (deprecated).
- `embedding_provider` (str, optional): Embedding provider name (deprecated).

**Note:** You must call `await async_memory.initialize()` after creation.

### Initialization

#### `initialize()`

Initialize async memory components. Must be called after creation.

**Example:**
```python
async_memory = AsyncMemory(config=config)
await async_memory.initialize()
```

### Async Methods

All async methods follow the same interface as `Memory` but are async.

#### `async add(messages, user_id=None, agent_id=None, run_id=None, metadata=None, filters=None, infer=True)`

Add a memory asynchronously.

**Parameters:**
- `messages` (str | dict | list[dict]): Memory content. Can be:
  - A string (will be converted to a message)
  - A single message dict with `role` and `content`
  - A list of message dicts in OpenAI format
- `user_id` (str, optional): User identifier.
- `agent_id` (str, optional): Agent identifier.
- `run_id` (str, optional): Run/conversation identifier.
- `metadata` (dict, optional): Additional metadata.
- `filters` (dict, optional): Filter metadata for advanced filtering.
- `infer` (bool): Enable intelligent memory processing (default: True).

**Returns:**
- `dict`: Result containing added memory information.

**Example:**
```python
result = await async_memory.add(
    messages="User likes async programming",
    user_id="user123"
)
```

#### `async search(query, user_id=None, agent_id=None, run_id=None, filters=None, limit=30, threshold=None)`

Search memories asynchronously.

**Parameters:**
- `query` (str): Search query string.
- `user_id` (str, optional): Filter by user ID.
- `agent_id` (str, optional): Filter by agent ID.
- `run_id` (str, optional): Filter by run ID.
- `filters` (dict, optional): Metadata filters for advanced filtering.
- `limit` (int): Maximum number of results (default: 30).
- `threshold` (float, optional): Similarity threshold (0.0-1.0) for filtering results.

**Returns:**
- `dict`: Search results with memories and scores.

**Example:**
```python
results = await async_memory.search(
    query="user preferences",
    user_id="user123",
    limit=5,
    threshold=0.7
)
```

#### `async get(memory_id, user_id=None, agent_id=None)`

Retrieve a memory asynchronously.

**Parameters:**
- `memory_id` (int): Memory identifier.
- `user_id` (str, optional): User identifier for permission check.
- `agent_id` (str, optional): Agent identifier for permission check.

**Returns:**
- `dict | None`: Memory data or None if not found.

**Example:**
```python
memory_data = await async_memory.get(123, user_id="user123")
```

#### `async update(memory_id, content=None, user_id=None, agent_id=None, metadata=None)`

Update a memory asynchronously.

**Parameters:**
- `memory_id` (int): Memory identifier.
- `content` (str, optional): New memory content.
- `user_id` (str, optional): User identifier for permission check.
- `agent_id` (str, optional): Agent identifier for permission check.
- `metadata` (dict, optional): Updated metadata.

**Returns:**
- `dict`: Updated memory data.

**Example:**
```python
updated = await async_memory.update(
    memory_id=123,
    content="Updated content",
    user_id="user123"
)
```

#### `async delete(memory_id, user_id=None, agent_id=None)`

Delete a memory asynchronously.

**Parameters:**
- `memory_id` (int): Memory identifier.
- `user_id` (str, optional): User identifier for permission check.
- `agent_id` (str, optional): Agent identifier for permission check.

**Returns:**
- `bool`: True if deleted, False otherwise.

**Example:**
```python
success = await async_memory.delete(123, user_id="user123")
```

#### `async delete_all(user_id=None, agent_id=None, run_id=None)`

Delete all matching memories asynchronously.

**Example:**
```python
result = await async_memory.delete_all(user_id="user123")
```

#### `async get_all(user_id=None, agent_id=None, run_id=None, limit=100, offset=0, filters=None)`

Retrieve all matching memories asynchronously.

**Parameters:**
- `user_id` (str, optional): Filter by user ID.
- `agent_id` (str, optional): Filter by agent ID.
- `run_id` (str, optional): Filter by run ID.
- `limit` (int): Maximum number of results (default: 100).
- `offset` (int): Offset for pagination (default: 0).
- `filters` (dict, optional): Metadata filters for advanced filtering.

**Returns:**
- `dict`: All matching memories.

**Example:**
```python
all_memories = await async_memory.get_all(user_id="user123", limit=50, offset=0)
```

### Usage Examples

#### Basic Async Usage

```python
import asyncio
from powermem import AsyncMemory, auto_config

async def main():
    config = auto_config()
    async_memory = AsyncMemory(config=config)
    await async_memory.initialize()
    
            # Add memory
            await async_memory.add(
                messages="User prefers async operations",
                user_id="user123"
            )
            
            # Search
            results = await async_memory.search(
                query="user preferences",
                user_id="user123"
            )
    
    print(f"Found {len(results.get('results', []))} memories")

asyncio.run(main())
```

#### Concurrent Operations

```python
import asyncio
from powermem import AsyncMemory, auto_config

async def add_multiple_memories():
    config = auto_config()
    async_memory = AsyncMemory(config=config)
    await async_memory.initialize()
    
    # Add multiple memories concurrently
    tasks = [
        async_memory.add(messages=f"Memory {i}", user_id="user123")
        for i in range(10)
    ]
    
    results = await asyncio.gather(*tasks)
    print(f"Added {len(results)} memories")

asyncio.run(add_multiple_memories())
```

#### Batch Processing

```python
import asyncio
from powermem import AsyncMemory, auto_config

async def batch_process():
    config = auto_config()
    async_memory = AsyncMemory(config=config)
    await async_memory.initialize()
    
    messages_list = [
        [{"role": "user", "content": f"Message {i}"}]
        for i in range(100)
    ]
    
    # Process in batches
    batch_size = 10
    for i in range(0, len(messages_list), batch_size):
        batch = messages_list[i:i+batch_size]
        tasks = [
            async_memory.add(messages=msg, user_id="user123", infer=True)
            for msg in batch
        ]
        await asyncio.gather(*tasks)
        print(f"Processed batch {i//batch_size + 1}")

asyncio.run(batch_process())
```

### Limitation: Embedded SeekDB Does Not Support Async

Embedded SeekDB (local file mode with no `host` configured) uses a single-threaded C++ engine that **does not support concurrent multi-threaded access**. `AsyncMemory` internally submits synchronous operations to a `ThreadPoolExecutor`, which causes multiple threads to read and write the same embedded SeekDB instance simultaneously. This leads to C++-level crashes such as `pure virtual method called` or `Segmentation fault`.

**`AsyncMemory` cannot be used with embedded SeekDB.** Use the synchronous `Memory` class instead.

```python
# ❌ Not supported with embedded SeekDB
from powermem import AsyncMemory
async_memory = AsyncMemory(config=embedded_seekdb_config)  # crashes

# ✓ Use the synchronous interface with embedded SeekDB
from powermem import Memory
memory = Memory(config=embedded_seekdb_config)
```

Remote OceanBase (with `host` configured) is not affected by this limitation and fully supports `AsyncMemory`.

### When to Use AsyncMemory

Use `AsyncMemory` when:
- Processing many memories concurrently
- Building async web applications (FastAPI, aiohttp)
- Implementing batch processing pipelines
- Need non-blocking memory operations
- Using **remote OceanBase** (with `host` configured)

Use `Memory` when:
- Simple synchronous scripts
- Interactive notebooks
- Simple use cases without concurrency needs
- Using **embedded SeekDB** (local file mode, no `host`)

