# UserMemory Guide

UserMemory is PowerMem's advanced user profile management module. It automatically extracts and maintains user profile information from conversations while also managing conversation event memory.

## Prerequisites

- Python 3.10+
- PowerMem installed (`pip install powermem`)
- LLM and embedding services configured (for profile extraction)
- Vector store configured (for storing memories and profiles)

## Overview

UserMemory adds user profile management on top of `Memory`:

1. **Automatic profile extraction**: extract user-related information from conversations (basic info, interests, work background, etc.)
2. **Continuous profile updates**: update and refine the profile based on new conversations
3. **Memory + profile combined**: optionally include profile information when searching memories

### Core Capabilities

- **Conversation storage**: store user conversations as event memories
- **Profile extraction**: use an LLM to extract user profile information from conversations
- **Profile management**: save, update, and query user profiles
- **Joint search**: optionally include profile information with memory search results

## Initialization

### Basic initialization

Initialization of `UserMemory` is similar to `Memory` and accepts the same configuration options:

```python
from powermem import UserMemory, auto_config

# Use auto configuration (loads from .env)
config = auto_config()
user_memory = UserMemory(config=config)
```

### Configure with dict

```python
from powermem import UserMemory

config = {
    'llm': {
        'provider': 'qwen',
        'config': {
            'api_key': 'your_api_key_here',
            'model': 'qwen-plus',
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
            }
        }
    }
}

user_memory = UserMemory(config=config)
```

> **Important Note**: `UserMemory` internally creates a `Memory` instance to store conversation events and a `UserProfileStore` to store user profiles. Currently, `UserProfileStore` only supports OceanBase as the storage backend. If you attempt to use `UserMemory` with a different storage provider (e.g., SQLite, PostgreSQL), it will raise a `ValueError` with a clear error message. To use `UserMemory`, please configure OceanBase as your vector store provider.

## Core APIs

### 1. `add()` — Add conversation and extract user profile

Add conversation content, automatically store it as event memory, and extract/update the user profile.

#### Signature

```python
def add(
    self,
    messages,
    user_id: str,
    agent_id: Optional[str] = None,
    run_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    filters: Optional[Dict[str, Any]] = None,
    scope: Optional[str] = None,
    memory_type: Optional[str] = None,
    prompt: Optional[str] = None,
    infer: bool = True,
) -> Dict[str, Any]
```

#### Parameters
Same as `Memory.add()`
- `messages` (str | dict | list[dict]): conversation content, supports:
  - string: a raw conversation string
  - dict: a single message with `role` and `content`
  - list: OpenAI-style messages `[{"role": "user", "content": "..."}, ...]`
- `user_id` (str, required): user identifier
- `agent_id` (str, optional): agent identifier
- `run_id` (str, optional): run/session identifier
- `metadata` (dict, optional): extra metadata
- `filters` (dict, optional): advanced filter metadata
- `scope` (str, optional): memory scope (e.g., `user`, `agent`, `session`)
- `memory_type` (str, optional): memory type/category
- `prompt` (str, optional): custom prompt for intelligent processing
- `infer` (bool): whether to enable intelligent memory processing (default: True)

#### Return value

Returns a dictionary with:

```python
{
    # From Memory.add()
    "results": [
        {
            "id": 123,                    # memory ID
            "memory": "...",              # memory content
            "event": "ADD",               # event type (ADD/UPDATE/DELETE)
            "user_id": "user_001",        # user ID
            "agent_id": "test_agent",     # agent ID
            "run_id": "run_001",          # run ID
            "metadata": {...},              # metadata
            "created_at": "2024-01-01T00:00:00",  # created time
            "previous_memory": "..."      # previous memory (UPDATE only)
        },
        ...
    ],
    "relations": {...},                   # graph relations (if graph storage enabled)

    # New fields from UserMemory
    "profile_extracted": True,            # whether profile was successfully extracted
    "profile_content": "..."             # extracted profile content (if any)
}
```

#### Examples

```python
# Example 1: add a conversation list
conversation = [
    {"role": "user", "content": "My name is Zhang. I'm a software engineer and I like Python."},
    {"role": "assistant", "content": "Nice to meet you, Zhang! Python is a great language."}
]

result = user_memory.add(
    messages=conversation,
    user_id="user_001",
    agent_id="test_agent",
    run_id="run_001"
)

print(f"Profile extracted: {result.get('profile_extracted', False)}")
if result.get('profile_content'):
    print(f"User profile: {result['profile_content']}")
print(f"Memory count: {len(result.get('results', []))}")

# Example 2: add a single message
result = user_memory.add(
    messages={"role": "user", "content": "I also enjoy reading sci‑fi and watching movies."},
    user_id="user_001",
    agent_id="test_agent"
)

# Example 3: add a raw string
result = user_memory.add(
    messages="The user mentioned they have been learning machine learning recently.",
    user_id="user_001"
)
```

### 2. `search()` — Search memories (optionally include profile)

Search for relevant memories and optionally include the user's profile in the result.

#### Signature

```python
def search(
    self,
    query: str,
    user_id: Optional[str] = None,
    agent_id: Optional[str] = None,
    run_id: Optional[str] = None,
    filters: Optional[Dict[str, Any]] = None,
    limit: int = 30,
    threshold: Optional[float] = None,
    add_profile: bool = False,
) -> Dict[str, Any]
```

#### Parameters
Same as `Memory.search()`
- `query` (str, required): search query string
- `user_id` (str, optional): filter by user ID
- `agent_id` (str, optional): filter by agent ID
- `run_id` (str, optional): filter by run ID
- `filters` (dict, optional): metadata filters for advanced filtering
- `limit` (int): max number of results (default: 30)
- `threshold` (float, optional): similarity threshold (0.0–1.0) to filter results
- `add_profile` (bool): include user profile in the results (default: False)

#### Return value

Returns a dictionary with:

```python
{
    "results": [
        {
            "memory": "...",              # memory content
            "metadata": {...},              # metadata
            "score": 0.85,                  # similarity score
            "id": 123,                      # memory ID
            "created_at": "2024-01-01T00:00:00",  # created time
            "updated_at": "2024-01-01T00:00:00",  # updated time
            "user_id": "user_001",         # user ID
            "agent_id": "test_agent",      # agent ID
            "run_id": "run_001"            # run ID
        },
        ...
    ],
    "relations": [...],                     # graph relations (if enabled)

    # If add_profile=True and user_id is provided
    "profile_content": "..."               # user profile content
}
```

#### Examples

```python
# Example 1: basic search
results = user_memory.search(
    query="user's work and interests",
    user_id="user_001",
    limit=10
)

for result in results.get('results', []):
    print(f"Memory: {result['memory']}")
    print(f"Score: {result.get('score', 0)}")
    print("---")

# Example 2: search and include profile
results = user_memory.search(
    query="user preferences",
    user_id="user_001",
    agent_id="test_agent",
    limit=5,
    threshold=0.7,  # only return results with similarity >= 0.7
    add_profile=True  # include user profile
)

# Check if profile is present
if 'profile_content' in results:
    print(f"User profile: {results['profile_content']}")

# Iterate results
for result in results.get('results', []):
    print(f"Memory: {result['memory']}")
    print(f"Score: {result.get('score', 0)}")
```

### 3. `profile()` — Get user profile

Directly get profile information for a specific user.

#### Signature

```python
def profile(
    self,
    user_id: str,
) -> Dict[str, Any]
```

#### Parameters

- `user_id` (str, required): user identifier

#### Return value

If a user profile is found, returns a dictionary containing:

```python
{
    "id": 1,                               # profile ID
    "user_id": "user_001",                 # user ID
    "profile_content": "...",              # profile content (text)
    "created_at": "2024-01-01T00:00:00",   # created time (ISO format)
    "updated_at": "2024-01-01T00:00:00"    # last updated time (ISO format)
}
```

If no profile is found, returns an empty dict `{}`.

#### Examples

```python
# Get user profile
profile = user_memory.profile(
    user_id="user_001"
)

if profile:
    print(f"User ID: {profile['user_id']}")
    print(f"Profile content: {profile['profile_content']}")
    print(f"Created at: {profile['created_at']}")
    print(f"Updated at: {profile['updated_at']}")
else:
    print("No profile found")
```

## Complete Example

Below is a complete example demonstrating the main features of `UserMemory`:

```python
from powermem import UserMemory, auto_config

# Initialize
config = auto_config()
user_memory = UserMemory(config=config)

# 1. Add initial conversation and extract profile
conversation1 = [
    {"role": "user", "content": "Hello, I'm Li, a data scientist specializing in machine learning."},
    {"role": "assistant", "content": "Nice to meet you, Li! Machine learning is a promising field."}
]

result1 = user_memory.add(
    messages=conversation1,
    user_id="user_002",
)

print("=== First conversation ===")
print(f"Profile extracted: {result1.get('profile_extracted', False)}")
if result1.get('profile_content'):
    print(f"Profile content: {result1['profile_content']}")

# 2. Add more conversation, update profile
conversation2 = [
    {"role": "user", "content": "I like reading tech blogs and often attend tech conferences."},
    {"role": "assistant", "content": "Sounds like you're passionate about learning new technologies!"}
]

result2 = user_memory.add(
    messages=conversation2,
    user_id="user_002",
)

print("\n=== Second conversation ===")
print(f"Profile updated: {result2.get('profile_extracted', False)}")
if result2.get('profile_content'):
    print(f"Updated profile: {result2['profile_content']}")

# 3. Get the full user profile
profile = user_memory.profile(
    user_id="user_002",
)

print("\n=== Full user profile ===")
if profile:
    print(f"Profile ID: {profile['id']}")
    print(f"Profile content: {profile['profile_content']}")
    print(f"Last updated: {profile['updated_at']}")

# 4. Search memories and include profile
search_results = user_memory.search(
    query="user's work and interests",
    user_id="user_002",
    limit=5,
    add_profile=True  # include user profile
)

print("\n=== Search results ===")
if 'profile_content' in search_results:
    print(f"User profile: {search_results['profile_content']}\n")

print(f"Found {len(search_results.get('results', []))} related memories:")
for i, result in enumerate(search_results.get('results', []), 1):
    print(f"{i}. {result['memory']} (score: {result.get('score', 0):.2f})")
```

## Best Practices

1. **Always provide `user_id`**: the `add()` method requires `user_id`; ensure each user has a unique identifier

2. **Use `agent_id` to distinguish agents**: in multi‑agent scenarios, use `agent_id` to separate profiles and memories across agents

3. **Use `run_id` appropriately**: use `run_id` to distinguish sessions or runs for more precise profile management

4. **Check profiles regularly**: use `profile()` to periodically check and ensure profile information remains accurate

5. **Include profile when needed**: when additional context is useful, set `add_profile=True` to include the user profile in search results

6. **Handle empty profiles**: if `profile()` returns `{}`, no profile has been extracted yet; call `add()` with conversation data first

## Relationship with Memory

`UserMemory` uses an internal `Memory` instance to store conversation events, so:

- `UserMemory` supports all configuration options of `Memory`
- `UserMemory.add()` and `UserMemory.search()` call the underlying `Memory` methods
- `UserMemory` adds user profile management on top of `Memory`
- You can directly access `user_memory.memory` to use the underlying `Memory` instance if needed

## Related Documents

- [Getting Started](0001-getting_started.md) — learn the basics of PowerMem
- [Configuration Guide](0003-configuration.md) — detailed configuration
- [Multi‑agent Guide](0005-multi_agent.md) — using multiple agents
