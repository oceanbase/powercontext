
## Prerequisites

- Python 3.10+
- powermem installed (`pip install powermem`)
- Basic understanding of Python async/await syntax

## When to Use Async Memory?

The async version of powermem (`AsyncMemory`) is ideal when you need:

- **High-throughput applications**: Processing many memory operations simultaneously
- **Concurrent operations**: Adding, searching, or updating multiple memories at once
- **I/O-bound workloads**: When operations involve network calls (API requests, database queries)
- **Non-blocking operations**: Keeping your application responsive while processing memories

### Async vs Sync: Which Should You Use?

- **Use `AsyncMemory` (async)** for:
  - High-concurrency scenarios
  - Batch processing large datasets
  - When you need maximum throughput

> **Note:** If you're new to powermem, start with the [synchronous guide](docs/guides/0001-getting_started.md) to learn the basics, then come back here for async operations.

## Configuration

Before using async memory, you need to configure powermem. The configuration is the same for both sync and async versions. Powermem can automatically load configuration from a `.env` file in your project directory.

### Creating a `.env` File

1. Copy the example configuration file:
   ```bash
   cp .env.example .env
   ```

2. Edit the `.env` file and configure your settings:
   - **LLM Provider**: Choose your language model provider
   - **Embedding Provider**: Select how text will be converted to vectors
   - **Vector Store**: Choose your database (SQLite for development, OceanBase for production)

> **Note:** The `auto_config()` function automatically:
> - Looks for a `.env` file in the current directory
> - Loads configuration from environment variables
> - Uses sensible defaults if no configuration is found

For more configuration options, see the full example in `.env.example` or refer to the [Configuration Guide](docs/guides/0003-configuration.md).

## Understanding Async Operations

Async operations in powermem provide several key advantages:

### Key Benefits

- **Non-blocking**: Operations don't block the event loop, allowing other tasks to run
- **Concurrent processing**: Execute multiple memory operations simultaneously
- **High throughput**: Process many operations in parallel, significantly improving performance
- **Better I/O performance**: Ideal for operations involving network calls or database queries
- **Scalability**: Handle more concurrent requests without creating multiple threads

### How Async Works in Powermem

1. **Async methods**: All memory operations (`add`, `search`, `update`, `delete`) are async
2. **Event loop**: Operations run on Python's asyncio event loop
3. **Concurrent execution**: Use `asyncio.gather()` to run multiple operations concurrently
4. **Resource management**: Properly initialize and cleanup async resources

### Performance Considerations

- **I/O-bound operations**: Async shines when waiting for network or database responses
- **CPU-bound operations**: For CPU-intensive tasks, consider using `asyncio.to_thread()`
- **Batch size**: When processing many items, use batching to avoid overwhelming the system
- **Connection pooling**: Async operations can efficiently share database connections

## Initialize Async Memory

The first step in using async memory is to create and initialize an `AsyncMemory` instance. Unlike the synchronous version, async memory requires explicit initialization.

### Understanding AsyncMemory Initialization

The `AsyncMemory` class:
- Requires explicit initialization with `await async_memory.initialize()`
- Sets up async connections to vector stores and embedding services
- Must be initialized before any operations can be performed
- Should be properly closed when done (though Python's garbage collector handles this)

### Important: Always Initialize

Unlike the sync `Memory` class, `AsyncMemory` requires you to call `initialize()` before use. This is because:
- Async connections need to be established
- Some vector stores require async connection pools
- Embedding services may need async HTTP clients

Let's create and initialize an AsyncMemory instance:

```python
import asyncio
from powermem import AsyncMemory, auto_config

async def main():
    # Load configuration from .env file
    config = auto_config()
    
    # Create async memory instance
    async_memory = AsyncMemory(config=config)
    
    # Initialize (required for async)
    await async_memory.initialize()
    
    print("✓ AsyncMemory initialized successfully!")
    
    # Your async operations go here
    # Don't forget to close when done (optional, but recommended)
    # await async_memory.close()

# Run async function
asyncio.run(main())
```

### Using JSON/Dictionary Configuration

Instead of using `.env` files, you can also pass configuration directly as a Python dictionary (JSON-like format). This is useful when:
- You want to load configuration from a JSON file
- You need to programmatically generate configuration
- You're embedding configuration in your application code

Here's an example using a dictionary configuration:

```python
import asyncio
from powermem import AsyncMemory

async def main():
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
    
    # Create async memory instance with dictionary config
    async_memory = AsyncMemory(config=config)
    
    # Initialize (required for async)
    await async_memory.initialize()
    
    print("✓ AsyncMemory initialized with JSON config!")
    
    # Your async operations go here
    # await async_memory.close()

# Run async function
asyncio.run(main())
```

You can also load configuration from a JSON file:

```python
import asyncio
import json
from powermem import AsyncMemory

async def main():
    # Load configuration from JSON file
    with open('config.json', 'r') as f:
        config = json.load(f)
    
    # Create async memory instance
    async_memory = AsyncMemory(config=config)
    
    # Initialize (required for async)
    await async_memory.initialize()
    
    print("✓ AsyncMemory initialized from JSON file!")
    
    # Your async operations go here
    # await async_memory.close()

# Run async function
asyncio.run(main())
```

> **Note:** When using dictionary/JSON configuration, make sure to include all required fields (`llm`, `embedder`, `vector_store`) with their respective `provider` and `config` sections. For more configuration options, see the [Configuration Guide](docs/guides/0003-configuration.md).

> **Tip:** In production applications, consider using async context managers or ensuring proper cleanup. The `initialize()` method sets up connections that should ideally be closed when your application shuts down.

## Add Memories Asynchronously

Adding memories asynchronously is similar to the synchronous version, but uses `await` to handle the async operation. This allows your application to continue processing other tasks while the memory is being stored.

### Understanding Async Add Operations

When you call `await async_memory.add()`:
1. The operation is scheduled on the event loop
2. Your code can yield control to other tasks
3. The memory is processed (embedded, stored) asynchronously
4. The result is returned when complete

### Key Differences from Sync Version

- **Use `await`**: All operations must be awaited
- **Inside async function**: Must be called from an async function
- **Non-blocking**: Other async tasks can run while waiting
- **Same parameters**: Takes the same parameters as sync version

### Handling Results

The result format is identical to the sync version:
- Returns a dictionary with a `results` list
- Each result contains memory ID, content, and metadata
- Empty results list may indicate deduplication occurred

Let's add a memory asynchronously:

```python
import asyncio
from powermem import AsyncMemory, auto_config

async def main():
    config = auto_config()
    async_memory = AsyncMemory(config=config)
    await async_memory.initialize()
    
    # Add memory asynchronously
    result = await async_memory.add(
        "User likes Python programming",  # messages parameter (first positional argument)
        user_id="user123"
    )
    
    # Handle result - check if results list is not empty
    results_list = result.get('results', [])
    if results_list:
        memory_id = results_list[0].get('id', 'N/A')
        print(f"✓ Memory added! ID: {memory_id}")
    else:
        print("✓ Memory operation completed (may have been deduplicated)")

asyncio.run(main())
```

> **Note:** The async `add()` method works exactly like the sync version, but doesn't block your event loop. This means other async operations can run concurrently while the memory is being processed.

## Concurrent Memory Operations

One of the biggest advantages of async operations is the ability to process multiple memories concurrently. Instead of waiting for each operation to complete sequentially, you can run them all at the same time.

### Why Concurrent Operations Matter

- **Speed**: Process multiple operations simultaneously instead of one-by-one
- **Efficiency**: Better utilization of I/O resources (network, database)
- **Throughput**: Handle more operations per second
- **Scalability**: Essential for high-performance applications

### How Concurrent Execution Works

1. **Create tasks**: Build a list of coroutine objects (don't await them yet)
2. **Gather tasks**: Use `asyncio.gather()` to run all tasks concurrently
3. **Wait for completion**: All tasks execute in parallel
4. **Collect results**: Get results from all operations

### Understanding `asyncio.gather()`

- **Concurrent execution**: All tasks run at the same time
- **Result order**: Results are returned in the same order as input tasks
- **Error handling**: If any task fails, you can choose to cancel others or continue
- **Resource limits**: Be mindful of connection limits (database, API rate limits)

### Best Practices

- **Batch size**: Don't create too many concurrent tasks at once
- **Rate limiting**: Respect API rate limits when using external services
- **Error handling**: Wrap in try-except to handle individual failures
- **Resource management**: Monitor connection pool usage

Let's add multiple memories concurrently:

```python
import asyncio
from powermem import AsyncMemory, auto_config

async def main():
    config = auto_config()
    async_memory = AsyncMemory(config=config)
    await async_memory.initialize()
    
    # Add multiple memories concurrently
    memories = [
        "User likes Python programming",
        "User prefers email support",
        "User works as a software engineer",
        "User favorite color is blue"
    ]
    
    # Create tasks for concurrent execution
    # Note: We're creating coroutine objects, not awaiting them yet
    tasks = [
        async_memory.add(mem, user_id="user123")  # messages as first positional argument
        for mem in memories
    ]
    
    # Execute all tasks concurrently
    # This runs all add operations in parallel
    results = await asyncio.gather(*tasks)
    
    print(f"✓ Added {len(results)} memories concurrently")
    
    # Process individual results if needed
    for i, result in enumerate(results):
        results_list = result.get('results', [])
        if results_list:
            print(f"  Memory {i+1}: {results_list[0].get('memory', 'N/A')}")

asyncio.run(main())
```

> **Tip:** The `*tasks` syntax unpacks the list of tasks. This allows `asyncio.gather()` to receive multiple coroutine objects as separate arguments. All operations will execute concurrently, significantly faster than sequential execution.

## Async Search

Searching memories asynchronously allows you to perform semantic searches without blocking your application. This is especially useful in web applications where you need to handle multiple search requests concurrently.

### Understanding Async Search

Async search works the same way as sync search, but:
- **Non-blocking**: Doesn't block the event loop while searching
- **Concurrent queries**: Can handle multiple search requests simultaneously
- **Same semantics**: Uses the same semantic search algorithm
- **Better performance**: In high-concurrency scenarios

### Search Performance with Async

- **Multiple users**: Handle search requests from multiple users concurrently
- **Parallel searches**: Run multiple searches at the same time
- **Resource sharing**: Efficiently share database connections
- **Response time**: Better response times under load

### Use Cases for Async Search

- **Web APIs**: Handle multiple search requests in parallel
- **Real-time applications**: Search while maintaining responsiveness
- **Batch queries**: Search across multiple users concurrently
- **Analytics**: Run multiple analytical queries in parallel

Let's search memories asynchronously:

```python
import asyncio
from powermem import AsyncMemory, auto_config

async def main():
    config = auto_config()
    async_memory = AsyncMemory(config=config)
    await async_memory.initialize()
    
    # Add some memories first
    await async_memory.add("User likes Python", user_id="user123")
    await async_memory.add("User prefers email", user_id="user123")
    
    # Search asynchronously
    results = await async_memory.search(
        query="user preferences",
        user_id="user123",
        limit=5
    )
    
    print(f"Found {len(results.get('results', []))} memories:")
    for result in results.get('results', []):
        print(f"  - {result['memory']}")

asyncio.run(main())
```

> **Note:** You can also run multiple searches concurrently using `asyncio.gather()`, just like with `add()` operations. This is useful when you need to search for different users or different queries simultaneously.

## Batch Processing

When dealing with large datasets, it's important to process memories in batches rather than all at once. This prevents overwhelming your system and helps manage resources efficiently.

### Why Batch Processing?

- **Resource management**: Avoids exhausting database connections or API rate limits
- **Memory efficiency**: Processes data in manageable chunks
- **Progress tracking**: Easier to track progress and handle errors
- **Rate limiting**: Respects API rate limits when using external services
- **Error recovery**: If one batch fails, others can still succeed

### Batch Processing Strategy

1. **Divide into batches**: Split your data into smaller chunks
2. **Process concurrently**: Within each batch, process items concurrently
3. **Wait for completion**: Wait for each batch to complete before starting the next
4. **Handle errors**: Implement error handling for failed batches

### Choosing Batch Size

The optimal batch size depends on:
- **API rate limits**: How many requests per second your API allows
- **Database connections**: Connection pool size
- **Memory constraints**: Available system memory
- **Network bandwidth**: Your network capacity

Common batch sizes:
- **Small batches (5-10)**: For rate-limited APIs
- **Medium batches (10-50)**: For most use cases
- **Large batches (50-100)**: For high-capacity systems

### Best Practices

- **Monitor performance**: Adjust batch size based on actual performance
- **Handle failures**: Implement retry logic for failed batches
- **Progress reporting**: Log progress to track long-running operations
- **Resource cleanup**: Ensure proper cleanup between batches

Let's process memories in batches:

```python
import asyncio
from powermem import AsyncMemory, auto_config

async def main():
    config = auto_config()
    async_memory = AsyncMemory(config=config)
    await async_memory.initialize()
    
    # Large list of memories
    memories = [
        f"Memory {i}: User preference {i}"
        for i in range(100)
    ]
    
    # Process in batches
    batch_size = 10
    total_batches = (len(memories) + batch_size - 1) // batch_size
    
    for i in range(0, len(memories), batch_size):
        batch = memories[i:i+batch_size]
        batch_num = i // batch_size + 1
        
        # Create tasks for batch
        tasks = [
            async_memory.add(mem, user_id="user123")  # messages as first positional argument
            for mem in batch
        ]
        
        # Execute batch concurrently
        try:
            results = await asyncio.gather(*tasks)
            print(f"✓ Processed batch {batch_num}/{total_batches} ({len(results)} memories)")
        except Exception as e:
            print(f"✗ Error processing batch {batch_num}: {e}")

asyncio.run(main())
```

> **Tip:** For production applications, consider adding:
> - Progress bars or logging
> - Retry logic for failed operations
> - Rate limiting to respect API limits
> - Checkpointing to resume from failures


## Async with Intelligent Memory

Intelligent memory features work seamlessly with async operations. This allows you to leverage powermem's intelligent fact extraction and deduplication while maintaining high performance through async operations.

### What is Intelligent Memory?

Intelligent memory (`infer=True`) enables:
- **Automatic fact extraction**: Extracts facts from conversations automatically
- **Smart deduplication**: Prevents storing duplicate or very similar memories
- **Context understanding**: Better understands context from conversation history
- **Multi-turn conversations**: Handles conversation threads effectively

### Async + Intelligent Memory Benefits

- **Non-blocking processing**: LLM calls for fact extraction don't block your event loop
- **Concurrent processing**: Process multiple conversations simultaneously
- **Better throughput**: Handle more intelligent memory operations per second
- **Scalability**: Scale intelligent memory processing efficiently

### When to Use Intelligent Memory

- **Conversation processing**: When processing chat logs or conversations
- **Automatic extraction**: When you want facts extracted automatically
- **Deduplication**: When you need to prevent duplicate memories
- **Complex contexts**: When memories depend on conversation context

### Performance Considerations

- **LLM calls**: Intelligent memory requires LLM API calls, which are I/O-bound (perfect for async)
- **Processing time**: Takes longer than simple add operations
- **Cost**: Each intelligent operation uses LLM tokens
- **Concurrent limits**: Be mindful of LLM API rate limits

Let's use async operations with intelligent memory features:

```python
import asyncio
from powermem import AsyncMemory, auto_config

async def main():
    config = auto_config()
    async_memory = AsyncMemory(config=config)
    await async_memory.initialize()
    
    # Add memory with intelligent processing
    # This will extract facts from the conversation automatically
    result = await async_memory.add(
        messages=[
            {"role": "user", "content": "I'm Alice, a software engineer"},
            {"role": "assistant", "content": "Nice to meet you!"}
        ],
        user_id="user123",
        infer=True  # Enable intelligent fact extraction
    )
    
    print(f"✓ Processed conversation, extracted {len(result.get('results', []))} memories:")
    for mem in result.get('results', []):
        print(f"  - {mem.get('memory', '')}")

asyncio.run(main())
```

> **Note:** Intelligent memory operations are perfect for async because they involve network calls to LLM APIs. Async allows these operations to run concurrently, significantly improving throughput when processing multiple conversations.

## Async Update and Delete

Updating and deleting memories asynchronously follows the same patterns as other async operations. These operations are particularly useful in web applications where you need to handle multiple update/delete requests concurrently.

### Understanding Async Update

The `update()` method:
- **Requires memory ID**: You need the ID from when the memory was created
- **Regenerates embedding**: Creates a new vector embedding for the updated content
- **Non-blocking**: Doesn't block while updating
- **Same parameters**: Takes the same parameters as sync version

### Understanding Async Delete

The `delete()` method:
- **Requires memory ID**: You need the specific memory ID to delete
- **Permanent**: Deletion is permanent (same as sync version)
- **Returns boolean**: Returns `True` on success, `False` on failure
- **Non-blocking**: Executes asynchronously

### Use Cases for Async Update/Delete

- **Web APIs**: Handle multiple update/delete requests concurrently
- **Batch operations**: Update or delete multiple memories in parallel
- **Real-time applications**: Update memories without blocking the application
- **Data management**: Efficiently manage large numbers of memories

### Best Practices

- **Error handling**: Always check return values and handle errors
- **Concurrent operations**: You can update/delete multiple memories concurrently
- **Resource cleanup**: Ensure proper cleanup in long-running applications
- **Validation**: Validate memory IDs before operations

Let's update and delete memories asynchronously:

```python
import asyncio
from powermem import AsyncMemory, auto_config

async def main():
    config = auto_config()
    async_memory = AsyncMemory(config=config)
    await async_memory.initialize()
    
    # Add memory (using infer=False to ensure it's added for demo purposes)
    # In production, you might want infer=True for intelligent deduplication
    result = await async_memory.add(
        "User likes Python",  # messages as first positional argument
        user_id="user123",
        infer=False  # Disable intelligent deduplication for demo
    )
    
    # Handle result - check if results list is not empty
    results_list = result.get('results', [])
    if not results_list:
        print("Error: No memory was added")
        raise ValueError("Cannot update/delete: memory was not added")
    
    memory_id = results_list[0].get('id')
    if not memory_id:
        print("Error: Memory ID not found in result")
        raise ValueError("Cannot update/delete: memory ID not found")
    
    # Update memory asynchronously
    updated = await async_memory.update(
        memory_id=memory_id,
        content="User loves Python programming"
    )
    print(f"✓ Updated memory: {updated.get('memory', 'N/A')}")
    
    # Delete memory asynchronously
    success = await async_memory.delete(memory_id)
    if success:
        print(f"✓ Deleted memory {memory_id}")
    else:
        print(f"✗ Failed to delete memory {memory_id}")

asyncio.run(main())
```

### Concurrent Update/Delete Operations

You can also perform multiple update or delete operations concurrently:

```python
# Example: Delete multiple memories concurrently
memory_ids = ["id1", "id2", "id3"]
tasks = [async_memory.delete(mid) for mid in memory_ids]
results = await asyncio.gather(*tasks)
success_count = sum(1 for r in results if r)
print(f"✓ Deleted {success_count}/{len(memory_ids)} memories")
```

> **Tip:** Like other async operations, you can use `asyncio.gather()` to perform multiple update or delete operations concurrently. This is especially useful when managing large numbers of memories or handling batch operations.