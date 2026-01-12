# Memory API Reference

`Memory` is the core synchronous memory management class in powermem. It provides a simple interface for storing, retrieving, and managing memories in LLM applications.

## Class: `Memory`

```python
from powermem import Memory

memory = Memory(config=config)
```

### Constructor

#### `Memory(config=None, storage_type=None, llm_provider=None, embedding_provider=None, agent_id=None)`

Create a new Memory instance.

**Parameters:**
- `config` (dict, optional): Configuration dictionary. If None, loads from environment variables.
- `storage_type` (str, optional): Storage backend type (deprecated, use config).
- `llm_provider` (str, optional): LLM provider name (deprecated, use config).
- `embedding_provider` (str, optional): Embedding provider name (deprecated, use config).
- `agent_id` (str, optional): Agent identifier for multi-agent scenarios.

**Example:**
```python
from powermem import Memory, auto_config

# Auto-load from .env
config = auto_config()
memory = Memory(config=config, agent_id="my_agent")
```

### Core Methods

#### `add(messages, user_id=None, agent_id=None, run_id=None, metadata=None, filters=None, scope=None, memory_type=None, prompt=None, infer=True)`

Add a memory to the store.

**Parameters:**
- `messages` (str | dict | list[dict]): Memory content. Can be:
  - A string (will be converted to a message)
  - A single message dict with `role` and `content`
  - A list of message dicts in OpenAI format
- `user_id` (str, optional): User identifier.
- `agent_id` (str, optional): Agent identifier.
- `run_id` (str, optional): Run/conversation identifier.
- `metadata` (dict, optional): Additional metadata.
- `filters` (dict, optional): Filter metadata for sub-store routing and advanced filtering. Used for routing memories to specific sub-stores based on metadata values. See [Filter Parameter Format](#filter-parameter-format) below for format details.
- `scope` (str, optional): Memory scope (e.g., 'user', 'agent', 'session').
- `memory_type` (str, optional): Memory type classification.
- `prompt` (str, optional): Custom prompt for intelligent processing.
- `infer` (bool): Enable intelligent memory processing (default: True).

**Returns:**
- `dict`: Result containing added memory information.

**Example:**
```python
# Simple text memory
result = memory.add(
    messages="User likes Python programming",
    user_id="user123"
)

# With intelligent processing (default)
result = memory.add(
    messages=[
        {"role": "user", "content": "I'm Alice, a software engineer"},
        {"role": "assistant", "content": "Nice to meet you!"}
    ],
    user_id="user123"
    # infer=True by default - enables intelligent fact extraction
)

# Single message dict
result = memory.add(
    messages={"role": "user", "content": "I prefer dark mode"},
    user_id="user123"
)
```

#### `search(query, user_id=None, agent_id=None, run_id=None, filters=None, limit=30, threshold=None)`

Search for memories using semantic similarity.

**Parameters:**
- `query` (str): Search query string.
- `user_id` (str, optional): Filter by user ID.
- `agent_id` (str, optional): Filter by agent ID.
- `run_id` (str, optional): Filter by run ID.
- `filters` (dict, optional): Metadata filters for advanced filtering. See [Filter Parameter Format](#filter-parameter-format) below for detailed documentation.
- `limit` (int): Maximum number of results (default: 30).
- `threshold` (float, optional): Similarity threshold (0.0-1.0) for filtering results.

**Returns:**
- `dict`: Search results with memories and scores. Format:
  ```python
  {
      "results": [
          {
              "memory": "memory content",
              "metadata": {...},
              "score": 0.85,
              "id": 123,
              ...
          }
      ],
      "relations": [...]  # If graph store is enabled
  }
  ```

**Example:**
```python
results = memory.search(
    query="user preferences",
    user_id="user123",
    limit=5,
    threshold=0.7  # Only return results with similarity >= 0.7
)

for result in results.get('results', []):
    print(f"Memory: {result['memory']}")
    print(f"Score: {result.get('score', 0)}")
```

### Filter Parameter Format

The `filters` parameter allows you to perform advanced filtering on memory metadata. It supports both simple and complex filter formats with various operators.

#### Simple Filter Format

**Exact Match:**
```python
# Filter by exact value
filters = {"category": "food"}
filters = {"priority": "high"}
filters = {"status": "active"}
```

**List Values (IN operator):**
```python
# Filter where field value is in a list
filters = {"category": ["food", "drink", "dessert"]}
filters = {"tag": ["important", "urgent"]}
```

**None/Null Check:**
```python
# Filter where field is None
filters = {"deleted_at": None}
```

#### Comparison Operators

Use comparison operators for numeric or date comparisons:

```python
# Single comparison operator
filters = {"rating": {"gte": 4.0}}  # rating >= 4.0
filters = {"price": {"lt": 100}}     # price < 100
filters = {"age": {"gt": 18}}        # age > 18
filters = {"score": {"lte": 0.8}}    # score <= 0.8

# Multiple operators on same field (AND logic)
filters = {"rating": {"gte": 4.0, "lte": 5.0}}  # 4.0 <= rating <= 5.0
filters = {"price": {"gt": 10, "lt": 100}}      # 10 < price < 100
```

**Supported Comparison Operators:**
- `eq`: Equal to (`==`)
- `ne`: Not equal to (`!=`)
- `gt`: Greater than (`>`)
- `gte`: Greater than or equal to (`>=`)
- `lt`: Less than (`<`)
- `lte`: Less than or equal to (`<=`)

#### List Operators

**IN and NOT IN:**
```python
# Field value is in list
filters = {"category": {"in": ["food", "drink"]}}
filters = {"user_id": {"in": ["user1", "user2", "user3"]}}

# Field value is NOT in list
filters = {"status": {"nin": ["deleted", "archived"]}}
filters = {"tag": {"nin": ["deprecated"]}}
```

#### String Pattern Matching

**LIKE and ILIKE:**
```python
# Case-sensitive pattern matching (LIKE)
filters = {"name": {"like": "%python%"}}      # Contains "python"
filters = {"email": {"like": "%@example.com"}} # Ends with "@example.com"

# Case-insensitive pattern matching (ILIKE)
filters = {"title": {"ilike": "%tutorial%"}}  # Contains "tutorial" (case-insensitive)
filters = {"description": {"ilike": "how to%"}} # Starts with "how to" (case-insensitive)
```

**Note:** Use `%` as wildcard for pattern matching.

#### Logical Operators (AND/OR)

Combine multiple conditions using logical operators:

**AND Logic:**
```python
# All conditions must be true
filters = {
    "AND": [
        {"user_id": "alice"},
        {"category": "food"},
        {"rating": {"gte": 4.0}}
    ]
}
```

**OR Logic:**
```python
# At least one condition must be true
filters = {
    "OR": [
        {"rating": {"gte": 4.0}},
        {"priority": "high"}
    ]
}
```

**Nested Logic:**
```python
# Complex nested conditions
filters = {
    "AND": [
        {"user_id": "alice"},
        {
            "OR": [
                {"rating": {"gte": 4.0}},
                {"priority": "high"}
            ]
        },
        {"category": {"in": ["food", "drink"]}}
    ]
}
```

#### Filterable Fields

You can filter on the following fields:

**Standard Fields:**
- `user_id` (str): User identifier
- `agent_id` (str): Agent identifier
- `run_id` (str): Run/conversation identifier
- `actor_id` (str): Actor identifier
- `hash` (str): Memory hash
- `created_at` (str/datetime): Creation timestamp
- `updated_at` (str/datetime): Last update timestamp
- `category` (str): Memory category

**Custom Metadata Fields:**
Any fields stored in the `metadata` dictionary when adding memories are also filterable:
```python
# When adding memory with custom metadata
memory.add(
    messages="User likes Python",
    user_id="user123",
    metadata={
        "tags": ["programming", "python"],
        "priority": "high",
        "rating": 5.0,
        "department": "engineering"
    }
)

# Filter by custom metadata fields
filters = {"tags": {"in": ["programming"]}}
filters = {"priority": "high"}
filters = {"rating": {"gte": 4.0}}
filters = {"department": "engineering"}
```

#### Complete Examples

**Example 1: Filter by User ID and Category**
```python
results = memory.search(
    query="favorite foods",
    filters={"category": "food"}
)
```

**Example 2: Filter by Rating Range**
```python
results = memory.search(
    query="restaurant recommendations",
    filters={"rating": {"gte": 4.0, "lte": 5.0}}
)
```

**Example 3: Filter by Tags (IN operator)**
```python
results = memory.search(
    query="programming tips",
    filters={"tags": {"in": ["python", "tutorial"]}}
)
```

**Example 4: Filter by Time Range**
```python
from datetime import datetime, timedelta

# Memories created in the last 7 days
week_ago = (datetime.now() - timedelta(days=7)).isoformat()
filters = {"created_at": {"gte": week_ago}}

results = memory.search(
    query="recent conversations",
    filters=filters
)
```

**Example 5: Complex Filter with AND/OR**
```python
results = memory.search(
    query="important tasks",
    filters={
        "AND": [
            {"priority": "high"},
            {
                "OR": [
                    {"status": "pending"},
                    {"status": "in_progress"}
                ]
            },
            {"category": {"in": ["work", "personal"]}}
        ]
    }
)
```

**Example 6: Filter by Custom Metadata**
```python
# Search for memories with specific custom tags
results = memory.search(
    query="project updates",
    filters={
        "AND": [
            {"project_id": "project-123"},
            {"department": "engineering"},
            {"status": {"ne": "archived"}}
        ]
    }
)
```

**Example 7: Pattern Matching**
```python
# Find memories with email addresses from specific domain
results = memory.search(
    query="contact information",
    filters={"email": {"ilike": "%@company.com"}}
)
```

#### Notes

1. **Filter Precedence**: When both `user_id`/`agent_id`/`run_id` parameters and `filters` are provided, they are merged. The explicit parameters take precedence if there's a conflict.

2. **Storage Backend Support**: 
   - **OceanBase**: Supports all operators and complex logic (AND/OR)
   - **SQLite**: Supports simple equality filters only
   - **PostgreSQL**: Supports simple equality filters

3. **Performance**: Filters are applied at the database level for optimal performance. Use filters to narrow down results before semantic search.

4. **Metadata Fields**: Custom metadata fields are stored in JSON format and can be filtered using the same syntax as standard fields.

#### `get(memory_id, user_id=None, agent_id=None)`

Retrieve a specific memory by ID.

**Parameters:**
- `memory_id` (int): Memory identifier.
- `user_id` (str, optional): User identifier for permission check.
- `agent_id` (str, optional): Agent identifier for permission check.

**Returns:**
- `dict | None`: Memory data or None if not found.

**Example:**
```python
memory_data = memory.get(123, user_id="user123")
if memory_data:
    print(f"Content: {memory_data.get('memory', '')}")
```

#### `update(memory_id, content=None, user_id=None, agent_id=None, metadata=None)`

Update an existing memory.

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
updated = memory.update(
    memory_id=123,
    content="User prefers Python over Java",
    user_id="user123",
    metadata={"updated_at": "2024-01-01"}
)
```

#### `delete(memory_id, user_id=None, agent_id=None)`

Delete a memory by ID.

**Parameters:**
- `memory_id` (int): Memory identifier.
- `user_id` (str, optional): User identifier for permission check.
- `agent_id` (str, optional): Agent identifier for permission check.

**Returns:**
- `bool`: True if deleted, False otherwise.

**Example:**
```python
success = memory.delete(123, user_id="user123")
```

#### `delete_all(user_id=None, agent_id=None, run_id=None)`

Delete all memories matching criteria.

**Parameters:**
- `user_id` (str, optional): Filter by user ID.
- `agent_id` (str, optional): Filter by agent ID.
- `run_id` (str, optional): Filter by run ID.

**Returns:**
- `dict`: Deletion result with count.

**Example:**
```python
result = memory.delete_all(user_id="user123")
print(f"Deleted {result.get('count', 0)} memories")
```

#### `get_all(user_id=None, agent_id=None, run_id=None, limit=100, offset=0, filters=None)`

Retrieve all memories matching criteria.

**Parameters:**
- `user_id` (str, optional): Filter by user ID.
- `agent_id` (str, optional): Filter by agent ID.
- `run_id` (str, optional): Filter by run ID.
- `limit` (int): Maximum number of results (default: 100).
- `offset` (int): Offset for pagination (default: 0).
- `filters` (dict, optional): Metadata filters for advanced filtering. See [Filter Parameter Format](#filter-parameter-format) above for detailed documentation.

**Returns:**
- `dict`: All matching memories. Format:
  ```python
  {
      "results": [
          {
              "id": 123,
              "memory": "content",
              "metadata": {...},
              ...
          }
      ],
      "relations": [...]  # If graph store is enabled
  }
  ```

**Example:**
```python
# Simple retrieval
all_memories = memory.get_all(user_id="user123", limit=50, offset=0)
for mem in all_memories.get('results', []):
    print(f"- {mem.get('memory', '')}")

# With advanced filters
all_memories = memory.get_all(
    user_id="user123",
    filters={
        "category": "food",
        "rating": {"gte": 4.0}
    },
    limit=50,
    offset=0
)
```

### Intelligent Memory Features

When `infer=True` (default), Memory automatically:

- **Fact Extraction**: Extracts facts from conversations
- **Duplicate Detection**: Prevents duplicate memories
- **Memory Updates**: Updates existing memories when information changes
- **Conflict Resolution**: Handles contradictory information
- **Memory Consolidation**: Merges related memories

**Note:** Intelligent processing is enabled by default. Set `infer=False` to disable it for simple storage operations.

See [Getting Started Guide](../guides/0001-getting_started.md) for more details on intelligent memory features.

### Error Handling

All methods may raise exceptions. Common errors:

- `ValueError`: Invalid parameters
- `ConnectionError`: Storage backend connection issues
- `RuntimeError`: LLM or embedding service errors

**Example:**
```python
try:
    result = memory.add(memory="Test", user_id="user123")
except Exception as e:
    print(f"Error: {e}")
```

