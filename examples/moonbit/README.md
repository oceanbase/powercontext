# PowerMem MoonBit Client Example

A simple, lightweight MoonBit client example demonstrating how to integrate PowerMem's intelligent memory capabilities into MoonBit applications.

## Prerequisites

1. **MoonBit toolchain** installed from <https://www.moonbitlang.com>
2. **PowerMem API Server** running (see [API Server Documentation](../../docs/api/0005-api_server.md))
3. **LLM/Embedding API Key** — Required for creating memories and semantic search. Configure in the server's `.env` file

> **Note**: PowerMem uses **SQLite** by default, no OceanBase installation required. SQLite is perfect for development and testing.

## Quick Start

### 1. Start the PowerMem Server

> **Note**:
> - Default database is **SQLite** (no OceanBase required)
> - Authentication is **disabled** by default (`POWERMEM_SERVER_AUTH_ENABLED=false`)

```bash
pip install powermem
powermem-server --host 0.0.0.0 --port 8000
```

#### Configure API Keys (Required)

Configure the server's `.env` file with your LLM/Embedding provider API key:

```bash
# Copy from .env.example if not exists
cp .env.example .env

# Edit .env and set your API keys:
LLM_API_KEY=your-llm-api-key                # Required for intelligent memory extraction
EMBEDDING_API_KEY=your-embedding-api-key    # Required for creating memories and search

# Database (SQLite is default, no changes needed)
DATABASE_PROVIDER=sqlite
SQLITE_PATH=./data/powermem_dev.db
```

#### Enable Authentication (Optional)

To enable API key authentication, configure the server's `.env` file:

```bash
# In your .env file
POWERMEM_SERVER_AUTH_ENABLED=true
POWERMEM_SERVER_API_KEYS=your-api-key-123,another-key-456
```

### 2. Run the MoonBit Example

```bash
cd examples/moonbit

# Run with default settings (localhost:8000, no auth).
# `moon run` fetches dependencies on first invocation.
moon run --target native .

# Or with custom configuration
# Base URL of the PowerMem API server
export POWERMEM_BASE_URL=http://localhost:8000
# API key for authentication (if server auth enabled)
export POWERMEM_API_KEY=your-api-key-123
moon run --target native .
```

## API Operations

### 1. Health Check

Check the health status of the PowerMem API server. This is a public endpoint that does not require authentication.

```moonbit
let client = Client::new("http://localhost:8000", api_key="your-api-key")
let health = client.health()
println("Status: \{health.status}")
```

**Example Output:**

```
  status: healthy
```

### 2. System Status

Get runtime information about the server — version, status, storage backend, and the active LLM provider.

```moonbit
let s = client.system_status()
println("Version: \{s.version}")
println("Status:  \{s.status}")
```

**Example Output:**

```
  version: 1.1.0
  status:  operational
  storage: sqlite
  llm:     ollama
```

### 3. Create Memory

Create a new memory. When `infer` is enabled, PowerMem uses LLM to automatically extract multiple facts from the content and stores them as separate memories.

```moonbit
let metadata = Json::object({
  "source": "conversation".to_json(),
  "importance": "high".to_json(),
})
let memories = client.create_memory(
  content="User likes coffee and goes to Starbucks every morning. They prefer latte.",
  user_id="user-123",
  agent_id="agent-456",
  metadata~,
  infer=true,
)
for m in memories {
  println("Created: \{m.id} - \{m.content}")
}
```

**Example Output:**

```
  created 3 memory(ies):
    [1] 672687041732935680: User is a coffee drinker
    [2] 672687041741324288: User has preference for latte
    [3] 672687041749712896: User goes to Starbucks every morning
```

### 4. List Memories

Retrieve a list of memories with pagination and filtering by user or agent.

```moonbit
let list = client.list_memories(user_id="user-123", limit=10)
println("Total: \{list.total} memories")
for m in list.memories {
  println("  \{m.id}: \{m.content}")
}
```

**Example Output:**

```
  total: 3, showing 3:
    [1] 672687041749712896: User goes to Starbucks every morning
    [2] 672687041741324288: User has preference for latte
    [3] 672687041732935680: User is a coffee drinker
```

### 5. Search Memories

Perform semantic search to find relevant memories based on natural language queries. Results are ranked by relevance score.

```moonbit
let resp = client.search_memories(
  query="What beverages does the user like?",
  user_id="user-123",
  limit=5,
)
println("Total: \{resp.total}")
for r in resp.results {
  let score = match r.score {
    Some(s) => s.to_string()
    None => "-"
  }
  println("\{r.memory_id} score=\{score}: \{r.content}")
}
```

**Example Output:**

```
  query: What beverages does the user like?
  total: 3
    [1] 672687041732935680 score=0.7046449923421033: User is a coffee drinker
    [2] 672687041741324288 score=0.6876197672707064: User has preference for latte
    [3] 672687041749712896 score=0.5320688635347244: User goes to Starbucks every morning
```

### 6. Update Memory

Update an existing memory's content and/or metadata. The memory ID is required.

```moonbit
let updated = client.update_memory(
  memory_id="672687041732935680",
  content="User loves espresso and visits Starbucks daily",
)
println("Updated \{updated.id}: \{updated.content}")
match updated.updated_at {
  Some(ts) => println("At: \{ts}")
  None => ()
}
```

**Example Output:**

```
  updated 672687041732935680: User loves espresso and visits Starbucks daily
  at: 2026-04-21T05:45:47.342816Z
```

### 7. Delete Memory

Permanently delete a memory by its ID.

```moonbit
let deleted = client.delete_memory(memory_id="672687041732935680")
println("Deleted \{deleted.memory_id}")
```

**Example Output:**

```
  deleted 672687041732935680
```

## Handling 64-bit Memory IDs

PowerMem uses 64-bit Snowflake integers for memory IDs, which exceed MoonBit's 32-bit `Int`. The server sidesteps this by serializing `memory_id` as a JSON string, and this client mirrors that choice:

```moonbit
struct Memory {
  id : String        // always a string — no precision loss, ready for paths and URLs
  content : String
  // ...
}
```

You can pass these IDs directly to `update_memory`, `delete_memory`, and path-building helpers without any conversion.

## Error Handling

The client raises a typed `ApiError` suberror with specific variants so callers can distinguish transport failures from server-side rejections:

```moonbit
suberror ApiError {
  Http(status_code~ : Int, body~ : String)   // 4xx/5xx with response body
  Server(String)                             // success=false envelope from the API
  Parse(String)                              // malformed JSON or unexpected shape
  Request(String)                            // connection / transport failure
} derive(Debug)
```

Use a `try`/`catch` block to handle each case. Pattern-match the caught value to branch on specific variants:

```moonbit
try {
  let memories = client.create_memory(content="...", user_id="user-123")
  println("created \{memories.length()}")
} catch {
  Http(status_code=401, ..) => println("auth failed — check POWERMEM_API_KEY")
  e => println("other failure: \{@debug.to_string(e)}")
}
```
