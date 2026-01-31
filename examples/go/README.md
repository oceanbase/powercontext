# PowerMem Go Client Example

A simple, lightweight Go client example demonstrating how to integrate PowerMem's intelligent memory capabilities into Go applications.

## Prerequisites

1. **Go 1.24+** installed
2. **PowerMem API Server** running (see [API Server Documentation](../../docs/api/0005-api_server.md))
3. **LLM/Embedding API Key** - Required for creating memories and semantic search. Configure in the server's `.env` file

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

### 2. Run the Go Example

```bash
cd examples/go

# Run with default settings (localhost:8000, no auth)
go run .

# Or with custom configuration
# Base URL of the PowerMem API server
export POWERMEM_BASE_URL=http://localhost:8000
# API key for authentication (if server auth enabled)
export POWERMEM_API_KEY=your-api-key-123 
go run .
```

## API Operations

### 1. Health Check

Check the health status of the PowerMem API server. This is a public endpoint that does not require authentication.

```go
client := NewClient("http://localhost:8000", "your-api-key")
health, err := client.Health()
fmt.Printf("Status: %s\n", health.Status)
```

**Example Output:**

```
✓ Status: healthy
  Timestamp: 2026-01-31 06:18:04
```

### 2. Create Memory

Create a new memory. When `Infer` is enabled, PowerMem uses LLM to automatically extract multiple facts from the content and stores them as separate memories.

```go
infer := true // Enable intelligent extraction
req := &CreateMemoryRequest{
    Content: "User likes coffee and goes to Starbucks every morning. They prefer latte.",
    UserID:  "user-123",
    AgentID: "agent-456",
    Metadata: map[string]interface{}{
        "source": "conversation",
    },
    Infer: &infer,
}

memories, err := client.CreateMemory(req)
for _, mem := range memories {
    fmt.Printf("Created: %s - %s\n", mem.MemoryID, mem.Content)
}
```

**Example Output:**

```
✓ Created 3 memory(ies):
  [1] ID: 672687041732935680
      Content: Likes coffee
  [2] ID: 672687041741324288
      Content: Goes to Starbucks every morning
  [3] ID: 672687041749712896
      Content: Prefers latte
```

### 3. List Memories

Retrieve a list of memories with pagination, filtering by user/agent, and sorting options.

```go
params := ListMemoriesParams{
    UserID: "user-123",
    Limit:  10,
    Offset: 0,
    SortBy: "created_at",
    Order:  "desc",
}

list, err := client.ListMemories(params)
fmt.Printf("Total: %d memories\n", list.Total)
```

**Example Output:**

```
✓ Found 3 memories (showing 3):
  [1] ID: 672687041749712896
      Content: Prefers latte
  [2] ID: 672687041741324288
      Content: Goes to Starbucks every morning
  [3] ID: 672687041732935680
      Content: Likes coffee
```

### 4. Search Memories

Perform semantic search to find relevant memories based on natural language queries. Results are ranked by relevance score.

```go
req := &SearchMemoryRequest{
    Query:   "What beverages does the user like?",
    UserID:  "user-123",
    Limit:   5,
}

results, err := client.SearchMemories(req)
for _, r := range results.Results {
    fmt.Printf("Score: %.4f - %s\n", r.Score, r.Content)
}
```

**Example Output:**

```
✓ Query: "What beverages does the user like?"
  Found 3 results:
  [1] Score: 0.6504
      ID: 672687041749712896
      Content: Prefers latte
  [2] Score: 0.6492
      ID: 672687041732935680
      Content: Likes coffee
  [3] Score: 0.4774
      ID: 672687041741324288
      Content: Goes to Starbucks every morning
```

### 5. Update Memory

Update an existing memory's content and/or metadata. The memory ID is required.

```go
req := &UpdateMemoryRequest{
    Content: "User loves espresso",
    UserID:  "user-123",
    Metadata: map[string]interface{}{
        "updated": true,
    },
}

memory, err := client.UpdateMemory(memoryID, req)
```

**Example Output:**

```
✓ Updated memory ID: 672687041732935680
  New content: User loves espresso and visits Starbucks daily
  Updated at: 2026-01-31 06:18:11
```

### 6. Delete Memory

Permanently delete a memory by its ID. Requires user_id and agent_id for access control.

```go
err := client.DeleteMemory(memoryID, "user-123", "agent-456")
```

**Example Output:**

```
✓ Deleted memory ID: 672687041732935680
```

### 7. Get User Memories

Retrieve all memories for a specific user with pagination support.

```go
list, err := client.GetUserMemories("user-123", 20, 0)
```

**Example Output:**

```
✓ Found 2 memories for user-123
```

## Handling 64-bit Memory IDs

PowerMem uses 64-bit integers for memory IDs, which can exceed JavaScript's safe integer range. This client handles them properly:

```go
// MemoryID is a custom type that handles both number and string JSON representations
type MemoryID int64

// Use as string to avoid precision loss in JSON
memoryIDStr := memoryID.String()

// Use as int64 when needed
memoryIDInt := memoryID.Int64()
```

## Error Handling

The client provides detailed error messages:

```go
memories, err := client.CreateMemory(req)
if err != nil {
    // Error messages include:
    // - HTTP status codes
    // - API error codes and messages
    // - Request/response details
    log.Printf("Error: %v", err)
}
```
